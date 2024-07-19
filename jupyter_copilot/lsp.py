import json
import subprocess
import threading
import time
from typing import Dict, Callable, Any, Optional
import os


class LSPWrapper:
    def __init__(self, lsp_command: list, logger):
        logger.info(f"Initializing LSPWrapper with command: {
            ' '.join(lsp_command)}")
        self.logger = logger
        try:
            self.process = subprocess.Popen(
                lsp_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0
            )
        except FileNotFoundError as e:
            logger.error(
                f"Error: Could not find the specified file or directory. Full error: {e}")
            logger.error(f"Current working directory: {os.getcwd()}")
            raise
        except PermissionError as e:
            logger.error(
                f"Error: Permission denied when trying to execute the command. Full error: {e}")
            raise
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while starting the LSP server: {e}")
            raise

        self.request_id = 0
        self.resolve_map: Dict[int, Callable[[Any], None]] = {}
        self.reject_map: Dict[int, Callable[[Any], None]] = {}

        # Start reading output in a separate thread
        self.output_thread = threading.Thread(target=self._read_output)
        self.output_thread.start()

        # Check if the process started successfully
        if not self.is_process_running():
            raise RuntimeError("Failed to start the LSP server process")

    def is_process_running(self) -> bool:
        if self.process.poll() is None:
            return True
        else:
            self.logger.error(f"LSP server process has terminated. Exit code: {
                self.process.returncode}")
            self.logger.error("stderr output:")
            self.logger.error(self.process.stderr.read())
            return False

    def _read_output(self):
        while self.is_process_running():
            header = self.process.stdout.readline()
            if not header:
                break
            try:
                content_length = int(header.strip().split(': ')[1])
                self.process.stdout.readline()  # Read the empty line
                content = self.process.stdout.read(content_length)
                self._handle_received_payload(json.loads(content))
            except Exception as e:
                self.logger.error(f"Error processing server output: {e}")

    def _send_message(self, data: dict):
        if not self.is_process_running():
            raise RuntimeError(
                "Cannot send message. LSP server process is not running.")

        message = json.dumps({**data, "jsonrpc": "2.0"})
        content_length = len(message.encode('utf-8'))
        rpc_message = f"Content-Length: {content_length}\r\n\r\n{message}"
        try:
            self.process.stdin.write(rpc_message)
            self.process.stdin.flush()
        except BrokenPipeError:
            self.logger.error(
                "Error: Broken pipe. The LSP server process may have terminated unexpectedly.")
            raise

    def send_request(self, method: str, params: dict) -> Any:
        self.request_id += 1
        self._send_message(
            {"id": self.request_id, "method": method, "params": params})
        result = threading.Event()
        response = {}

        def resolve(payload):
            response['result'] = payload
            result.set()

        def reject(payload):
            response['error'] = payload
            result.set()

        self.resolve_map[self.request_id] = resolve
        self.reject_map[self.request_id] = reject
        result.wait(timeout=10)  # Add a timeout to prevent indefinite waiting

        if not result.is_set():
            raise TimeoutError(f"Request timed out: method={
                               method}, id={self.request_id}")

        if 'error' in response:
            raise Exception(response['error'])
        return response['result']

    def send_notification(self, method: str, params: dict):
        self._send_message({"method": method, "params": params})

    def _handle_received_payload(self, payload: dict):
        if "id" in payload:
            if "result" in payload:
                resolve = self.resolve_map.pop(payload["id"], None)
                if resolve:
                    resolve(payload["result"])
            elif "error" in payload:
                reject = self.reject_map.pop(payload["id"], None)
                if reject:
                    reject(payload["error"])

    @staticmethod
    def wait(ms: int):
        print(f"Waiting for {ms} ms")
        time.sleep(ms / 1000)
