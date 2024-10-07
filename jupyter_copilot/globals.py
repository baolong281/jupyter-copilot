from typing import Dict, Optional, Any
from jupyter_copilot.lsp import LSPWrapper

class Globals:
    __instance = None
    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super().__new__(cls)
            cls.lsp_client: Optional[LSPWrapper] = kwargs.get("lsp_client")
            cls.logging: Optional[Any] =  kwargs.get("logging")
            cls.root_dir: Optional[str] = kwargs.get("root_dir")
        return cls.__instance
