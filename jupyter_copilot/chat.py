from jupyter_copilot.globals import Globals
from jupyter_copilot.lsp import LSPWrapper
from jupyter_copilot.notebook import NotebookManager
from typing import Dict, Any

class ChatManager:
    def __init__(self, notebook_manager: NotebookManager) -> None:
        self.notebook_manager = notebook_manager
        self.conversation_id: str | None = None  # Tracks the conversation ID
        self.work_done_token = f"copilot_chat://{self.notebook_manager.name}"
        globals = Globals()
        self.logging: Any = globals.logging

        assert globals.lsp_client is not None

        self.lsp_client: LSPWrapper = globals.lsp_client

    def start_conversation(self) -> None:
        """Starts a new conversation with the LSP server."""

        turns = [{
            "request": "message",
            "message": "Hello, what is this about?",  # Your initial message
            "workDoneToken": f"copilot_chat://{self.notebook_manager.name}",
        }]

        capabilties = {
            "skills": [],
            "allSkills": True
        }
        
        # Prepare the request for starting the conversation
        request = {
            "message": "Hello, what is this about?",
            "workDoneToken": self.work_done_token,
            "doc": {
                    "uri": f"file:///{self.notebook_manager.name}",
                    "languageId": self.notebook_manager.language,
                    "version": self.notebook_manager.document_version,
                    "text": self.notebook_manager.get_full_code(),
                },
            "source": "panel",  # or "inline" based on your use case
            "computeSuggestions": True,
            "turns": turns,
            "capabilities": capabilties,
        }

        # Send the conversation start request to the LSP
        response = self.lsp_client.send_request("conversation/create", request)

        if 'error' in response:
            raise Exception(f"Error starting conversation: {response['error']}")

        self.conversation_id = response["conversationId"]
        self.logging.info(f"[Copilot] Conversation started with ID: {self.conversation_id}")



    def send_conversation_turn(self, user_message: str) -> Dict[str, Any]:
        """
        Sends a message (turn) in the current conversation.
        """

        self.logging.debug(f"[Copilot] Sending message to conversation {self.conversation_id}")
        response = self.lsp_client.send_request(
            "conversation/turn",
            {
                "conversationId": self.conversation_id,
                "message": "What does this function do?",
                "workDoneToken": self.work_done_token,
            },
        )
        self.logging.debug(f"[Copilot] Received response for conversation turn: {response}")
        return response

    def end_conversation(self) -> None:
        """
        Ends the active conversation.
        """
        if not self.conversation_id:
            raise Exception("No active conversation to end.")
            
        self.logging.debug(f"[Copilot] Ending conversation {self.conversation_id}")
        self.lsp_client.send_notification(
            "conversation/destroy",
            {"conversationId": self.conversation_id},
        )
        self.conversation_id = None

    def get_conversation_context(self) -> Dict[str, Any]:
        """
        Collects and returns context data for the conversation.
        This can include the code, environment, or other information to help the LSP.
        """
        notebook = self.notebook_manager
        return {
            "document": {
                "uri": f"file:///{notebook.name}",
                "languageId": notebook.language,
                "version": notebook.document_version,
                "text": notebook.get_full_code(),
            }
        }
