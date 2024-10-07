import { ChatModel, IChatMessage, INewMessage } from '@jupyter/chat';
import { UUID } from '@lumino/coreutils';

class CopilotChat extends ChatModel {
  sendMessage(
    newMessage: INewMessage
  ): Promise<boolean | void> | boolean | void {
    console.log(`New Message:\n${newMessage.body}`);
    const message: IChatMessage = {
      body: newMessage.body,
      id: newMessage.id ?? UUID.uuid4(),
      type: 'msg',
      time: Date.now() / 1000,
      sender: { username: 'me' }
    };
    this.messageAdded(message);
  }
}

export { CopilotChat };
