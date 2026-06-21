#!/usr/bin/env bash
# say.sh — local text-to-speech on Apple Silicon via mlx-audio (Kokoro-82M).
# Fully local, no API, no quota. Built for the 8 GB M2 mini: small model,
# lazy-loaded, one model in RAM at a time.
#
# Usage:
#   say.sh "text to speak" [--out FILE.wav] [--voice af_heart] [--speed 1.0]
#   echo "long text" | say.sh --stdin [--out FILE.wav]
#   cat analysis.md | say.sh --stdin --out /Users/beam/.openclaw/media/analysis.wav
#
# Defaults:
#   model = mlx-community/Kokoro-82M-bf16   (~339MB, faster-than-realtime on M2)
#   voice = af_heart
#   out   = ~/.openclaw/media/say_<epoch>.wav   (allowed Discord media dir)
#
# Notes:
#   - Output goes to ~/.openclaw/media by default so it can be attached to
#     Discord directly (workspace paths are NOT an allowed media dir).
#   - Needs the venv at ~/mlx-audio-explore/.venv with mlx-audio + misaki[en].
#     If missing, this script bootstraps it.
#   - Markdown is lightly stripped (#, *, `, links) before synthesis.
set -euo pipefail

VENV="$HOME/mlx-audio-explore/.venv"
MODEL="${MLX_AUDIO_MODEL:-mlx-community/Kokoro-82M-bf16}"
VOICE="af_heart"
SPEED="1.0"
MEDIA_DIR="$HOME/.openclaw/media"
OUT=""
STDIN_MODE=0
TEXT=""

# ---- args ------------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --stdin) STDIN_MODE=1; shift ;;
    --out)   OUT="$2"; shift 2 ;;
    --voice) VOICE="$2"; shift 2 ;;
    --speed) SPEED="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    *)       TEXT="$1"; shift ;;
  esac
done

if [ "$STDIN_MODE" = "1" ]; then
  TEXT="$(cat)"
fi
if [ -z "${TEXT// }" ]; then
  echo "say.sh: no text given (pass a string or --stdin)" >&2
  exit 2
fi

[ -z "$OUT" ] && OUT="$MEDIA_DIR/say_$(date +%s).wav"
mkdir -p "$(dirname "$OUT")"

# ---- bootstrap venv if needed ---------------------------------------------
if [ ! -x "$VENV/bin/python3" ]; then
  echo "say.sh: bootstrapping mlx-audio venv at $VENV ..." >&2
  uv venv --python 3.13 "$VENV" >&2
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  uv pip install -U mlx-audio "misaki[en]" num2words >&2
else
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi
python3 -c "import mlx_audio" 2>/dev/null || { echo "say.sh: mlx-audio missing in venv" >&2; exit 1; }

# ---- light markdown strip (keep it speakable) ------------------------------
CLEAN="$(printf '%s' "$TEXT" | python3 -c '
import sys, re, unicodedata
t = sys.stdin.read()
t = unicodedata.normalize("NFC", t)
t = re.sub(r"```.*?```", " ", t, flags=re.S)      # code blocks
t = re.sub(r"`([^`]*)`", r"\1", t)                 # inline code
t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)     # md links -> text
t = re.sub(r"https?://\S+", " ", t)                # bare urls

# --- Unicode symbols Kokoro reads as literal garbage ------------------------
# Arrows -> spoken direction words (so "X -> Y" reads naturally, not as noise).
t = re.sub(r"[\u2190\u21d0\u27f5\u2b05\u2906\u2b60\u219e]", " from ", t)   # left arrows
t = re.sub(r"[\u2194\u21d4\u27f7\u2b0c\u2b04]", " and ", t)               # bidirectional
t = re.sub(r"[\u2192\u21d2\u27f6\u2b95\u2799\u279c\u279e\u27a4\u2906\u21a6\u2907\u21e8\u25b6\u25b8\u27a1\u2b62\u2b9e]", " to ", t)  # right arrows
t = re.sub(r"[\u2191\u2193\u21d1\u21d3\u2b06\u2b07]", " ", t)             # up/down arrows -> drop

# Dashes (em/en/figure/horizontal-bar/double-hyphen) -> pause
t = re.sub(r"\s*(?:--+|[\u2012\u2013\u2014\u2015\u2212])\s*", ", ", t)

# Bullets / list glyphs -> pause
t = re.sub(r"[\u2022\u2023\u2043\u204c\u204d\u2219\u25aa\u25ab\u25cf\u25e6\u00b7\u2027\u2756\u00bb\u203a]", ", ", t)

# Common math/relation symbols -> words (else read as noise)
t = t.replace("\u2248", " approximately ").replace("\u2260", " not equal to ")
t = t.replace("\u2265", " at least ").replace("\u2264", " at most ")
t = re.sub(r"\s*\u00d7\s*", " times ", t)            # multiplication sign
t = t.replace("\u2026", ", ")                        # ellipsis -> pause
t = re.sub(r"\s*&\s*", " and ", t)                   # ampersand -> and

# Smart quotes/apostrophes -> straight ASCII
t = t.translate({0x2018:0x27,0x2019:0x27,0x201a:0x27,0x201b:0x27,
                 0x201c:0x22,0x201d:0x22,0x201e:0x22,0x201f:0x22})

# Strip emoji / pictographs / dingbats / misc symbols entirely
t = re.sub("["
    "\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF\U00002190-\U000021FF\U0000FE00-\U0000FE0F"
    "\U00002300-\U000023FF\U00002000-\U0000206F"
    "]+", " ", t)

t = re.sub(r"[*_#>]+", " ", t)                     # md emphasis/headers
t = re.sub(r"(?m)^[\s]*[-+]\s+", ", ", t)           # list bullets -> pause
t = re.sub(r"\n{2,}", ". ", t)                      # blank line -> sentence break
t = t.replace("\n", ". ")                           # any remaining newline -> pause
# Drop any leftover non-speakable symbol chars (keep letters/digits/basic punct)
t = re.sub(r"[^\w\s.,!?;:%$\u0027\"()/+-]", " ", t)
t = re.sub(r"[ \t]+", " ", t)                       # collapse whitespace (incl. tabs)
t = re.sub(r"\s*\.\s*\.\s*", ". ", t)               # collapse ". ." runs
t = re.sub(r"(,\s*){2,}", ", ", t)                  # collapse ", ," runs
t = re.sub(r"\s+([.,!?;:])", r"\1", t)              # no space before punctuation
t = re.sub(r"([.,!?;:])(?=[A-Za-z])", r"\1 ", t)    # space after punct if glued
t = t.strip(" ,.")
if len(t) < 2:
    sys.stderr.write("say.sh: nothing speakable after cleaning\n"); sys.exit(3)
print(t)
')"

# Robust generation: Kokoro can crash (shape-broadcast) or silently truncate on
# long inputs and on certain acronyms (gRPC, HTTP). So: expand acronyms, split
# into sentence-bounded chunks (<=360 chars), synth each, retry-split any chunk
# that fails, then concat. This is what makes long heartbeat narrations reliable.
MODEL="$MODEL" VOICE="$VOICE" SPEED="$SPEED" OUT="$OUT" python3 - "$CLEAN" << 'PYEOF' >&2 || { echo "say.sh: generation failed" >&2; exit 1; }
import sys, os, re, subprocess, tempfile
text = sys.argv[1]
MODEL=os.environ["MODEL"]; VOICE=os.environ["VOICE"]; SPEED=os.environ["SPEED"]; OUT=os.environ["OUT"]
# Expand acronyms Kokoro mangles / crashes on, into speakable letter runs.
for a,b in {"gRPC":"gee-arr-pee-see","HTTP":"H-T-T-P","HTTPS":"H-T-T-P-S",
            "gRPCs":"gee-arr-pee-sees"}.items():
    text=re.sub(r"\b"+re.escape(a)+r"\b", b, text)
sents=re.split(r'(?<=[.!?])\s+', text)
chunks=[]; cur=""
for s in sents:
    if len(cur)+len(s)+1>360 and cur: chunks.append(cur); cur=s
    else: cur=(cur+" "+s).strip()
if cur: chunks.append(cur)
work=tempfile.mkdtemp(prefix="say_")
FMT=OUT.rsplit(".",1)[-1] if "." in os.path.basename(OUT) else "wav"
def synth(t,pfx):
    try:
        subprocess.run([sys.executable,"-m","mlx_audio.tts.generate","--model",MODEL,
            "--voice",VOICE,"--speed",SPEED,"--file_prefix",pfx,"--audio_format",FMT,"--text",t],
            cwd=work,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120)
        g=os.path.join(work,f"{pfx}_000.{FMT}"); return g if os.path.exists(g) else None
    except Exception: return None
def synth_retry(t,pfx,depth=0):
    g=synth(t,pfx)
    if g: return [g]
    if depth>=3 or len(t)<40:
        sys.stderr.write(f"say.sh: dropped fragment ({len(t)}c)\n"); return []
    mid=len(t)//2; cut=t.rfind(", ",0,mid+80)
    if cut<40: cut=t.rfind(" ",0,mid+80)
    if cut<40: cut=mid
    return synth_retry(t[:cut].strip(" ,"),pfx+"a",depth+1)+synth_retry(t[cut:].strip(" ,"),pfx+"b",depth+1)
parts=[]
for i,ch in enumerate(chunks): parts+=synth_retry(ch,f"p{i:03d}")
if not parts: sys.exit(1)
if len(parts)==1:
    subprocess.run(["ffmpeg","-y","-i",parts[0],"-ar","24000","-ac","1",OUT],check=True,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
else:
    lst=os.path.join(work,"list.txt"); open(lst,"w").write("\n".join(f"file '{p}'" for p in parts))
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",lst,"-ar","24000","-ac","1",OUT],
        check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
sys.stderr.write(f"say.sh: {len(chunks)} chunks, {len(parts)} parts -> {OUT}\n")
PYEOF

if [ ! -f "$OUT" ]; then
  echo "say.sh: generation failed (no output file)" >&2
  exit 1
fi

# Print ONLY the final path on stdout (so callers can capture it).
echo "$OUT"
