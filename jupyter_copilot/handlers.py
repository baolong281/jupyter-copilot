import asyncio
from tornado.ioloop import IOLoop
from jupyter_server.base.handlers import APIHandler
from tornado.websocket import WebSocketHandler
from jupyter_server.utils import url_path_join
import tornado
import json
import nbformat
import os
import subprocess
from jupyter_copilot.lsp import LSPWrapper

# manages the content of the notebook in memory


class NotebookManager:
    def __init__(self, path):
        self.path = path
        # keep all the code in an array of strings so that we can easily update the content of a cell
        # when a cell is updated, we update the corresponding string in this array
        # notebook_cells: string[]
        self.notebook_cells = self.load_notebook()
        logging.info(self.notebook_cells)

    # load notebook content into memory
    # returns a list of the content in the code cells
    # should only run when a notebook is first opened
    def load_notebook(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, 'r') as f:
            nb = nbformat.read(f, as_version=4)
        code = self.extract_code_cells(nb)
        return code

    # extract code cells from a notebook, iterate through all cells then put the content in the code cells into a list
    def extract_code_cells(self, notebook):
        return [cell.source for cell in notebook.cells if (cell.cell_type == "code" or cell.cell_type == "markdown")]

    # deletes a cell from the array
    def delete_cell(self, cell_id):
        logging.info(f"Deleting cell {cell_id}")
        if 0 <= cell_id < len(self.notebook_cells):
            self.notebook_cells.pop(cell_id)
        else:
            logging.error(f"Cell {cell_id} does not exist")

    # insert a cell into the array
    def add_cell(self, cell_id, content):
        logging.info(f"Adding cell {cell_id} with content: {content}")
        if 0 <= cell_id <= len(self.notebook_cells):
            self.notebook_cells.insert(cell_id, content)
        elif cell_id > len(self.notebook_cells):
            # fill in the gap with empty strings if the cell_id is greater than the length of the array for some reason
            for _ in range(cell_id - len(self.notebook_cells)):
                self.notebook_cells.append('')
            self.notebook_cells.append(content)

        logging.info(self.notebook_cells)

    # index into array and update the content of a cell
    def update_cell(self, cell_id, content):
        logging.info(f"Updating cell {cell_id} with content: {content}")
        if 0 <= cell_id < len(self.notebook_cells):
            self.notebook_cells[cell_id] = content
        else:
            logging.error(f"Cell {cell_id} does not exist")

    def get_full_code(self):
        return "\n\n".join(self.notebook_cells)


class NotebookLSPHandler(WebSocketHandler):
    def initialize(self):
        self.notebook_manager = None
        # we need a queue so that we can fully process one request before moving onto the next
        self.message_queue = asyncio.Queue()
        # register functino to run in the background
        IOLoop.current().add_callback(self.process_message_queue)

    async def open(self):
        self.notebook_path = self.get_argument('path', '')
        self.notebook_manager = NotebookManager(self.notebook_path)
        await self.send_message('connection_established', {})

    async def on_message(self, message):
        try:
            data = json.loads(message)
            await self.message_queue.put(data)
        except json.JSONDecodeError:
            logging.error(f"Received invalid JSON: {message}")

    # constantly runs in the background to process messages from the queue
    # fully processes one message before moving onto the next to not break stuff
    async def process_message_queue(self):
        while True:
            try:
                logging.info("q info %s", self.message_queue._format())
                data = await self.message_queue.get()
                if data['type'] == 'cell_update':
                    await self.handle_cell_update(data)
                elif data['type'] == 'cell_add':
                    await self.handle_cell_add(data)
                elif data['type'] == 'cell_delete':
                    await self.handle_cell_delete(data)
                elif data['type'] == 'sync_request':
                    await self.handle_sync_request()
                # Add other message types as needed
            except Exception as e:
                logging.error(f"Error processing message: {e}")
            finally:
                self.message_queue.task_done()

    async def handle_sync_request(self):
        code = self.notebook_manager.get_full_code()
        await self.send_message('sync_response', {'code': code})

    async def handle_cell_add(self, data):
        self.notebook_manager.add_cell(data['cell_id'], data['content'])

    async def handle_cell_update(self, data):
        self.notebook_manager.update_cell(data['cell_id'], data['content'])
        code = self.notebook_manager.get_full_code()
        await self.send_message('lsp_update', {'code': code})

    async def handle_cell_delete(self, data):
        self.notebook_manager.delete_cell(data['cell_id'])

    async def send_message(self, msg_type, payload):
        message = json.dumps({'type': msg_type, **payload})
        try:
            await self.write_message(message)
        except Exception as e:
            logging.error(f"Error sending message: {e}")

    def on_close(self):
        logging.info("WebSocket closed")
        self.notebook_manager = None


def setup_handlers(server_app):
    global logging
    logging = server_app.log

    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    lsp_path = os.path.join(
        parent_dir, "node_modules", "copilot-node-server", "copilot", "dist", "language-server.js")
    global lsp
    lsp = LSPWrapper(["node", lsp_path, "--stdio"], logging)

    web_app = server_app.web_app
    host_pattern = ".*$"
    base_url = web_app.settings["base_url"] + "jupyter-copilot"
    handlers = [
        (url_path_join(base_url, "ws"), NotebookLSPHandler)
    ]
    logging.info("base url: %s", base_url)
    web_app.add_handlers(host_pattern, handlers)

    lsp.wait(1000)

    init_result = lsp.send_request("initialize", {
        "capabilities": {"workspace": {"workspaceFolders": True}}
    })

    # Send `initialized` notification
    lsp.send_notification("initialized", {})

    # Send `textDocument/didOpen` notification
    lsp.send_notification("textDocument/didOpen", {
        "textDocument": {
            "uri": "file:///home/fakeuser/my-project/test.py",
            "languageId": "python",
            "version": 0,
            "text": "def he\n    \n    print(world)# prints 'hello code' then 'world' in different lines\n"
        }
    })

    # Send `getCompletions` request
    completions = lsp.send_request("getCompletions", {
        "doc": {
            "version": 0,
            "position": {"line": 0, "character": 7},
            "uri": "file:///home/fakeuser/my-project/test.py"
        }
    })

    logging.info(completions)
