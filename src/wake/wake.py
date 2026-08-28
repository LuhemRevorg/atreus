import os
import time
from contextlib import nullcontext
from . import tflite_compat
tflite_compat.install()
from openwakeword.model import Model
import pyaudio
import numpy as np

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "models", "uh_tray_us_.tflite")

def wake(mic=None):
    model = Model(wakeword_models=[MODEL_PATH], ncpu=1)
    curr=time.time()
    while True:
        with (mic.hold() if mic is not None else nullcontext()):
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1280)
            try:
                while mic is None or not mic.wanted:
                    data=stream.read(1280, exception_on_overflow=False)
                    audio = np.frombuffer(data, dtype=np.int16)

                    prediction = model.predict(audio)
                    for key in prediction:
                        t=time.time()-curr
                        if prediction[key] > 0.002 and t >= 1: # Threshold
                            curr=time.time()
                            return
            finally:
                stream.stop_stream()
                stream.close()
                p.terminate()

        mic.wait_until_free()
        model.reset()
