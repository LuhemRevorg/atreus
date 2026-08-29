
def end_conversation():
    '''
    Finish the conversation once your reply has been given: Atreus stops
    listening, and the user has to say the wake word again to come back.

    Call it in the same turn as the reply that completes the request. Your reply
    is still spoken -- listening stops after it, not instead of it. For a spoken
    request this is how a turn normally ends, and it is your call to make.

    Do NOT call it in a turn where you are asking the user a question, or where
    you still owe them an answer. Do NOT call it in the typed chat, which the
    user closes themselves.
    '''
    print("Ending Conversation")
    return "The conversation will end once you have given your reply."
