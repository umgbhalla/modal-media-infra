#!/usr/bin/env bash
# transcribe.sh — speech-to-text via the Modal Parakeet endpoint (zod-stt).
# Drop-in replacement for the old local parakeet-mlx wrapper: same CLI.
#
# The Modal service normalizes any input (audio OR video) to 16k mono wav
# server-side, so this client just POSTs the bytes. URLs are fetched locally
# first. Long-form audio is handled by the service (local-attention).
#
# Env (from ~/.dev.env):
#   STT_MODAL_URL    default https://cronus--zod-stt-stt-web.modal.run
#   STT_MODAL_TOKEN  bearer token (required)
#
# Usage:
#   transcribe.sh <input-file-or-url> [--out /path/out.txt] [--format txt|srt|vtt|json]
set -euo pipefail

IN=""; OUT=""; FMT="txt"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --format) FMT="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) IN="$1"; shift ;;
  esac
done
[[ -z "$IN" ]] && { echo "error: no input file/url" >&2; exit 2; }

[[ -f "$HOME/.dev.env" ]] && source "$HOME/.dev.env"
URL="${STT_MODAL_URL:-https://cronus--zod-stt-stt-web.modal.run}"
TOKEN="${STT_MODAL_TOKEN:-}"
[[ -z "$TOKEN" ]] && { echo "error: STT_MODAL_TOKEN not set (~/.dev.env)" >&2; exit 3; }
command -v curl >/dev/null || { echo "error: curl missing" >&2; exit 3; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
SRC="$IN"
if [[ "$IN" =~ ^https?:// ]]; then
  SRC="$WORK/input.bin"
  echo "[stt] downloading $IN ..." >&2
  curl -sL --retry 8 --retry-all-errors -o "$SRC" "$IN"
fi
[[ -f "$SRC" ]] || { echo "error: input not found: $SRC" >&2; exit 4; }

echo "[stt] transcribing via Modal ($FMT) ..." >&2
RESP="$WORK/resp.json"
code=$(curl -s -o "$RESP" -w '%{http_code}' -X POST "$URL/transcribe" \
        -H "Authorization: Bearer $TOKEN" -F "file=@$SRC")
if [[ "$code" != "200" ]]; then
  echo "error: endpoint returned HTTP $code" >&2; cat "$RESP" >&2; echo >&2; exit 5
fi

# Format the JSON {text, segments[]} into the requested output.
RENDER="$(FMT="$FMT" python3 - "$RESP" <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1]))
fmt = os.environ["FMT"]
def ts(t, comma=True):
    h=int(t//3600); m=int((t%3600)//60); s=t%60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace('.', ',' if comma else '.')
if fmt == "json":
    print(json.dumps(d, ensure_ascii=False, indent=2))
elif fmt in ("srt", "vtt"):
    segs = d.get("segments") or []
    out = []
    if fmt == "vtt": out.append("WEBVTT\n")
    for i, s in enumerate(segs, 1):
        if fmt == "srt": out.append(str(i))
        out.append(f"{ts(s['start'], fmt=='srt')} --> {ts(s['end'], fmt=='srt')}")
        out.append(s["text"].strip()); out.append("")
    print("\n".join(out).strip())
else:
    print(d.get("text", "").strip())
PY
)"

if [[ -n "$OUT" ]]; then printf '%s\n' "$RENDER" > "$OUT"; echo "[stt] saved -> $OUT" >&2; fi
printf '%s\n' "$RENDER"
