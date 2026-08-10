import os
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs.play import play

load_dotenv()

class TTS:
    def __init__(self):
        self.client = ElevenLabs(api_key=os.getenv("ELEVEN_LABS_KEY"))
    def res(self, txt):
        res = self.client.text_to_speech.convert(
            voice_id="SOYHLrjzK2X1ezoPC6cr",
            output_format="mp3_44100_128",
            text=txt,
            model_id="eleven_multilingual_v2",
        )
        play(res)
