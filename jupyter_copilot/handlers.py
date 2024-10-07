import asyncio
from typing import Any, Dict
from tornado.ioloop import IOLoop
from tornado.websocket import WebSocketHandler
from jupyter_server.utils import url_path_join
import json
import os
from jupyter_copilot.lsp import LSPWrapper
from jupyter_server.base.handlers import JupyterHandler
from jupyter_copilot.globals import Globals
from jupyter_copilot.notebook import NotebookManager
from jupyter_copilot.completions import CompletionManager
from jupyter_copilot.chat import ChatManager

class NotebookLSPHandler(WebSocketHandler):
    def initialize(self):
        self.notebook_manager: NotebookManager | None = None
        self.completion_manager: CompletionManager | None = None
        self.message_queue = asyncio.Queue()

        globals = Globals()
        self.logging: Any = globals.logging

        assert globals.lsp_client is not None
        assert globals.root_dir is not None

        self.lsp_client: LSPWrapper = globals.lsp_client
        self.root_dir: str = globals.root_dir

        # register function to run in the background
        IOLoop.current().add_callback(self.process_message_queue)

    async def open(self, *args, **kwargs):
        notebook_path = self.get_argument("path", "")
        notebook_path = os.path.join(self.root_dir, notebook_path)
        self.notebook_manager = NotebookManager(notebook_path)
        self.completion_manager = CompletionManager(self.notebook_manager)
        self.chat_manager = ChatManager(self.notebook_manager)
        # Start a new conversation
        conversation_id = self.chat_manager.start_conversation()

        # Send a user message to the conversation
        response = self.chat_manager.send_conversation_turn("What does this function do?")
        print(response)
        response = self.chat_manager.send_conversation_turn("What do you see?")
        print(response)

        await self.send_message("connection_established", {})
        self.logging.debug("[Copilot] WebSocket opened")

    async def on_message(self, message):
        try:
            data = json.loads(message)
            await self.message_queue.put(data)
        except json.JSONDecodeError:
            self.logging.error(f"Received invalid JSON: {message}")

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
                self.logging.error(f"Error processing message: {e}")
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
        notebook_path = os.path.join(self.root_dir, notebook_path)

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
            self.logging.error(f"Error sending message: {e}")

    def on_close(self):
        self.logging.debug("[Copilot] WebSocket closed")

        if self.notebook_manager is None:
            raise Exception("Notebook manager not initialized")
        if self.completion_manager is None:
            raise Exception("Completion manager not initialized")

        # when socket is closed send the close signal to server
        # unregister the lsp server restart callback
        self.completion_manager.send_close_signal()
        self.lsp_client.unregister_restart_callback(self.completion_manager._callback)
        self.notebook_manager = None


class AuthHandler(JupyterHandler):
    def initialize(self):
        globals = Globals()
        self.logging: Any = globals.logging
        self.root_dir =  globals.root_dir

        assert globals.lsp_client is not None

        self.lsp_client: LSPWrapper = globals.lsp_client 

    async def post(self):
        action = self.request.path.split("/")[-1]
        if action == "login":
            res = self.lsp_client.send_request("signInInitiate", {})
        elif action == "signout":
            res = self.lsp_client.send_request("signOut", {})
        else:
            self.set_status(404)
            res = {"error": "Invalid action"}

        self.finish(res)


def setup_handlers(server_app):
    lsp_client = LSPWrapper(server_app.log) 
    logging = server_app.log
    root_dir = server_app.root_dir

    # initialize global variables
    globals = Globals(lsp_client=lsp_client, logging=logging, root_dir=root_dir)

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
