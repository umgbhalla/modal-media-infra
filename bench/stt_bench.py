"""
Modal STT benchmark — LATEST SOTA open models only (no whisper):
  - NVIDIA Parakeet TDT 0.6b v3   (throughput SOTA, latest)
  - NVIDIA Canary-1b-flash        (accuracy SOTA tier, fast)
Measures cold load, warm infer, RTF on a real sample.

Run:  modal run stt_bench.py --audio /path/to/sample.wav
"""
import time
import modal

app = modal.App("zod-stt-bench")

# NeMo on CUDA base; pin pytorch-lightning so NeMo 2.x's NeptuneLogger import resolves.
nemo_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install("nemo_toolkit[asr]", "matplotlib")  # latest: canary2 formatter + numpy2 stack
)

cache = modal.Volume.from_name("zod-stt-cache", create_if_missing=True)
CACHE = "/cache"


def _run(model_id, audio_bytes, multitask=False):
    import os
    os.environ["HF_HOME"] = CACHE
    os.environ["NEMO_CACHE_DIR"] = CACHE
    open("/tmp/a.wav", "wb").write(audio_bytes)
    # Shim: NeMo 2.0.0 imports NeptuneLogger, removed in pytorch-lightning 2.5+.
    import pytorch_lightning.loggers as _pll
    if not hasattr(_pll, "NeptuneLogger"):
        class _NL:  # minimal stand-in; we never use Neptune logging
            pass
        _pll.NeptuneLogger = _NL
    import nemo.collections.asr as nemo_asr
    t0 = time.time()
    if multitask:
        m = nemo_asr.models.EncDecMultiTaskModel.from_pretrained(model_id)
    else:
        m = nemo_asr.models.ASRModel.from_pretrained(model_id)
    load = time.time() - t0
    t1 = time.time()
    out = m.transcribe(["/tmp/a.wav"])
    infer = time.time() - t1
    o = out[0]
    text = o.text if hasattr(o, "text") else (o if isinstance(o, str) else str(o))
    cache.commit()
    return {
        "engine": model_id, "load_sec": round(load, 2), "infer_sec": round(infer, 2),
        "chars": len(text), "preview": text[:220],
    }


@app.function(image=nemo_image, gpu="L4", volumes={CACHE: cache}, timeout=1800)
def parakeet_l4(audio_bytes: bytes):
    r = _run("nvidia/parakeet-tdt-0.6b-v3", audio_bytes)
    r["gpu"] = "L4"
    return r


@app.function(image=nemo_image, gpu="L4", volumes={CACHE: cache}, timeout=1800)
def canary_l4(audio_bytes: bytes):
    r = _run("nvidia/canary-1b-flash", audio_bytes, multitask=True)
    r["gpu"] = "L4"
    return r


@app.local_entrypoint()
def main(audio: str):
    data = open(audio, "rb").read()
    print(f"audio bytes: {len(data)}  ({audio})")
    print("\n--- Parakeet TDT 0.6b v3 @ L4 ---")
    try:
        print(parakeet_l4.remote(data))
    except Exception as e:
        print(f"parakeet failed: {e}")
    print("\n--- Canary-1b-flash @ L4 ---")
    try:
        print(canary_l4.remote(data))
    except Exception as e:
        print(f"canary failed: {e}")
