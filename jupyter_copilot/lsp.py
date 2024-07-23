import json
import subprocess
import threading
import time
from typing import Dict, Callable, Any, List
import os
# Wrapper class for interfacing with the Copilot LSP.
# initializes, sends messages, and reads output
# the actual LSP server is from copilot-node-server which actually calls Copilot servers
# https://www.npmjs.com/package/copilot-node-server?activeTab=dependents
# the LSP requires that we communicate with it through stdout using json rpc


class LSPWrapper:
    def __init__(self, logger):
        self.logger = logger

        self.process = self._spawn_process()
        self.request_id = 0

        # lock for restarting callback thread
        self.restart_lock = threading.Lock()

        # these maps hold callbacks for requests for when we recieve a response
        self.resolve_map: Dict[int, Callable[[Any], None]] = {}
        self.reject_map: Dict[int, Callable[[Any], None]] = {}

        # Start reading output in a separate thread
        self.output_thread = threading.Thread(target=self._read_output)
        self.output_thread.start()
        self.restart_callbacks: List[Callable[[], None]] = []

        # Check if the process started successfully
        if not self.is_process_running():
            raise RuntimeError("Failed to start the LSP server process")

        self.wait(500)
        self.send_startup_notification()

    def register_restart_callback(self, callback: Callable[[], None]):
        self.logger.info("registering callback...")
        self.logger.info(callback)
        self.restart_callbacks.append(callback)

    def unregister_restart_callback(self, callback: Callable[[], None]):
        self.restart_callbacks.remove(callback)
        self.logger.info(callback)
        self.logger.info("call back being unregistered")


    # spawns the lsp process and returns it
    def _spawn_process(self) -> subprocess.Popen[str]:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(current_dir)
        lsp_path = os.path.join(
            parent_dir, "node_modules", "copilot-node-server", "copilot", "dist", "language-server.js")
        self.logger.info(f"Initializing Copilot LSP server in: {
            ''.join(lsp_path)}")
        try:
            # start the process and throw an error if it fails
            process = subprocess.Popen(
                ["node", lsp_path, "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0
            )
        except FileNotFoundError as e:
            self.logger.error(
                f"Error: Could not find the specified file or directory. Full error: {e}")
            self.logger.error(f"Current working directory: {os.getcwd()}")
            raise
        except PermissionError as e:
            self.logger.error(
                f"Error: Permission denied when trying to execute the command. Full error: {e}")
            raise
        except Exception as e:
            self.logger.error(
                f"An unexpected error occurred while starting the LSP server: {e}")
            raise


        return process

    def send_startup_notification(self):
        self.send_request("initialize", {
            "capabilities": {"workspace": {"workspaceFolders": True}}
        })

        # Send `initialized` notification
        self.send_notification("initialized", {})


    # polls the process to see if it is running
    # if it is running return 0 else return the exit code
    # this might be be bad if the process exited with code 0 
    # TODO check
    def is_process_running(self) -> int:
        if self.process.poll() is None:
            return 0
        else:

            # only print out if exit code is not 130
            # if exit code is 130, ctrl + c was pressed in terminal
            # printing will mess up the exit confirmation
            if self.process.returncode != 130:
                self.logger.error(f"LSP server process has terminated. Exit code: {
                    self.process.returncode}")
                self.logger.error("stderr output:")

            return self.process.returncode


    # restart the server
    # this should run in a separate thread
    def restart_server(self):
        with self.restart_lock:
            self.logger.info("Restarting LSP server...")
            if self.process:
                self.process.terminate()
                self.process.wait()
            
            self.process = self._spawn_process()

            self.wait(500)

            self.send_startup_notification()

            if not self.is_process_running():
                raise RuntimeError("Failed to restart the LSP server process")

            for callback in self.restart_callbacks:
                callback()


    # constantly runs in a separate thread to read the output from the lsp
    # if the process is not running and the exit code was not 130 (ctrl + c) then restart the server
    def _read_output(self):
        while True:
            process_return_code = self.is_process_running()
            # if ctrl + c just exit the thread
            # process should have already been killed
            if process_return_code == 130:
                return
            # if the process is not running, restart it
            elif process_return_code != 1:
                self.logger.info("LSP server process has stopped. Attempting to restart...")
                if not self.restart_lock.locked():
                    # restart the server in a separate thread
                    # making sure that we don't try to restart the server multiple times
                    restart_thread = threading.Thread(target=self.restart_server)
                    restart_thread.start()
                # wait 10 ms before checking again
                # the output thread keeps looping so it would print out the error message multiple times
                self.wait(10)
                continue

            header = self.process.stdout.readline()
            if not header:
                continue
            try:
                content_length = int(header.strip().split(': ')[1])
                self.process.stdout.readline()  # Read the empty line
                content = self.process.stdout.read(content_length)
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
        time.sleep(ms / 1000)
