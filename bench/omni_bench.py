"""
Modal omni (audio+video understanding) benchmark — Qwen2.5-Omni-7B.
Validates: transcribe audio + reason + emit a correlated useful-signal verdict.
Same pipeline ingests video (use_audio_in_video=True).

Run:  modal run omni_bench.py --audio sample30.wav
"""
import time
import modal

app = modal.App("zod-omni-bench")

omni_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "torch", "torchvision", "torchaudio",
        "transformers", "accelerate", "qwen-omni-utils", "soundfile", "librosa",
    )
)

cache = modal.Volume.from_name("zod-omni-cache", create_if_missing=True)
CACHE = "/cache"

PROMPT = (
    "You are a media-understanding gate. Listen to the audio. Then output strictly:\n"
    "1) a one-sentence summary,\n2) USEFUL_SIGNAL: yes/no (is there substantive technical content?),\n"
    "3) three topic tags."
)


@app.function(image=omni_image, gpu="L40S", volumes={CACHE: cache}, timeout=2400)
def qwen_omni(audio_bytes: bytes):
    import os
    os.environ["HF_HOME"] = CACHE
    import torch
    from transformers import Qwen2_5OmniForConditionalGeneration, Qwen2_5OmniProcessor
    from qwen_omni_utils import process_mm_info
    open("/tmp/a.wav", "wb").write(audio_bytes)
    mid = "Qwen/Qwen2.5-Omni-7B"
    t0 = time.time()
    model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
        mid, torch_dtype=torch.bfloat16, device_map="auto", cache_dir=CACHE
    )
    proc = Qwen2_5OmniProcessor.from_pretrained(mid, cache_dir=CACHE)
    load = time.time() - t0
    conv = [
        {"role": "system", "content": [{"type": "text", "text": "You are Qwen, a multimodal assistant."}]},
        {"role": "user", "content": [
            {"type": "audio", "audio": "/tmp/a.wav"},
            {"type": "text", "text": PROMPT},
        ]},
    ]
    text = proc.apply_chat_template(conv, add_generation_prompt=True, tokenize=False)
    audios, images, videos = process_mm_info(conv, use_audio_in_video=True)
    inputs = proc(text=text, audio=audios, images=images, videos=videos,
                  return_tensors="pt", padding=True, use_audio_in_video=True)
    inputs = inputs.to(model.device).to(model.dtype)
    t1 = time.time()
    out = model.generate(**inputs, max_new_tokens=256, return_audio=False, use_audio_in_video=True)
    gen = time.time() - t1
    txt = proc.batch_decode(out, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
    vram = round(torch.cuda.max_memory_allocated() / 1e9, 1)
    cache.commit()
    return {"engine": "Qwen2.5-Omni-7B", "gpu": "L40S", "load_sec": round(load, 2),
            "gen_sec": round(gen, 2), "vram_gb": vram, "output": txt[-800:]}


@app.local_entrypoint()
def main(audio: str):
    data = open(audio, "rb").read()
    print(f"audio bytes: {len(data)}")
    try:
        print(qwen_omni.remote(data))
    except Exception as e:
        print(f"omni failed: {e}")
