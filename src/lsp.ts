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

  public getCopilotCompletion(cell: number, line: number, character: number) {
    this.sendMessage('get_completion', {
      cell_id: cell,
      line: line,
      character: character
    });
  }

  private sendMessage(type: string, payload: any) {
    this.socket.send(JSON.stringify({ type, ...payload }));
  }

  // TODO
  private sendToLSP(code: string) {
    // Implement LSP communication here
    console.log('NotebookHandler current representation:\n', code);
  }

  // cleans up the socket connection
  public dispose() {
    this.socket.close();
    console.log('socket connection closed');
  }
}

export { NotebookLSPClient };
