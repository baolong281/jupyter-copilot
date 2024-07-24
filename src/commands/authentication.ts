import { JupyterFrontEnd } from '@jupyterlab/application';
import { makePostRequest } from '../utils';
import { Widget } from '@lumino/widgets';
import { MainAreaWidget } from '@jupyterlab/apputils';

interface AlreadySignedInResponse {
  status: 'AlreadySignedIn';
  user: string;
}

interface PendingLoginResponse {
  status: 'PendingLogin';
  user?: string;
  userCode: string;
  verificationUri: string;
  expiresIn: number;
  interval: number;
}

interface SignOutResponse {
  status: string;
}
type LoginResponse = AlreadySignedInResponse | PendingLoginResponse;

const defaultWidgetCSS = `
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue', sans-serif;
      color: #333;
      background-color: #fff;
      padding: 30px;
      max-width: 400px;
      margin: 0 auto;
      border-radius: 8px;
      box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
      text-align: center;
`;

const signWidget = (authData: PendingLoginResponse) => {
  const content = new Widget();

  const messageElement = document.createElement('div');

  messageElement.style.cssText = defaultWidgetCSS;

  messageElement.innerHTML = `
            <h2 style="font-size: 24px; margin-bottom: 20px; color: #0366d6;">GitHub Copilot Authentication</h2>
            <p style="margin-bottom: 10px;">Enter this code on GitHub:</p>
            <div style="font-size: 32px; font-weight: bold; background-color: #f6f8fa; color: #0366d6; padding: 15px; border-radius: 5px; margin: 20px 0; letter-spacing: 2px; box-shadow: inset 0 2px 4px rgba(0,0,0,0.05);">${authData.userCode}</div>
            <p style="margin-bottom: 10px;">Go to: <a href="${authData.verificationUri}" target="_blank" style="color: #0366d6; text-decoration: none;">${authData.verificationUri}</a></p>
            <p style="font-size: 14px; color: #666;">This code will expire in <span id="timer" style="font-weight: bold;">${authData.expiresIn}</span> seconds.</p>
          `;
  content.node.appendChild(messageElement);
  const widget = new MainAreaWidget({ content });
  widget.id = 'apod-jupyterlab';
  widget.title.label = 'Sign In';
  widget.title.closable = true;
  return widget;
};

const alreadySignedInWidget = (username: string) => {
  const content = new Widget();

  const messageElement = document.createElement('div');

  messageElement.style.cssText = defaultWidgetCSS;

  messageElement.innerHTML = `
            <h2 style="font-size: 24px; margin-bottom: 20px;">Copilot already signed in as: <span style="color: #2366d6;">${username}</span></h2>
          `;

  content.node.appendChild(messageElement);
  const widget = new MainAreaWidget({ content });
  widget.id = 'apod-jupyterlab';
  widget.title.label = 'Already Signed In';
  widget.title.closable = true;
  return widget;
};

const SignedOutWidget = () => {
  const content = new Widget();

  const messageElement = document.createElement('div');

  messageElement.style.cssText = defaultWidgetCSS;

  messageElement.innerHTML = `
            <h2 style="font-size: 24px; margin-bottom: 20px; color: #2366d6;">Successfully signed out with GitHub!</h2>
          `;

  content.node.appendChild(messageElement);
  const widget = new MainAreaWidget({ content });
  widget.id = 'apod-jupyterlab';
  widget.title.label = 'Sign Out Successful';
  widget.title.closable = true;
  return widget;
};

// function to execute whenever the login command is called
export const LoginExecute = (app: JupyterFrontEnd): void => {
  makePostRequest('login', {}).then(data => {
    // data is a string turned into a json object
    const res = JSON.parse(data) as LoginResponse;

    // handle this branch later
    if (res.status === 'AlreadySignedIn') {
      let widget = alreadySignedInWidget(res.user);
      if (!widget.isDisposed) {
        widget.dispose();
        widget = alreadySignedInWidget(res.user);
      }
      if (!widget.isAttached) {
        app.shell.add(widget, 'main');
      }
      return;
    }

    let widget = signWidget(res);
    if (!widget.isDisposed) {
      widget.dispose();
      widget = signWidget(res);
    }
    if (!widget.isAttached) {
      app.shell.add(widget, 'main');
    }

    // countdown timer for expires in the this code will expire in {expiresin seconds}
    let timeRemaining = res.expiresIn;
    const interval = setInterval(() => {
      if (timeRemaining <= 0) {
        clearInterval(interval);
        widget.dispose();
        return;
      }
      const timerElement = widget.node.querySelector('#timer');
      if (timerElement) {
        timerElement.textContent = timeRemaining.toString();
      }
      timeRemaining--;
    }, 1000);
    app.shell.activateById(widget.id);
  });
};

// function to execute when the signout command is called
export const SignOutExecute = (app: JupyterFrontEnd): void => {
  makePostRequest('signout', {}).then(data => {
    const res = JSON.parse(data) as SignOutResponse;

    if (res.status === 'NotSignedIn') {
      let widget = SignedOutWidget();
      if (!widget.isDisposed) {
        widget.dispose();
        widget = SignedOutWidget();
      }
      if (!widget.isAttached) {
        app.shell.add(widget, 'main');
      }
    }
  });
};
