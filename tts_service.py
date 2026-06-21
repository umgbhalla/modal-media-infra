"""
TTS — deployable Modal service with BOTH options behind one URL.

  - kokoro     : Kokoro-82m on T4   — fast/cheap default (~$0.06/audio-hr)
  - chatterbox : Chatterbox on L4   — ~ElevenLabs naturalness (~$1.05/audio-hr)

A light CPU router (one URL) auth-checks and dispatches to the requested GPU
backend, returning wav bytes. Pick per call with ?model= / -F model=.

Deploy:  modal deploy tts_service.py
Call:    curl -H "Authorization: Bearer $TTS_MODAL_TOKEN" \
              -F text="hello world" -F model=kokoro \
              https://<workspace>--zod-tts-web.modal.run/tts -o out.wav
"""
import io
import time
import modal

app = modal.App("zod-tts")

kokoro_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("espeak-ng", "ffmpeg", "libsndfile1")
    .pip_install("kokoro>=0.9", "soundfile")
)
chatterbox_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1", "git")
    .pip_install("chatterbox-tts", "torchaudio")
)
router_image = modal.Image.debian_slim(python_version="3.11").pip_install("fastapi[standard]")

cache = modal.Volume.from_name("zod-tts-cache", create_if_missing=True)
CACHE = "/cache"
SECRET = modal.Secret.from_name("zod-tts-token")


@app.cls(image=kokoro_image, gpu="T4", volumes={CACHE: cache},
         scaledown_window=240, timeout=900)
class Kokoro:
    @modal.enter()
    def load(self):
        import os
        os.environ["HF_HOME"] = CACHE
        from kokoro import KPipeline
        self.KPipeline = KPipeline
        self.pipe = KPipeline(lang_code="a")

    @modal.method()
    def generate(self, text: str, voice: str = "af_heart"):
        import numpy as np, soundfile as sf
        t0 = time.time()
        audio = np.concatenate([a for _, _, a in self.pipe(text, voice=voice or "af_heart")])
        gen = time.time() - t0
        buf = io.BytesIO(); sf.write(buf, audio, 24000, format="WAV")
        return buf.getvalue(), round(gen, 2), round(len(audio) / 24000, 2)


@app.cls(image=chatterbox_image, gpu="L4", volumes={CACHE: cache},
         scaledown_window=240, timeout=900)
class Chatterbox:
    @modal.enter()
    def load(self):
        import os
        os.environ["HF_HOME"] = CACHE
        from chatterbox.tts import ChatterboxTTS
        self.model = ChatterboxTTS.from_pretrained(device="cuda")

    @modal.method()
    def generate(self, text: str, voice: str = ""):
        import torchaudio as ta
        t0 = time.time()
        wav = self.model.generate(text)
        gen = time.time() - t0
        buf = io.BytesIO(); ta.save(buf, wav.cpu(), self.model.sr, format="wav")
        return buf.getvalue(), round(gen, 2), round(wav.shape[-1] / self.model.sr, 2)


@app.function(image=router_image, secrets=[SECRET], scaledown_window=300)
@modal.asgi_app()
def web():
    import os
    from fastapi import FastAPI, Form, Header, HTTPException
    from fastapi.responses import Response
    api = FastAPI(title="zod-tts")

    @api.post("/tts")
    async def tts(text: str = Form(...), model: str = Form("kokoro"),
                  voice: str = Form(""), authorization: str = Header(None)):
        if authorization != f"Bearer {os.environ['TTS_TOKEN']}":
            raise HTTPException(status_code=401, detail="bad token")
        if not text.strip():
            raise HTTPException(status_code=400, detail="empty text")
        m = (model or "kokoro").lower()
        if m == "chatterbox":
            wav, gen, dur = Chatterbox().generate.remote(text, voice)
        elif m == "kokoro":
            wav, gen, dur = Kokoro().generate.remote(text, voice)
        else:
            raise HTTPException(status_code=400, detail="model must be kokoro|chatterbox")
        return Response(content=wav, media_type="audio/wav",
                        headers={"X-Model": m, "X-Gen-Sec": str(gen), "X-Audio-Sec": str(dur)})

    @api.get("/health")
    async def health():
        return {"ok": True, "models": ["kokoro", "chatterbox"]}

    return api
