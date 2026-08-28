# A Request that drops a conversation instead of answering one; `message` is unused.
DELETE_CHAT = "delete_chat"


class Request:

    def __init__(self, type, message, id, reply_to=None):
        self.type = type
        self.message = message
        self.id = id
        self.reply_to = reply_to
