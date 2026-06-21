---
name: parakeet
description: "Speech-to-text via the Modal Parakeet endpoint (NVIDIA Parakeet TDT 0.6b v3 on GPU). Use for ALL voice-message transcription AND any audio/video where you need the spoken content. Server normalizes audio/video (ffmpeg) and handles long-form natively. Local parakeet-mlx remains as an offline fallback. Triggers: voice notes, shared videos, audio files, 'transcribe', 'what does this say', 'pull the audio'."
metadata:
  openclaw:
    emoji: "🎧"
    requires:
      bins: ["curl", "python3"]
triggers:
  - voice message
  - voice note
  - transcribe
  - what does this say
  - audio file
  - shared video
  - video link
  - pull the audio
  - strip audio
---

# Parakeet — Speech-to-Text (Modal endpoint)

Transcription via the **Modal** service `zod-stt` running
`nvidia/parakeet-tdt-0.6b-v3` (throughput-SOTA open ASR) on an L4 GPU. This is the
**DEFAULT** for every voice/audio/video transcription. ~$0.04 per hour of audio,
RTF ~0.01–0.05, long-form handled server-side. The old local `parakeet-mlx` path
is kept as an **offline fallback** only (`scripts/transcribe-local.sh`).

Repo: <https://github.com/umgbhalla/modal-media-infra> · service `stt_service.py`.

**Engine = `zmedia`.** `scripts/transcribe.sh` is a thin wrapper over `zmedia stt`
(`~/.local/bin/zmedia`), which adds **dedup** (re-transcribing the same file is a
no-op), **ETA** (duration × RTF + cold-start), and **warm/cold** detection. For
batch/recurrent jobs call it directly: `zmedia stt a.wav b.mp4 --pace 1`, and
`zmedia warm stt` to pre-warm. `zmedia verify` health-checks all endpoints.

## When to use (auto-trigger)

- **Any voice message / voice note** the user sends → transcribe and act on the content like a text command.
- **Any video the user shares OR any video you find** while doing a task → transcribe to capture the spoken content. The server strips audio itself; just send the file/URL.
- Any standalone audio file (`ogg`, `m4a`, `mp3`, `wav`, `opus`, ...).
- Requests like "transcribe this", "what does he say", "pull the audio".

Don't ask permission — just transcribe and use the result.

## Quick start

```bash
# Audio or video, local path or URL. Server normalizes + handles long-form.
~/.agents/skills/parakeet/scripts/transcribe.sh /path/to/input.(ogg|mp4|m4a|wav)

# Save transcript + pick format
~/.agents/skills/parakeet/scripts/transcribe.sh clip.mp4 --out /tmp/clip.txt --format txt

# Remote audio/video (downloads, then transcribes on the endpoint)
~/.agents/skills/parakeet/scripts/transcribe.sh "https://site.com/talk.mp4" --format srt
```

Formats: `txt` (default), `srt`, `vtt`, `json` (json/srt/vtt use server segment timestamps).

## How it works

1. If the input is a URL → `curl` it down (resumable, retried).
2. POST the bytes to `$STT_MODAL_URL/transcribe` with a bearer token. The service
   runs `ffmpeg -vn -ac 1 -ar 16000` server-side (so it accepts audio OR video) and
   transcribes with bounded-memory local attention (long clips never OOM).
3. Returns `{text, segments[], infer_sec}`; the client renders `txt/srt/vtt/json`.

### Config (in `~/.dev.env`)
```
STT_MODAL_URL=https://cronus--zod-stt-stt-web.modal.run
STT_MODAL_TOKEN=<bearer token>     # also stored as Modal secret zod-stt-token
```

### Direct curl (no wrapper)
```bash
curl -s -X POST "$STT_MODAL_URL/transcribe" \
  -H "Authorization: Bearer $STT_MODAL_TOKEN" -F "file=@audio.ogg" | jq .text
```

## Fallback chain (only if the endpoint fails)

**Local model usage is OFF by default** (policy 2026-06-21). Active chain is
Modal → cloud APIs; the local mlx path is offline/manual-only.

1. **Modal `zod-stt`** (default) — Parakeet v3 on GPU.
2. **ElevenLabs Scribe** — `curl -X POST https://api.elevenlabs.io/v1/speech-to-text -H "xi-api-key: $ELEVEN_LABS_API_KEY" -F model_id=scribe_v1 -F file=@audio.ogg`
3. **OpenAI Whisper API** — last resort.
4. **(disabled) Local `parakeet-mlx`** — `scripts/transcribe-local.sh <input>`. Only
   if explicitly asked / fully offline; the model cache was cleaned off the Mac mini,
   so it would re-download (`uv tool install parakeet-mlx --with hf_transfer`).

## Redeploy / operate the endpoint

```bash
cd <repo>/                       # github.com/umgbhalla/modal-media-infra
modal deploy stt_service.py      # rebuild + redeploy
modal app logs zod-stt           # tail logs
curl -s "$STT_MODAL_URL/health"  # {"ok":true,...}
```
Token rotation: `modal secret create zod-stt-token STT_TOKEN=$(openssl rand -hex 24)`
then update `STT_MODAL_TOKEN` in `~/.dev.env` and redeploy.
