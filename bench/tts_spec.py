"""Chatterbox GPU spec sweep — does higher spec kill the slower-than-realtime penalty?"""
import time
import modal

app = modal.App("zod-tts-spec")

TEXT = (
    "Recursive language models let a model call itself as a tool, decomposing a hard "
    "question into sub-questions it answers in fresh context windows. The headline result "
    "is depth without context rot: each recursive call starts clean, so reasoning that would "
    "overflow a single prompt stays tractable. For our stack, that maps directly onto a durable, "
    "forkable execution heap."
)

img = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1", "git")
    .pip_install("chatterbox-tts", "torchaudio")
)
cache = modal.Volume.from_name("zod-tts-cache", create_if_missing=True)
CACHE = "/cache"


def _gen():
    import os, time
    os.environ["HF_HOME"] = CACHE
    import torch
    from chatterbox.tts import ChatterboxTTS
    t0 = time.time(); m = ChatterboxTTS.from_pretrained(device="cuda"); load = time.time() - t0
    # warm + timed (avg of 2 timed runs)
    m.generate(TEXT)
    runs = []
    for _ in range(2):
        t1 = time.time(); wav = m.generate(TEXT); runs.append(time.time() - t1)
    gen = sum(runs) / len(runs)
    audio_sec = wav.shape[-1] / m.sr
    return load, gen, audio_sec


@app.function(image=img, gpu="A100-40GB", volumes={CACHE: cache}, timeout=1200)
def a100():
    load, gen, a = _gen()
    return {"gpu": "A100-40GB", "rate_hr": 2.10, "load_sec": round(load, 2),
            "gen_sec": round(gen, 2), "audio_sec": round(a, 2), "rtf": round(gen / a, 3)}


@app.function(image=img, gpu="H100", volumes={CACHE: cache}, timeout=1200)
def h100():
    load, gen, a = _gen()
    return {"gpu": "H100", "rate_hr": 3.95, "load_sec": round(load, 2),
            "gen_sec": round(gen, 2), "audio_sec": round(a, 2), "rtf": round(gen / a, 3)}


@app.local_entrypoint()
def main():
    for fn, n in [(a100, "A100-40GB"), (h100, "H100")]:
        try:
            r = fn.remote()
            r["cost_per_audio_hr"] = round(r["rtf"] * r["rate_hr"], 3)
            print(r)
        except Exception as e:
            print(f"{n} failed: {e}")
