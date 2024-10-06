import {
  IInlineCompletionItem,
  IInlineCompletionList,
  IInlineCompletionProvider,
  IInlineCompletionContext,
  CompletionHandler
} from '@jupyterlab/completer';
import { GLOBAL_SETTINGS } from './index';
import { CodeEditor } from '@jupyterlab/codeeditor';
import { NotebookLSPClient } from './lsp';

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
    if (!GLOBAL_SETTINGS.enabled || !GLOBAL_SETTINGS.authenticated) {
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

export { CopilotInlineProvider };
