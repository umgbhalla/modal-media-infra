#!/usr/bin/env bash
# say.sh — text-to-speech via the Modal TTS endpoint (zod-tts).
# Drop-in replacement for the old local mlx-audio say.sh: same interface,
# plus --model to choose the backend.
#
# Models:
#   kokoro      (default) — fast/cheap, ~$0.06/audio-hr
#   chatterbox            — higher naturalness (~ElevenLabs), ~$1.05/audio-hr
#
# Usage:
#   say.sh "text" [--out FILE.wav] [--model kokoro|chatterbox] [--voice af_heart]
#   echo "long text" | say.sh --stdin --out /Users/beam/.openclaw/media/x.wav
#
# Env (from ~/.dev.env):
#   TTS_MODAL_URL    default https://cronus--zod-tts-web.modal.run
#   TTS_MODAL_TOKEN  bearer token (required)
set -euo pipefail

MEDIA_DIR="$HOME/.openclaw/media"
MODEL="${TTS_MODEL:-kokoro}"
VOICE="af_heart"
OUT=""; STDIN_MODE=0; TEXT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stdin) STDIN_MODE=1; shift ;;
    --out)   OUT="$2"; shift 2 ;;
    --voice) VOICE="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --speed) shift 2 ;;   # accepted for compat, ignored
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) TEXT="${TEXT:+$TEXT }$1"; shift ;;
  esac
done

[[ "$STDIN_MODE" -eq 1 ]] && TEXT="$(cat)"
[[ -z "${TEXT// }" ]] && { echo "error: no text" >&2; exit 2; }

# light markdown strip
TEXT="$(printf '%s' "$TEXT" | sed -E 's/`+//g; s/\*+//g; s/^#+ //g; s/\[([^]]*)\]\([^)]*\)/\1/g')"

[[ -f "$HOME/.dev.env" ]] && source "$HOME/.dev.env"
URL="${TTS_MODAL_URL:-https://cronus--zod-tts-web.modal.run}"
TOKEN="***"
[[ -z "$TOKEN" ]] && { echo "error: TTS_MODAL_TOKEN not set (~/.dev.env)" >&2; exit 3; }

mkdir -p "$MEDIA_DIR"
[[ -z "$OUT" ]] && OUT="$MEDIA_DIR/say_$(date +%s).wav"

echo "[tts] $MODEL -> $OUT" >&2
code=$(curl -s -o "$OUT" -w '%{http_code}' -X POST "$URL/tts" \
        -H "Authorization: Bearer $TOKEN" \
        -F "text=$TEXT" -F "model=$MODEL" -F "voice=$VOICE")
if [[ "$code" != "200" ]]; then
  echo "error: endpoint returned HTTP $code" >&2; head -c 300 "$OUT" >&2; echo >&2; rm -f "$OUT"; exit 5
fi
echo "$OUT"
