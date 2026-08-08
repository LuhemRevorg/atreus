import logging
import signal
import sys
import time

from wake import wake
from llama import llama
from stt import stt
from tts import tts
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


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("atreus listening")
    while True:
        try:
            #wake()
            pop_up()
            text = stt()
            log.info("heard: %s", text)
            res = llama(text)
            log.info("said: %s", res)
            tts(res)
        except Exception:
            log.exception("cycle failed, retrying")
            time.sleep(2)


if __name__=="__main__":
    main()
