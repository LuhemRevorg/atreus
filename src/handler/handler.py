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
            try:
                res = llama(req.message, chat)
            except Exception as e:
                res = f"[error] {e}"
            queue = self.res_text_queue if req.type == "text" else self.res_voice_queue
            queue.put(Response(res=res, id=req.id))

    def delete_chat(self, id):
        self.chats.pop(id, None)
