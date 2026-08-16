import collections
import wave

import numpy as np
import pyaudio
import whispermlx as wmlx

CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000                # whisper resamples to 16k anyway, so record there
CHUNK_SECONDS = CHUNK / RATE

SILENCE_SECONDS = 1.5       # how much trailing silence ends the recording
START_TIMEOUT = 8.0         # give up if nobody ever starts talking
MAX_SECONDS = 30.0          # hard cap on a single utterance
CALIBRATE_SECONDS = 0.4     # ambient noise sample taken before listening
PREROLL_SECONDS = 0.3       # audio kept from just before speech was detected
NOISE_MULTIPLIER = 3.0      # speech must be this much louder than ambient
MIN_THRESHOLD = 300.0       # floor for a dead-quiet room (int16 RMS)


class STT:
    def __init__(self):
        self.recorder = wmlx.load_model("mlx-community/whisper-small-mlx", device="cpu")

    def __rms(self, data):
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples * samples)))

    def __record(self, outputFile):
        p = pyaudio.PyAudio()
        sample_width = p.get_sample_size(FORMAT)
        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)

        try:
            # Measure the room so the threshold adapts to the current mic/environment.
            ambient = [self.__rms(stream.read(CHUNK, exception_on_overflow=False))
                       for _ in range(max(1, int(CALIBRATE_SECONDS / CHUNK_SECONDS)))]
            threshold = max(MIN_THRESHOLD, NOISE_MULTIPLIER * (sum(ambient) / len(ambient)))

            silence_chunks = int(SILENCE_SECONDS / CHUNK_SECONDS)
            start_chunks = int(START_TIMEOUT / CHUNK_SECONDS)
            max_chunks = int(MAX_SECONDS / CHUNK_SECONDS)
            preroll = collections.deque(maxlen=max(1, int(PREROLL_SECONDS / CHUNK_SECONDS)))

            frames = []
            started = False
            silent_run = 0
            waited = 0

            while len(frames) < max_chunks:
                data = stream.read(CHUNK, exception_on_overflow=False)
                loud = self.__rms(data) >= threshold

                if not started:
                    preroll.append(data)
                    if loud:
                        started = True
                        frames.extend(preroll)
                        continue
                    waited += 1
                    if waited >= start_chunks:
                        break
                    continue

                frames.append(data)
                silent_run = 0 if loud else silent_run + 1
                if silent_run >= silence_chunks:
                    break
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

        with wave.open(outputFile, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(sample_width)
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))

        return started

    def req(self):
        print("Speak now")
        if not self.__record('what_i_said.wav'):
            return {"text": ""}
        result = self.recorder.transcribe('what_i_said.wav', language='en')
        print(result)
        return result['segments'][0]['text']

    
    def shutdown(self):
        self.recorder.shutdown()
