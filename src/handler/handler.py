from pathlib import Path

from llama import channel, llama

from .request import DELETE_CHAT, Request
from .response import Response

PROMPTS = {
    "text": (Path(__file__).parent / ".." / "TEXTPROMPT.txt").read_text(encoding="utf-8"),
    "voice": (Path(__file__).parent / ".." / "VOICEPROMPT.txt").read_text(encoding="utf-8"),
}


def ended(messages):
    return any(
        isinstance(m, dict) and m.get("tool_name") == "end_conversation"
        for m in messages
    )


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
                req.id, [{"role": "system", "content": PROMPTS[req.type]}]
            )
            turn = len(chat)
            # How ttss reaches this request while the turn is still running. It
            # goes back the same way the answer does, so the caller says one
            # thing at a time instead of talking over itself.
            channel.bind(
                lambda text: self.reply(
                    req, Response(res=text, id=req.id, interim=True)
                )
            )
            try:
                res = llama(req.message, chat)
            except Exception as e:
                res = f"[error] {e}"
            finally:
                channel.bind(None)
            self.reply(req, Response(res=res, id=req.id, end=ended(chat[turn:])))

    def reply(self, req, res):
        if req.reply_to is not None:
            req.reply_to.put(res)
        elif req.type == "text":
            self.res_text_queue.put(res)
        else:
            self.res_voice_queue.put(res)

    def delete_chat(self, id):
        self.chats.pop(id, None)
