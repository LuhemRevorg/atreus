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
            log.info("heard: %s", text)
            res = llama(text)
            log.info("said: %s", res)
            stt.mute()
            tts.res(res)
            stt.unmute()
            print("deeedede")
        except KeyboardInterrupt:
            return
        except TimeoutError:
            break
    stt.shutdown()
        

def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("atreus listening")

    try:
        #wake()
        p1 = Process(target=agent)
        p2 = Process(target=pop_up)
        p1.start()
        p2.start()
        p1.join()
        p2.kill()
        
    except Exception:
        log.exception("End convo")
        return


if __name__=="__main__":
    main()
