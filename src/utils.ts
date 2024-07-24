import { URLExt } from '@jupyterlab/coreutils';
import { ServerConnection } from '@jupyterlab/services';

// takes in the route and body of the request (json object)
export const makePostRequest = async (route: string, body: object) => {
  try {
    const settings = ServerConnection.makeSettings();
    const requestUrl = URLExt.join(settings.baseUrl, 'jupyter-copilot', route);

    const init: RequestInit = {
      method: 'POST',
      body: JSON.stringify(body),
      headers: {
        'Content-Type': 'application/json',
        Authorization: `token ${settings.token}`
      }
    };

    const response = await ServerConnection.makeRequest(
      requestUrl,
      init,
      settings
    );

    if (!response.ok) {
      console.error('Response not OK:', response.status, response.statusText);
      const errorData = await response.text();
      console.error('Error data:', errorData);
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.text();
    return data;
  } catch (reason) {
    console.error(
      `The jupyter_copilot server extension appears to be missing or the request failed.\n${reason}`
    );
    throw reason;
  }
};
