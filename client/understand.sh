#!/usr/bin/env bash
# understand.sh — audio+video understanding via the Modal Qwen2.5-Omni endpoint.
# Transcribes audio AND reads video frames AND reasons jointly, in one call.
#
# Env (from ~/.dev.env):
#   OMNI_MODAL_URL    default https://cronus--zod-omni-omni-web.modal.run
#   OMNI_MODAL_TOKEN  bearer token (required)
#
# Usage:
#   understand.sh <file-or-url> [--prompt "question"] [--json]
#   # default prompt = summary + USEFUL_SIGNAL gate + topic tags
set -euo pipefail

IN=""; PROMPT=""; JSON=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT="$2"; shift 2 ;;
    --json) JSON=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) IN="$1"; shift ;;
  esac
done
[[ -z "$IN" ]] && { echo "error: no input file/url" >&2; exit 2; }

[[ -f "$HOME/.dev.env" ]] && source "$HOME/.dev.env"
URL="${OMNI_MODAL_URL:-https://cronus--zod-omni-omni-web.modal.run}"
TOKEN="${OMNI_MODAL_TOKEN:-}"
[[ -z "$TOKEN" ]] && { echo "error: OMNI_MODAL_TOKEN not set (~/.dev.env)" >&2; exit 3; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
SRC="$IN"
if [[ "$IN" =~ ^https?:// ]]; then
  SRC="$WORK/input.bin"
  echo "[omni] downloading $IN ..." >&2
  curl -sL --retry 8 --retry-all-errors -o "$SRC" "$IN"
fi
[[ -f "$SRC" ]] || { echo "error: input not found: $SRC" >&2; exit 4; }

echo "[omni] understanding via Modal ..." >&2
RESP="$WORK/resp.json"
code=$(curl -s -o "$RESP" -w '%{http_code}' -X POST "$URL/understand" \
        -H "Authorization: Bearer $TOKEN" \
        -F "file=@$SRC" -F "prompt=$PROMPT")
if [[ "$code" != "200" ]]; then
  echo "error: endpoint returned HTTP $code" >&2; cat "$RESP" >&2; echo >&2; exit 5
fi
if [[ "$JSON" -eq 1 ]]; then cat "$RESP"; echo; else
  python3 -c 'import sys,json; print(json.load(open(sys.argv[1]))["output"])' "$RESP"
fi
