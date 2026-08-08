from ollama import chat
from ollama import ChatResponse

session_messages= [
    {
        'role': 'system',
        'content': "You are Atreus, you will help me with my day to day to work, you have access to various tools available. Use them whenever needed."
    }
]

def llama(message: str):
    # Users request a.k.a. me
    session_messages.append(
        {
            'role': 'user',
            'content': message
        }
    )

    response: ChatResponse = chat(model="qwen3:8b", messages=session_messages)
    session_messages.append(response['message'])

    return response.message.content

