#!/usr/bin/env python3
"""Quantify whether guruguru state differences stay inside eye/mouth ROIs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter


PAIR_KINDS = {
    "B": ("A", "mouth"),
    "C": ("A", "mouth"),
    "D": ("A", "eyes"),
    "E": ("A", "eyes_mouth"),
    "F": ("A", "eyes_mouth"),
}


def load_rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def alpha_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    alpha = np.array(img.getchannel("A"))
    ys, xs = np.nonzero(alpha > 8)
    if len(xs) == 0:
        return (0, 0, img.width, img.height)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def roi_mask(size: tuple[int, int], bbox: tuple[int, int, int, int], kind: str) -> Image.Image:
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if kind in ("eyes", "eyes_mouth"):
        draw.ellipse((
            x0 + int(w * 0.18),
            y0 + int(h * 0.14),
            x1 - int(w * 0.18),
            y0 + int(h * 0.50),
        ), fill=255)
    if kind in ("mouth", "eyes_mouth"):
        draw.ellipse((
            x0 + int(w * 0.30),
            y0 + int(h * 0.43),
            x1 - int(w * 0.30),
            y0 + int(h * 0.68),
        ), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(2.0))


def diff_arr(a: Image.Image, b: Image.Image, threshold: int) -> np.ndarray:
    rgb = np.array(ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L"))
    aa = np.array(ImageChops.lighter(a.getchannel("A"), b.getchannel("A")))
    return (rgb >= threshold) & (aa > 8)


def make_overlay(base: Image.Image, diff: np.ndarray, roi: np.ndarray) -> Image.Image:
    out = base.convert("RGBA")
    red = Image.new("RGBA", base.size, (255, 0, 0, 130))
    green = Image.new("RGBA", base.size, (0, 255, 80, 120))
    outside = Image.fromarray(np.where(diff & ~roi, 255, 0).astype(np.uint8), "L")
    inside = Image.fromarray(np.where(diff & roi, 255, 0).astype(np.uint8), "L")
    out.paste(green, (0, 0), inside)
    out.paste(red, (0, 0), outside)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threshold", type=int, default=18)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = []
    worst = []
    for sheet, (base_sheet, kind) in PAIR_KINDS.items():
        totals = {"diff": 0, "inside": 0, "outside": 0}
        for r in range(5):
            for c in range(5):
                base = load_rgba(args.dir / base_sheet / f"r{r}c{c}.png")
                img = load_rgba(args.dir / sheet / f"r{r}c{c}.png")
                diff = diff_arr(base, img, args.threshold)
                roi = np.array(roi_mask(base.size, alpha_bbox(base), kind)) > 0
                inside = int(np.count_nonzero(diff & roi))
                outside = int(np.count_nonzero(diff & ~roi))
                total = int(np.count_nonzero(diff))
                ratio = outside / total if total else 0.0
                rows.append({
                    "sheet": sheet,
                    "row": r,
                    "col": c,
                    "kind": kind,
                    "diff_pixels": total,
                    "inside_pixels": inside,
                    "outside_pixels": outside,
                    "outside_ratio": ratio,
                })
                totals["diff"] += total
                totals["inside"] += inside
                totals["outside"] += outside
                worst.append((ratio, sheet, r, c, base, diff, roi))
        totals["outside_ratio"] = totals["outside"] / totals["diff"] if totals["diff"] else 0.0
        print(f"{sheet}: diff={totals['diff']} inside={totals['inside']} outside={totals['outside']} outside_ratio={totals['outside_ratio']:.4f}")

    worst.sort(reverse=True, key=lambda x: x[0])
    for idx, (ratio, sheet, r, c, base, diff, roi) in enumerate(worst[:12], start=1):
        overlay = make_overlay(base, diff, roi)
        overlay.save(args.out / f"worst_{idx:02d}_{sheet}_r{r}c{c}_{ratio:.3f}.png")

    with (args.out / "diff_metrics.json").open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
