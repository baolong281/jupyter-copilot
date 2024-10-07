import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { ICompletionProviderManager } from '@jupyterlab/completer';
import { IDisposable } from '@lumino/disposable';
import { INotebookTracker } from '@jupyterlab/notebook';
import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';
import { NotebookLSPClient } from './lsp';
import { ICommandPalette } from '@jupyterlab/apputils';
import { LoginExecute, SignOutExecute } from './commands/authentication';
import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { makePostRequest } from './utils';
import { CopilotInlineProvider } from './completions';
import { CopilotChat } from './chat';
import { IRenderMimeRegistry } from '@jupyterlab/rendermime';
import { ChatWidget } from '@jupyter/chat';

class GlobalSettings {
  enabled: boolean;
  completionBind: string;
  authenticated: boolean;

  constructor() {
    this.enabled = true;
    this.completionBind = 'Ctrl J';
    this.authenticated = false;

    makePostRequest('login', {})
      .then(response => {
        const res = JSON.parse(response) as any;
        this.authenticated = res.status === 'AlreadySignedIn';
      })
      .catch(error => {
        console.error('Error checking authentication state:', error);
      });
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
  }

  setCompletionBind(completionBind: string) {
    this.completionBind = completionBind;
  }

  setAuthenticated(authenticated: boolean) {
    this.authenticated = authenticated;
  }
}

export const GLOBAL_SETTINGS = new GlobalSettings();

/**
 * Initialization data for the jupyter_copilot extension.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter_copilot:plugin',
  description: 'GitHub Copilot for Jupyter',
  autoStart: true,
  requires: [
    IRenderMimeRegistry,
    INotebookTracker,
    ICompletionProviderManager,
    ICommandPalette,
    ISettingRegistry
  ],
  activate: (
    app: JupyterFrontEnd,
    rmRegistry: IRenderMimeRegistry,
    notebookTracker: INotebookTracker,
    providerManager: ICompletionProviderManager,
    palette: ICommandPalette,
    settingRegistry: ISettingRegistry
  ) => {
    console.debug('Jupyter Copilot Extension Activated');

    const command = 'jupyter_copilot:completion';

    app.commands.addCommand(command, {
      label: 'Copilot Completion',
      execute: () => {
        // get id of current notebook panel
        const notebookPanelId = notebookTracker.currentWidget?.id;
        providerManager.inline?.accept(notebookPanelId || '');
      }
    });

    Promise.all([app.restored, settingRegistry.load(plugin.id)]).then(
      ([, settings]) => {
        let keybindingDisposer: IDisposable | null = null;
        const loadSettings = (settings: ISettingRegistry.ISettings) => {
          const enabled = settings.get('flag').composite as boolean;
          const completion_bind = settings.get('keybind').composite as string;
          GLOBAL_SETTINGS.setEnabled(enabled);
          GLOBAL_SETTINGS.setCompletionBind(completion_bind);

          console.debug('Settings loaded:', enabled, completion_bind);

          if (keybindingDisposer) {
            const currentKeys = app.commands.keyBindings.find(
              kb => kb.command === command
            )?.keys;
            console.debug('Disposing old keybinding ', currentKeys);
            keybindingDisposer.dispose();
            keybindingDisposer = null;
          }
          keybindingDisposer = app.commands.addKeyBinding({
            command,
            keys: [completion_bind],
            selector: '.cm-editor'
          });
        };

        loadSettings(settings);

        settings.changed.connect(loadSettings);
        const SignInCommand = 'Copilot: Sign In';
        app.commands.addCommand(SignInCommand, {
          label: 'Copilot: Sign In With GitHub',
          execute: () => LoginExecute(app)
        });

        const SignOutCommand = 'Copilot: Sign Out';
        app.commands.addCommand(SignOutCommand, {
          label: 'Copilot: Sign Out With GitHub',
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

    const model = new CopilotChat();

    // Log when messages are updated
    model.messagesUpdated.connect(() => {
      console.log('Messages updated:', model.messages);
    });

    const widget = new ChatWidget({ model, rmRegistry });

    console.log('widget:', widget);

    // Ensure the widget is properly connected to the model

    app.shell.add(widget, 'right');

    // Test sending a message programmatically
    model.sendMessage({ body: 'Test message' });

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
