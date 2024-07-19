/*
    This class is responsible for communicating with the LSP server and
    the notebook frontend. It establishes a WebSocket connection with the
    LSP server and listens for messages. It also sends messages to the LSP
    server when a cell is updated in the notebook frontend.
*/
class NotebookLSPClient {
  private socket: WebSocket;

  constructor(notebookPath: string, wsUrl: string) {
    wsUrl = `${wsUrl}?path=${encodeURI(notebookPath)}`;
    this.socket = new WebSocket(wsUrl);
    this.socket.onmessage = this.handleMessage.bind(this);
    this.socket.onopen = () => this.sendMessage('sync_request', {});
  }

  // Handle messages from the extension server
  private handleMessage(event: MessageEvent) {
    const data = JSON.parse(event.data);
    switch (data.type) {
      case 'sync_response':
      case 'lsp_update':
        this.sendToLSP(data.code);
        break;
      // Handle other message types
    }
  }

  // Send a message to the LSP server to update the cell content
  // we don't want to update the entire file every time something is changed
  // so we specify a cell id and the now content so we can modify just that single cell
  public sendCellUpdate(cellId: number, content: string) {
    this.sendMessage('cell_update', { cell_id: cellId, content });
  }

  private sendMessage(type: string, payload: any) {
    this.socket.send(JSON.stringify({ type, ...payload }));
  }

  // TODO
  private sendToLSP(code: string) {
    // Implement LSP communication here
    console.log('Sending to LSP:', code);
  }

  // cleans up the socket connection
  public dispose() {
    this.socket.close();
    console.log('socket connection closed');
  }
}

export { NotebookLSPClient };
