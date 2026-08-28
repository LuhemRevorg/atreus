from collections import deque
from llama import llama
from request import Request
from response import Response

class Handler:
    def __init__(self):
        self.req_queue = deque()
        self.res_text_queue = deque()
        self.res_voice_queue = deque()

    def submit_req(self, req):
        self.req_queue.append(req)

    def handle_reqs(self):
        while True:
            if len(self.req_queue) != 0:
                req = self.req_queue.popleft()
                res = llama(req.message, req.chat)
                if req.type == "text":
                    self.res_text_queue.append(Response(res=res, id=req.id))
                else:
                    self.res_voice_queue.append(Response(res=res, id=req.id))

