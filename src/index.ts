import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { INotebookTracker } from '@jupyterlab/notebook';
import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';
import { NotebookLSPClient } from './lsp';
import { ICommandPalette } from '@jupyterlab/apputils';
import {
  ICompletionProviderManager,
  IInlineCompletionItem,
  IInlineCompletionList,
  IInlineCompletionProvider,
  IInlineCompletionContext,
  CompletionHandler
} from '@jupyterlab/completer';
import { CodeEditor } from '@jupyterlab/codeeditor';
import { LoginExecute, SignOutExecute } from './commands/authentication';
import { ISettingRegistry } from '@jupyterlab/settingregistry';

let ENABLED_FLAG = true;
let COMPLETION_BIND = 'Ctrl J';

class CopilotInlineProvider implements IInlineCompletionProvider {
  readonly name = 'GitHub Copilot';
  readonly identifier = 'jupyter_copilot:provider';
  readonly rank = 1000;
  notebookClients: Map<string, NotebookLSPClient>;
  private lastRequestTime: number = 0;
  private timeout: any = null;
  private lastResolved: (
    value:
      | IInlineCompletionList<IInlineCompletionItem>
      | PromiseLike<IInlineCompletionList<IInlineCompletionItem>>
  ) => void = () => {};
  private requestInProgress: boolean = false;

  constructor(notebookClients: Map<string, NotebookLSPClient>) {
    this.notebookClients = notebookClients;
  }

  async fetch(
    request: CompletionHandler.IRequest,
    context: IInlineCompletionContext
  ): Promise<IInlineCompletionList<IInlineCompletionItem>> {
    if (!ENABLED_FLAG) {
      return { items: [] };
    }

    const now = Date.now();

    // debounce mechanism
    // if a request is made within 90ms of the last request, throttle the request
    // but if it is the last request, then make the request
    if (this.requestInProgress || now - this.lastRequestTime < 150) {
      this.lastRequestTime = now;

      // this request was made less than 90ms after the last request
      // so we resolve the last request with an empty list then clear the timeout
      this.lastResolved({ items: [] });
      clearTimeout(this.timeout);

      return new Promise(resolve => {
        this.lastResolved = resolve;
        // set a timeout that will resolve the request after 200ms
        // if no calls are made within 90ms then this will resolve and fetch
        // if a call comes in < 90ms then this will be cleared and the request will be solved to empty list
        this.timeout = setTimeout(async () => {
          this.requestInProgress = true;
          this.lastRequestTime = Date.now();

          const items = await this.fetchCompletion(request, context);

          resolve(items);
        }, 200);
      });
    } else {
      // if request is not throttled, just get normally
      this.requestInProgress = true;
      this.lastRequestTime = now;

      return await this.fetchCompletion(request, context);
    }
  }

  // logic to actually fetch the completion
  private async fetchCompletion(
    _request: CompletionHandler.IRequest,
    context: IInlineCompletionContext
  ): Promise<IInlineCompletionList<IInlineCompletionItem>> {
    const editor = (context as any).editor as CodeEditor.IEditor;
    const cell = (context.widget as any)._content._activeCellIndex;
    const client = this.notebookClients.get((context.widget as any).id);
    const cursor = editor?.getCursorPosition();
    const { line, column } = cursor;
    client?.sendUpdateLSPVersion();
    const items: IInlineCompletionItem[] = [];
    const completions = await client?.getCopilotCompletion(cell, line, column);
    completions?.forEach(completion => {
      items.push({
        // sometimes completions have ``` in them, so we remove it
        insertText: completion.displayText.replace('```', ''),
        isIncomplete: false
      });
    });
    this.requestInProgress = false;
    return { items };
  }
}

/**
 * Initialization data for the jupyter_copilot extension.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter_copilot:plugin',
  description: 'GitHub Copilot for Jupyter',
  autoStart: true,
  requires: [
    INotebookTracker,
    ICompletionProviderManager,
    ICommandPalette,
    ISettingRegistry
  ],
  activate: (
    app: JupyterFrontEnd,
    notebookTracker: INotebookTracker,
    providerManager: ICompletionProviderManager,
    palette: ICommandPalette,
    settingRegistry: ISettingRegistry
  ) => {
    console.log('Jupyter Copilot Extension Activated');
    Promise.all([app.restored, settingRegistry.load(plugin.id)]).then(
      ([, settings]) => {
        const loadSettings = () => {
          ENABLED_FLAG = settings.get('flag').composite as boolean;
          console.log('Settings loaded:', ENABLED_FLAG, COMPLETION_BIND);
        };

        COMPLETION_BIND = settings.get('keybind').composite as string;
        loadSettings();

        settings.changed.connect(loadSettings);

        const command = 'jupyter_copilot:completion';
        app.commands.addCommand(command, {
          label: 'Copilot Completion',
          execute: () => {
            // get id of current notebook panel
            const notebookPanelId = notebookTracker.currentWidget?.id;
            providerManager.inline?.accept(notebookPanelId || '');
          }
        });

        app.commands.addKeyBinding({
          command,
          keys: [COMPLETION_BIND],
          selector: '.cm-editor'
        });

        const SignInCommand = 'Copilot: Sign In';
        app.commands.addCommand(SignInCommand, {
          label: 'Copilot: Sign In With GitHub',
          iconClass: 'cpgithub-icon',
          execute: () => LoginExecute(app)
        });

        const SignOutCommand = 'Copilot: Sign Out';
        app.commands.addCommand(SignOutCommand, {
          label: 'Copilot: Sign Out With GitHub',
          iconClass: 'cpgithub-icon',
          execute: () => SignOutExecute(app)
        });

        // make them pop up at the top of the palette first items on the palleete commands and update rank
        palette.addItem({
          command: SignInCommand,
          category: 'GitHub Copilot',
          rank: 0
        });
        palette.addItem({
          command: SignOutCommand,
          category: 'GitHub Copilot',
          rank: 1
        });
      }
    );

    const notebookClients = new Map<string, NotebookLSPClient>();

    const provider = new CopilotInlineProvider(notebookClients);
    providerManager.registerInlineProvider(provider);

    const serverSettings = ServerConnection.makeSettings();

    // notebook tracker is used to keep track of the notebooks that are open
    // when a new notebook is opened, we create a new LSP client and socket connection for that notebook
    notebookTracker.widgetAdded.connect(async (_, notebook) => {
      await notebook.context.ready;

      const wsURL = URLExt.join(serverSettings.wsUrl, 'jupyter-copilot', 'ws');
      const client = new NotebookLSPClient(notebook.context.path, wsURL);
      notebookClients.set(notebook.id, client);

      notebook.sessionContext.ready.then(() => {
        notebook.sessionContext.session?.kernel?.info.then(info => {
          client.setNotebookLanguage(info.language_info.name);
        });

        notebook.sessionContext.kernelChanged.connect(async (_, kernel) => {
          const info = await kernel.newValue?.info;
          client.setNotebookLanguage(info?.language_info.name as string);
        });
      });

      // run whenever a notebook cell updates
      // types are of ISharedCodeCell and CellChange
      // i cannot import them and i cannot find where they are supposed to be
      const onCellUpdate = (update: any, change: any) => {
        // only change if it is a source change
        if (change.sourceChange) {
          const content = update.source;
          client.sendCellUpdate(notebook.content.activeCellIndex, content);
        }
      };

      // keep the current cell so when can clean up whenever this changes
      let current_cell = notebook.content.activeCell;
      current_cell?.model.sharedModel.changed.connect(onCellUpdate);

      // run cleanup when notebook is closed
      notebook.disposed.connect(() => {
        client.dispose();
        notebookClients.delete(notebook.id);
      });

      // notifies the extension server when a cell is added or removed
      // swapping consists of an add and a remove, so this should be sufficient
      notebook.model?.cells.changed.connect((_, change) => {
        if (change.type === 'remove') {
          client.sendCellDelete(change.oldIndex);
        } else if (change.type === 'add') {
          const content = change.newValues[0].sharedModel.getSource();
          client.sendCellAdd(change.newIndex, content);
        }
      });

      notebook.context.pathChanged.connect((_, newPath) => {
        client.sendPathChange(newPath);
      });

      // whenever active cell changes remove handler then add to new one
      notebook.content.activeCellChanged.connect((_, cell) => {
        current_cell?.model.sharedModel.changed.disconnect(onCellUpdate);
        current_cell = cell;
        current_cell?.model.sharedModel.changed.connect(onCellUpdate);
      });
    });
  }
};

export default plugin;
