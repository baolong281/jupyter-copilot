import asyncio
import time
from typing import Any, Dict, Optional
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
        self.document_version = 0
        self.language = "python"
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

        # if new notebook, code will be empty so just add empty string
        if len(code) == 0:
            code = ['']

        # when a notebook is newly created and never run this information is not available
        if nb.metadata and nb.metadata.kernelspec:
            self.language = nb.metadata.kernelspec.language.lower()
            logging.info("SETTING LANGUAGE TO %s", self.language)

        logging.info("Sending open signal to LSP")
        lsp_client.send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": f"file:///{self.path}",
                "languageId": self.language,
                "version": self.document_version,
                "text": "".join(code)
            }
        })

        return code

    # extract code cells from a notebook, iterate through all cells then put the content in the code cells into a list
    def extract_code_cells(self, notebook):
        return [cell.source for cell in notebook.cells if (cell.cell_type == "code" or cell.cell_type == "markdown")]

    # deletes a cell from the array
    def delete_cell(self, cell_id):
        if 0 <= cell_id < len(self.notebook_cells):
            self.notebook_cells.pop(cell_id)
        else:
            logging.error(f"Cell {cell_id} does not exist")

    # insert a cell into the array
    def add_cell(self, cell_id, content):
        if 0 <= cell_id <= len(self.notebook_cells):
            self.notebook_cells.insert(cell_id, content)
        elif cell_id > len(self.notebook_cells):
            # fill in the gap with empty strings if the cell_id is greater than the length of the array for some reason
            for _ in range(cell_id - len(self.notebook_cells)):
                self.notebook_cells.append('')
            self.notebook_cells.append(content)
        logging.info(f"Added cell {cell_id}")


    # index into array and update the content of a cell
    def update_cell(self, cell_id, content):
        logging.info(f"Updating cell {cell_id}")
        if 0 <= cell_id < len(self.notebook_cells):
            self.notebook_cells[cell_id] = content
        else:
            logging.error(f"Cell {cell_id} does not exist")

    def get_full_code(self):
        return "\n\n".join(self.notebook_cells)

    # sends full code to lsp server
    def send_full_update(self):
        self.document_version += 1
        code = self.get_full_code()
        lsp_client.send_notification("textDocument/didChange", {
            "textDocument": {
                "uri": f"file:///{self.path}",
                "version": self.document_version
            },
            "contentChanges": [{"text": code}]
        })

    def request_completion(self, cell_id: int, line: int, character: int) -> Dict[str, Any]:
        line = self._get_absolute_line_num(cell_id, line)
        logging.info(f"Requesting completion for line {line} character {character}, language: {self.language}")
        response = lsp_client.send_request("getCompletions", {
            "doc": {
                "uri": f"file:///{self.path}",
                "position": {"line": line, "character": character},
                "version": self.document_version
            }
        })

        return response

    # given cellid and line of the current cell, return the absolute line number in the code representation
    # this sort of sucks but it works for now
    def _get_absolute_line_num(self, cellId: int, line: int) -> int:
        return sum([len(cell.split('\n')) for cell in self.notebook_cells[:cellId]]) + line + cellId

    def handle_path_change(self, path):
        new_path = f"file:///{path}"
        
        # send close notification
        self.send_close_signal()

        # send open notification
        lsp_client.send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": new_path,
                "languageId": self.language,
                "version": self.document_version,
                "text": self.get_full_code()
            }
        })

        self.path = path


    def send_close_signal(self):
        logging.info("Sending close signal to LSP")
        lsp_client.send_notification("textDocument/didClose", {
            "textDocument": {
                "uri": f"file:///{self.path}"
            }
        })

    def set_language(self, language):
        self.language = language
        self.send_close_signal( )
        lsp_client.send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": f"file:///{self.path}",
                "languageId": self.language,
                "version": self.document_version,
                "text": self.get_full_code()
            }
        })
        logging.info(f"Language set to {language}")


class NotebookLSPHandler(WebSocketHandler):
    def initialize(self):
        self.notebook_manager = None
        # we need a queue so that we can fully process one request before moving onto the next
        self.message_queue = asyncio.Queue()
        # register functino to run in the background
        IOLoop.current().add_callback(self.process_message_queue)

    async def open(self):
        notebook_path = self.get_argument('path', '')
        self.notebook_manager = NotebookManager(notebook_path)
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
                data = await self.message_queue.get()
                if data['type'] == 'cell_update':
                    await self.handle_cell_update(data)
                elif data['type'] == 'cell_add':
                    await self.handle_cell_add(data)
                elif data['type'] == 'get_completion':
                    await self.handle_completion_request(data)
                elif data['type'] == 'update_lsp_version':
                    await self.handle_update_lsp_version()
                elif data['type'] == 'cell_delete':
                    await self.handle_cell_delete(data)
                elif data['type'] == 'sync_request':
                    await self.handle_sync_request()
                elif data['type'] == 'change_path':
                    await self.handler_path_change(data);
                elif data['type'] == 'set_language':
                    await self.handle_set_language(data)

                # Add other message types as needed
            except Exception as e:
                logging.error(f"Error processing message: {e}")
            finally:
                self.message_queue.task_done()

    async def handle_update_lsp_version(self):
        self.notebook_manager.send_full_update()

    async def handler_path_change(self, data):
        self.notebook_manager.handle_path_change(data['new_path'])

    async def handle_set_language(self, data):
        self.notebook_manager.set_language(data['language'])


    async def handle_completion_request(self, data):
        response = self.notebook_manager.request_completion(
            data['cell_id'],
            data['line'], data['character'])
        response['req_id'] = data['req_id']
        await self.send_message('completion', response)

    async def handle_sync_request(self):
        code = self.notebook_manager.get_full_code()
        await self.send_message('sync_response', {'code': code})

    async def handle_cell_add(self, data):
        self.notebook_manager.add_cell(data['cell_id'], data['content'])

    async def handle_cell_update(self, data):
        self.notebook_manager.update_cell(data['cell_id'], data['content'])
        code = self.notebook_manager.get_full_code()

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
        self.notebook_manager.send_close_signal()
        self.notebook_manager = None


def setup_handlers(server_app):
    global logging
    logging = server_app.log

    global lsp_client
    lsp_client = LSPWrapper(logging)

    web_app = server_app.web_app
    host_pattern = ".*$"
    base_url = web_app.settings["base_url"] + "jupyter-copilot"
    handlers = [
        (url_path_join(base_url, "ws"), NotebookLSPHandler)
    ]
    logging.info("base url: %s", base_url)
    web_app.add_handlers(host_pattern, handlers)

    lsp_client.wait(1000)

    init_result = lsp_client.send_request("initialize", {
        "capabilities": {"workspace": {"workspaceFolders": True}}
    })


    # Send `initialized` notification
    lsp_client.send_notification("initialized", {})

