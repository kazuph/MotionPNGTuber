#!/usr/bin/env python3
"""Remove purple/lilac background from a 5x5 sheet and verify cell alignment."""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROWS = COLS = 5
MIN_SAFE_MARGIN_PX = 8
MAX_VISIBLE_BACKGROUND_RESIDUE_PIXELS = 24
MAX_CELL_CENTER_DELTA_RATIO = 0.085
MIN_CELL_AREA_RATIO_TO_REFERENCE = 0.78
MAX_CELL_AREA_RATIO_TO_REFERENCE = 1.24
MIN_CELL_DIMENSION_RATIO_TO_REFERENCE = 0.78
MAX_CELL_DIMENSION_RATIO_TO_REFERENCE = 1.24


def purple_background_candidate(rgb: np.ndarray) -> np.ndarray:
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    # Designed for generated lilac/purple backgrounds, including mild gradients.
    return (r > 165) & (b > 175) & (g > 80) & ((b - g) > 12) & ((r - g) > 12)


def red_grid_candidate(rgb: np.ndarray) -> np.ndarray:
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    return (r > 180) & (g < 90) & (b < 110) & ((r - g) > 100) & ((r - b) > 90)


def border_connected(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        if mask[0, x]:
            seen[0, x] = True
            q.append((0, x))
        if mask[h - 1, x]:
            seen[h - 1, x] = True
            q.append((h - 1, x))
    for y in range(h):
        if mask[y, 0] and not seen[y, 0]:
            seen[y, 0] = True
            q.append((y, 0))
        if mask[y, w - 1] and not seen[y, w - 1]:
            seen[y, w - 1] = True
            q.append((y, w - 1))
    while q:
        y, x = q.popleft()
        for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                seen[yy, xx] = True
                q.append((yy, xx))
    return seen


def remove_background(image: Image.Image, remove_red_grid: bool = False) -> Image.Image:
    rgb = np.array(image.convert("RGB"))
    bg_seed = purple_background_candidate(rgb)
    if remove_red_grid:
        bg_seed |= red_grid_candidate(rgb)
    bg = border_connected(bg_seed)
    if remove_red_grid:
        bg |= red_grid_candidate(rgb)
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    # Soften only the immediate edge by making adjacent background/foreground pixels semi-transparent.
    # This is intentionally conservative; character pixels are not recolored.
    h, w = alpha.shape
    edge = np.zeros_like(alpha, dtype=bool)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        shifted = np.zeros_like(bg)
        src_y0 = max(0, -dy)
        src_y1 = min(h, h - dy)
        src_x0 = max(0, -dx)
        src_x1 = min(w, w - dx)
        dst_y0 = max(0, dy)
        dst_y1 = min(h, h + dy)
        dst_x0 = max(0, dx)
        dst_x1 = min(w, w + dx)
        shifted[dst_y0:dst_y1, dst_x0:dst_x1] = bg[src_y0:src_y1, src_x0:src_x1]
        edge |= (~bg) & shifted
    alpha[edge] = 230
    rgba = np.dstack([rgb, alpha])
    rgba[alpha == 0, :3] = 0
    return Image.fromarray(rgba, "RGBA")


def find_grid_lines(image: Image.Image) -> tuple[list[int], list[int]]:
    rgb = np.array(image.convert("RGB"))
    red = red_grid_candidate(rgb)
    col_score = red.mean(axis=0)
    row_score = red.mean(axis=1)

    def peaks(score: np.ndarray) -> list[int]:
        active = score > 0.35
        spans: list[tuple[int, int]] = []
        start = None
        for i, value in enumerate(active):
            if value and start is None:
                start = i
            elif not value and start is not None:
                spans.append((start, i))
                start = None
        if start is not None:
            spans.append((start, len(active)))
        centers = [int(round((a + b - 1) / 2)) for a, b in spans if b - a >= 1]
        # If anti-aliasing splits a line, merge nearby centers.
        merged: list[int] = []
        for c in centers:
            if merged and c - merged[-1] <= 8:
                merged[-1] = int(round((merged[-1] + c) / 2))
            else:
                merged.append(c)
        return merged

    xs = peaks(col_score)
    ys = peaks(row_score)
    if len(xs) != 6 or len(ys) != 6:
        raise ValueError(f"expected 6 vertical and 6 horizontal grid lines, got xs={xs} ys={ys}")
    return xs, ys


def crop_grid_inner(image: Image.Image, pad: int = 4) -> Image.Image:
    xs, ys = find_grid_lines(image)
    rgb = np.array(image.convert("RGB"))
    h, w = rgb.shape[:2]
    cell_widths = [max(1, min(w, xs[col + 1] - pad) - max(0, xs[col] + pad)) for col in range(COLS)]
    cell_heights = [max(1, min(h, ys[row + 1] - pad) - max(0, ys[row] + pad)) for row in range(ROWS)]
    target_w = max(cell_widths)
    target_h = max(cell_heights)
    out = np.full((target_h * ROWS, target_w * COLS, 3), np.array([238, 183, 255], dtype=np.uint8))
    for row in range(ROWS):
        for col in range(COLS):
            x0 = min(w, max(0, xs[col] + pad))
            x1 = min(w, max(0, xs[col + 1] - pad))
            y0 = min(h, max(0, ys[row] + pad))
            y1 = min(h, max(0, ys[row + 1] - pad))
            crop = rgb[y0:y1, x0:x1]
            dy = row * target_h + (target_h - crop.shape[0]) // 2
            dx = col * target_w + (target_w - crop.shape[1]) // 2
            out[dy : dy + crop.shape[0], dx : dx + crop.shape[1]] = crop
    return Image.fromarray(out, "RGB")


def bbox(mask: np.ndarray) -> tuple[int, int, int, int, int]:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return 0, 0, 0, 0, 0
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, int(xs.size)


def largest_component(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    best: list[tuple[int, int]] = []
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]:
                continue
            q: deque[tuple[int, int]] = deque([(y, x)])
            seen[y, x] = True
            comp: list[tuple[int, int]] = []
            while q:
                yy, xx = q.popleft()
                comp.append((yy, xx))
                for ny in (yy - 1, yy, yy + 1):
                    for nx in (xx - 1, xx, xx + 1):
                        if (
                            0 <= ny < h
                            and 0 <= nx < w
                            and not seen[ny, nx]
                            and mask[ny, nx]
                        ):
                            seen[ny, nx] = True
                            q.append((ny, nx))
            if len(comp) > len(best):
                best = comp
    out = np.zeros_like(mask, dtype=bool)
    for y, x in best:
        out[y, x] = True
    return out


def keep_largest_component_per_cell(alpha_image: Image.Image) -> Image.Image:
    arr = np.array(alpha_image.convert("RGBA"))
    h, w = arr.shape[:2]
    cw = w / COLS
    ch = h / ROWS
    keep = np.zeros((h, w), dtype=bool)
    visible = arr[..., 3] > 16
    for row in range(ROWS):
        for col in range(COLS):
            x0 = int(round(col * cw))
            x1 = int(round((col + 1) * cw))
            y0 = int(round(row * ch))
            y1 = int(round((row + 1) * ch))
            keep[y0:y1, x0:x1] = largest_component(visible[y0:y1, x0:x1])
    arr[~keep, 3] = 0
    arr[~keep, :3] = 0
    return Image.fromarray(arr, "RGBA")


def verify(alpha_image: Image.Image) -> dict[str, object]:
    arr = np.array(alpha_image.convert("RGBA"))
    alpha = arr[..., 3] > 16
    visible_background_residue = purple_background_candidate(arr[..., :3]) & alpha
    h, w = alpha.shape
    cw = w / COLS
    ch = h / ROWS
    cells: list[dict[str, object]] = []
    centers_x: list[float] = []
    centers_y: list[float] = []
    widths: list[int] = []
    heights: list[int] = []
    margin_violations = 0
    margin_violation_cells: list[dict[str, object]] = []
    residue_violations = 0
    residue_violation_cells: list[dict[str, object]] = []
    empty = 0
    for row in range(ROWS):
        for col in range(COLS):
            x0 = int(round(col * cw))
            x1 = int(round((col + 1) * cw))
            y0 = int(round(row * ch))
            y1 = int(round((row + 1) * ch))
            cell = alpha[y0:y1, x0:x1]
            residue_pixels = int(visible_background_residue[y0:y1, x0:x1].sum())
            bx0, by0, bx1, by1, area = bbox(cell)
            if area == 0:
                empty += 1
                cell_data = {
                    "row": row,
                    "col": col,
                    "empty": True,
                    "visible_background_residue_pixels": residue_pixels,
                }
                cells.append(cell_data)
                continue
            if residue_pixels > MAX_VISIBLE_BACKGROUND_RESIDUE_PIXELS:
                residue_violations += 1
                residue_violation_cells.append(
                    {
                        "row": row,
                        "col": col,
                        "visible_background_residue_pixels": residue_pixels,
                    }
                )
            width = bx1 - bx0
            height = by1 - by0
            cx = bx0 + width / 2
            cy = by0 + height / 2
            margins = {
                "left": bx0,
                "top": by0,
                "right": (x1 - x0) - bx1,
                "bottom": (y1 - y0) - by1,
            }
            min_margin = min(margins.values())
            if min_margin < MIN_SAFE_MARGIN_PX:
                margin_violations += 1
                margin_violation_cells.append(
                    {
                        "row": row,
                        "col": col,
                        "min_margin": min_margin,
                        "margins": margins,
                    }
                )
            centers_x.append(cx / (x1 - x0))
            centers_y.append(cy / (y1 - y0))
            widths.append(width)
            heights.append(height)
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "bbox": [bx0, by0, bx1, by1],
                    "area": area,
                    "center_ratio": [round(cx / (x1 - x0), 4), round(cy / (y1 - y0), 4)],
                    "size_ratio": [round(width / (x1 - x0), 4), round(height / (y1 - y0), 4)],
                    "margins": margins,
                    "visible_background_residue_pixels": residue_pixels,
                }
            )
    metrics = {
        "image_size": [w, h],
        "cell_size": [cw, ch],
        "empty_cells": empty,
        "min_safe_margin_px": MIN_SAFE_MARGIN_PX,
        "margin_violations": margin_violations,
        "margin_violation_cells": margin_violation_cells,
        "max_visible_background_residue_pixels": MAX_VISIBLE_BACKGROUND_RESIDUE_PIXELS,
        "residue_violations": residue_violations,
        "residue_violation_cells": residue_violation_cells,
        "center_x_range": [round(min(centers_x), 4), round(max(centers_x), 4)] if centers_x else [0, 0],
        "center_y_range": [round(min(centers_y), 4), round(max(centers_y), 4)] if centers_y else [0, 0],
        "width_range": [min(widths), max(widths)] if widths else [0, 0],
        "height_range": [min(heights), max(heights)] if heights else [0, 0],
        "area_range": [min(cell["area"] for cell in cells if not cell.get("empty")), max(cell["area"] for cell in cells if not cell.get("empty"))] if centers_x else [0, 0],
        "cells": cells,
    }
    # Conservative first-pass gate; visual approval still required.
    metrics["passes_mechanical_gate"] = (
        empty == 0
        and margin_violations == 0
        and residue_violations == 0
        and (metrics["center_x_range"][1] - metrics["center_x_range"][0]) <= 0.18
        and (metrics["center_y_range"][1] - metrics["center_y_range"][0]) <= 0.18
        and (metrics["width_range"][1] - metrics["width_range"][0]) <= max(20, int(w / 5 * 0.28))
        and (metrics["height_range"][1] - metrics["height_range"][0]) <= max(20, int(h / 5 * 0.28))
    )
    return metrics


def compare_to_reference(metrics: dict[str, object], reference_metrics: dict[str, object]) -> dict[str, object]:
    width = metrics["width_range"]
    height = metrics["height_range"]
    ref_width = reference_metrics["width_range"]
    ref_height = reference_metrics["height_range"]
    width_mid = (width[0] + width[1]) / 2
    height_mid = (height[0] + height[1]) / 2
    ref_width_mid = (ref_width[0] + ref_width[1]) / 2
    ref_height_mid = (ref_height[0] + ref_height[1]) / 2
    width_ratio = width_mid / ref_width_mid if ref_width_mid else 0
    height_ratio = height_mid / ref_height_mid if ref_height_mid else 0
    ref_cells = {
        (cell["row"], cell["col"]): cell
        for cell in reference_metrics["cells"]
        if not cell.get("empty")
    }
    cell_checks: list[dict[str, object]] = []
    max_center_delta = 0.0
    min_area_ratio = 10.0
    max_area_ratio = 0.0
    min_width_ratio = 10.0
    max_width_ratio = 0.0
    min_height_ratio = 10.0
    max_height_ratio = 0.0
    violating_cells: list[dict[str, object]] = []
    for cell in metrics["cells"]:
        if cell.get("empty"):
            continue
        key = (cell["row"], cell["col"])
        ref = ref_cells.get(key)
        if not ref:
            continue
        area_ratio = cell["area"] / ref["area"] if ref["area"] else 0
        width_ratio = (cell["bbox"][2] - cell["bbox"][0]) / (ref["bbox"][2] - ref["bbox"][0])
        height_ratio = (cell["bbox"][3] - cell["bbox"][1]) / (ref["bbox"][3] - ref["bbox"][1])
        center_delta = max(
            abs(cell["center_ratio"][0] - ref["center_ratio"][0]),
            abs(cell["center_ratio"][1] - ref["center_ratio"][1]),
        )
        max_center_delta = max(max_center_delta, center_delta)
        min_area_ratio = min(min_area_ratio, area_ratio)
        max_area_ratio = max(max_area_ratio, area_ratio)
        min_width_ratio = min(min_width_ratio, width_ratio)
        max_width_ratio = max(max_width_ratio, width_ratio)
        min_height_ratio = min(min_height_ratio, height_ratio)
        max_height_ratio = max(max_height_ratio, height_ratio)
        passes_cell = (
            center_delta <= MAX_CELL_CENTER_DELTA_RATIO
            and MIN_CELL_AREA_RATIO_TO_REFERENCE <= area_ratio <= MAX_CELL_AREA_RATIO_TO_REFERENCE
            and MIN_CELL_DIMENSION_RATIO_TO_REFERENCE <= width_ratio <= MAX_CELL_DIMENSION_RATIO_TO_REFERENCE
            and MIN_CELL_DIMENSION_RATIO_TO_REFERENCE <= height_ratio <= MAX_CELL_DIMENSION_RATIO_TO_REFERENCE
        )
        check = {
            "row": cell["row"],
            "col": cell["col"],
            "center_delta_ratio": round(center_delta, 4),
            "area_ratio": round(area_ratio, 4),
            "width_ratio": round(width_ratio, 4),
            "height_ratio": round(height_ratio, 4),
            "passes_cell_reference_gate": passes_cell,
        }
        cell_checks.append(check)
        if not passes_cell:
            violating_cells.append(check)

    passes_cell_reference_gate = len(violating_cells) == 0 and len(cell_checks) == 25
    result = {
        "reference_width_range": ref_width,
        "reference_height_range": ref_height,
        "width_ratio_to_reference": round(width_ratio, 4),
        "height_ratio_to_reference": round(height_ratio, 4),
        "passes_reference_scale_gate": 0.9 <= width_ratio <= 1.12 and 0.9 <= height_ratio <= 1.12,
        "max_cell_center_delta_ratio": round(max_center_delta, 4),
        "cell_area_ratio_range": [round(min_area_ratio, 4), round(max_area_ratio, 4)] if cell_checks else [0, 0],
        "cell_width_ratio_range": [round(min_width_ratio, 4), round(max_width_ratio, 4)] if cell_checks else [0, 0],
        "cell_height_ratio_range": [round(min_height_ratio, 4), round(max_height_ratio, 4)] if cell_checks else [0, 0],
        "cell_reference_gate_limits": {
            "max_center_delta_ratio": MAX_CELL_CENTER_DELTA_RATIO,
            "area_ratio": [MIN_CELL_AREA_RATIO_TO_REFERENCE, MAX_CELL_AREA_RATIO_TO_REFERENCE],
            "dimension_ratio": [
                MIN_CELL_DIMENSION_RATIO_TO_REFERENCE,
                MAX_CELL_DIMENSION_RATIO_TO_REFERENCE,
            ],
        },
        "passes_cell_reference_gate": passes_cell_reference_gate,
        "cell_reference_violations": violating_cells,
        "cell_reference_checks": cell_checks,
    }
    return result


def make_contact(alpha_image: Image.Image, metrics: dict[str, object], out: Path) -> None:
    bg = (28, 29, 32)
    tile = 160
    gap = 8
    label = 20
    canvas = Image.new("RGB", (COLS * (tile + gap) - gap, ROWS * (tile + label + gap) - gap), bg)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    w, h = alpha_image.size
    cw = w / COLS
    ch = h / ROWS
    for row in range(ROWS):
        for col in range(COLS):
            x0 = int(round(col * cw))
            x1 = int(round((col + 1) * cw))
            y0 = int(round(row * ch))
            y1 = int(round((row + 1) * ch))
            crop = alpha_image.crop((x0, y0, x1, y1))
            crop.thumbnail((tile, tile), Image.Resampling.LANCZOS)
            cell = Image.new("RGBA", (tile, tile), (*bg, 255))
            cell.alpha_composite(crop, ((tile - crop.width) // 2, (tile - crop.height) // 2))
            dx = col * (tile + gap)
            dy = row * (tile + label + gap) + label
            canvas.paste(cell.convert("RGB"), (dx, dy))
            draw.text((dx + 4, dy - label + 4), f"r{row}c{col}", fill=(240, 240, 240), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=94)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--alpha-out", required=True, type=Path)
    parser.add_argument("--metrics-out", required=True, type=Path)
    parser.add_argument("--contact-out", required=True, type=Path)
    parser.add_argument("--reference-metrics", type=Path)
    parser.add_argument("--grid-mode", action="store_true")
    parser.add_argument("--grid-pad", type=int, default=4)
    parser.add_argument("--keep-largest-per-cell", action="store_true")
    parser.add_argument("--gridless-out", type=Path)
    args = parser.parse_args()
    image = Image.open(args.input)
    if args.grid_mode:
        image = crop_grid_inner(image, pad=args.grid_pad)
        if args.gridless_out:
            args.gridless_out.parent.mkdir(parents=True, exist_ok=True)
            image.save(args.gridless_out)
    alpha = remove_background(image, remove_red_grid=args.grid_mode)
    if args.keep_largest_per_cell:
        alpha = keep_largest_component_per_cell(alpha)
    args.alpha_out.parent.mkdir(parents=True, exist_ok=True)
    alpha.save(args.alpha_out)
    metrics = verify(alpha)
    if args.reference_metrics:
        ref = json.loads(args.reference_metrics.read_text(encoding="utf-8"))
        metrics["reference_comparison"] = compare_to_reference(metrics, ref)
        metrics["passes_mechanical_gate"] = (
            metrics["passes_mechanical_gate"]
            and metrics["reference_comparison"]["passes_reference_scale_gate"]
            and metrics["reference_comparison"]["passes_cell_reference_gate"]
        )
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    make_contact(alpha, metrics, args.contact_out)
    print(args.alpha_out)
    print(args.metrics_out)
    print(args.contact_out)
    print(f"passes_mechanical_gate={metrics['passes_mechanical_gate']}")


if __name__ == "__main__":
    main()
