import json
import subprocess
import threading
import time
from typing import Dict, Callable, Any, Optional
import os
# Wrapper class for interfacing with the Copilot LSP.
# initializes, sends messages, and reads output
# the actual LSP server is from copilot-node-server which actually calls Copilot servers
# https://www.npmjs.com/package/copilot-node-server?activeTab=dependents
# the LSP requires that we communicate with it through stdout using json rpc


class LSPWrapper:
    def __init__(self, logger):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(current_dir)
        lsp_path = os.path.join(
            parent_dir, "node_modules", "copilot-node-server", "copilot", "dist", "language-server.js")
        logger.info(f"Initializing Copilot LSP server in: {
            ''.join(lsp_path)}")
        self.logger = logger
        try:
            # start the process and throw an error if it fails
            self.process = subprocess.Popen(
                ["node", lsp_path, "--stdio"],
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

        # these maps hold callbacks for requests for when we recieve a response
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

    # constantly polling in a seperate thread
    def _read_output(self):
        while self.is_process_running():
            header = self.process.stdout.readline()
            if not header:
                break
            try:
                content_length = int(header.strip().split(': ')[1])
                self.process.stdout.readline()  # Read the empty line
                content = self.process.stdout.read(content_length)
                # whenever we get a message, we process it
                self._handle_received_payload(json.loads(content))
            except Exception as e:
                self.logger.error(f"Error processing server output: {e}")

    # when we send notifications, we don't expect a response

    def send_notification(self, method: str, params: dict):
        self._send_message({"method": method, "params": params})

    # send message to lsp through stdin with special lsp format

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

    # send request to lsp and wait for response
    # if a response comes, then handle_received_payload will be called
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

        # put the callback into the map
        # when we get the response, we will call resolve or reject and the entry will be popped
        self.resolve_map[self.request_id] = resolve
        self.reject_map[self.request_id] = reject

        # 10 second timeout to prevent indefinite waiting
        # this will immediately stop blocking if the result is set by calling either resolve or reject
        result.wait(timeout=10)

        # at this point if a response has not been received then result will not be set, so we throw an error
        if not result.is_set():
            raise TimeoutError(f"Request timed out: method={
                               method}, id={self.request_id}")

        if 'error' in response:
            raise Exception(response['error'])

        self.resolve_map.pop(self.request_id, None)
        self.reject_map.pop(self.request_id, None)
        return response['result']

    # get the completion from copilot
    def get_completion(self, text, line, character, file):
        result = self.send_request("textDocument/completion", {
            "textDocument": {
                "uri": f"file:///{file}"
            },
            "position": {
                "line": line,
                "character": character
            }
        })

        if len(result) == 0:
            return None

        result = result[0]
        return result

    # when we get a message, we process it
    # if it has an id, then we call the resolve or reject callback

    def _handle_received_payload(self, payload: dict):
        self.logger.info("payload: %s", payload)
        if "id" in payload:
            if "result" in payload:
                # pop from map then call
                resolve = self.resolve_map.pop(payload["id"], None)
                if resolve:
                    resolve(payload["result"])
            elif "error" in payload:
                reject = self.reject_map.pop(payload["id"], None)
                if reject:
                    reject(payload["error"])

    @ staticmethod
    def wait(ms: int):
        print(f"Waiting for {ms} ms")
        time.sleep(ms / 1000)
