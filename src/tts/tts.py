import subprocess

class TTS:
    def res(self, txt):
        subprocess.run(["say", txt])