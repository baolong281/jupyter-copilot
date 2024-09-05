/*
    This class is responsible for communicating with the LSP server and
    the notebook frontend. It establishes a WebSocket connection with the
    LSP server and listens for messages. It also sends messages to the LSP
    server when a cell is updated in the notebook frontend.
*/

interface Completion {
  displayText: string;
  docVersion: number;
  position: { line: number; character: number };
  range: {
    start: { line: number; character: number };
    end: { line: number; character: number };
  };
  text: string;
  uuid: string;
}

class NotebookLSPClient {
  private socket: WebSocket | undefined;
  private pendingCompletions: Map<
    string,
    { resolve: (value: any) => void; reject: (reason?: any) => void }
  > = new Map();
  private wsUrl: string;

  constructor(notebookPath: string, wsUrl: string) {
    this.wsUrl = `${wsUrl}?path=${encodeURIComponent(notebookPath)}`;
    this.initializeWebSocket();
  }

  private initializeWebSocket() {
    this.socket = new WebSocket(this.wsUrl);
    this.setupSocketEventHandlers();
  }

  private setupSocketEventHandlers() {
    if (!this.socket) {
      return;
    }

    this.socket.onmessage = this.handleMessage.bind(this);
    this.socket.onopen = () => this.sendMessage('sync_request', {});
    this.socket.onclose = this.handleSocketClose;
  }

  private handleSocketClose = () => {
    console.log('Socket connection closed, reconnecting...');
    this.initializeWebSocket();
  };

  // Handle messages from the extension server
  private handleMessage(event: MessageEvent) {
    const data = JSON.parse(event.data);
    switch (data.type) {
      case 'sync_response':
        break;
      case 'completion':
        const pendingCompletion = this.pendingCompletions.get(data.req_id);
        if (pendingCompletion) {
          pendingCompletion.resolve(data.completions);
          this.pendingCompletions.delete(data.req_id);
        }
        break;
      case 'connection_established':
        console.log('Copilot connected to extension server...');
        break;
      default:
        console.log('Unknown message type:', data);
    }
  }

  // Send a message to the LSP server to update the cell content
  // we don't want to update the entire file every time something is changed
  // so we specify a cell id and the now content so we can modify just that single cell
  public sendCellUpdate(cellId: number, content: string) {
    this.sendMessage('cell_update', { cell_id: cellId, content: content });
  }

  public sendCellDelete(cellID: number) {
    this.sendMessage('cell_delete', { cell_id: cellID });
  }

  public sendCellAdd(cellID: number, content: string) {
    this.sendMessage('cell_add', { cell_id: cellID, content: content });
  }

  // sends a message to the server which will then send the updated code to the lsp server
  public sendUpdateLSPVersion() {
    this.sendMessage('update_lsp_version', {});
  }

  public async getCopilotCompletion(
    cell: number,
    line: number,
    character: number
  ): Promise<Completion[]> {
    return new Promise((resolve, reject) => {
      const requestId = `${cell}-${line}-${character}-${Date.now()}`;
      this.pendingCompletions.set(requestId, { resolve, reject });

      this.sendMessage('get_completion', {
        req_id: requestId,
        cell_id: cell,
        line: line,
        character: character
      });

      // add a timeout to reject the promise if no response is received
      setTimeout(() => {
        if (this.pendingCompletions.has(requestId)) {
          this.pendingCompletions.delete(requestId);
          reject(new Error('Completion request timed out'));
        }
      }, 10000); // 10 seconds timeout
    });
  }

  private sendMessage(type: string, payload: any) {
    this.socket?.send(JSON.stringify({ type, ...payload }));
  }

  public sendPathChange(newPath: string) {
    this.sendMessage('change_path', { new_path: newPath });
  }

  public setNotebookLanguage(language: string) {
    this.sendMessage('set_language', { language: language });
  }

  // cleans up the socket connection
  public dispose() {
    this.socket?.close();
    console.log('socket connection closed');
  }
}

export { NotebookLSPClient };
