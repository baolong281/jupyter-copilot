import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { ISettingRegistry } from '@jupyterlab/settingregistry';

import { makePostRequest, createCompletion } from './handler';

/**
 * Initialization data for the jupyter_copilot extension.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter_copilot:plugin',
  description: 'GitHub Copilot for Jupyter',
  autoStart: true,
  optional: [ISettingRegistry],
  activate: (
    app: JupyterFrontEnd,
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

    // sends request to our extensions local server
    createCompletion(`def fibonacci(n):
        if n <= 0:

        `)
      .then(response => {
        // Handle the response here
        console.log('Copilot suggestion:', response);
      })
      .catch(error => {
        console.error('Error:', error);
      });

    makePostRequest('login', {}).then(response => {
      console.log('login response:', response);
    });
  }
};

export default plugin;
