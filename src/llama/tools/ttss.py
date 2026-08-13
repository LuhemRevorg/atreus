from tts import TTS

def ttss(text: str):
    '''
    Say one sentence out loud to the user right now, before the rest of your turn.

    Everything else you say is only spoken once every tool in this turn has
    finished, so a slow tool leaves the user sitting in silence wondering whether
    you heard them. Use this to fill that gap: call it in the same turn as a
    long-running tool -- claude, or a bash command that will take a while -- and
    say what you have just started. This is the one place you may speak about
    something that is not done yet; everywhere else, speak about what you did.

    Do NOT use it for your normal reply, which is already read aloud -- calling it
    there makes the user hear the same thing twice. Do NOT use it with quick tools
    like spotify, where the real answer arrives just as fast. Never call it on its
    own, with no slow work running behind it.

    Args:
        text: The sentence to speak, in the same short conversational voice as
            your replies, e.g. "Okay, Claude is building that now -- I'll tell you
            when it's done." It is read aloud verbatim, so no markdown, lists or
            code.
    '''
    tts = TTS()
    tts.res(text)
