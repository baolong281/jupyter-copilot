import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { INotebookTracker } from '@jupyterlab/notebook';
import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';
import { NotebookLSPClient } from './lsp';

/**
 * Initialization data for the jupyter_copilot extension.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter_copilot:plugin',
  description: 'GitHub Copilot for Jupyter',
  autoStart: true,
  optional: [ISettingRegistry],
  requires: [INotebookTracker],
  activate: (
    app: JupyterFrontEnd,
    notebookTracker: INotebookTracker,
    settingRegistry: ISettingRegistry | null
  ) => {
    console.log('JupyterLab extension jupyter_copilot is activated!');

    const command = 'jupyter_copilot:completion';
    const noteBookClients = new Map<string, NotebookLSPClient>();

    const getCompletionAtCursor = async () => {
      const notebook = notebookTracker.currentWidget;
      if (!notebook) {
        console.log('No active notebook');
        return;
      }
      const client = noteBookClients.get(notebook.context.path);
      // print character position
      const cursor = notebook.content.activeCell?.editor?.getCursorPosition();
      if (cursor) {
        const { line, column } = cursor;
        console.log('Active cell id:', notebook.content.activeCellIndex);
        console.log(
          `Current line: ${line}, Current character position: ${column}`
        );
        client?.sendUpdateLSPVersion();
        client?.getCopilotCompletion(
          notebook.content.activeCellIndex,
          line,
          column
        );
      }
    };

    app.commands.addCommand(command, {
      label: 'Copilot Completion',
      execute: () => {
        getCompletionAtCursor();
      }
    });

    app.commands.addKeyBinding({
      command,
      keys: ['Ctrl J'],
      selector: '.jp-Notebook'
    });

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

    const settings = ServerConnection.makeSettings();
    // notebook tracker is used to keep track of the notebooks that are open
    // when a new notebook is opened, we create a new LSP client and socket connection for that notebook

    notebookTracker.widgetAdded.connect((_, notebook) => {
      notebook.context.ready.then(() => {
        const wsURL = URLExt.join(settings.wsUrl, 'jupyter-copilot', 'ws');
        const client = new NotebookLSPClient(notebook.context.path, wsURL);
        noteBookClients.set(notebook.context.path, client);

        // run cleanup when notebook is closed
        notebook.disposed.connect(() => {
          client.dispose();
          noteBookClients.delete(notebook.context.path);
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
            // activate the copilot when a new cell is added
            // this is temporary
            client.sendUpdateLSPVersion();
            // print active cell id
            console.log('Active cell id:', notebook.content.activeCellIndex);
            // client.getCopilotCompletion(1, 4);
          }
        });

        // send the cell content to the LSP server when the current cell is updated
        notebook.content.activeCellChanged.connect((_, cell) => {
          cell?.model.contentChanged.connect(cell => {
            const content = cell.sharedModel.getSource();
            client.sendCellUpdate(notebook.content.activeCellIndex, content);
          });
        });
      });
    });
  }
};

export default plugin;
