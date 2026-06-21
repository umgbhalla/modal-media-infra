"""
Modal TTS benchmark — LATEST open models:
  - Chatterbox (Resemble, 500M, MIT)  -> quality SOTA tier
  - Kokoro-82M                        -> fast/cheap reference (what we run locally)
Measures gen time, RTF, returns wav bytes so we can listen.

Run:  modal run tts_bench.py
"""
import time
import modal

app = modal.App("zod-tts-bench")

TEXT = (
    "Recursive language models let a model call itself as a tool, decomposing a hard "
    "question into sub-questions it answers in fresh context windows. The headline result "
    "is depth without context rot: each recursive call starts clean, so reasoning that would "
    "overflow a single prompt stays tractable. For our stack, that maps directly onto a durable, "
    "forkable execution heap."
)

chatterbox_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1", "git")
    .pip_install("chatterbox-tts", "torchaudio")
)

kokoro_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("espeak-ng", "ffmpeg", "libsndfile1")
    .pip_install("kokoro>=0.9", "soundfile")
)

cache = modal.Volume.from_name("zod-tts-cache", create_if_missing=True)
CACHE = "/cache"


@app.function(image=chatterbox_image, gpu="L4", volumes={CACHE: cache}, timeout=1200)
def chatterbox():
    import os, io
    os.environ["HF_HOME"] = CACHE
    import torch, torchaudio as ta
    from chatterbox.tts import ChatterboxTTS
    t0 = time.time()
    m = ChatterboxTTS.from_pretrained(device="cuda")
    load = time.time() - t0
    t1 = time.time()
    wav = m.generate(TEXT)
    gen = time.time() - t1
    audio_sec = wav.shape[-1] / m.sr
    buf = io.BytesIO()
    ta.save(buf, wav.cpu(), m.sr, format="wav")
    cache.commit()
    return {"engine": "chatterbox", "gpu": "L4", "load_sec": round(load, 2),
            "gen_sec": round(gen, 2), "audio_sec": round(audio_sec, 2),
            "rtf": round(gen / audio_sec, 3), "wav": buf.getvalue()}


@app.function(image=kokoro_image, gpu="T4", volumes={CACHE: cache}, timeout=1200)
def kokoro():
    import os, io
    os.environ["HF_HOME"] = CACHE
    import numpy as np, soundfile as sf
    from kokoro import KPipeline
    t0 = time.time()
    pipe = KPipeline(lang_code="a")
    load = time.time() - t0
    t1 = time.time()
    audio = np.concatenate([a for _, _, a in pipe(TEXT, voice="af_heart")])
    gen = time.time() - t1
    audio_sec = len(audio) / 24000
    buf = io.BytesIO()
    sf.write(buf, audio, 24000, format="WAV")
    cache.commit()
    return {"engine": "kokoro-82m", "gpu": "T4", "load_sec": round(load, 2),
            "gen_sec": round(gen, 2), "audio_sec": round(audio_sec, 2),
            "rtf": round(gen / audio_sec, 3), "wav": buf.getvalue()}


@app.local_entrypoint()
def main():
    for fn, name in [(chatterbox, "chatterbox"), (kokoro, "kokoro")]:
        print(f"\n--- {name} ---")
        try:
            r = fn.remote()
            wav = r.pop("wav")
            open(f"tts_{name}.wav", "wb").write(wav)
            print(r, f"-> tts_{name}.wav ({len(wav)} bytes)")
        except Exception as e:
            print(f"{name} failed: {e}")
