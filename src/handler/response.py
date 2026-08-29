class Response:

    def __init__(self, res, id, end=False, interim=False):
        self.res = res
        self.id = id
        # end_conversation was called: stop listening once this has been said.
        self.end = end
        # Something to say mid-turn (the ttss tool), not the turn's answer. More
        # is still coming on this queue, so the caller must not stop reading.
        self.interim = interim
