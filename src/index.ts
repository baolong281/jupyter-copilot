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

        // run cleanup when notebook is closed
        notebook.disposed.connect(() => {
          client.dispose();
          console.log('Notebook disposed:', notebook.context.path);
        });

        // send the cell content to the LSP server when the current cell is updated
        // TODO: logic for when cells are swapped
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
