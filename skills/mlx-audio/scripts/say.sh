#!/usr/bin/env bash
# say.sh — thin wrapper over `zmedia tts` (Modal TTS + dedup + ETA).
# Preserves the original interface; offline fallback is scripts/say-local.sh.
#
# Usage:
#   say.sh "text" [--out FILE.wav] [--model kokoro|chatterbox] [--voice af_heart] [--force]
#   echo "long text" | say.sh --stdin --out FILE.wav
set -euo pipefail
ZMEDIA="${ZMEDIA:-$HOME/.local/bin/zmedia}"
[[ -x "$ZMEDIA" ]] || { echo "error: zmedia not installed ($ZMEDIA)" >&2; exit 3; }

MODEL="${TTS_MODEL:-kokoro}"; VOICE="af_heart"; OUT=""; STDIN_MODE=0; TEXT=""; FORCE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stdin) STDIN_MODE=1; shift ;;
    --out)   OUT="$2"; shift 2 ;;
    --voice) VOICE="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --force) FORCE="--force"; shift ;;
    --speed) shift 2 ;;   # accepted for compat, ignored
    -h|--help) "$ZMEDIA" tts -h 2>/dev/null || true; exit 0 ;;
    *) TEXT="${TEXT:+$TEXT }$1"; shift ;;
  esac
done
[ "$STDIN_MODE" -eq 1 ] && TEXT="$(cat)"
[ -z "${TEXT// }" ] && { echo "error: no text" >&2; exit 2; }

ARGS=(tts "$TEXT" --model "$MODEL" --voice "$VOICE")
[ -n "$FORCE" ] && ARGS+=("$FORCE")
[ -n "$OUT" ] && ARGS+=(--out "$OUT")
"$ZMEDIA" "${ARGS[@]}"
