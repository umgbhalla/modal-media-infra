# Modal STT bench — 2026-06-21
Sample: 929.5s (15.5 min) wav, 16k mono.
| engine | gpu | load_s | infer_s | RTF | chars |
faster-whisper large-v3-turbo | L4 | 2.07 | 24.17 | 0.026 | 13924
faster-whisper large-v3-turbo | T4 | 3.83 | 29.14 | 0.0314 | 13588
parakeet-tdt-0.6b-v3 | L4 | FAILED: NeMo 2.0.0 vs pytorch_lightning NeptuneLogger import (pin lightning to fix)

Cost (billed/sec): T4 $0.59/hr, L4 $0.80/hr.
=> T4: RTF 0.0314 -> ~113 GPU-s per audio-hr -> ~$0.0185 / hour of audio (~$0.0003 per 1-min note)
=> L4: RTF 0.026  -> ~94 GPU-s per audio-hr  -> ~$0.021 / hour of audio
Verdict: T4 + faster-whisper large-v3-turbo = cheapest & fast enough. Output text accurate.
