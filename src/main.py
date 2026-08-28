import itertools
import logging
import signal
import sys
from pathlib import Path
from multiprocessing import Process, Queue
from queue import Empty

from wake import wake
from llama import llama
from stt import STT
from tts import TTS
from pop_up import pop_up
from top_bar import top_bar
from handler import DELETE_CHAT
from handler import Handler
from handler import Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("atreus")
SYSTEM_PROMPT= (Path(__file__).parent / "SYSTEMPROMPT.txt").read_text(encoding="utf-8")

def shutdown(signum, frame):
    log.info("got signal %s, exiting", signum)
    sys.exit(0)

INITIAL_CHAT_ID = 0

def voice_agent(req_queue, res_queue):
    stt = STT()
    tts = TTS()  
    session_messages= [
        {
            'role': 'system',
            'content': SYSTEM_PROMPT,
        }
    ]
    wake()
    p2 = Process(target=pop_up)
    p2.start()
    while True:
        try:
            text = stt.req()
            log.info("heard: %s", text)
            res = llama(text, session_messages)
            log.info("said: %s", res)
            tts.res(res)
        except Exception as e:
            print(e)
            break
    p2.kill()

def text_agent(req_queue, res_queue):
    ids = itertools.count(INITIAL_CHAT_ID + 1)

    def respond(text, id):
        log.info("typed in chat %s: %s", id, text)
        req_queue.put(Request(type="text", message=text, id=id))

    def new_chat():
        id = next(ids)
        log.info("new chat %s", id)
        return id

    def poll():
        replies = []
        while True:
            try:
                res = res_queue.get_nowait()
            except Empty:
                break
            log.info("replied in chat %s: %s", res.id, res.res)
            replies.append((res.id, res.res))
        return replies

    def delete_chat(id):
        log.info("delete chat %s", id)
        req_queue.put(Request(type=DELETE_CHAT, message=None, id=id))

    top_bar(
        responder=respond,
        on_new_chat=new_chat,
        on_delete_chat=delete_chat,
        on_poll=poll,
    ).run()
x

def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("atreus listening")

    req_queue, res_text_queue, res_voice_queue = Queue(), Queue(), Queue()
    req_handler = Handler(req_queue, res_text_queue, res_voice_queue)

    try:
        p1 = Process(target=voice_agent)
        p3 = Process(target=text_agent, args=(req_queue, res_text_queue))
        p4 = Process(target=req_handler.handle_reqs)
        p1.start()
        p3.start()
        p4.start()
        p1.join()
        p3.join()
        p4.join()
        
    except Exception:
        log.exception("End convo")
        return


if __name__=="__main__":
    main()
