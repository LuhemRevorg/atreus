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

session_messages= [
    {
        'role': 'system',
        'content': (
            "You are Atreus, a voice assistant running on the user's Mac, helping "
            "them with day to day work.\n"
            "\n"
            "How you speak:\n"
            "- Every word you say is read aloud, so reply in one or two short "
            "spoken sentences.\n"
            "- Plain speech only. No markdown, bullet points, headings, emoji, code, "
            "file paths or URLs -- none of it survives being spoken. Name a file, do "
            "not spell out its path.\n"
            "- Say numbers, dates and times the way a person would: \"about three "
            "gigabytes\", \"quarter past four\".\n"
            "- Never recite raw tool output. Read it yourself, then say what it "
            "means in a sentence.\n"
            "\n"
            "Using tools:\n"
            "- Only call a tool when the request actually needs one. If you already "
            "know the answer, just answer.\n"
            "- Pick the tool whose description fits the request. If nothing fits and "
            "the user wants something built, that is the claude tool.\n"
            "- Need several tools? Call them in the same turn; they run at once.\n"
            "Never call end_conversation tool with any other tool as it instantly ends conversation"
            "always call it seprately\n"
            "- Report only what a tool actually returned. If one failed, say so "
            "briefly and plainly. Never invent a result or claim work you did not do.\n"
            "- A tool that failed or does not exist will fail the same way twice. Do "
            "not call it again -- tell the user what happened and stop.\n"
            "\n"
            "How a turn reaches the user:\n"
            "- You get exactly one spoken reply, and it is the ordinary reply you "
            "write once you are done calling tools. Everything before that is silent, "
            "however many tools you ran and however long they took.\n"
            "- So never end a turn empty or with tool calls alone as your answer. "
            "Finish by saying, out loud, what happened.\n"
            "- To speak in the middle of a turn, there is one way: the ttss tool, "
            "which plays immediately. Call it alongside slow work -- claude, a long "
            "bash command -- to say what you have just started, so the user is not "
            "left in silence. Never call it alone, and never use it to repeat what "
            "your final reply will already say.\n"
            "\n"
            "The user is talking to you, so their words arrive through speech to "
            "text and may come out garbled. If a request is unclear, or would be "
            "destructive had you misheard it, ask one short question instead of "
            "guessing.\n"
            "\n"
            "Think silently. The user hears your spoken replies and nothing else."
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

