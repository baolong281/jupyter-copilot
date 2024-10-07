from jupyter_copilot.globals import Globals
from jupyter_copilot.notebook import NotebookManager
from jupyter_copilot.lsp import LSPWrapper
from typing import Dict, Any


class CompletionManager:
    def __init__(self, notebook_manager: NotebookManager) -> None:
        self.notebook_manager = notebook_manager
        self.document_version = 0

        globals = Globals()
        self.logging: Any = globals.logging

        assert globals.lsp_client is not None

        self.lsp_client: LSPWrapper = globals.lsp_client

        # callback to run if the lsp server is ever restarted
        # need to reload the notebook content into the lsp server
        def _restart_callback():
            code = notebook_manager.load_notebook()

            self.lsp_client.send_notification(
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
        self.lsp_client.register_restart_callback(self._callback)

    def send_full_update(self) -> None:
        """sends an update to the lsp with the latest code"""
        self.document_version += 1
        notebook = self.notebook_manager
        code = notebook.get_full_code()
        self.lsp_client.send_notification(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": f"file:///{notebook.name}",
                    "version": self.document_version,
                },
                "contentChanges": [{"text": code}],
            },
        )
        self.logging.debug("[Copilot] Sending full update for %s", notebook.path)

    def request_completion(
        self, cell_id: int, line: int, character: int
    ) -> Dict[str, Any]:
        """
        requests a completion from the lsp server given a cell id, line number, and character position
        then returns the response
        """
        notebook = self.notebook_manager
        line = notebook.get_absolute_line_num(cell_id, line)
        self.logging.debug(
            f"[Copilot] Requesting completion for cell {cell_id}, line {line}, character {character}"
        )
        response = self.lsp_client.send_request(
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

        self.lsp_client.send_notification(
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
        self.logging.debug("[Copilot] Sending close signal to LSP for %s", path)
        self.lsp_client.send_notification(
            "textDocument/didClose", {"textDocument": {"uri": f"file:///{path}"}}
        )

    def handle_set_language(self):
        notebook = self.notebook_manager

        self.send_close_signal()
        self.lsp_client.send_notification(
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
