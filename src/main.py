from wake import wake 
from llama import llama
from stt import stt

def main():
    wake()
    text = stt()
    print(text)
    res = llama(text)
    print(res)


if __name__=="__main__":
    main()  