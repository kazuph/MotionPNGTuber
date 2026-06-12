#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${TMPDIR:-/tmp}"

DOKOCHAN_AUDIO_DEVICE_SPEC="${DOKOCHAN_AUDIO_DEVICE_SPEC:-sd:1}"
DOKOCHAN_RUNTIME_ASSET_DIR="${DOKOCHAN_RUNTIME_ASSET_DIR:-$ROOT/assets/dokochan_vtuber/seedance_layers/composited}"
DOKOCHAN_RUNTIME_MOUTH_DIR="${DOKOCHAN_RUNTIME_MOUTH_DIR:-$ROOT/assets/dokochan_vtuber/mouth}"
export PYTHONUNBUFFERED=1

exec uv run \
  --no-project \
  --python 3.10 \
  --with numpy==1.26.4 \
  --with opencv-python==4.10.0.84 \
  --with sounddevice==0.5.3 \
  python "$ROOT/loop_lipsync_runtime_patched_emotion_auto.py" \
    --loop-video "$DOKOCHAN_RUNTIME_ASSET_DIR/loop_joy_mouthless.mp4" \
    --mouth-dir "$DOKOCHAN_RUNTIME_MOUTH_DIR" \
    --track "$DOKOCHAN_RUNTIME_ASSET_DIR/mouth_track_joy_calibrated.npz" \
    --audio-device-spec "$DOKOCHAN_AUDIO_DEVICE_SPEC" \
    --emotion-video-dir "$DOKOCHAN_RUNTIME_ASSET_DIR" \
    --emotion joy \
    --no-emotion-hud \
    --irodori-tts-ui \
    --irodori-tts-dir "$ROOT/.runtime_logs/irodori" \
    "$@"
