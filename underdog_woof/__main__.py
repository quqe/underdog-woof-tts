"""python -m underdog_woof "Text to speak." -o out.wav"""
import argparse
import time

from . import SR, VOICES, Woof


def main():
    ap = argparse.ArgumentParser(prog="underdog_woof", description="Speak English text in one of Woof's voices.")
    ap.add_argument("text")
    ap.add_argument("-o", "--out", default="woof.wav")
    ap.add_argument("--voice", default="juno", choices=VOICES)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--temp", type=float, default=0.5)
    a = ap.parse_args()
    tts = Woof(voice=a.voice, temp=a.temp)
    t = time.perf_counter()
    wav = tts(a.text, seed=a.seed)
    el = time.perf_counter() - t
    tts.save(a.out, wav)
    print(f"{a.out}: {len(wav) / SR:.2f} s of audio in {el:.2f} s ({len(wav) / SR / el:.1f}x real time)")


if __name__ == "__main__":
    main()
