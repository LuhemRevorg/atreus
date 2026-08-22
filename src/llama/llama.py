from ollama import chat
from ollama import ChatResponse
import importlib
import pkgutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from . import tools

TOOLS = {}

# The reply is handed straight to the speech engine, which has nothing to say
# about None. qwen3 with think=True can end a turn on reasoning alone.
FALLBACK_REPLY = "Sorry, I didn't catch that."

def load_tools():
    """Rebuild TOOLS from whatever currently lives in tools/.

    Each module exposes one function named after it: tools/weather.py -> weather().
    Called again after the claude tool runs, so a tool it just wrote is picked up
    without a restart.
    """
    importlib.invalidate_caches()
    TOOLS.clear()
    for info in pkgutil.iter_modules(tools.__path__):
        name = f"{tools.__name__}.{info.name}"
        try:
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



def llama(message: str, session_messages):
    # Users request a.k.a. me
    session_messages.append(
        {
            'role': 'user',
            'content': message
        }
    )
    while True:
        response: ChatResponse = chat(model="qwen3:8b", messages=session_messages, think=True, tools=TOOLS.values())
        session_messages.append(response['message'])
        claude_contains = False
        if response.message.tool_calls:
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {}
                for tc in response.message.tool_calls:
                    try:
                        futures[executor.submit(TOOLS[tc.function.name], **(tc.function.arguments or {}))] = tc
                        if tc.function.name=='claude':
                            claude_contains=True
                    except KeyError:
                        session_messages.append({'role': 'tool', 'tool_name': tc.function.name, 'content': f'Tool DNE'})

                for future in as_completed(futures):
                    tc = futures[future]
                    try:
                        result = future.result()
                        session_messages.append({'role': 'tool', 'tool_name': tc.function.name, 'content': str(result)})
                    except TimeoutError as e:
                        print("\n--- Initiating pool-wide shutdown! ---")
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise TimeoutError
                    except Exception as e:
                        session_messages.append({'role': 'tool', 'tool_name': tc.function.name, 'content': f'Error: {e}'})
            if claude_contains:
                load_tools()
        else:
            return response.message.content or FALLBACK_REPLY

