#!/usr/bin/env bash
# understand.sh — thin wrapper over `zmedia omni` (Modal Qwen2.5-Omni + dedup + ETA).
# Preserves the original interface.
#
# Usage: understand.sh <file-or-url> [--prompt "question"] [--json] [--force]
set -euo pipefail
ZMEDIA="${ZMEDIA:-$HOME/.local/bin/zmedia}"
[[ -x "$ZMEDIA" ]] || { echo "error: zmedia not installed ($ZMEDIA)" >&2; exit 3; }

IN=""; PROMPT=""; JSON=""; FORCE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT="$2"; shift 2 ;;
    --json) JSON="--json"; shift ;;
    --force) FORCE="--force"; shift ;;
    -h|--help) "$ZMEDIA" omni -h 2>/dev/null || true; exit 0 ;;
    *) IN="$1"; shift ;;
  esac
done
[[ -z "$IN" ]] && { echo "error: no input file/url" >&2; exit 2; }

ARGS=(omni "$IN")
[[ -n "$PROMPT" ]] && ARGS+=(--prompt "$PROMPT")
[[ -n "$JSON" ]] && ARGS+=("$JSON")
[[ -n "$FORCE" ]] && ARGS+=("$FORCE")
"$ZMEDIA" "${ARGS[@]}"
