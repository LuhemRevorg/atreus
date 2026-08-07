from wake import wake 
from llama import llama
from stt import stt
from tts import tts

def main():
    wake()
    text = stt()
    print(text)
    res = llama(text)
    print(res)
    tts(res)


if __name__=="__main__":
    main()  