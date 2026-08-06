import os
import time
import subprocess
import tflite_compat
tflite_compat.install()
import openwakeword
from openwakeword.model import Model
import pyaudio
import numpy as np

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "uh_tray_us_.tflite")

def wake():
    # pyaudio setup
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1280)

    model = Model(wakeword_models=[MODEL_PATH], ncpu=1)
    last=time.time()
    try:
        while True:
            data=stream.read(1280, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.int16)
            
            prediction = model.predict(audio)
            for key in prediction:
                t=time.time()-curr
                if prediction[key] > 0.002 and t >= 1: # Threshold
                    curr=time.time()
                    print(f"atreus {prediction[key]}")
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

if __name__ == "__main__":
    wake()