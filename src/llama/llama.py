from ollama import chat
from ollama import ChatResponse
from . import tools

TOOLS = {
    'end_conversation': tools.end_conversation,
}

session_messages= [
    {
        'role': 'system',
        'content': (
            "You are Atreus, a voice assistant helping the user with day to day work. "
            "Your replies are spoken aloud, so keep them short, plain and conversational. "
            "You have tools available, but only call a tool when the request actually "
            "requires it. Answer directly whenever you can. When you do use a tool, the "
            "tool runs first and the user only hears you afterwards, so speak about what "
            "you did, not what you are about to do."
        )
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
    while True:
        response: ChatResponse = chat(model="qwen3:8b", messages=session_messages, think=False, tools=TOOLS.values())
        session_messages.append(response['message'])
        # TODO: if tool fails and throws error(except EndConversartion) that should be appended
        if response.message.tool_calls:
            for tc in response.message.tool_calls:
                if tc.function.name in TOOLS:
                    print(f"Calling {tc.function.name} with arguments {tc.function.arguments}")
                    result = TOOLS[tc.function.name](**tc.function.arguments)
                    print(f"Result: {result}")
                    # add the tool result to the messages
                    session_messages.append({'role': 'tool', 'tool_name': tc.function.name, 'content': str(result)})
                else:
                    session_messages.append({'role': 'tool', 'tool_name': tc.function.name, 'content': f'Error: Tool not found'})

        else:
            return response.message.content

