from RealtimeSTT import AudioToTextRecorder

class STT:
    def __init__(self):
        self.recorder = AudioToTextRecorder()
    
    def req(self):
        print("Speak now")
        text=self.recorder.text()
        return text
    def mute(self):
        self.recorder.set_microphone(False)

    def unmute(self):
        self.recorder.clear_audio_queue()
        self.recorder.set_microphone(True)
    
    def shutdown(self):
        self.recorder.shutdown()


