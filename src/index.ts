import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { INotebookTracker } from '@jupyterlab/notebook';
import { ServerConnection } from '@jupyterlab/services';
import { URLExt } from '@jupyterlab/coreutils';
import { NotebookLSPClient } from './lsp';
import { ICommandPalette, MainAreaWidget } from '@jupyterlab/apputils';
import { Widget } from '@lumino/widgets';
import {
  ICompletionProviderManager,
  IInlineCompletionItem,
  IInlineCompletionList,
  IInlineCompletionProvider,
  IInlineCompletionContext,
  CompletionHandler
} from '@jupyterlab/completer';
import { CodeEditor } from '@jupyterlab/codeeditor';
import { makePostRequest } from './handler';

interface AlreadySignedInResponse {
  status: 'AlreadySignedIn';
  user: string;
}

interface PendingLoginResponse {
  status: 'PendingLogin';
  user?: string;
  userCode: string;
  verificationUri: string;
  expiresIn: number;
  interval: number;
}

type LoginResponse = AlreadySignedInResponse | PendingLoginResponse;

class CopilotInlineProvider implements IInlineCompletionProvider {
  readonly name = 'GitHub Copilot';
  readonly identifier = 'jupyter_copilot:provider';
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
    const now = Date.now();

    // debounce mechanism
    // if a request is made within 90ms of the last request, throttle the request
    // but if it is the last request, then make the request
    console.log('time since last request', now - this.lastRequestTime);
    if (this.requestInProgress || now - this.lastRequestTime < 150) {
      console.log('THROTTLING');
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
          console.log('RESOLVING REQUEST AFTER THROTTLE');
          this.requestInProgress = true;
          this.lastRequestTime = Date.now();

          const items = await this.fetchCompletion(request, context);

          resolve(items);
        }, 200);
      });
    } else {
      // if request is not throttled, just get normally
      console.log('NORMAL REQUEST');
      this.requestInProgress = true;
      this.lastRequestTime = now;

      return await this.fetchCompletion(request, context);
    }
  }

  // logic to actually fetch the completion
  private async fetchCompletion(
    request: CompletionHandler.IRequest,
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
  requires: [INotebookTracker, ICompletionProviderManager, ICommandPalette],
  activate: (
    app: JupyterFrontEnd,
    notebookTracker: INotebookTracker,
    providerManager: ICompletionProviderManager,
    palette: ICommandPalette
  ) => {
    const notebookClients = new Map<string, NotebookLSPClient>();

    const provider = new CopilotInlineProvider(notebookClients);
    providerManager.registerInlineProvider(provider);

    // TODO: make work
    // if (settingRegistry) {
    //   settingRegistry
    //     .load(plugin.id)
    //     .then(settings => {
    //       console.log('jupyter_copilot settings loaded:', settings.composite);
    //     })
    //     .catch(reason => {
    //       console.error('Failed to load settings for jupyter_copilot.', reason);
    //     });
    // }

    const command = 'jupyter_copilot:completion';
    app.commands.addCommand(command, {
      label: 'Copilot Completion',
      execute: () => {
        // get id of current notebook panel
        console.log('command executing');
        const notebookPanelId = notebookTracker.currentWidget?.id;
        providerManager.inline?.accept(notebookPanelId || '');
      }
    });

    app.commands.addKeyBinding({
      command,
      keys: ['Ctrl J'],
      selector: '.cm-editor'
    });

    const commandID = 'GitHub Copilot: Sign In';
    const toggled = false;
    app.commands.addCommand(commandID, {
      label: 'Github Copilot: Sign In With GitHub',
      isToggled: () => toggled,
      isVisible: () => true,
      isEnabled: () => true,
      iconClass: 'cpgithub-icon',
      execute: () => {
        makePostRequest('login', {}).then(data => {
          // data is a string turned into a json object
          const res = JSON.parse(data) as LoginResponse;

          // handle this branch later
          if (res.status === 'AlreadySignedIn') {
            console.log('Already signed in as', res.user);
            return;
          }

          const signWidget = (authData: PendingLoginResponse) => {
            const content = new Widget();

            const messageElement = document.createElement('div');

            messageElement.style.cssText = `
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue', sans-serif;
                color: #333;
                background-color: #fff;
                padding: 30px;
                max-width: 400px;
                margin: 0 auto;
                border-radius: 8px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                text-align: center;
            `;

            messageElement.innerHTML = `
              <h2 style="font-size: 24px; margin-bottom: 20px; color: #0366d6;">GitHub Copilot Authentication</h2>
              <p style="margin-bottom: 10px;">Enter this code on GitHub:</p>
              <div style="font-size: 32px; font-weight: bold; background-color: #f6f8fa; color: #0366d6; padding: 15px; border-radius: 5px; margin: 20px 0; letter-spacing: 2px; box-shadow: inset 0 2px 4px rgba(0,0,0,0.05);">${authData.userCode}</div>
              <p style="margin-bottom: 10px;">Go to: <a href="${authData.verificationUri}" target="_blank" style="color: #0366d6; text-decoration: none;">${authData.verificationUri}</a></p>
              <p style="font-size: 14px; color: #666;">This code will expire in <span id="timer" style="font-weight: bold;">${authData.expiresIn}</span> seconds.</p>
            `;
            content.node.appendChild(messageElement);
            const widget = new MainAreaWidget({ content });
            widget.id = 'apod-jupyterlab';
            widget.title.label = 'Sign In';
            widget.title.closable = true;
            return widget;
          };
          console.log('AUTH MESSAGE');
          console.log(res);
          let widget = signWidget(res);
          if (!widget.isDisposed) {
            widget.dispose();
            widget = signWidget(res);
          }
          if (!widget.isAttached) {
            app.shell.add(widget, 'main');
          }
          // countdown timer for expires in the this code will expire in {expiresin seconds}
          let timeRemaining = res.expiresIn;
          const interval = setInterval(() => {
            if (timeRemaining <= 0) {
              clearInterval(interval);
              widget.dispose();
              return;
            }
            const timerElement = widget.node.querySelector('#timer');
            if (timerElement) {
              timerElement.textContent = timeRemaining.toString();
            }
            timeRemaining--;
          }, 1000);
          app.shell.activateById(widget.id);
        });
      }
    });

    const SignOutCommand = 'GitHub Copilot: Sign Out';
    app.commands.addCommand(SignOutCommand, {
      label: 'Github Copilot: Sign Out With GitHub',
      isToggled: () => toggled,
      isVisible: () => true,
      isEnabled: () => true,
      iconClass: 'cpgithub-icon',
      execute: () => {
        console.log('MAKING POST ');
        makePostRequest('signout', {}).then(data => {
          console.log(data);
        });
      }
    });

    console.log(palette);
    // make them pop up at the top of the palette first items on the palleete commands and update rank
    palette.addItem({
      command: commandID,
      category: 'GitHub Copilot',
      rank: 0
    });
    palette.addItem({
      command: SignOutCommand,
      category: 'GitHub Copilot',
      rank: 1
    });

    const settings = ServerConnection.makeSettings();
    // notebook tracker is used to keep track of the notebooks that are open
    // when a new notebook is opened, we create a new LSP client and socket connection for that notebook

    notebookTracker.widgetAdded.connect((_, notebook) => {
      notebook.context.ready.then(() => {
        const wsURL = URLExt.join(settings.wsUrl, 'jupyter-copilot', 'ws');
        const client = new NotebookLSPClient(notebook.context.path, wsURL);
        notebookClients.set(notebook.id, client);

        notebook.sessionContext.ready.then(() => {
          notebook.sessionContext.session?.kernel?.info.then(info => {
            client.setNotebookLanguage(info.language_info.name);
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
          console.log('Notebook disposed:', notebook.context.path);
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
    });
  }
};

export default plugin;
