#!/usr/bin/env python3
"""Export Dokochan npz mouth tracks to JSON for the Swift runtime."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


REPO = Path(__file__).resolve().parents[1]
BASE = REPO / "assets" / "dokochan_vtuber" / "seedance_layers" / "composited"
EMOTIONS = ("joy", "anger", "sad", "surprise")


def export_one(emotion: str) -> Path:
    src = BASE / f"mouth_track_{emotion}_calibrated.npz"
    dst = BASE / f"mouth_track_{emotion}_calibrated.json"
    data = np.load(src, allow_pickle=False)
    payload = {
        "emotion": emotion,
        "fps": float(data["fps"]),
        "width": int(data["w"]),
        "height": int(data["h"]),
        "valid": np.asarray(data["valid"], dtype=np.uint8).astype(int).tolist(),
        "quad": np.asarray(data["quad"], dtype=np.float32).round(3).tolist(),
    }
    dst.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return dst


def main() -> int:
    for emotion in EMOTIONS:
        print(export_one(emotion))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
