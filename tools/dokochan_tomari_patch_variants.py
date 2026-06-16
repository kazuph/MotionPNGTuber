#!/usr/bin/env python3
"""Make tomari-guruguru A-F sheets with invariant body/head scale.

Image generation is good at creating the direction sheet, but separate A-F
generations can drift in scale. This script follows the quality note from
tomari-guruguru's replacement guide: keep one base sheet and combine only the
eye/mouth regions from generated variants.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "assets/dokochan_vtuber/tomari_guruguru"
DEFAULT_SOURCE = ROOT / "sheets_alpha"
DEFAULT_OUT = ROOT / "sheets_patched"


def load_rgba(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGBA"))


def save_rgba(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(image, 0, 255).astype(np.uint8), "RGBA").save(path)


def cell_bounds(width: int, height: int, row: int, col: int) -> tuple[int, int, int, int]:
    x0 = round(width * col / 5)
    x1 = round(width * (col + 1) / 5)
    y0 = round(height * row / 5)
    y1 = round(height * (row + 1) / 5)
    return x0, y0, x1, y1


def alpha_bbox(image: np.ndarray, threshold: int = 24) -> tuple[int, int, int, int]:
    ys, xs = np.where(image[..., 3] > threshold)
    if xs.size == 0:
        h, w = image.shape[:2]
        return 0, 0, w, h
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def align_to_base(source: np.ndarray, base: np.ndarray) -> np.ndarray:
    bx0, by0, bx1, by1 = alpha_bbox(base)
    sx0, sy0, sx1, sy1 = alpha_bbox(source)
    bw = max(1, bx1 - bx0)
    bh = max(1, by1 - by0)
    sw = max(1, sx1 - sx0)
    sh = max(1, sy1 - sy0)
    scale_x = bw / sw
    scale_y = bh / sh
    matrix = np.float32(
        [
            [scale_x, 0, bx0 - scale_x * sx0],
            [0, scale_y, by0 - scale_y * sy0],
        ]
    )
    h, w = base.shape[:2]
    return cv2.warpAffine(
        source,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )


def feathered_rect(shape: tuple[int, int], bbox: tuple[int, int, int, int], feather: int) -> np.ndarray:
    h, w = shape
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    mask = np.zeros((h, w), np.float32)
    if x1 <= x0 or y1 <= y0:
        return mask
    mask[y0:y1, x0:x1] = 1.0
    if feather > 0:
        k = feather * 2 + 1
        mask = cv2.GaussianBlur(mask, (k, k), 0)
    return np.clip(mask, 0.0, 1.0)


def blend_patch(base: np.ndarray, source: np.ndarray, bbox: tuple[int, int, int, int], feather: int = 4) -> np.ndarray:
    out = base.copy().astype(np.float32)
    mask = feathered_rect(base.shape[:2], bbox, feather)
    mask *= (source[..., 3].astype(np.float32) / 255.0)
    mask *= (base[..., 3].astype(np.float32) / 255.0)
    m = mask[..., None]
    out[..., :3] = source[..., :3].astype(np.float32) * m + out[..., :3] * (1.0 - m)
    # Preserve the base silhouette exactly so A-F never resize or jump.
    out[..., 3] = base[..., 3]
    return np.clip(out, 0, 255).astype(np.uint8)


def filtered_component_mask(mask: np.ndarray, keep) -> np.ndarray:
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    out = np.zeros(mask.shape, np.uint8)
    for idx in range(1, count):
        x, y, w, h, area = stats[idx]
        cx, cy = centroids[idx]
        if keep(int(x), int(y), int(w), int(h), int(area), float(cx), float(cy)):
            out[labels == idx] = 255
    return out


def connected_bboxes(mask: np.ndarray, min_area: int) -> list[tuple[int, int, int, int, int]]:
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    boxes: list[tuple[int, int, int, int, int]] = []
    for idx in range(1, count):
        x, y, w, h, area = stats[idx]
        if area >= min_area:
            boxes.append((int(x), int(y), int(x + w), int(y + h), int(area)))
    return boxes


def mouth_bbox(source: np.ndarray, base: np.ndarray, row: int, col: int) -> tuple[int, int, int, int]:
    bx0, by0, bx1, by1 = alpha_bbox(base)
    bw, bh = bx1 - bx0, by1 - by0
    y0 = int(by0 + bh * 0.34)
    y1 = int(by0 + bh * 0.67)
    region = np.zeros(base.shape[:2], bool)
    region[max(0, y0) : min(base.shape[0], y1), max(0, bx0) : min(base.shape[1], bx1)] = True
    r, g, b, a = [source[..., i] for i in range(4)]
    red_mouth = (
        (a > 40)
        & region
        & (r > 85)
        & (g < 135)
        & (b < 135)
        & (r > g + 12)
        & (r > b + 12)
    )
    boxes = connected_bboxes(red_mouth, min_area=3)
    if boxes:
        # Prefer the largest red mouth component inside the lower face.
        x0, y0, x1, y1, _area = max(boxes, key=lambda box: box[4])
    else:
        # Conservative fallback, used only if a generated mouth is too pale.
        expected_x = bx0 + bw * (0.24 + 0.13 * col)
        expected_y = by0 + bh * (0.49 + 0.02 * (row - 2))
        x0, y0, x1, y1 = (
            int(expected_x - bw * 0.08),
            int(expected_y - bh * 0.045),
            int(expected_x + bw * 0.08),
            int(expected_y + bh * 0.06),
        )
    pad_x = max(5, int(bw * 0.035))
    pad_y = max(4, int(bh * 0.028))
    return x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y


def mouth_mask(source: np.ndarray, base: np.ndarray, row: int, col: int) -> np.ndarray:
    bx0, by0, bx1, by1 = alpha_bbox(base)
    bw, bh = bx1 - bx0, by1 - by0
    expected_x = bx0 + bw * (0.24 + 0.13 * col)
    expected_y = by0 + bh * (0.50 + 0.025 * (row - 2))
    region = np.zeros(base.shape[:2], bool)
    rx0 = int(expected_x - bw * 0.16)
    rx1 = int(expected_x + bw * 0.16)
    ry0 = int(expected_y - bh * 0.11)
    ry1 = int(expected_y + bh * 0.12)
    region[max(0, ry0) : min(base.shape[0], ry1), max(0, rx0) : min(base.shape[1], rx1)] = True

    r, g, b, a = [source[..., i].astype(np.int16) for i in range(4)]
    red = (a > 40) & region & (r > 95) & (g < 145) & (b < 145) & (r > g + 10) & (r > b + 10)
    dark = (a > 40) & region & (r < 95) & (g < 82) & (b < 82)
    mouth_like = red | dark
    h, w = source.shape[:2]

    def keep(_x: int, _y: int, ww: int, hh: int, area: int, cx: float, cy: float) -> bool:
        if area < 3:
            return False
        if ww > w * 0.22 or hh > h * 0.16:
            return False
        dist = abs(cx - expected_x) / max(1.0, bw) + abs(cy - expected_y) / max(1.0, bh)
        return dist < 0.22

    mask = filtered_component_mask(mouth_like, keep)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.dilate(mask, kernel, iterations=1)
    mask[~region] = 0
    return mask


def blend_mouth(base: np.ndarray, source: np.ndarray, row: int, col: int) -> np.ndarray:
    out = base.copy().astype(np.float32)
    mask = mouth_mask(source, base, row, col).astype(np.float32) / 255.0
    if not np.any(mask):
        # Keep the old bbox fallback only for genuinely faint mouths. The mask
        # path is preferred because it cannot copy eyes back into blink frames.
        return blend_patch(base, source, mouth_bbox(source, base, row, col))
    mask = cv2.GaussianBlur(mask, (3, 3), 0)
    mask *= source[..., 3].astype(np.float32) / 255.0
    mask *= base[..., 3].astype(np.float32) / 255.0
    m = mask[..., None]
    out[..., :3] = source[..., :3].astype(np.float32) * m + out[..., :3] * (1.0 - m)
    out[..., 3] = base[..., 3]
    return np.clip(out, 0, 255).astype(np.uint8)


def red_mouth_mask(source: np.ndarray, base: np.ndarray) -> np.ndarray:
    bx0, by0, bx1, by1 = alpha_bbox(base)
    bw, bh = bx1 - bx0, by1 - by0
    region = np.zeros(base.shape[:2], bool)
    x0 = int(bx0 + bw * 0.04)
    x1 = int(bx0 + bw * 0.96)
    y0 = int(by0 + bh * 0.32)
    y1 = int(by0 + bh * 0.80)
    region[max(0, y0) : min(base.shape[0], y1), max(0, x0) : min(base.shape[1], x1)] = True

    r, g, b, a = [source[..., i].astype(np.int16) for i in range(4)]
    red = (
        (a > 40)
        & region
        & (r > 115)
        & (g < 150)
        & (b < 145)
        & (r > g + 18)
        & (r > b + 18)
    )
    boxes = connected_bboxes(red, min_area=5)
    if not boxes:
        return np.zeros(base.shape[:2], np.uint8)

    # Mouth blobs are the largest saturated red component in the lower face.
    x0, y0, x1, y1, _area = max(boxes, key=lambda box: box[4])
    pad_x = max(4, int(bw * 0.025))
    pad_y = max(3, int(bh * 0.020))
    mask = np.zeros(base.shape[:2], np.uint8)
    mask[max(0, y0 - pad_y) : min(base.shape[0], y1 + pad_y), max(0, x0 - pad_x) : min(base.shape[1], x1 + pad_x)] = 255
    mask[~region] = 0
    mask = cv2.GaussianBlur(mask, (3, 3), 0)
    return mask


def blend_masked_rgb(base: np.ndarray, source: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = base.copy().astype(np.float32)
    m = mask.astype(np.float32) / 255.0
    m *= source[..., 3].astype(np.float32) / 255.0
    m *= base[..., 3].astype(np.float32) / 255.0
    out[..., :3] = source[..., :3].astype(np.float32) * m[..., None] + out[..., :3] * (1.0 - m[..., None])
    out[..., 3] = base[..., 3]
    return np.clip(out, 0, 255).astype(np.uint8)


def eye_bboxes_bbox(base: np.ndarray) -> list[tuple[int, int, int, int]]:
    bx0, by0, bx1, by1 = alpha_bbox(base)
    bw, bh = bx1 - bx0, by1 - by0
    region = np.zeros(base.shape[:2], bool)
    y0 = int(by0 + bh * 0.12)
    y1 = int(by0 + bh * 0.52)
    region[max(0, y0) : min(base.shape[0], y1), max(0, bx0) : min(base.shape[1], bx1)] = True
    r, g, b, a = [base[..., i] for i in range(4)]
    blue_eye = (
        (a > 40)
        & region
        & (b > 75)
        & (g > 70)
        & (r < 175)
        & (b > r + 12)
        & (g > r - 5)
    )
    boxes = connected_bboxes(blue_eye, min_area=12)
    if not boxes:
        return [
            (
                int(bx0 + bw * 0.22),
                int(by0 + bh * 0.20),
                int(bx0 + bw * 0.78),
                int(by0 + bh * 0.48),
            )
        ]
    padded: list[tuple[int, int, int, int]] = []
    for x0, y0, x1, y1, _area in boxes:
        pad_x = max(9, int(bw * 0.055))
        pad_y = max(8, int(bh * 0.045))
        padded.append((x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y))
    return padded


def eye_region(base: np.ndarray, row: int) -> np.ndarray:
    bx0, by0, bx1, by1 = alpha_bbox(base)
    bw, bh = bx1 - bx0, by1 - by0
    y0_ratio = 0.28 if row < 3 else 0.34
    y1_ratio = 0.60 if row < 3 else 0.69
    region = np.zeros(base.shape[:2], bool)
    x0 = int(bx0 + bw * 0.07)
    x1 = int(bx0 + bw * 0.93)
    y0 = int(by0 + bh * y0_ratio)
    y1 = int(by0 + bh * y1_ratio)
    region[max(0, y0) : min(base.shape[0], y1), max(0, x0) : min(base.shape[1], x1)] = True
    return region


def open_eye_remove_mask(base: np.ndarray, row: int) -> np.ndarray:
    region = eye_region(base, row)
    r, g, b, a = [base[..., i].astype(np.int16) for i in range(4)]
    blue = (a > 40) & region & (b > 55) & (g > 50) & (r < 190) & (b > r + 3)
    dark = (a > 40) & region & (r < 95) & (g < 88) & (b < 88)
    eye_like = blue | dark
    h, w = base.shape[:2]

    def keep(_x: int, _y: int, ww: int, hh: int, area: int, _cx: float, _cy: float) -> bool:
        if area < 8:
            return False
        if ww > w * 0.42 or hh > h * 0.24:
            return False
        return True

    mask = filtered_component_mask(eye_like, keep)
    kernel_w = max(9, int(w * 0.055))
    kernel_h = max(7, int(h * 0.038))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_w | 1, kernel_h | 1))
    mask = cv2.dilate(mask, kernel, iterations=1)
    mask[~region] = 0
    return mask


def closed_eye_line_mask(source: np.ndarray, row: int) -> np.ndarray:
    region = eye_region(source, row)
    r, g, b, a = [source[..., i].astype(np.int16) for i in range(4)]
    dark = (a > 40) & region & (r < 90) & (g < 82) & (b < 82)
    h, w = source.shape[:2]

    def keep(_x: int, _y: int, ww: int, hh: int, area: int, _cx: float, _cy: float) -> bool:
        if area < 4:
            return False
        if ww < 4 or hh < 1:
            return False
        if ww > w * 0.42 or hh > h * 0.12:
            return False
        return ww >= hh * 1.4

    mask = filtered_component_mask(dark, keep)
    mask[~region] = 0
    return mask


def apply_closed_eyes(base: np.ndarray, closed: np.ndarray, row: int) -> np.ndarray:
    out = base.copy()
    remove = open_eye_remove_mask(base, row)
    if np.any(remove):
        rgb = cv2.cvtColor(out[..., :3], cv2.COLOR_RGB2BGR)
        inpainted = cv2.inpaint(rgb, remove, 3, cv2.INPAINT_TELEA)
        out[..., :3] = cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB)

    lines = closed_eye_line_mask(closed, row)
    if np.any(lines):
        feather = cv2.GaussianBlur(lines.astype(np.float32) / 255.0, (3, 3), 0)
        feather *= (closed[..., 3].astype(np.float32) / 255.0)
        m = feather[..., None]
        out_rgb = out[..., :3].astype(np.float32)
        src_rgb = closed[..., :3].astype(np.float32)
        out[..., :3] = np.clip(src_rgb * m + out_rgb * (1.0 - m), 0, 255).astype(np.uint8)

    out[..., 3] = base[..., 3]
    return out


def transfer_rgb_preserve_alpha(base: np.ndarray, source: np.ndarray) -> np.ndarray:
    out = base.copy().astype(np.float32)
    source = source.astype(np.float32)
    mask = (source[..., 3:4] / 255.0) * (base[..., 3:4].astype(np.float32) / 255.0)
    out[..., :3] = source[..., :3] * mask + out[..., :3] * (1.0 - mask)
    out[..., 3] = base[..., 3]
    return np.clip(out, 0, 255).astype(np.uint8)


def patch_sheet(
    base_sheet: np.ndarray,
    mouth_half_sheet: np.ndarray,
    mouth_open_sheet: np.ndarray,
    eyes_closed_sheet: np.ndarray,
    eyes_closed_half_sheet: np.ndarray,
    eyes_closed_open_sheet: np.ndarray,
    closed_mode: str,
) -> dict[str, np.ndarray]:
    h, w = base_sheet.shape[:2]
    sheets = {
        "A": base_sheet.copy(),
        "B": base_sheet.copy(),
        "C": base_sheet.copy(),
        "D": base_sheet.copy(),
        "E": base_sheet.copy(),
        "F": base_sheet.copy(),
    }
    for row in range(5):
        for col in range(5):
            x0, y0, x1, y1 = cell_bounds(w, h, row, col)
            base = base_sheet[y0:y1, x0:x1]
            half = align_to_base(mouth_half_sheet[y0:y1, x0:x1], base)
            opened = align_to_base(mouth_open_sheet[y0:y1, x0:x1], base)
            closed = align_to_base(eyes_closed_sheet[y0:y1, x0:x1], base)
            closed_half = align_to_base(eyes_closed_half_sheet[y0:y1, x0:x1], base)
            closed_open = align_to_base(eyes_closed_open_sheet[y0:y1, x0:x1], base)

            b_cell = blend_patch(base, half, mouth_bbox(half, base, row, col))
            c_cell = blend_patch(base, opened, mouth_bbox(opened, base, row, col))
            if closed_mode == "hybrid":
                d_cell = transfer_rgb_preserve_alpha(base, closed)
                e_cell = blend_masked_rgb(d_cell, closed_half, red_mouth_mask(closed_half, base))
                f_cell = blend_masked_rgb(d_cell, closed_open, red_mouth_mask(closed_open, base))
            elif closed_mode == "bbox":
                d_cell = base.copy()
                for bbox in eye_bboxes_bbox(base):
                    d_cell = blend_patch(d_cell, closed, bbox, feather=5)
                e_cell = b_cell.copy()
                f_cell = c_cell.copy()
                for bbox in eye_bboxes_bbox(base):
                    e_cell = blend_patch(e_cell, closed, bbox, feather=5)
                    f_cell = blend_patch(f_cell, closed, bbox, feather=5)
            elif closed_mode == "full-rgb":
                d_cell = transfer_rgb_preserve_alpha(base, closed)
                e_cell = blend_mouth(d_cell, half, row, col)
                f_cell = blend_mouth(d_cell, opened, row, col)
            else:
                d_cell = apply_closed_eyes(base, closed, row)
                e_cell = apply_closed_eyes(b_cell, closed, row)
                f_cell = apply_closed_eyes(c_cell, closed, row)

            sheets["B"][y0:y1, x0:x1] = b_cell
            sheets["C"][y0:y1, x0:x1] = c_cell
            sheets["D"][y0:y1, x0:x1] = d_cell
            sheets["E"][y0:y1, x0:x1] = e_cell
            sheets["F"][y0:y1, x0:x1] = f_cell
    return sheets


def swap_sheet_columns(sheets: dict[str, np.ndarray], left: int, right: int) -> dict[str, np.ndarray]:
    swapped: dict[str, np.ndarray] = {}
    for key, sheet in sheets.items():
        h, w = sheet.shape[:2]
        out = sheet.copy()
        for row in range(5):
            lx0, ly0, lx1, ly1 = cell_bounds(w, h, row, left)
            rx0, ry0, rx1, ry1 = cell_bounds(w, h, row, right)
            left_cell = sheet[ly0:ly1, lx0:lx1].copy()
            right_cell = sheet[ry0:ry1, rx0:rx1].copy()
            out[ly0:ly1, lx0:lx1] = right_cell
            out[ry0:ry1, rx0:rx1] = left_cell
        swapped[key] = out
    return swapped


def make_contact(sheets: dict[str, np.ndarray], out: Path) -> None:
    thumb = 180
    gap = 12
    blocks: list[np.ndarray] = []
    for sheet in "ABCDEF":
        image = Image.fromarray(sheets[sheet], "RGBA")
        w, h = image.size
        block = Image.new("RGBA", (thumb * 5, thumb * 5), (255, 248, 238, 255))
        for row in range(5):
            for col in range(5):
                x0, y0, x1, y1 = cell_bounds(w, h, row, col)
                cell = image.crop((x0, y0, x1, y1))
                cell.thumbnail((thumb, thumb), Image.Resampling.LANCZOS)
                tile = Image.new("RGBA", (thumb, thumb), (255, 248, 238, 255))
                tile.alpha_composite(cell, ((thumb - cell.width) // 2, (thumb - cell.height) // 2))
                block.alpha_composite(tile, (col * thumb, row * thumb))
        blocks.append(np.array(block.convert("RGB")))

    width = thumb * 10 + gap
    height = thumb * 15 + gap * 2
    canvas = np.full((height, width, 3), 245, np.uint8)
    for idx, block in enumerate(blocks):
        x = (idx % 2) * (thumb * 5 + gap)
        y = (idx // 2) * (thumb * 5 + gap)
        canvas[y : y + block.shape[0], x : x + block.shape[1]] = block
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(canvas).save(out, quality=94)


def same_alpha_report(sheets: dict[str, np.ndarray]) -> str:
    base = sheets["A"][..., 3]
    lines = []
    for name in "ABCDEF":
        alpha = sheets[name][..., 3]
        diff = int(np.count_nonzero(alpha != base))
        lines.append(f"{name}: alpha_diff_vs_A={diff}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--eyes-closed-source",
        type=Path,
        default=None,
        help="Optional replacement source for D/E/F closed-eye patches.",
    )
    parser.add_argument(
        "--closed-mode",
        choices=["hybrid", "bbox", "eye-lines", "full-rgb"],
        default="eye-lines",
        help="How to build D/E/F from the closed-eye source.",
    )
    parser.add_argument(
        "--swap-cols",
        default="1,3",
        help="Comma-separated 0-based columns to swap after patching. Empty disables it.",
    )
    args = parser.parse_args()

    base = load_rgba(args.source_dir / "A_open_closed.png")
    half = load_rgba(args.source_dir / "B_open_half.png")
    opened = load_rgba(args.source_dir / "C_open_open.png")
    closed_path = args.eyes_closed_source or args.source_dir / "D_closed_closed.png"
    closed = load_rgba(closed_path)
    closed_half = load_rgba(args.source_dir / "E_closed_half.png")
    closed_open = load_rgba(args.source_dir / "F_closed_open.png")
    sheets = patch_sheet(base, half, opened, closed, closed_half, closed_open, args.closed_mode)
    if args.swap_cols:
        left, right = (int(part) for part in args.swap_cols.split(",", 1))
        sheets = swap_sheet_columns(sheets, left, right)

    names = {
        "A": "A_目開け_口とじ.png",
        "B": "B_目開け_口中間.png",
        "C": "C_目開け_口開け.png",
        "D": "D_目閉じ_口とじ.png",
        "E": "E_目閉じ_口中間.png",
        "F": "F_目閉じ_口開け.png",
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for key, name in names.items():
        save_rgba(args.out_dir / name, sheets[key])
    make_contact(sheets, ROOT / "verification/patched_sheets_contact.jpg")
    print(same_alpha_report(sheets))
    print(f"wrote={args.out_dir}")
    print(f"contact={ROOT / 'verification/patched_sheets_contact.jpg'}")


if __name__ == "__main__":
    main()
