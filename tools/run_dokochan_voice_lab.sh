#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${TMPDIR:-/tmp}"

export DOKOCHAN_VOICE_LAB_PORT="${DOKOCHAN_VOICE_LAB_PORT:-8766}"

exec uv run \
  --no-project \
  --python 3.10 \
  --with fastapi==0.135.4 \
  --with uvicorn==0.48.0 \
  --with mlx-audio==0.4.3 \
  --with soundfile==0.13.1 \
  python "$ROOT/tools/dokochan_voice_lab.py"
