import time
from contextlib import contextmanager
from multiprocessing import Lock, Value


class Mic:
    def __init__(self):
        self._lock = Lock()
        self._wanted = Value("i", 0)

    @property
    def wanted(self):
        return self._wanted.value > 0

    @contextmanager
    def hold(self):
        """Own the mic, but yield it to anyone who asks."""
        with self._lock:
            yield

    @contextmanager
    def take(self):
        """Claim the mic, asking whoever holds it to step aside."""
        with self._wanted.get_lock():
            self._wanted.value += 1
        try:
            with self._lock:
                yield
        finally:
            with self._wanted.get_lock():
                self._wanted.value -= 1

    def wait_until_free(self, poll=0.05):
        while self.wanted:
            time.sleep(poll)
