import os
from dotenv import load_dotenv
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage
import asyncio



load_dotenv()

async def _build(request: str, options: ClaudeAgentOptions) -> str:
    result = ""
    async for message in query(prompt=request, options=options):
        if isinstance(message, ResultMessage):
            result = message.result or ""
    return result


def claude(request: str, cwd: str | None):
    '''
    Ask Claude, a coding agent, to build something.

    Use this when the user asks you to build, write or make code: a script, a
    whole project, or a new tool for yourself when you do not have one that fits,
    e.g. they ask you to check the weather and there is no weather tool. Building
    takes a while and runs in the background, so tell the user it has started. Do
    NOT use it to answer questions or chat, and do NOT use it to redo work an
    existing tool already does.

    Args:
        request: A description of what Claude should build: what it should do,
            what inputs it takes and what it should produce.
        cwd: Path of the directroy to build in leave empty if asked to build a tool for you
    '''

    options = ClaudeAgentOptions(
        system_prompt="You are an expert Python developer",
        model="opus",
        permission_mode="auto",
        cwd=cwd,
    )
    return asyncio.run(_build(request, options))


