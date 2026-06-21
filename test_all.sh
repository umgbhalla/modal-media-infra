#!/usr/bin/env bash
# test_all.sh — smoke-test all three Modal media endpoints end-to-end.
# Usage: test_all.sh <audio.wav> <video.mp4>
set -euo pipefail
[[ -f "$HOME/.dev.env" ]] && source "$HOME/.dev.env"
AUDIO="${1:?need an audio file}"; VIDEO="${2:?need a video file}"
OUT="${OUTDIR:-/tmp}"; mkdir -p "$OUT"

echo "================ 1) STT  (zod-stt / Parakeet) ================"
curl -s -m 120 -X POST "$STT_MODAL_URL/transcribe" \
  -H "Authorization: Bearer $STT_MODAL_TOKEN" -F "file=@$AUDIO" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("infer_sec",d["infer_sec"],"| segs",len(d["segments"]));print("TEXT:",d["text"][:160],"...")'

echo; echo "================ 2) TTS  (zod-tts / kokoro + chatterbox) ===="
for M in kokoro chatterbox; do
  curl -s -m 300 -X POST "$TTS_MODAL_URL/tts" \
    -H "Authorization: Bearer $TTS_MODAL_TOKEN" \
    -F "text=This is the $M voice synthesized on Modal GPU." -F "model=$M" \
    -D "$OUT/h_$M.txt" -o "$OUT/tts_$M.wav"
  echo "  $M -> $OUT/tts_$M.wav  ($(grep -i x-gen-sec "$OUT/h_$M.txt" | tr -d '\r'))"
done

echo; echo "================ 3) OMNI (zod-omni / Qwen2.5-Omni) =========="
curl -s -m 300 -X POST "$OMNI_MODAL_URL/understand" \
  -H "Authorization: Bearer $OMNI_MODAL_TOKEN" \
  -F "file=@$VIDEO" -F "prompt=What is shown on screen and what is being said?" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("modality",d["modality"],"| gen_sec",d["gen_sec"],"| vram",d["vram_gb"]);print("OUTPUT:",d["output"][:400])'

echo; echo "all three OK."
