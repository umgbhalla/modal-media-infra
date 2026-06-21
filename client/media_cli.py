#!/usr/bin/env python3
"""
zmedia — unified client for the Modal media endpoints (STT / TTS / OMNI),
built for recurrent/batch runs: auth auto-loaded, dedup ledger, time estimation,
cold-start warm/sleep helpers, and progress feedback.

Subcommands:
  zmedia verify                         env + health for all three
  zmedia warm [all|stt|tts|omni]        poll /health until ready (cold-start helper)
  zmedia stt  <file...> [--format txt|srt|vtt|json] [--out-dir DIR] [--force] [--pace S]
  zmedia tts  <text | -> [--model kokoro|chatterbox] [--out FILE] [--force]
  zmedia omni <file...> [--prompt P] [--force] [--pace S]
  zmedia stats                          dedup ledger summary
  zmedia sleep <seconds>                plain sleep helper

Auth: read from ~/.dev.env (STT|TTS|OMNI_MODAL_URL / _TOKEN). No flags needed.
Dedup: ~/.cache/zod-media/ledger.json keyed by sha256(kind+input+params);
re-running the same input is a no-op (prints 'dedup hit') unless --force.
"""
import argparse, hashlib, json, os, subprocess, sys, time, pathlib

STATE = pathlib.Path.home() / ".cache" / "zod-media"
STATE.mkdir(parents=True, exist_ok=True)
LEDGER = STATE / "ledger.json"
WARM = STATE / "warm.json"           # last-success ts per endpoint (cold detection)
SCALEDOWN = 300                      # endpoints scale to zero after ~5 min idle
# rough RTF / cost model from benchmarks (gen_sec ≈ audio_sec * factor)
RTF = {"stt": 0.05, "omni": 0.45, "tts_kokoro": 0.11, "tts_chatterbox": 1.4}
COLD = {"stt": 22, "tts": 16, "omni": 95}   # cold-start load seconds


def load_env():
    env = os.environ.copy()
    p = pathlib.Path.home() / ".dev.env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


ENV = load_env()
URLS = {"stt": ENV.get("STT_MODAL_URL", ""), "tts": ENV.get("TTS_MODAL_URL", ""),
        "omni": ENV.get("OMNI_MODAL_URL", "")}
TOKENS = {"stt": ENV.get("STT_MODAL_TOKEN", ""), "tts": ENV.get("TTS_MODAL_TOKEN", ""),
          "omni": ENV.get("OMNI_MODAL_TOKEN", "")}


def _jload(p, default):
    try: return json.loads(p.read_text())
    except Exception: return default


def ledger(): return _jload(LEDGER, {})
def save_ledger(d): LEDGER.write_text(json.dumps(d, indent=2))
def warmstate(): return _jload(WARM, {})


def mark_warm(kind):
    w = warmstate(); w[kind] = time.time(); WARM.write_text(json.dumps(w))


def is_cold(kind):
    w = warmstate(); return (time.time() - w.get(kind, 0)) > SCALEDOWN


def sha_file(path, params=""):
    h = hashlib.sha256(); h.update(params.encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def sha_text(text, params=""):
    return hashlib.sha256((params + "\x00" + text).encode()).hexdigest()


def duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return None


def eta(kind, dur, model=None):
    cold = COLD[kind] if is_cold(kind) else 0
    if kind == "stt" and dur: return dur * RTF["stt"] + cold
    if kind == "omni" and dur: return dur * RTF["omni"] + 3 + cold
    if kind == "tts": return None  # handled by caller (text-based)
    return cold or None


def fb(msg): print(f"  {msg}", file=sys.stderr)


def curl_post(url, token, fields, out_path=None, timeout=600):
    cmd = ["curl", "-s", "-m", str(timeout), "-X", "POST", url,
           "-H", f"Authorization: Bearer {token}"]
    for k, v in fields:
        cmd += ["-F", f"{k}={v}"]
    if out_path:
        cmd += ["-o", out_path, "-w", "%{http_code}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout, r.returncode


def need(kind):
    if not URLS[kind] or not TOKENS[kind]:
        print(f"error: {kind.upper()}_MODAL_URL/_TOKEN missing in ~/.dev.env", file=sys.stderr)
        sys.exit(3)


# ---------------- subcommands ----------------
def cmd_verify(a):
    ok = True
    for kind in ("stt", "tts", "omni"):
        u, t = URLS[kind], TOKENS[kind]
        tag = f"{kind.upper():4}"
        if not u or not t:
            print(f"[{tag}] ✗ env missing"); ok = False; continue
        out, _ = subprocess.run(["curl", "-s", "-m", "120", f"{u}/health"],
                                capture_output=True, text=True), None
        body = out.stdout if hasattr(out, "stdout") else ""
        if '"ok":true' in body.replace(" ", ""):
            print(f"[{tag}] ✓ healthy  {body.strip()}"); mark_warm(kind)
        else:
            print(f"[{tag}] ✗ unhealthy: {body[:120]}"); ok = False
    sys.exit(0 if ok else 1)


def cmd_warm(a):
    targets = ["stt", "tts", "omni"] if a.which == "all" else [a.which]
    for kind in targets:
        need(kind)
        t0 = time.time()
        fb(f"warming {kind} (cold≈{COLD[kind]}s) ...")
        while time.time() - t0 < a.timeout:
            out = subprocess.run(["curl", "-s", "-m", "120", f"{URLS[kind]}/health"],
                                 capture_output=True, text=True).stdout
            if '"ok":true' in out.replace(" ", ""):
                print(f"[{kind.upper()}] ready in {time.time()-t0:.0f}s"); mark_warm(kind); break
            time.sleep(3)
        else:
            print(f"[{kind.upper()}] NOT ready after {a.timeout}s"); sys.exit(1)


def cmd_stt(a):
    need("stt"); led = ledger()
    for i, f in enumerate(a.files):
        if not os.path.exists(f): fb(f"skip (missing): {f}"); continue
        params = f"stt|{a.format}"
        key = sha_file(f, params)
        if key in led and not a.force:
            print(led[key]["text"] if a.format == "txt" else led[key].get("raw", led[key]["text"]))
            fb(f"dedup hit: {os.path.basename(f)} (cached {led[key].get('infer_sec','?')}s)")
            continue
        d = duration(f); e = eta("stt", d)
        fb(f"{os.path.basename(f)} dur={d:.0f}s ~ETA {e:.0f}s [{'cold' if is_cold('stt') else 'warm'}]" if d else f"{f} [transcribing]")
        resp, rc = curl_post(f"{URLS['stt']}/transcribe", TOKENS["stt"], [("file", f"@{f}")])
        try: data = json.loads(resp)
        except Exception: fb(f"error: {resp[:160]}"); continue
        text = data.get("text", "")
        led[key] = {"kind": "stt", "input": f, "text": text, "raw": resp,
                    "infer_sec": data.get("infer_sec"), "ts": time.time()}
        save_ledger(led); mark_warm("stt")
        print(text if a.format == "txt" else resp)
        fb(f"done in {data.get('infer_sec','?')}s ({len(data.get('segments',[]))} segs)")
        if a.pace and i < len(a.files) - 1: time.sleep(a.pace)


def cmd_tts(a):
    need("tts"); led = ledger()
    text = sys.stdin.read() if a.text == "-" else a.text
    if not text.strip(): fb("error: empty text"); sys.exit(2)
    params = f"tts|{a.model}|{a.voice}"
    key = sha_text(text, params)
    out = a.out or str(pathlib.Path.home() / ".openclaw" / "media" / f"say_{int(time.time())}.wav")
    if key in led and not a.force and os.path.exists(led[key]["output"]):
        fb(f"dedup hit -> {led[key]['output']}"); print(led[key]["output"]); return
    rtf = RTF.get(f"tts_{a.model}", 0.2); est = max(1, len(text)/15) * rtf + (COLD["tts"] if is_cold("tts") else 0)
    fb(f"{a.model}: ~{len(text)} chars ~ETA {est:.0f}s [{'cold' if is_cold('tts') else 'warm'}] -> {out}")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    code, rc = curl_post(f"{URLS['tts']}/tts", TOKENS["tts"],
                         [("text", text), ("model", a.model), ("voice", a.voice)],
                         out_path=out)
    if code.strip() != "200": fb(f"error HTTP {code}"); sys.exit(5)
    led[key] = {"kind": "tts", "model": a.model, "output": out, "ts": time.time()}
    save_ledger(led); mark_warm("tts"); print(out)


def cmd_omni(a):
    need("omni"); led = ledger()
    for i, f in enumerate(a.files):
        if not os.path.exists(f): fb(f"skip (missing): {f}"); continue
        params = f"omni|{a.prompt}"
        key = sha_file(f, params)
        if key in led and not a.force:
            print(led[key]["output"]); fb(f"dedup hit: {os.path.basename(f)}"); continue
        d = duration(f); e = eta("omni", d)
        fb(f"{os.path.basename(f)} dur={d:.0f}s ~ETA {e:.0f}s [{'cold' if is_cold('omni') else 'warm'}]" if d else f"{f} [understanding]")
        flds = [("file", f"@{f}")] + ([("prompt", a.prompt)] if a.prompt else [])
        resp, rc = curl_post(f"{URLS['omni']}/understand", TOKENS["omni"], flds)
        try: data = json.loads(resp)
        except Exception: fb(f"error: {resp[:160]}"); continue
        led[key] = {"kind": "omni", "input": f, "output": data.get("output", ""),
                    "gen_sec": data.get("gen_sec"), "ts": time.time()}
        save_ledger(led); mark_warm("omni")
        print(data.get("output", ""))
        fb(f"done in {data.get('gen_sec','?')}s ({data.get('modality')}, vram {data.get('vram_gb')}GB)")
        if a.pace and i < len(a.files) - 1: time.sleep(a.pace)


def cmd_stats(a):
    led = ledger()
    by = {}
    for v in led.values(): by[v["kind"]] = by.get(v["kind"], 0) + 1
    print(f"ledger: {LEDGER}")
    print(f"entries: {len(led)}  " + "  ".join(f"{k}={n}" for k, n in by.items()))
    if a.clear:
        save_ledger({}); print("ledger cleared")


def cmd_sleep(a): time.sleep(a.seconds)


def main():
    p = argparse.ArgumentParser(prog="zmedia")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    w = sub.add_parser("warm"); w.add_argument("which", nargs="?", default="all",
        choices=["all", "stt", "tts", "omni"]); w.add_argument("--timeout", type=int, default=180); w.set_defaults(fn=cmd_warm)
    s = sub.add_parser("stt"); s.add_argument("files", nargs="+"); s.add_argument("--format", default="txt")
    s.add_argument("--force", action="store_true"); s.add_argument("--pace", type=float, default=0); s.set_defaults(fn=cmd_stt)
    t = sub.add_parser("tts"); t.add_argument("text"); t.add_argument("--model", default="kokoro", choices=["kokoro", "chatterbox"])
    t.add_argument("--voice", default="af_heart"); t.add_argument("--out"); t.add_argument("--force", action="store_true"); t.set_defaults(fn=cmd_tts)
    o = sub.add_parser("omni"); o.add_argument("files", nargs="+"); o.add_argument("--prompt", default="")
    o.add_argument("--force", action="store_true"); o.add_argument("--pace", type=float, default=0); o.set_defaults(fn=cmd_omni)
    st = sub.add_parser("stats"); st.add_argument("--clear", action="store_true"); st.set_defaults(fn=cmd_stats)
    sl = sub.add_parser("sleep"); sl.add_argument("seconds", type=float); sl.set_defaults(fn=cmd_sleep)
    a = p.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
