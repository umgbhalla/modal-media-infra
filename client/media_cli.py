#!/usr/bin/env python3
"""
zmedia — unified client for the Modal media endpoints (STT / TTS / OMNI),
built for recurrent/batch runs: auth auto-loaded, SQLite dedup + usage tracking,
time estimation, cold-start warm/sleep helpers, and progress feedback.

Subcommands:
  zmedia verify                         env + health for all three
  zmedia warm [all|stt|tts|omni]        poll /health until ready (cold-start helper)
  zmedia stt  <file...> [--format txt|srt|vtt|json] [--force] [--pace S]
  zmedia tts  <text | -> [--model kokoro|chatterbox] [--voice V] [--out FILE] [--force]
  zmedia omni <file...> [--prompt P] [--json] [--force] [--pace S]
  zmedia stats [--json] [--clear]       usage summary (calls, GPU sec, est cost, cache hits)
  zmedia sleep <seconds>                plain sleep helper

Auth: read from ~/.dev.env (STT|TTS|OMNI_MODAL_URL / _TOKEN). No flags needed.
State: SQLite at ~/.cache/zod-media/zmedia.db
  - cache  : dedup (key = sha256(kind+input+params)); re-run = 'dedup hit', no API call
  - usage  : every call logged (kind, model, infer_sec, est_cost, cache_hit, ts)
"""
import argparse, hashlib, json, os, sqlite3, subprocess, sys, time, pathlib

STATE = pathlib.Path.home() / ".cache" / "zod-media"
STATE.mkdir(parents=True, exist_ok=True)
DB = STATE / "zmedia.db"
LEGACY_LEDGER = STATE / "ledger.json"
SCALEDOWN = 300                      # endpoints scale to zero after ~5 min idle
RTF = {"stt": 0.05, "omni": 0.45, "tts_kokoro": 0.11, "tts_chatterbox": 1.4}
COLD = {"stt": 22, "tts": 16, "omni": 95}            # cold-start load seconds
# Modal $/GPU-second by the GPU each backend runs on (for rough usage cost).
RATE = {"stt": 0.000222, "tts_kokoro": 0.000164, "tts_chatterbox": 0.000222, "omni": 0.000542}


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


# ---------------- SQLite state ----------------
def db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, kind TEXT, "
              "input TEXT, params TEXT, raw TEXT, output TEXT, infer_sec REAL, ts REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "kind TEXT, model TEXT, input TEXT, infer_sec REAL, est_cost REAL, "
              "cache_hit INTEGER, ts REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v REAL)")
    return c


DBC = db()
# one-time import of the legacy JSON ledger
if LEGACY_LEDGER.exists() and not DBC.execute("SELECT 1 FROM cache LIMIT 1").fetchone():
    try:
        for k, v in json.loads(LEGACY_LEDGER.read_text()).items():
            DBC.execute("INSERT OR IGNORE INTO cache VALUES(?,?,?,?,?,?,?,?)",
                        (k, v.get("kind"), v.get("input"), "", v.get("raw"),
                         v.get("output"), v.get("infer_sec"), v.get("ts")))
        DBC.commit(); LEGACY_LEDGER.rename(LEGACY_LEDGER.with_suffix(".json.imported"))
    except Exception:
        pass


def cache_get(key):
    r = DBC.execute("SELECT kind,raw,output,infer_sec FROM cache WHERE key=?", (key,)).fetchone()
    return {"kind": r[0], "raw": r[1], "output": r[2], "infer_sec": r[3]} if r else None


def cache_put(key, kind, inp, params, raw, output, infer_sec):
    DBC.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?,?,?)",
                (key, kind, inp, params, raw, output, infer_sec, time.time())); DBC.commit()


def log_usage(kind, model, inp, infer_sec, hit):
    rate = RATE.get(f"tts_{model}" if kind == "tts" else kind, 0)
    cost = (infer_sec or 0) * rate
    DBC.execute("INSERT INTO usage(kind,model,input,infer_sec,est_cost,cache_hit,ts) "
                "VALUES(?,?,?,?,?,?,?)", (kind, model, inp, infer_sec, cost, int(hit), time.time()))
    DBC.commit()
    return cost


def kv_get(k):
    r = DBC.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone(); return r[0] if r else 0


def kv_put(k, v):
    DBC.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, v)); DBC.commit()


def mark_warm(kind): kv_put(f"warm:{kind}", time.time())
def is_cold(kind): return (time.time() - kv_get(f"warm:{kind}")) > SCALEDOWN


# ---------------- helpers ----------------
def sha_file(path, params=""):
    h = hashlib.sha256(); h.update(params.encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def sha_text(text, params=""):
    return hashlib.sha256((params + "\x00" + text).encode()).hexdigest()


def duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return None


def eta(kind, dur):
    cold = COLD[kind] if is_cold(kind) else 0
    if kind == "stt" and dur: return dur * RTF["stt"] + cold
    if kind == "omni" and dur: return dur * RTF["omni"] + 3 + cold
    return cold or None


def fb(msg): print(f"  {msg}", file=sys.stderr)


def _ts(t, comma=True):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace('.', ',' if comma else '.')


def render_stt(data, fmt):
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2)
    if fmt in ("srt", "vtt"):
        segs = data.get("segments") or []
        out = (["WEBVTT", ""] if fmt == "vtt" else [])
        for i, s in enumerate(segs, 1):
            if fmt == "srt": out.append(str(i))
            out.append(f"{_ts(s['start'], fmt=='srt')} --> {_ts(s['end'], fmt=='srt')}")
            out.append(s["text"].strip()); out.append("")
        return "\n".join(out).strip()
    return data.get("text", "").strip()


def resolve(f):
    if f.startswith("http://") or f.startswith("https://"):
        dst = str(STATE / f"dl_{hashlib.sha256(f.encode()).hexdigest()[:16]}.bin")
        subprocess.run(["curl", "-sL", "--retry", "8", "--retry-all-errors", "-o", dst, f], check=True)
        return dst, True
    return f, False


def curl_post(url, token, fields, out_path=None, timeout=600):
    cmd = ["curl", "-s", "-m", str(timeout), "-X", "POST", url, "-H", f"Authorization: Bearer {token}"]
    for k, v in fields: cmd += ["-F", f"{k}={v}"]
    if out_path: cmd += ["-o", out_path, "-w", "%{http_code}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout, r.returncode


def need(kind):
    if not URLS[kind] or not TOKENS[kind]:
        print(f"error: {kind.upper()}_MODAL_URL/_TOKEN missing in ~/.dev.env", file=sys.stderr); sys.exit(3)


def health(kind):
    out = subprocess.run(["curl", "-s", "-m", "120", f"{URLS[kind]}/health"],
                         capture_output=True, text=True).stdout
    return out, ('"ok":true' in out.replace(" ", ""))


# ---------------- subcommands ----------------
def cmd_verify(a):
    ok = True
    for kind in ("stt", "tts", "omni"):
        tag = f"{kind.upper():4}"
        if not URLS[kind] or not TOKENS[kind]:
            print(f"[{tag}] ✗ env missing"); ok = False; continue
        body, good = health(kind)
        if good: print(f"[{tag}] ✓ healthy  {body.strip()}"); mark_warm(kind)
        else: print(f"[{tag}] ✗ unhealthy: {body[:120]}"); ok = False
    sys.exit(0 if ok else 1)


def cmd_warm(a):
    targets = ["stt", "tts", "omni"] if a.which == "all" else [a.which]
    for kind in targets:
        need(kind); t0 = time.time(); fb(f"warming {kind} (cold≈{COLD[kind]}s) ...")
        while time.time() - t0 < a.timeout:
            _, good = health(kind)
            if good: print(f"[{kind.upper()}] ready in {time.time()-t0:.0f}s"); mark_warm(kind); break
            time.sleep(3)
        else:
            print(f"[{kind.upper()}] NOT ready after {a.timeout}s"); sys.exit(1)


def cmd_stt(a):
    need("stt")
    for i, src in enumerate(a.files):
        f, _ = resolve(src)
        if not os.path.exists(f): fb(f"skip (missing): {src}"); continue
        key = sha_file(f, "stt")
        c = cache_get(key)
        if c and not a.force:
            data = json.loads(c["raw"]); print(render_stt(data, a.format))
            log_usage("stt", "parakeet", f, c["infer_sec"], hit=True)
            fb(f"dedup hit: {os.path.basename(f)} (cached {c['infer_sec']}s)"); continue
        d = duration(f); e = eta("stt", d)
        fb(f"{os.path.basename(f)} dur={d:.0f}s ~ETA {e:.0f}s [{'cold' if is_cold('stt') else 'warm'}]" if d else f"{f} [transcribing]")
        resp, _ = curl_post(f"{URLS['stt']}/transcribe", TOKENS["stt"], [("file", f"@{f}")])
        try: data = json.loads(resp)
        except Exception: fb(f"error: {resp[:160]}"); continue
        cache_put(key, "stt", f, "stt", resp, data.get("text", ""), data.get("infer_sec"))
        mark_warm("stt"); cost = log_usage("stt", "parakeet", f, data.get("infer_sec"), hit=False)
        print(render_stt(data, a.format))
        fb(f"done in {data.get('infer_sec','?')}s ({len(data.get('segments',[]))} segs, ~${cost:.4f})")
        if a.pace and i < len(a.files) - 1: time.sleep(a.pace)


def cmd_tts(a):
    need("tts")
    text = sys.stdin.read() if a.text == "-" else a.text
    if not text.strip(): fb("error: empty text"); sys.exit(2)
    key = sha_text(text, f"tts|{a.model}|{a.voice}")
    out = a.out or str(pathlib.Path.home() / ".openclaw" / "media" / f"say_{int(time.time())}.wav")
    c = cache_get(key)
    if c and not a.force and os.path.exists(c["output"]):
        log_usage("tts", a.model, c["output"], 0, hit=True)
        fb(f"dedup hit -> {c['output']}"); print(c["output"]); return
    rtf = RTF.get(f"tts_{a.model}", 0.2)
    est = max(1, len(text) / 15) * rtf + (COLD["tts"] if is_cold("tts") else 0)
    fb(f"{a.model}: ~{len(text)} chars ~ETA {est:.0f}s [{'cold' if is_cold('tts') else 'warm'}] -> {out}")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t0 = time.time()
    code, _ = curl_post(f"{URLS['tts']}/tts", TOKENS["tts"],
                        [("text", text), ("model", a.model), ("voice", a.voice)], out_path=out)
    if code.strip() != "200": fb(f"error HTTP {code}"); sys.exit(5)
    gen = time.time() - t0
    cache_put(key, "tts", out, f"tts|{a.model}", "", out, gen)
    mark_warm("tts"); cost = log_usage("tts", a.model, out, gen, hit=False)
    fb(f"done (~${cost:.4f})"); print(out)


def cmd_omni(a):
    need("omni")
    for i, src in enumerate(a.files):
        f, _ = resolve(src)
        if not os.path.exists(f): fb(f"skip (missing): {src}"); continue
        key = sha_file(f, f"omni|{a.prompt}")
        c = cache_get(key)
        if c and not a.force:
            print(c["raw"] if a.json else c["output"]); log_usage("omni", "qwen-omni", f, c["infer_sec"], hit=True)
            fb(f"dedup hit: {os.path.basename(f)}"); continue
        d = duration(f); e = eta("omni", d)
        fb(f"{os.path.basename(f)} dur={d:.0f}s ~ETA {e:.0f}s [{'cold' if is_cold('omni') else 'warm'}]" if d else f"{f} [understanding]")
        flds = [("file", f"@{f}")] + ([("prompt", a.prompt)] if a.prompt else [])
        resp, _ = curl_post(f"{URLS['omni']}/understand", TOKENS["omni"], flds)
        try: data = json.loads(resp)
        except Exception: fb(f"error: {resp[:160]}"); continue
        cache_put(key, "omni", f, f"omni|{a.prompt}", resp, data.get("output", ""), data.get("gen_sec"))
        mark_warm("omni"); cost = log_usage("omni", "qwen-omni", f, data.get("gen_sec"), hit=False)
        print(resp if a.json else data.get("output", ""))
        fb(f"done in {data.get('gen_sec','?')}s ({data.get('modality')}, vram {data.get('vram_gb')}GB, ~${cost:.4f})")
        if a.pace and i < len(a.files) - 1: time.sleep(a.pace)


def cmd_stats(a):
    if a.clear:
        DBC.execute("DELETE FROM cache"); DBC.execute("DELETE FROM usage"); DBC.commit()
        print("cleared cache + usage"); return
    tot = DBC.execute("SELECT COUNT(*), COALESCE(SUM(infer_sec),0), COALESCE(SUM(est_cost),0), "
                      "COALESCE(SUM(cache_hit),0) FROM usage").fetchone()
    by_kind = DBC.execute("SELECT kind, COUNT(*), COALESCE(SUM(est_cost),0) FROM usage GROUP BY kind").fetchall()
    by_model = DBC.execute("SELECT model, COUNT(*) FROM usage GROUP BY model").fetchall()
    cached = DBC.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    calls, secs, cost, hits = tot
    hr = (hits / calls * 100) if calls else 0
    if a.json:
        print(json.dumps({"db": str(DB), "calls": calls, "gpu_sec": round(secs, 2),
                          "est_cost_usd": round(cost, 4), "cache_hits": hits,
                          "cache_hit_rate_pct": round(hr, 1), "cached_items": cached,
                          "by_kind": {k: {"calls": n, "cost": round(c, 4)} for k, n, c in by_kind},
                          "by_model": {m: n for m, n in by_model}}, indent=2))
        return
    print(f"db: {DB}")
    print(f"calls: {calls}  |  cache hits: {hits} ({hr:.0f}%)  |  cached items: {cached}")
    print(f"GPU compute billed-ish: {secs:.1f}s  |  est cost: ${cost:.4f}")
    if by_kind: print("by kind:  " + "  ".join(f"{k}={n}(${c:.4f})" for k, n, c in by_kind))
    if by_model: print("by model: " + "  ".join(f"{m}={n}" for m, n in by_model))


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
    o.add_argument("--json", action="store_true"); o.add_argument("--force", action="store_true")
    o.add_argument("--pace", type=float, default=0); o.set_defaults(fn=cmd_omni)
    st = sub.add_parser("stats"); st.add_argument("--json", action="store_true"); st.add_argument("--clear", action="store_true"); st.set_defaults(fn=cmd_stats)
    sl = sub.add_parser("sleep"); sl.add_argument("seconds", type=float); sl.set_defaults(fn=cmd_sleep)
    a = p.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
