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

def text_agent():
    
    return


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("atreus listening")

    try:
        p1 = Process(target=voice_agent)
        p2 = Process(target=pop_up)
        p3 = Process(target=text_agent)
        p1.start()
        p2.start()
        p3.start()
        p1.join()
        p3.join()
        p2.kill()
        
    except Exception:
        log.exception("End convo")
        return


if __name__=="__main__":
    main()
