from ollama import chat
from ollama import ChatResponse
import importlib
import pkgutil
import sys
from . import tools

TOOLS = {}

def load_tools():
    """Rebuild TOOLS from whatever currently lives in tools/.

    Each module exposes one function named after it: tools/weather.py -> weather().
    Called again after the claude tool runs, so a tool it just wrote is picked up
    without a restart.
    """
    # Cheap insurance: FileFinder refreshes on directory mtime, which can miss a
    # file written in the same tick as the last cache fill.
    importlib.invalidate_caches()
    TOOLS.clear()
    for info in pkgutil.iter_modules(tools.__path__):
        name = f"{tools.__name__}.{info.name}"
        try:
            # reload re-executes the module body, so only use it on modules we
            # already have -- otherwise a fresh import would run twice.
            if name in sys.modules:
                module = importlib.reload(sys.modules[name])
            else:
                module = importlib.import_module(name)
        except Exception as e:
            # An LLM writes these files; one bad tool shouldn't take Atreus down.
            print(f"skipping tool {info.name}: {e!r}")
            continue
        fn = getattr(module, info.name, None)
        if callable(fn):
            TOOLS[info.name] = fn

load_tools()

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
                    if tc.function.name=='claude':
                        load_tools()
                else:
                    session_messages.append({'role': 'tool', 'tool_name': tc.function.name, 'content': 'Error: Tool not found'})
            

        else:
            return response.message.content

