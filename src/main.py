import logging
import signal
import sys
from pathlib import Path
from multiprocessing import Process

from wake import wake
from llama import llama
from stt import STT
from tts import TTS
from pop_up import pop_up
from top_bar import top_bar

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

def voice_agent():
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

def text_agent():
    state = {'messages': [{'role': 'system', 'content': SYSTEM_PROMPT}]}

    def respond(text):
        log.info("typed: %s", text)
        res = llama(text, state['messages'])
        log.info("replied: %s", res)
        return res

    def new_chat():
        log.info("new chat")
        state['messages'] = [{'role': 'system', 'content': SYSTEM_PROMPT}]

    top_bar(responder=respond, on_new_chat=new_chat).run()


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("atreus listening")

    try:
        p1 = Process(target=voice_agent)
        p3 = Process(target=text_agent)
        p1.start()
        p3.start()
        p1.join()
        p3.join()
        
    except Exception:
        log.exception("End convo")
        return


if __name__=="__main__":
    main()
