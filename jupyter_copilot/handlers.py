import asyncio
from typing import Any, Dict, List
from tornado.ioloop import IOLoop
from tornado.websocket import WebSocketHandler
from jupyter_server.utils import url_path_join
import logging
import json
import nbformat
import os
from jupyter_copilot.lsp import LSPWrapper
from jupyter_server.base.handlers import JupyterHandler


class NotebookManager:
    """
    class managing the content of the notebook in memory
    notebook code is stored in an array of strings, each string representing a cell
    on an update we update the cell index in the array
    """

    def __init__(self, path: str) -> None:
        self.path = path
        # remove leading slash for name
        self.name = path[1:] if path.startswith("/") else path
        self.document_version = 0
        self.language = "python"
        self.notebook_cells = self.load_notebook()

        logging.debug("[Copilot] Notebook manager initialized for %s", self.path)

    def load_notebook(self) -> List[str]:
        """
        read the content of the notebook into the cells
        only runs on the first sync / when the notebook is opened
        """

        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Notebook {self.path} not found")

        with open(self.path, "r") as f:
            nb = nbformat.read(f, as_version=4)

        code = self.extract_code_cells(nb)

        # if new notebook, code will be empty so just add empty string
        if len(code) == 0:
            code = [""]

        # when a notebook is newly created and never run this information is not available
        if nb.metadata and nb.metadata.kernelspec and nb.metadata.kernelspec.language:
            self.language = nb.metadata.kernelspec.language.lower()

        return code

    def extract_code_cells(self, notebook: nbformat.NotebookNode) -> List[str]:
        """extract code cells from a notebook into a list of strings"""
        return [
            cell.source
            for cell in notebook.cells
            if (cell.cell_type == "code" or cell.cell_type == "markdown")
        ]

    def delete_cell(self, cell_id: int) -> None:
        """deletes a cell id from the array if it exists"""
        if 0 <= cell_id < len(self.notebook_cells):
            self.notebook_cells.pop(cell_id)
        else:
            logging.error(f"Cell {cell_id} does not exist")

    def add_cell(self, cell_id: int, content: str) -> None:
        """
        inserts a cell into the array at the given index
        if the cell index is larger than the length, make a blunch of blank cells
        """
        if 0 <= cell_id <= len(self.notebook_cells):
            self.notebook_cells.insert(cell_id, content)
        elif cell_id > len(self.notebook_cells):
            # fill in the gap with empty strings if the cell_id is greater than the length of the array for some reason
            for _ in range(cell_id - len(self.notebook_cells)):
                self.notebook_cells.append("")
            self.notebook_cells.append(content)

    def update_cell(self, cell_id: int, content: str) -> None:
        """index into array and update the cell content if it exists"""
        if 0 <= cell_id < len(self.notebook_cells):
            self.notebook_cells[cell_id] = content
        else:
            logging.error(f"Cell {cell_id} does not exist")

    def get_full_code(self) -> str:
        """return the full code of the notebook as a string"""
        return "\n\n".join(self.notebook_cells)

    def get_absolute_line_num(self, cellId: int, line: int) -> int:
        """
        given cellid and line of the current cell, return the absolute line number in the code representation
        this sort of sucks but it works
        """
        return (
            sum([len(cell.split("\n")) for cell in self.notebook_cells[:cellId]])
            + line
            + cellId
        )

    def set_language(self, language: str) -> None:
        """
        closes and opens the lsp server with the new language
        this runs whenever a notebook is initially loaded
        """
        self.language = language
        logging.debug(f"[Copilot] Language set to {language}")

    def update_path(self, path: str) -> None:
        """
        sends a close signal to the lsp server and then opens a new one
        this runs whenever a notebook is initially loaded
        """
        self.path = path
        self.name = path[1:] if path.startswith("/") else path

        logging.debug(f"[Copilot] Path changed to {self.path}")


class CompletionManager:
    def __init__(self, notebook_manager: NotebookManager) -> None:
        self.notebook_manager = notebook_manager
        self.document_version = 0

        # callback to run if the lsp server is ever restarted
        # need to reload the notebook content into the lsp server
        def _restart_callback():
            code = notebook_manager.load_notebook()

            lsp_client.send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": f"file:///{notebook_manager.name}",
                        "languageId": notebook_manager.language,
                        "version": self.document_version,
                        "text": "".join(code),
                    }
                },
            )

        self._callback = _restart_callback
        lsp_client.register_restart_callback(self._callback)

    def send_full_update(self) -> None:
        """sends an update to the lsp with the latest code"""
        self.document_version += 1
        notebook = self.notebook_manager
        code = notebook.get_full_code()
        lsp_client.send_notification(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": f"file:///{notebook.name}",
                    "version": self.document_version,
                },
                "contentChanges": [{"text": code}],
            },
        )
        logging.debug("[Copilot] Sending full update for %s", notebook.path)

    def request_completion(
        self, cell_id: int, line: int, character: int
    ) -> Dict[str, Any]:
        """
        requests a completion from the lsp server given a cell id, line number, and character position
        then returns the response
        """
        notebook = self.notebook_manager
        line = notebook.get_absolute_line_num(cell_id, line)
        logging.debug(
            f"[Copilot] Requesting completion for cell {cell_id}, line {line}, character {character}"
        )
        response = lsp_client.send_request(
            "getCompletions",
            {
                "doc": {
                    "uri": f"file:///{notebook.name}",
                    "position": {"line": line, "character": character},
                    "version": self.document_version,
                }
            },
        )

        return response

    def handle_path_change(self) -> None:
        notebook = self.notebook_manager

        self.send_close_signal()

        lsp_client.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": f"file:///{notebook.name}",
                    "languageId": notebook.language,
                    "version": self.document_version,
                    "text": notebook.get_full_code(),
                }
            },
        )

    def send_close_signal(self) -> None:
        """send a close signal to the lsp server"""
        path = self.notebook_manager.path
        logging.debug("[Copilot] Sending close signal to LSP for %s", path)
        lsp_client.send_notification(
            "textDocument/didClose", {"textDocument": {"uri": f"file:///{path}"}}
        )

    def handle_set_language(self):
        notebook = self.notebook_manager

        self.send_close_signal()
        lsp_client.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": f"file:///{notebook.name}",
                    "languageId": notebook.language,
                    "version": self.document_version,
                    "text": notebook.get_full_code(),
                }
            },
        )


class NotebookLSPHandler(WebSocketHandler):
    def initialize(self):
        self.notebook_manager: NotebookManager | None = None
        self.completion_manager: CompletionManager | None = None
        self.message_queue = asyncio.Queue()
        # register function to run in the background
        IOLoop.current().add_callback(self.process_message_queue)

    async def open(self, *args, **kwargs):
        notebook_path = self.get_argument("path", "")
        notebook_path = os.path.join(root_dir, notebook_path)
        self.notebook_manager = NotebookManager(notebook_path)
        self.completion_manager = CompletionManager(self.notebook_manager)
        await self.send_message("connection_established", {})
        logging.debug("[Copilot] WebSocket opened")

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
                if data["type"] == "cell_update":
                    await self.handle_cell_update(data)
                elif data["type"] == "cell_add":
                    await self.handle_cell_add(data)
                elif data["type"] == "get_completion":
                    await self.handle_completion_request(data)
                elif data["type"] == "update_lsp_version":
                    await self.handle_update_lsp_version()
                elif data["type"] == "cell_delete":
                    await self.handle_cell_delete(data)
                elif data["type"] == "sync_request":
                    await self.handle_sync_request()
                elif data["type"] == "change_path":
                    await self.handler_path_change(data)
                elif data["type"] == "set_language":
                    await self.handle_set_language(data)

                # Add other message types as needed
            except Exception as e:
                logging.error(f"Error processing message: {e}")
            finally:
                self.message_queue.task_done()

    async def handle_update_lsp_version(self):
        if not self.completion_manager:
            raise Exception("Completion manager not initialized")

        self.completion_manager.send_full_update()

    async def handler_path_change(self, data):
        if self.notebook_manager is None:
            raise Exception("Notebook manager not initialized")
        if self.completion_manager is None:
            raise Exception("Completion manager not initialized")

        notebook_path = data["new_path"]
        notebook_path = os.path.join(root_dir, notebook_path)

        self.notebook_manager.update_path(notebook_path)
        self.completion_manager.handle_path_change()

    async def handle_set_language(self, data):
        if self.notebook_manager is None:
            raise Exception("Notebook manager not initialized")
        if self.completion_manager is None:
            raise Exception("Completion manager not initialized")

        self.notebook_manager.set_language(data["language"])
        self.completion_manager.handle_set_language()

    async def handle_completion_request(self, data):
        if self.notebook_manager is None:
            raise Exception("Notebook manager not initialized")
        if self.completion_manager is None:
            raise Exception("Completion manager not initialized")

        response = self.completion_manager.request_completion(
            data["cell_id"], data["line"], data["character"]
        )
        response["req_id"] = data["req_id"]
        await self.send_message("completion", response)

    async def handle_sync_request(self):
        if self.notebook_manager is None:
            raise Exception("Notebook manager not initialized")

        code = self.notebook_manager.get_full_code()
        await self.send_message("sync_response", {"code": code})

    async def handle_cell_add(self, data):
        if self.notebook_manager is None:
            raise Exception("Notebook manager not initialized")

        self.notebook_manager.add_cell(data["cell_id"], data["content"])

    async def handle_cell_update(self, data):
        if self.notebook_manager is None:
            raise Exception("Notebook manager not initialized")

        self.notebook_manager.update_cell(data["cell_id"], data["content"])

    async def handle_cell_delete(self, data):
        if self.notebook_manager is None:
            raise Exception("Notebook manager not initialized")
        self.notebook_manager.delete_cell(data["cell_id"])

    async def send_message(self, msg_type, payload):
        message = json.dumps({"type": msg_type, **payload})
        try:
            await self.write_message(message)
        except Exception as e:
            logging.error(f"Error sending message: {e}")

    def on_close(self):
        logging.debug("[Copilot] WebSocket closed")

        if self.notebook_manager is None:
            raise Exception("Notebook manager not initialized")
        if self.completion_manager is None:
            raise Exception("Completion manager not initialized")

        # when socket is closed send the close signal to server
        # unregister the lsp server restart callback
        self.completion_manager.send_close_signal()
        lsp_client.unregister_restart_callback(self.completion_manager._callback)
        self.notebook_manager = None


class AuthHandler(JupyterHandler):
    async def post(self):
        action = self.request.path.split("/")[-1]
        if action == "login":
            res = lsp_client.send_request("signInInitiate", {})
        elif action == "signout":
            res = lsp_client.send_request("signOut", {})
        else:
            self.set_status(404)
            res = {"error": "Invalid action"}

        self.finish(res)


def setup_handlers(server_app):
    global logging
    logging = server_app.log

    global root_dir
    root_dir = server_app.root_dir

    global lsp_client
    lsp_client = LSPWrapper(logging)

    print("NEW skIBIDI MODE")

    web_app = server_app.web_app
    host_pattern = ".*$"
    base_url = web_app.settings["base_url"] + "jupyter-copilot"
    handlers = [
        (url_path_join(base_url, "ws"), NotebookLSPHandler),
        (url_path_join(base_url, "login"), AuthHandler),
        (url_path_join(base_url, "signout"), AuthHandler),
    ]
    web_app.add_handlers(host_pattern, handlers)

    for handler in handlers:
        logging.info("jupyter_copilot | Registered handler at %s", handler[0])

    logging.info("jupyter_copilot | Sucessfully registered handlers at %s", base_url)
