import { URLExt } from '@jupyterlab/coreutils';
import { ServerConnection } from '@jupyterlab/services';
/**
 * Call the API extension
 *
 * @param endPoint API REST end point for the extension
 * @param init Initial values for the request
 * @returns The response body interpreted as JSON
 */
export async function requestAPI<T>(
  endPoint = '',
  init: RequestInit = {}
): Promise<T> {
  // Make request to Jupyter API
  const settings = ServerConnection.makeSettings();
  const requestUrl = URLExt.join(
    settings.baseUrl,
    'jupyter-copilot', // API Namespace
    endPoint
  );
  let response: Response;
  try {
    response = await ServerConnection.makeRequest(requestUrl, init, settings);
  } catch (error) {
    throw new ServerConnection.NetworkError(error as any);
  }
  let data: any = await response.text();
  if (data.length > 0) {
    try {
      data = JSON.parse(data);
    } catch (error) {
      console.log('Not a JSON response body.', response);
    }
  }
  if (!response.ok) {
    throw new ServerConnection.ResponseError(response, data.message || data);
  }
  return data;
}

export const createCompletion = async (
  prompt: string,
  language: string = 'python'
) => {
  const body = {
    prompt: prompt,
    language: language
  };
  return await makePostRequest('copilot', body);
};

// takes in the route and body of the request (json object)
export const makePostRequest = async (route: string, body: object) => {
  try {
    const settings = ServerConnection.makeSettings();
    const requestUrl = URLExt.join(settings.baseUrl, 'jupyter-copilot', route);
    console.log('Request URL:', requestUrl);

    const init: RequestInit = {
      method: 'POST',
      body: JSON.stringify(body),
      headers: {
        'Content-Type': 'application/json',
        Authorization: `token ${settings.token}`
      }
    };

    console.log('Request init:', init);

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
