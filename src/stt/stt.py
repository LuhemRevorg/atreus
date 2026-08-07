from RealtimeSTT import AudioToTextRecorder

def stt() -> str:
    with AudioToTextRecorder() as recorder:
        print("Speak now")
        text = recorder.text()

    return text
