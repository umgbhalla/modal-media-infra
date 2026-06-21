# modal-media-infra

Serverless **speech + media** infrastructure on [Modal](https://modal.com) — replacing
the local Apple-Silicon MLX stack (`parakeet-mlx` / `mlx-audio`) with latest-SOTA
open models on on-demand GPU. Pay-per-second, scale-to-zero.

## Status

| Layer | Model | GPU | Status |
|-------|-------|-----|--------|
| **STT** (speech-to-text) | `nvidia/parakeet-tdt-0.6b-v3` | L4 | ✅ **deployed** (`stt_service.py`) |
| **TTS** (text-to-speech) | Kokoro-82m **and** Chatterbox | T4 / L4 | ✅ **deployed** (`tts_service.py`, both options) |
| **OMNI** (audio+video understanding) | `Qwen/Qwen2.5-Omni-7B` | L40S | ✅ **deployed** (`omni_service.py`) |

Three token-auth'd endpoints (`zod-stt`, `zod-tts`, `zod-omni`). Local on-device
MLX usage is disabled — everything runs on Modal; the old local scripts remain only
as offline fallbacks.

## Why these models (benchmarks)

Measured on Modal, latest open models only (no whisper). Cost = RTF × GPU rate,
per hour of audio.

**STT** — winner **Parakeet TDT 0.6b v3** on throughput + long-form robustness:
- Parakeet v3 @ L4: RTF **0.012–0.05**, handles multi-minute audio natively, ~**$0.04/audio-hr**
- Canary-1b-flash @ L4: RTF 0.085 but truncates past its context window (needs chunking infra)

**TTS** — split by axis (cloning/realtime not needed here):
- Kokoro-82m @ T4: RTF 0.10, ~**$0.06/audio-hr** — speed/cost king
- Chatterbox @ L4 RTF 1.31 / A100 0.87 / **H100 0.44** (~$1.75/audio-hr) — ~ElevenLabs quality;
  it's latency-bound at batch=1, so bigger GPU buys *speed* not *cost* (batch to cut cost)

**OMNI** — **Qwen2.5-Omni-7B**, uncontested: transcribes + understands video + emits a
correlated useful-signal verdict in one call. 22.7 GB on L40S, gen 2.4s / 30s clip.

See [`results/`](results/) for raw numbers and the summary visual.

## STT service

`stt_service.py` — Parakeet on an L4 behind a token-auth'd FastAPI endpoint.

```bash
modal deploy stt_service.py
# -> https://<workspace>--zod-stt-stt-web.modal.run   (POST /transcribe, GET /health)
```

Auth token lives in a Modal secret:
```bash
modal secret create zod-stt-token STT_TOKEN=$(openssl rand -hex 24)
```

### Call it

```bash
curl -X POST "$STT_MODAL_URL/transcribe" \
  -H "Authorization: Bearer $STT_MODAL_TOKEN" \
  -F "file=@clip.wav"          # audio OR video; server normalizes via ffmpeg
# -> {"text": "...", "segments": [{"start","end","text"}...], "infer_sec": 1.1}
```

Or the drop-in CLI (`txt|srt|vtt|json`, same interface as the old local wrapper):

```bash
client/transcribe.sh clip.mp4 --format srt
client/transcribe.sh https://example.com/talk.m4a --out out.txt
```

Env (in `~/.dev.env`):
```
STT_MODAL_URL=https://cronus--zod-stt-stt-web.modal.run
STT_MODAL_TOKEN=<token>
```

### Design notes
- Class-based (`@app.cls`) so the model loads once per container (`@modal.enter`);
  `scaledown_window=300` keeps it warm 5 min between calls (no repeat cold loads).
- **Long-form safe**: switches Parakeet to bounded-memory local attention
  (`rel_pos_local_attn`) so multi-minute audio never OOMs (full-context attention
  on a 15-min clip OOM'd an L4 — that's why).
- Server-side ffmpeg normalize → accepts any audio/video container.

### Gotchas (hard-won)
- Use latest `nemo_toolkit[asr]` on a `debian_slim` base — CUDA rides in via the
  torch wheel. A CUDA base image forces source builds (pyarrow/scipy) that fail.
- NeMo imports `NeptuneLogger`, removed in pytorch-lightning ≥2.5 — shimmed at load.
- NeMo also needs `matplotlib` at runtime.

## Layout
```
stt_service.py     deployed STT service (Parakeet)
client/            transcribe.sh — CLI client (drop-in for old local wrapper)
bench/             stt_bench / tts_bench / tts_spec / omni_bench
results/           numbers + summary visual
```

## TTS service (`tts_service.py`)

One URL, both backends. A light CPU router auth-checks and dispatches to the
requested GPU backend, returning wav bytes.

```bash
modal deploy tts_service.py   # -> https://<workspace>--zod-tts-web.modal.run
curl -X POST "$TTS_MODAL_URL/tts" -H "Authorization: Bearer $TTS_MODAL_TOKEN" \
  -F text="hello world" -F model=kokoro -o out.wav     # model = kokoro | chatterbox
```
Client: `client/say.sh "text" --model chatterbox --out x.wav` (also `--stdin`).

## OMNI service (`omni_service.py`)

Qwen2.5-Omni-7B on L40S. Accepts an audio OR video file + optional prompt;
transcribes audio AND reads video frames together (`use_audio_in_video=True`),
returns text understanding (default = summary + `USEFUL_SIGNAL` gate + tags).

```bash
modal deploy omni_service.py  # -> https://<workspace>--zod-omni-omni-web.modal.run
curl -X POST "$OMNI_MODAL_URL/understand" -H "Authorization: Bearer $OMNI_MODAL_TOKEN" \
  -F file=@clip.mp4 -F prompt="what is shown and said?"
```
Client: `client/understand.sh clip.mp4 --prompt "..."`.
Note: video needs the **decord** reader (torchvision 0.27 dropped `io.read_video`) —
forced via `FORCE_QWENVL_VIDEO_READER=decord`.

## Roadmap
- [x] STT (Parakeet) deployed
- [x] TTS (Kokoro + Chatterbox) deployed
- [x] OMNI (Qwen2.5-Omni-7B) deployed + validated on real audio+video
- [ ] Memory snapshots to collapse cold-start load (21–95s → sub-second)
- [ ] Batch TTS to cut Chatterbox cost
