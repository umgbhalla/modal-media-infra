#!/usr/bin/env bash
# install.sh — set up the zmedia CLI + the three OpenClaw skills locally.
set -euo pipefail
mkdir -p "$HOME/.local/bin"
cp client/media_cli.py "$HOME/.local/bin/zmedia" && chmod +x "$HOME/.local/bin/zmedia"
echo "installed: ~/.local/bin/zmedia"
mkdir -p "$HOME/.agents/skills"
cp -R skills/parakeet skills/mlx-audio skills/media-understand "$HOME/.agents/skills/"
chmod +x "$HOME/.agents/skills"/*/scripts/*.sh 2>/dev/null || true
echo "installed skills: parakeet, mlx-audio, media-understand"
echo
echo "Next: set these in ~/.dev.env (see .env.example):"
echo "  STT_MODAL_URL/_TOKEN  TTS_MODAL_URL/_TOKEN  OMNI_MODAL_URL/_TOKEN"
echo "Then: zmedia verify"
