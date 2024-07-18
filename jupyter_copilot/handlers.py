import json
import requests
import time
import threading
from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
import tornado


class CopilotClient():
    @classmethod
    def __init__(cls):
        try:
            with open('.copilot_token', 'r') as f:
                access_token = f.read()
        except FileNotFoundError:
            access_token = None

        cls.token = access_token

    @classmethod
    def get_token(cls):
        # Check if the .copilot_token file exists
        while True:
            try:
                with open('.copilot_token', 'r') as f:
                    access_token = f.read()
                    break
            except FileNotFoundError:
                # hang until the file is created
                time.sleep(15)

        # Get a session with the access token
        resp = requests.get('https://api.github.com/copilot_internal/v2/token', headers={
            'authorization': f'token {access_token}',
            'editor-version': 'Neovim/0.6.1',
            'editor-plugin-version': 'copilot.vim/1.16.0',
            'user-agent': 'GithubCopilot/1.155.0'
        })

        # Parse the response json, isolating the token
        resp_json = resp.json()
        cls.token = resp_json.get('token')

    @classmethod
    def setup(cls):
        resp = requests.post('https://github.com/login/device/code', headers={
            'accept': 'application/json',
            'editor-version': 'Neovim/0.6.1',
            'editor-plugin-version': 'copilot.vim/1.16.0',
            'content-type': 'application/json',
            'user-agent': 'GithubCopilot/1.155.0',
            'accept-encoding': 'gzip,deflate,br'
        }, data='{"client_id":"Iv1.b507a08c87ecfe98","scope":"read:user"}')

        resp_json = resp.json()
        device_code = resp_json.get('device_code')
        user_code = resp_json.get('user_code')
        verification_uri = resp_json.get('verification_uri')

        logging.info(f'Please visit {verification_uri} and enter code {
            user_code} to authenticate.')

        while True:
            time.sleep(5)
            resp = requests.post('https://github.com/login/oauth/access_token', headers={
                'accept': 'application/json',
                'editor-version': 'Neovim/0.6.1',
                'editor-plugin-version': 'copilot.vim/1.16.0',
                'content-type': 'application/json',
                'user-agent': 'GithubCopilot/1.155.0',
                'accept-encoding': 'gzip,deflate,br'
            }, data=f'{{"client_id":"Iv1.b507a08c87ecfe98","device_code":"{device_code}","grant_type":"urn:ietf:params:oauth:grant-type:device_code"}}')

            resp_json = resp.json()
            access_token = resp_json.get('access_token')

            if access_token:
                break

        with open('.copilot_token', 'w') as f:
            f.write(access_token)

        logging.info('Authentication success!')

    @classmethod
    def token_thread(cls):
        while True:
            cls.get_token()
            time.sleep(25 * 60)

    @classmethod
    def is_token_invalid(cls):
        if cls.token is None or 'exp' not in cls.token or cls.extract_exp_value(cls.token) <= time.time():
            return True
        return False

    @classmethod
    def extract_exp_value(cls, token):
        pairs = token.split(';')
        for pair in pairs:
            key, value = pair.split('=')
            if key.strip() == 'exp':
                return int(value.strip())
        return None

    async def get_completion(self, prompt, language='python'):
        resp = await tornado.httpclient.AsyncHTTPClient().fetch(
            'https://copilot-proxy.githubusercontent.com/v1/engines/copilot-codex/completions',
            method='POST',
            headers={'authorization': f'Bearer {self.token}'},
            body=json.dumps({
                'prompt': prompt,
                'suffix': '',
                'max_tokens': 1000,
                'temperature': 0,
                'top_p': 1,
                'n': 1,
                'stop': ['\n'],
                'nwo': 'github/copilot.vim',
                'stream': True,
                'extra': {
                    'language': language
                }
            })
        )

        result = ''
        resp_text = resp.body.decode('utf-8').split('\n')
        for line in resp_text:
            if line.startswith('data: {'):
                json_completion = json.loads(line[6:])
                completion = json_completion.get('choices')[0].get('text')
                if completion:
                    result += completion
                else:
                    result += '\n'

        return result


Copilot = CopilotClient()


class CompletionHandler(APIHandler):

    @tornado.web.authenticated
    async def post(self):
        body = self.get_json_body()
        prompt = body.get('prompt')
        language = body.get('language', 'python')
        logging.info("Prompt: " + prompt)

        if Copilot.token is None or Copilot.is_token_invalid():
            # set error
            self.set_status(500)
            self.finish('Token is invalid or not set')

        try:
            resp = await Copilot.get_completion(prompt, language)
            logging.info(f'Copilot response: {resp}')
            self.finish(resp)
        except Exception as e:
            self.set_status(500)
            logging.info(f'Error: {e}')
            self.finish(str(e))

    @tornado.web.authenticated
    async def post_login(self):
        logging.info('Logging in')
        self.finish('Logging in')


class AuthHandler(APIHandler):
    @tornado.web.authenticated
    async def post(self):
        logging.info('Auth handler')
        self.finish("hello from Auth handler")


def setup_handlers(server_app):
    global logging
    logging = server_app.log

    web_app = server_app.web_app
    host_pattern = ".*$"
    base_url = web_app.settings['base_url']

    handlers = [
        (url_path_join(base_url, 'jupyter-copilot',
                       '/copilot'), CompletionHandler),
        (url_path_join(base_url, 'jupyter-copilot',
                       '/login'), AuthHandler),
    ]
    web_app.add_handlers(host_pattern, handlers)
    logging.info(
        f"Jupyter Copilot server extension is activated with {handlers}")

    if (Copilot.token is None):
        Copilot.setup()

    # Start token refresh thread
    threading.Thread(target=Copilot.token_thread, daemon=True).start()
