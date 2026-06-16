#!/usr/bin/env python3
"""Force B-F slice silhouettes to match A after component slicing."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SLICES = ROOT / "public/slices2"


def load(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGBA"))


def save(path: Path, array: np.ndarray) -> None:
    Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGBA").save(path)


def main() -> None:
    changed = 0
    for row in range(5):
        for col in range(5):
            ref_path = SLICES / "A" / f"r{row}c{col}.png"
            ref = load(ref_path)
            ref_alpha = ref[..., 3]
            for sheet in "BCDEF":
                path = SLICES / sheet / f"r{row}c{col}.png"
                image = load(path)
                missing = (ref_alpha > 0) & (image[..., 3] == 0)
                image[missing, :3] = ref[missing, :3]
                image[..., 3] = ref_alpha
                save(path, image)
                changed += 1
    print(f"fixed_alpha_slices={changed}")


if __name__ == "__main__":
    main()
