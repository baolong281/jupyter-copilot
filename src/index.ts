import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { INotebookTracker } from '@jupyterlab/notebook';
import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';
import { NotebookLSPClient } from './lsp';
import {
  ICompletionProviderManager,
  IInlineCompletionItem,
  IInlineCompletionList,
  IInlineCompletionProvider,
  IInlineCompletionContext,
  CompletionHandler
} from '@jupyterlab/completer';
import { CodeEditor } from '@jupyterlab/codeeditor';

class CopilotInlineProvider implements IInlineCompletionProvider {
  readonly name = 'GitHub Copilot';
  readonly identifier = 'jupyter_copilot:provider';
  notebookClients: Map<string, NotebookLSPClient>;

  constructor(notebookClients: Map<string, NotebookLSPClient>) {
    this.notebookClients = notebookClients;
  }

  async fetch(
    request: CompletionHandler.IRequest,
    context: IInlineCompletionContext
  ): Promise<IInlineCompletionList<IInlineCompletionItem>> {
    console.log('Fetching completions');

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
        insertText: completion.displayText,
        isIncomplete: false
      });
    });

    console.log('Completions:', items);

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
  optional: [ISettingRegistry],
  requires: [INotebookTracker, ICompletionProviderManager],
  activate: (
    app: JupyterFrontEnd,
    notebookTracker: INotebookTracker,
    providerManager: ICompletionProviderManager,
    settingRegistry: ISettingRegistry | null
  ) => {
    console.log('JupyterLab extension jupyter_copilot is activated!');

    const notebookClients = new Map<string, NotebookLSPClient>();

    const provider = new CopilotInlineProvider(notebookClients);
    providerManager.registerInlineProvider(provider);
    // providerManager.inline?.accept();

    if (settingRegistry) {
      settingRegistry
        .load(plugin.id)
        .then(settings => {
          console.log('jupyter_copilot settings loaded:', settings.composite);
        })
        .catch(reason => {
          console.error('Failed to load settings for jupyter_copilot.', reason);
        });
    }

    const command = 'jupyter_copilot:completion';
    app.commands.addCommand(command, {
      label: 'Copilot Completion',
      execute: () => {
        // get id of current notebook panel
        const notebookPanelId = notebookTracker.currentWidget?.id;
        console.log('ID of current notebook panel:', notebookPanelId);
        providerManager.inline?.accept(notebookPanelId || '');
      }
    });

    app.commands.addKeyBinding({
      command,
      keys: ['Ctrl J'],
      selector: '.jp-Notebook'
    });

    const settings = ServerConnection.makeSettings();
    // notebook tracker is used to keep track of the notebooks that are open
    // when a new notebook is opened, we create a new LSP client and socket connection for that notebook

    notebookTracker.widgetAdded.connect((_, notebook) => {
      notebook.context.ready.then(() => {
        const wsURL = URLExt.join(settings.wsUrl, 'jupyter-copilot', 'ws');
        const client = new NotebookLSPClient(notebook.context.path, wsURL);
        notebookClients.set(notebook.id, client);

        // run whenever a notebook cell updates
        // annotate type later when i have wifi
        const onCellUpdate = (cell: any) => {
          const content = cell.sharedModel.getSource();
          client.sendCellUpdate(notebook.content.activeCellIndex, content);
        };

        // keep the current cell so when can clean up whenever this changes
        let current_cell = notebook.content.activeCell;
        current_cell?.model.contentChanged.connect(onCellUpdate);

        // run cleanup when notebook is closed
        notebook.disposed.connect(() => {
          client.dispose();
          notebookClients.delete(notebook.id);
          console.log('Notebook disposed:', notebook.context.path);
        });

        // notifies the extension server when a cell is added or removed
        // swapping consists of an add and a remove, so this should be sufficient
        notebook.model?.cells.changed.connect((list, change) => {
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
          current_cell?.model.contentChanged.disconnect(onCellUpdate);
          current_cell = cell;
          current_cell?.model.contentChanged.connect(onCellUpdate);
        });
      });
    });
  }
};

export default plugin;
