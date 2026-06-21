"""
Omni media-understanding — deployable Modal service.

Model: Qwen/Qwen2.5-Omni-7B on an L40S. One model that transcribes audio AND
reads video frames AND reasons jointly. Token-auth'd HTTP endpoint accepts an
audio OR video file (multipart) plus an optional prompt, and returns the model's
text understanding (default = summary + USEFUL_SIGNAL gate + topic tags).

Deploy:  modal deploy omni_service.py
Call:    curl -H "Authorization: Bearer $OMNI_MODAL_TOKEN" \
              -F file=@clip.mp4 -F prompt="what happens in this video?" \
              https://<workspace>--zod-omni-omni-web.modal.run/understand
"""
import time
import modal

app = modal.App("zod-omni")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "torch", "torchvision", "torchaudio",
        "transformers", "accelerate", "qwen-omni-utils", "av", "decord",
        "soundfile", "librosa", "fastapi[standard]", "requests",
    )
)

cache = modal.Volume.from_name("zod-omni-cache", create_if_missing=True)
CACHE = "/cache"
MODEL_ID = "Qwen/Qwen2.5-Omni-7B"

DEFAULT_PROMPT = (
    "You are a media-understanding gate. Watch/listen to the media, then output:\n"
    "1) a one-sentence summary,\n"
    "2) USEFUL_SIGNAL: yes/no (is there substantive/technical content worth saving?),\n"
    "3) three topic tags."
)


@app.cls(
    image=image,
    gpu="L40S",
    volumes={CACHE: cache},
    secrets=[modal.Secret.from_name("zod-omni-token")],
    scaledown_window=240,
    timeout=2400,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
class Omni:
    @modal.enter(snap=True)
    def load(self):
        import os
        os.environ["HF_HOME"] = CACHE
        # torchvision 0.27 dropped io.read_video -> force the decord video backend.
        os.environ["FORCE_QWENVL_VIDEO_READER"] = "decord"
        import torch
        from transformers import Qwen2_5OmniForConditionalGeneration, Qwen2_5OmniProcessor
        self.torch = torch
        self.model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16, device_map="auto", cache_dir=CACHE
        )
        self.proc = Qwen2_5OmniProcessor.from_pretrained(MODEL_ID, cache_dir=CACHE)

    def _is_video(self, path: str) -> bool:
        import subprocess
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=30)
            return "video" in out.stdout
        except Exception:
            return False

    @modal.method()
    def run(self, media_bytes: bytes, filename: str, prompt: str):
        import tempfile, os
        from qwen_omni_utils import process_mm_info
        suffix = os.path.splitext(filename or "")[1] or ".bin"
        path = tempfile.NamedTemporaryFile(suffix=suffix, delete=False).name
        open(path, "wb").write(media_bytes)
        kind = "video" if self._is_video(path) else "audio"
        conv = [
            {"role": "system", "content": [{"type": "text",
             "text": "You are Qwen, a multimodal assistant."}]},
            {"role": "user", "content": [
                {"type": kind, kind: path},
                {"type": "text", "text": prompt or DEFAULT_PROMPT},
            ]},
        ]
        text = self.proc.apply_chat_template(conv, add_generation_prompt=True, tokenize=False)
        audios, images, videos = process_mm_info(conv, use_audio_in_video=True)
        inputs = self.proc(text=text, audio=audios, images=images, videos=videos,
                           return_tensors="pt", padding=True, use_audio_in_video=True)
        inputs = inputs.to(self.model.device).to(self.model.dtype)
        t0 = time.time()
        out = self.model.generate(**inputs, max_new_tokens=384,
                                  return_audio=False, use_audio_in_video=True)
        gen = round(time.time() - t0, 2)
        decoded = self.proc.batch_decode(out, skip_special_tokens=True,
                                         clean_up_tokenization_spaces=False)[0]
        # keep only the assistant turn
        answer = decoded.split("assistant\n")[-1].strip()
        vram = round(self.torch.cuda.max_memory_allocated() / 1e9, 1)
        try: os.unlink(path)
        except OSError: pass
        return {"output": answer, "modality": kind, "gen_sec": gen, "vram_gb": vram}

    @modal.asgi_app()
    def web(self):
        import os
        from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException
        api = FastAPI(title="zod-omni")

        @api.post("/understand")
        async def understand(file: UploadFile = File(...),
                             prompt: str = Form(""),
                             authorization: str = Header(None)):
            if authorization != f"Bearer {os.environ['OMNI_TOKEN']}":
                raise HTTPException(status_code=401, detail="bad token")
            data = await file.read()
            if not data:
                raise HTTPException(status_code=400, detail="empty file")
            return self.run.local(data, file.filename or "", prompt)

        @api.get("/health")
        async def health():
            return {"ok": True, "model": MODEL_ID}

        return api
