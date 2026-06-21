#!/usr/bin/env bash
# transcribe.sh — thin wrapper over `zmedia stt` (Modal Parakeet + dedup + ETA + warm).
# Preserves the original interface; offline fallback is scripts/transcribe-local.sh.
#
# Usage: transcribe.sh <file-or-url> [--out FILE] [--format txt|srt|vtt|json] [--force]
set -euo pipefail
ZMEDIA="${ZMEDIA:-$HOME/.local/bin/zmedia}"
[[ -x "$ZMEDIA" ]] || { echo "error: zmedia not installed ($ZMEDIA)" >&2; exit 3; }

IN=""; OUT=""; FMT="txt"; FORCE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --format) FMT="$2"; shift 2 ;;
    --force) FORCE="--force"; shift ;;
    -h|--help) "$ZMEDIA" stt -h 2>/dev/null || true; exit 0 ;;
    *) IN="$1"; shift ;;
  esac
done
[[ -z "$IN" ]] && { echo "error: no input file/url" >&2; exit 2; }

if [[ -n "$OUT" ]]; then
  "$ZMEDIA" stt "$IN" --format "$FMT" $FORCE | tee "$OUT"
  echo "[stt] saved -> $OUT" >&2
else
  "$ZMEDIA" stt "$IN" --format "$FMT" $FORCE
fi
