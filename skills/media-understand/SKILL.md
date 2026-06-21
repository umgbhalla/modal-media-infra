---
name: media-understand
description: "Audio+video UNDERSTANDING (not just transcription) via the Modal Qwen2.5-Omni endpoint. One call transcribes audio AND reads video frames AND reasons jointly — summary, a useful-signal gate, topic tags, or any custom question about a clip. Use when you need to know what a video/audio actually MEANS or contains, beyond the words. For plain word-for-word transcription use the `parakeet` skill instead. Triggers: 'what's in this video', 'understand this clip', 'is there useful signal', 'describe what happens', 'analyze this media'."
metadata:
  openclaw:
    emoji: "🎬"
    requires:
      bins: ["curl", "python3"]
triggers:
  - what's in this video
  - understand this clip
  - describe what happens
  - analyze this media
  - is there useful signal
  - watch this video
  - what does this show
---

# media-understand — Audio+Video Understanding (Modal Qwen2.5-Omni)

Multimodal understanding via the **Modal** service `zod-omni` running
`Qwen/Qwen2.5-Omni-7B` (L40S GPU). Unlike the `parakeet` STT skill (which only
gives you the words), this **understands** a clip end-to-end: it ingests the audio
*and* the video frames together and reasons over both. ~$0.001–0.003 per short
clip, gen ~2–5s after warm.

Repo: <https://github.com/umgbhalla/modal-media-infra> · service `omni_service.py`.

**Engine = `zmedia`.** `scripts/understand.sh` is a thin wrapper over `zmedia omni`
(`~/.local/bin/zmedia`) — adds **dedup** (same file+prompt = cached, no re-run),
**ETA**, warm/cold. Batch: `zmedia omni a.mp4 b.mp4 --pace 1`; `zmedia warm omni` to pre-warm (~90s cold).

## When to use

- "What's in this video?" / "what happens?" / "what does this show?" — needs visual + audio.
- **Useful-signal gating** of a shared clip: is there substantive/technical content worth saving? (default prompt returns summary + `USEFUL_SIGNAL: yes/no` + tags.)
- Any custom question about a media file ("what slide is on screen when they mention X?").
- **Use `parakeet` instead** when you only need a literal transcript — it's cheaper/faster for pure STT.

## Quick start

```bash
# default gate: summary + USEFUL_SIGNAL + tags
~/.agents/skills/media-understand/scripts/understand.sh clip.mp4

# custom question (audio + video reasoned together)
~/.agents/skills/media-understand/scripts/understand.sh clip.mp4 \
  --prompt "What is shown on screen and what is being explained?"

# audio-only file works too (it just reasons over the audio)
~/.agents/skills/media-understand/scripts/understand.sh voicenote.ogg --prompt "summarize + action items"

# remote url + raw json
~/.agents/skills/media-understand/scripts/understand.sh "https://site.com/talk.mp4" --json
```

## How it works

1. URL → `curl` down. File (audio or video) is POSTed to `$OMNI_MODAL_URL/understand`.
2. Server `ffprobe`-detects video vs audio, builds the right Qwen-Omni conversation
   (`use_audio_in_video=True` so audio+frames are correlated), and generates.
3. Returns `{output, modality, gen_sec, vram_gb}`; the client prints `output`.

### Config (in `~/.dev.env`)
```
OMNI_MODAL_URL=https://cronus--zod-omni-omni-web.modal.run
OMNI_MODAL_TOKEN=***     # also Modal secret zod-omni-token
```

### Direct curl
```bash
curl -s -X POST "$OMNI_MODAL_URL/understand" \
  -H "Authorization: Bearer $OMNI_MODAL_TOKEN" \
  -F "file=@clip.mp4" -F "prompt=what happens here?" | jq -r .output
```

## Operate
```bash
cd <repo>/ && modal deploy omni_service.py   # github.com/umgbhalla/modal-media-infra
modal app logs zod-omni
curl -s "$OMNI_MODAL_URL/health"
```
