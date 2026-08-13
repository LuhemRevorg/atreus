import logging
import signal
import sys
import time
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


def shutdown(signum, frame):
    log.info("got signal %s, exiting", signum)
    sys.exit(0)

def agent():
    stt = STT()
    tts = TTS()
    while True:
        try:
            text = stt.req()
            text = "call tool claude to add hello to README.md and when done confirm then end conversation"
            log.info("heard: %s", text)
            res = llama(text)
            log.info("said: %s", res)
            stt.mute()
            tts.res(res)
            stt.unmute()
        except KeyboardInterrupt:
            return
        except TimeoutError:
            return
        

def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("atreus listening")
    while True:
        try:
            wake()
            p1 = Process(target=agent)
            p2 = Process(target=pop_up)
            p1.start()
            p2.start()
            
            p1.join()
            p2.kill()
            break
        
        except Exception:
            log.exception("cycle failed, retrying")
            time.sleep(2)


if __name__=="__main__":
    main()
