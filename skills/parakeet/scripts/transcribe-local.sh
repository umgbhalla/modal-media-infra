#!/usr/bin/env bash
# transcribe.sh — local speech-to-text via parakeet-mlx (Apple Silicon, no API/quota).
# Handles audio AND video: if input has a video stream, audio is stripped with ffmpeg first.
#
# Usage:
#   transcribe.sh <input-file-or-url> [--out /path/out.txt] [--format txt|srt|vtt|json] [--keep-audio]
#
# Examples:
#   transcribe.sh voice.ogg
#   transcribe.sh clip.mp4 --out /tmp/clip.txt
#   transcribe.sh https://example.com/talk.mp4 --format srt
#
# Output: prints transcript to stdout; also writes a file (default alongside a temp dir).

set -euo pipefail

IN=""
OUT=""
FMT="txt"
KEEP_AUDIO=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --format) FMT="$2"; shift 2 ;;
    --keep-audio) KEEP_AUDIO=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) IN="$1"; shift ;;
  esac
done

if [[ -z "$IN" ]]; then
  echo "error: no input file/url given" >&2
  exit 2
fi

command -v parakeet-mlx >/dev/null 2>&1 || { echo "error: parakeet-mlx not installed (uv tool install parakeet-mlx --with hf_transfer)" >&2; exit 3; }
command -v ffmpeg >/dev/null 2>&1 || { echo "error: ffmpeg not installed (brew install ffmpeg)" >&2; exit 3; }

WORK="$(mktemp -d)"
cleanup() { [[ "$KEEP_AUDIO" -eq 0 ]] && rm -rf "$WORK" 2>/dev/null || true; }
trap cleanup EXIT

# 1. Fetch remote inputs locally
SRC="$IN"
if [[ "$IN" =~ ^https?:// ]]; then
  SRC="$WORK/input.$(basename "${IN%%\?*}" | sed 's/.*\.//')"
  [[ "$SRC" == "$WORK/input." ]] && SRC="$WORK/input.bin"
  echo "[parakeet] downloading $IN ..." >&2
  curl -sL --retry 10 --retry-all-errors -o "$SRC" "$IN"
fi

# 2. If the file carries a video stream, strip audio to 16k mono wav.
HAS_VIDEO=0
if ffprobe -v error -select_streams v:0 -show_entries stream=codec_type \
     -of csv=p=0 "$SRC" 2>/dev/null | grep -q video; then
  HAS_VIDEO=1
fi

AUDIO="$SRC"
if [[ "$HAS_VIDEO" -eq 1 ]]; then
  AUDIO="$WORK/audio.wav"
  echo "[parakeet] video detected — extracting audio (ffmpeg, 16k mono wav) ..." >&2
  ffmpeg -y -i "$SRC" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$AUDIO" >/dev/null 2>&1
fi

# 3. Transcribe with the cached parakeet-tdt-0.6b-v3 model (no --model flag = default).
echo "[parakeet] transcribing ($FMT) ..." >&2
parakeet-mlx "$AUDIO" --output-format "$FMT" --output-dir "$WORK" >/dev/null 2>&1

BASE="$(basename "$AUDIO")"; BASE="${BASE%.*}"
RESULT="$WORK/$BASE.$FMT"
[[ -f "$RESULT" ]] || RESULT="$(ls "$WORK"/*."$FMT" 2>/dev/null | head -1)"

if [[ -z "${RESULT:-}" || ! -f "$RESULT" ]]; then
  echo "error: transcription produced no $FMT output" >&2
  exit 4
fi

# 4. Persist + emit
if [[ -n "$OUT" ]]; then
  cp "$RESULT" "$OUT"
  echo "[parakeet] saved -> $OUT" >&2
fi
cat "$RESULT"
