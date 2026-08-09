import os
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs.play import play

load_dotenv()


def tts(txt):
    client = ElevenLabs(api_key=os.getenv("ELEVEN_LABS_KEY"))
    res = client.text_to_speech.convert(
        voice_id="SOYHLrjzK2X1ezoPC6cr",
        output_format="mp3_44100_128",
        text=txt,
        model_id="eleven_multilingual_v2",
    )

    play(res)
