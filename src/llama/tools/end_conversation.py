
class EndConversation(TimeoutError):
    pass

def end_conversation():
    '''
    End the conversation and stop listening.

    Only use this when the user explicitly signals they are done, e.g. "goodbye",
    "that's all", "stop", "nevermind", "Thank You". Do NOT use it for greetings, small talk,
    or any message you can simply answer.
    '''
    print("Ending Conversation")
    raise EndConversation
