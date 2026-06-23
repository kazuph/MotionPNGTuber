#!/usr/bin/env python3
"""Compose guruguru mouth/blink states from a fixed A slice set.

This is a Photoshop/GIMP-style replacement workflow:
- keep A (eyes open, mouth closed) as the base for every output state
- borrow only mouth pixels from B/C
- borrow only eye pixels from D
- compose E/F from A + D eyes + B/C mouth

The goal is to avoid whole-frame identity/position drift between states.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter


SHEETS = ("A", "B", "C", "D", "E", "F")


def load_rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def alpha_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    alpha = np.array(img.getchannel("A"))
    ys, xs = np.nonzero(alpha > 8)
    if len(xs) == 0:
        return (0, 0, img.width, img.height)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def center_of_bbox(box: tuple[int, int, int, int]) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def align_to_base(base: Image.Image, src: Image.Image) -> Image.Image:
    bx, by = center_of_bbox(alpha_bbox(base))
    sx, sy = center_of_bbox(alpha_bbox(src))
    dx = int(round(bx - sx))
    dy = int(round(by - sy))
    out = Image.new("RGBA", base.size, (0, 0, 0, 0))
    out.alpha_composite(src, (dx, dy))
    return out


def elliptical_roi(size: tuple[int, int], bbox: tuple[int, int, int, int], kind: str) -> Image.Image:
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if kind == "eyes":
        roi = (
            x0 + int(w * 0.12),
            y0 + int(h * 0.10),
            x1 - int(w * 0.12),
            y0 + int(h * 0.58),
        )
    elif kind == "mouth":
        roi = (
            x0 + int(w * 0.20),
            y0 + int(h * 0.34),
            x1 - int(w * 0.20),
            y0 + int(h * 0.76),
        )
    else:
        raise ValueError(f"unknown roi kind: {kind}")
    draw.ellipse(roi, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(2.0))


def diff_mask(base: Image.Image, src: Image.Image, roi: Image.Image, threshold: int) -> Image.Image:
    diff = ImageChops.difference(base.convert("RGB"), src.convert("RGB")).convert("L")
    alpha = ImageChops.lighter(base.getchannel("A"), src.getchannel("A"))
    raw = np.array(diff)
    alpha_arr = np.array(alpha)
    roi_arr = np.array(roi)
    mask_arr = np.where((raw >= threshold) & (alpha_arr > 16) & (roi_arr > 0), 255, 0).astype(np.uint8)
    mask = Image.fromarray(mask_arr, "L")
    mask = mask.filter(ImageFilter.MaxFilter(9))
    mask = mask.filter(ImageFilter.GaussianBlur(3.0))
    mask = ImageChops.multiply(mask, roi)
    return mask


def largest_components(mask_arr: np.ndarray, keep: int, min_area: int = 20) -> np.ndarray:
    h, w = mask_arr.shape
    seen = np.zeros((h, w), dtype=bool)
    comps: list[tuple[int, list[tuple[int, int]]]] = []
    ys, xs = np.nonzero(mask_arr)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if seen[sy, sx]:
            continue
        stack = [(sy, sx)]
        seen[sy, sx] = True
        pts: list[tuple[int, int]] = []
        while stack:
            y, x = stack.pop()
            pts.append((y, x))
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if ny < 0 or ny >= h or nx < 0 or nx >= w or seen[ny, nx] or not mask_arr[ny, nx]:
                        continue
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if len(pts) >= min_area:
            comps.append((len(pts), pts))
    comps.sort(reverse=True, key=lambda item: item[0])
    out = np.zeros((h, w), dtype=np.uint8)
    for _, pts in comps[:keep]:
        for y, x in pts:
            out[y, x] = 255
    return out


def semantic_part_mask(base: Image.Image, src: Image.Image, kind: str) -> Image.Image:
    bbox = alpha_bbox(base)
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0
    rgb = np.array(src.convert("RGB")).astype(np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = (r + g + b) / 3
    alpha = np.array(src.getchannel("A"))
    yy, xx = np.indices(alpha.shape)
    if kind == "mouth":
        roi = (
            (xx >= x0 + int(w * 0.28)) & (xx <= x1 - int(w * 0.28)) &
            (yy >= y0 + int(h * 0.42)) & (yy <= y0 + int(h * 0.68))
        )
        red_or_dark = ((r > g + 18) & (r > b + 10) & (lum < 190)) | (lum < 82)
        arr = red_or_dark & roi & (alpha > 20)
        arr = largest_components(arr, keep=2, min_area=10)
        mask = Image.fromarray(arr, "L").filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
        return mask
    if kind == "eyes":
        roi = (
            (xx >= x0 + int(w * 0.12)) & (xx <= x1 - int(w * 0.12)) &
            (yy >= y0 + int(h * 0.20)) & (yy <= y0 + int(h * 0.50))
        )
        dark = (lum < 105) & (alpha > 20) & roi
        arr = largest_components(dark, keep=8, min_area=15)
        mask = Image.fromarray(arr, "L").filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1.2))
        return mask
    raise ValueError(f"unknown semantic mask kind: {kind}")


def composite_part(base: Image.Image, src: Image.Image, kind: str, threshold: int) -> Image.Image:
    aligned = align_to_base(base, src)
    mask = semantic_part_mask(base, aligned, kind)
    if not np.any(np.array(mask) > 0):
        roi = elliptical_roi(base.size, alpha_bbox(base), kind)
        mask = diff_mask(base, aligned, roi, threshold)
    out = base.copy()
    out.paste(aligned, (0, 0), mask)
    return out


def make_contact(out_dir: Path, contact_path: Path) -> None:
    cell = 220
    label_h = 34
    gap = 12
    sheet_w = 5 * cell
    sheet_h = label_h + 5 * cell
    canvas = Image.new("RGB", (3 * sheet_w + 2 * gap, 2 * sheet_h + gap), (32, 32, 34))
    draw = ImageDraw.Draw(canvas)
    labels = {
        "A": "A open/closed base",
        "B": "B A + mouth half",
        "C": "C A + mouth open",
        "D": "D A + closed eyes",
        "E": "E A + closed eyes + half mouth",
        "F": "F A + closed eyes + open mouth",
    }
    for idx, sheet in enumerate(SHEETS):
        block_x = (idx % 3) * (sheet_w + gap)
        block_y = (idx // 3) * (sheet_h + gap)
        draw.text((block_x + 8, block_y + 8), labels[sheet], fill=(240, 236, 230))
        for r in range(5):
            for c in range(5):
                img = load_rgba(out_dir / sheet / f"r{r}c{c}.png")
                thumb = Image.new("RGBA", (cell, cell), (255, 248, 238, 255))
                img.thumbnail((cell, cell), Image.Resampling.LANCZOS)
                thumb.alpha_composite(img, ((cell - img.width) // 2, (cell - img.height) // 2))
                canvas.paste(thumb.convert("RGB"), (block_x + c * cell, block_y + label_h + r * cell))
    contact_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(contact_path, quality=92)


def write_sheet(out_dir: Path, sheet: str, images: list[Image.Image]) -> None:
    target = out_dir / sheet
    target.mkdir(parents=True, exist_ok=True)
    for idx, img in enumerate(images):
        r, c = divmod(idx, 5)
        img.save(target / f"r{r}c{c}.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mouth-threshold", type=int, default=18)
    parser.add_argument("--eye-threshold", type=int, default=18)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, list[Image.Image]] = {sheet: [] for sheet in SHEETS}

    for r in range(5):
        for c in range(5):
            base = load_rgba(args.src / "A" / f"r{r}c{c}.png")
            half_src = load_rgba(args.src / "B" / f"r{r}c{c}.png")
            open_src = load_rgba(args.src / "C" / f"r{r}c{c}.png")
            closed_src = load_rgba(args.src / "D" / f"r{r}c{c}.png")

            b = composite_part(base, half_src, "mouth", args.mouth_threshold)
            c_img = composite_part(base, open_src, "mouth", args.mouth_threshold)
            d = composite_part(base, closed_src, "eyes", args.eye_threshold)
            e = composite_part(d, half_src, "mouth", args.mouth_threshold)
            f = composite_part(d, open_src, "mouth", args.mouth_threshold)

            outputs["A"].append(base)
            outputs["B"].append(b)
            outputs["C"].append(c_img)
            outputs["D"].append(d)
            outputs["E"].append(e)
            outputs["F"].append(f)

    for sheet, images in outputs.items():
        write_sheet(args.out, sheet, images)

    make_contact(args.out, args.out.parent / "verification" / "composited_parts_contact.jpg")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
