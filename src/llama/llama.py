from ollama import chat
from ollama import ChatResponse

def llama(message: str):
    response: ChatResponse = chat(model="qwen3:8b", messages=[
        {
            'role': 'user',
            'content': message,
        }
    ], think=False)

    return response.message.content

