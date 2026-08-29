import itertools
import logging
import signal
import sys
from multiprocessing import Lock, Manager, Process, Queue
from queue import Empty

from wake import wake
from mic import Mic
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

def shutdown(signum, frame):
    log.info("got signal %s, exiting", signum)
    sys.exit(0)

INITIAL_CHAT_ID = 0

def voice_agent(req_queue, mic, speaker):
    stt = STT()
    ids = itertools.count()
    manager = Manager()
    sessions = {}

    while True:
        wake(mic)
        with mic.take():
            text = stt.req()
        if not text:
            continue

        chat_id = f"voice-{next(ids)}"
        log.info("heard in %s: %s", chat_id, text)
        replies = manager.Queue()
        session = Process(
            target=voice_session,
            args=(text, chat_id, replies, req_queue, mic, speaker),
        )
        session.start()
        sessions[session] = replies

        for done in [s for s in sessions if not s.is_alive()]:
            done.join()
            del sessions[done]


def voice_session(text, chat_id, replies, req_queue, mic, speaker):
    """Answer one spoken request, then keep the conversation going.

    Runs until end_conversation fires or the user stops replying. Each follow-up
    takes the mic back off the wake listener for as long as it takes to record.
    """
    tts = TTS()
    stt = None
    sprite = Process(target=pop_up)
    sprite.start()
    try:
        while text:
            req_queue.put(
                Request(type="voice", message=text, id=chat_id, reply_to=replies)
            )
            if stt is None:
                # Loaded here rather than up front: the handler is already
                # working on the request, so the wait is free.
                stt = STT()
            # ttss arrives on the same queue ahead of the answer, so keep
            # reading until the turn's actual reply shows up.
            while True:
                res = replies.get()
                if not res.interim:
                    break
                log.info("meanwhile in %s: %s", chat_id, res.res)
                with speaker:
                    tts.res(res.res)

            log.info("said in %s: %s", chat_id, res.res)
            if res.res:
                with speaker:
                    tts.res(res.res)
            if res.end:
                # end_conversation, spoken first and stopped after.
                log.info("%s ended", chat_id)
                break
            with mic.take():
                text = stt.req()
            log.info("heard in %s: %s", chat_id, text)
    except Exception:
        log.exception("voice session %s", chat_id)
    finally:
        sprite.kill()
        req_queue.put(Request(type=DELETE_CHAT, message=None, id=chat_id))


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
            if res.interim:
                # ttss is a voice affordance; the panel has no way to show a
                # half-turn without clearing the chat's pending state.
                log.info("ignoring interim in chat %s: %s", res.id, res.res)
                continue
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


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("atreus listening")

    req_queue, res_text_queue, res_voice_queue = Queue(), Queue(), Queue()
    req_handler = Handler(req_queue, res_text_queue, res_voice_queue)
    mic = Mic()
    # One `say` at a time, so two sessions answering at once don't talk over
    # each other.
    speaker = Lock()

    try:
        p1 = Process(target=voice_agent, args=(req_queue, mic, speaker))
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
