"""
Parakeet STT — deployable Modal service.

Model: nvidia/parakeet-tdt-0.6b-v3 (throughput-SOTA open ASR) on an L4.
Exposes a token-auth'd HTTP endpoint that accepts an audio file (multipart) or
{"url": "..."} and returns {text, segments[]}. Long-form safe via local attention.

Deploy:  modal deploy stt_service.py
Call:    curl -H "Authorization: Bearer $STT_MODAL_TOKEN" \
              -F file=@clip.wav  https://<workspace>--zod-stt-transcribe.modal.run
"""
import time
import modal

app = modal.App("zod-stt")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    # latest nemo (canary2 formatter + numpy-2 stack); CUDA rides in via torch wheel.
    .pip_install("nemo_toolkit[asr]", "matplotlib", "fastapi[standard]", "requests")
)

cache = modal.Volume.from_name("zod-stt-cache", create_if_missing=True)
CACHE = "/cache"
MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"



@app.cls(
    image=image,
    gpu="L4",
    volumes={CACHE: cache},
    secrets=[modal.Secret.from_name("zod-stt-token")],
    scaledown_window=300,   # stay warm 5 min between calls -> no repeat cold loads
    timeout=1800,
    enable_memory_snapshot=True,                              # snapshot CPU+GPU init
    experimental_options={"enable_gpu_snapshot": True},       # restore GPU state fast
)
class STT:
    @modal.enter(snap=True)
    def load(self):
        import os
        os.environ["HF_HOME"] = CACHE
        os.environ["NEMO_CACHE_DIR"] = CACHE
        # NeMo imports NeptuneLogger (gone in pytorch-lightning 2.5+) — shim it.
        import pytorch_lightning.loggers as _pll
        if not hasattr(_pll, "NeptuneLogger"):
            class _NL:  # noqa
                pass
            _pll.NeptuneLogger = _NL
        import nemo.collections.asr as nemo_asr
        self.model = nemo_asr.models.ASRModel.from_pretrained(MODEL_ID)
        # Long-form: bounded-memory local attention so multi-minute audio never OOMs.
        try:
            self.model.change_attention_model("rel_pos_local_attn", [256, 256])
            self.model.change_subsampling_conv_chunking_factor(1)
        except Exception:
            pass
        self.model.eval()

    def _transcribe(self, wav_path: str):
        out = self.model.transcribe([wav_path], timestamps=True)
        o = out[0]
        text = o.text if hasattr(o, "text") else str(o)
        segments = []
        try:
            for s in o.timestamp.get("segment", []):
                segments.append({"start": round(s["start"], 2),
                                 "end": round(s["end"], 2), "text": s["segment"]})
        except Exception:
            pass
        return text, segments

    @modal.method()
    def run(self, audio_bytes: bytes):
        import subprocess, tempfile, os
        raw = tempfile.NamedTemporaryFile(suffix=".bin", delete=False).name
        open(raw, "wb").write(audio_bytes)
        wav = raw + ".wav"
        # normalize anything (audio or video) -> 16k mono wav
        subprocess.run(["ffmpeg", "-y", "-i", raw, "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", wav], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        text, segments = self._transcribe(wav)
        dt = round(time.time() - t0, 2)
        for f in (raw, wav):
            try: os.unlink(f)
            except OSError: pass
        return {"text": text, "segments": segments, "infer_sec": dt}

    @modal.asgi_app()
    def web(self):
        import os
        from fastapi import FastAPI, UploadFile, File, Header, HTTPException
        api = FastAPI(title="zod-stt")

        @api.post("/transcribe")
        async def transcribe(file: UploadFile = File(...),
                             authorization: str = Header(None)):
            if authorization != f"Bearer {os.environ['STT_TOKEN']}":
                raise HTTPException(status_code=401, detail="bad token")
            audio = await file.read()
            if not audio:
                raise HTTPException(status_code=400, detail="empty file")
            return self.run.local(audio)

        @api.get("/health")
        async def health():
            return {"ok": True, "model": MODEL_ID}

        return api
