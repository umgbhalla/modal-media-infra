#!/usr/bin/env bash
# post-audio.sh — upload a local audio file to a Discord channel/thread as a REAL
# attachment (multipart), with an optional caption.
#
# WHY: the `MEDIA:<path>` directive works on `message action=send` but is silently
# dropped on the `thread-reply` path — it posts the caption text with NO file.
# This uploader posts the bytes directly via the Discord API, so audio always
# lands as a playable attachment in a thread.
#
# Usage:
#   post-audio.sh <thread_or_channel_id> <audio_file> ["caption text"]
#
# Pairs with say.sh:
#   AUDIO=$(printf '%s' "$NARRATION" | ~/.agents/skills/mlx-audio/scripts/say.sh --stdin \
#             --out ~/.openclaw/media/analysis_<id>.wav)
#   ~/.agents/skills/mlx-audio/scripts/post-audio.sh <thread_id> "$AUDIO" "🎧 audio version (N min)"
#
# Prints the created message id on success; non-zero exit on failure.
set -euo pipefail

CHANNEL="${1:-}"
FILE="${2:-}"
CAPTION="${3:-🎧 audio version}"

if [ -z "$CHANNEL" ] || [ -z "$FILE" ]; then
  echo "usage: post-audio.sh <thread_or_channel_id> <audio_file> [caption]" >&2
  exit 2
fi
if [ ! -f "$FILE" ]; then
  echo "post-audio.sh: file not found: $FILE" >&2
  exit 1
fi

TOKEN="$(python3 -c "import json; print(json.load(open('$HOME/.openclaw/openclaw.json'))['channels']['discord']['token'])")"

CHANNEL="$CHANNEL" FILE="$FILE" CAPTION="$CAPTION" TOKEN="$TOKEN" python3 - << 'PYEOF'
import os, sys, json, time, mimetypes, urllib.request
ch=os.environ["CHANNEL"]; path=os.environ["FILE"]; cap=os.environ["CAPTION"]; tok=os.environ["TOKEN"]
fn=os.path.basename(path)
ct=mimetypes.guess_type(fn)[0] or "application/octet-stream"
blob=open(path,"rb").read()
if len(blob) > 24_000_000:  # generous; non-boosted Discord ~25MB now, keep margin
    sys.stderr.write(f"post-audio.sh: file too large ({len(blob)//1024//1024}MB)\n"); sys.exit(1)
boundary="----audioboundary"+str(int(time.time()*1000))
parts=[]
parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
             f'Content-Type: application/json\r\n\r\n'.encode()
             + json.dumps({"content":cap[:1900]}).encode() + b"\r\n")
parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; '
             f'filename="{fn}"\r\nContent-Type: {ct}\r\n\r\n'.encode() + blob + b"\r\n")
parts.append(f"--{boundary}--\r\n".encode())
req=urllib.request.Request(f"https://discord.com/api/v10/channels/{ch}/messages",
    data=b"".join(parts), method="POST",
    headers={"Authorization":f"Bot {tok}",
             "Content-Type":f"multipart/form-data; boundary={boundary}",
             "User-Agent":"zod-audio/1.0"})
try:
    r=json.load(urllib.request.urlopen(req, timeout=120))
    att=r.get("attachments",[])
    if not att:
        sys.stderr.write("post-audio.sh: posted but NO attachment landed\n"); sys.exit(1)
    sys.stderr.write(f"post-audio.sh: uploaded {att[0]['filename']} ({len(blob)//1024}KB) -> msg {r['id']}\n")
    print(r["id"])
except urllib.error.HTTPError as e:
    sys.stderr.write(f"post-audio.sh: HTTP {e.code}: {e.read().decode()[:200]}\n"); sys.exit(1)
except Exception as e:
    sys.stderr.write(f"post-audio.sh: {e}\n"); sys.exit(1)
PYEOF
