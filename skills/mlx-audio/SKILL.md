---
name: mlx-audio
description: "Text-to-speech via the Modal TTS endpoint (zod-tts). Two backends: kokoro (fast/cheap default) and chatterbox (higher naturalness, ~ElevenLabs). Use to turn text into a listenable audio version — long #discussions analyses, research writeups, articles, or any reply the user would rather hear. Pairs with the parakeet (STT) + media-understand (omni) skills. Triggers: 'read this out', 'audio version', 'make it audio', 'TTS', 'narrate', 'say this', long-discussion analyses."
metadata:
  openclaw:
    emoji: "🗣️"
    requires:
      bins: ["curl", "ffprobe"]
triggers:
  - read this out
  - read it to me
  - audio version
  - make it audio
  - narrate
  - say this
  - tts
  - text to speech
  - voice it
  - long discussion audio
---

# mlx-audio — Text-to-Speech (Modal endpoint)

TTS via the **Modal** service `zod-tts`. **Default = local model usage is OFF** —
synthesis runs on Modal GPU now (the local Apple-Silicon MLX path is kept only as
a manual/offline fallback). Two backends, pick per call:

- **kokoro** (default) — Kokoro-82m on T4, ~10x realtime, ~$0.06/audio-hr. Use for
  bulk / long narrations / anything where speed+cost matter.
- **chatterbox** — Chatterbox on L4, ~ElevenLabs naturalness, ~$1.05/audio-hr. Use
  when the piece deserves the nicer voice (no realtime need, so cost is the only
  tradeoff and at our volume it's negligible).

Use whichever is preferred for the moment — kokoro by default, chatterbox when
quality matters. Repo: <https://github.com/umgbhalla/modal-media-infra> · `tts_service.py`.

**Engine = `zmedia`.** `scripts/say.sh` is a thin wrapper over `zmedia tts`
(`~/.local/bin/zmedia`) — adds **dedup** (same text+model = cached wav, no re-gen),
**ETA**, and warm/cold detection. Direct: `zmedia tts "text" --model chatterbox --out x.wav`.

## When to use

- **Long #discussions analyses** (<#1478394058153005066>): generate an audio version.
- Any "read this out / audio version / narrate / TTS / say this" request.
- Research writeups, article summaries, long replies the user wants as audio.

## Quick start

```bash
# kokoro (default)
~/.agents/skills/mlx-audio/scripts/say.sh "text to speak" --out ~/.openclaw/media/x.wav

# chatterbox (nicer voice)
echo "long text" | ~/.agents/skills/mlx-audio/scripts/say.sh --stdin --model chatterbox \
  --out ~/.openclaw/media/x.wav

# from a file
cat analysis.md | ~/.agents/skills/mlx-audio/scripts/say.sh --stdin --out ~/.openclaw/media/analysis.wav
```

Default out dir is `~/.openclaw/media/` (allowed Discord media dir). Markdown is
lightly stripped before synthesis. Transcode WAV→mp3 before uploading if you want
a smaller file: `ffmpeg -y -i x.wav -b:a 96k -ac 1 x.mp3`.

## Upload into a Discord thread

`MEDIA:` is dropped on `thread-reply`. Use `post-audio.sh` to attach audio:
```bash
~/.agents/skills/mlx-audio/scripts/post-audio.sh <thread_id> ~/.openclaw/media/x.mp3 "🎧 audio version"
```

## Config (in `~/.dev.env`)
```
TTS_MODAL_URL=https://cronus--zod-tts-web.modal.run
TTS_MODAL_TOKEN=***     # also Modal secret zod-tts-token
```

### Direct curl
```bash
curl -s -X POST "$TTS_MODAL_URL/tts" -H "Authorization: Bearer $TTS_MODAL_TOKEN" \
  -F "text=hello" -F "model=chatterbox" -o out.wav
```

## Fallback (offline only — local model usage is otherwise disabled)
`scripts/say-local.sh` runs the old local mlx-audio Kokoro path on-device. Only use
it when Modal is unreachable; per current policy local models stay OFF by default.

## Operate
```bash
cd <repo>/ && modal deploy tts_service.py   # github.com/umgbhalla/modal-media-infra
modal app logs zod-tts
curl -s "$TTS_MODAL_URL/health"
```
