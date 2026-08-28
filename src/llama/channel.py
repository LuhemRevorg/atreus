"""Where a tool can reach the caller in the middle of a turn.

Tools are called by name with nothing but the model's own arguments, so there is
no way to hand one the request it belongs to. The handler answers a single
request at a time in a single process, so the way back to that caller can just
be parked here for the length of the turn.

This lives outside tools/ deliberately: load_tools() reloads everything in there
whenever the claude tool writes a new one, which would drop whatever is parked.
"""

_speak = None


def bind(speak):
    """Park the current turn's way of reaching the caller. None between turns."""
    global _speak
    _speak = speak


def say(text):
    """Send something to be spoken now, ahead of the turn's final reply.

    False if nothing is bound -- the turn has no caller waiting on it.
    """
    if _speak is None:
        return False
    _speak(text)
    return True
