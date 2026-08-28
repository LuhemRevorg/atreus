from pathlib import Path

from llama import llama

from .request import DELETE_CHAT, Request
from .response import Response

SYSTEM_PROMPT = (Path(__file__).parent / ".." / "SYSTEMPROMPT.txt").read_text(encoding="utf-8")


class Handler:

    def __init__(self, req_queue, res_text_queue, res_voice_queue):
        self.req_queue = req_queue
        self.res_text_queue = res_text_queue
        self.res_voice_queue = res_voice_queue
        self.chats = {}

    def submit_req(self, req):
        self.req_queue.put(req)

    def handle_reqs(self):
        while True:
            req = self.req_queue.get()
            if req.type == DELETE_CHAT:
                self.delete_chat(req.id)
                continue
            chat = self.chats.setdefault(
                req.id, [{"role": "system", "content": SYSTEM_PROMPT}]
            )
            end = False
            try:
                res = llama(req.message, chat)
            except TimeoutError:
                # end_conversation raises out through llama's tool pool as a
                # bare TimeoutError. Nothing to say -- the session just stops.
                res, end = None, True
            except Exception as e:
                res = f"[error] {e}"
            self.reply(req, Response(res=res, id=req.id, end=end))

    def reply(self, req, res):
        if req.reply_to is not None:
            req.reply_to.put(res)
        elif req.type == "text":
            self.res_text_queue.put(res)
        else:
            self.res_voice_queue.put(res)

    def delete_chat(self, id):
        self.chats.pop(id, None)
