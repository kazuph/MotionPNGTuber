#!/usr/bin/env python3
"""Build verification images and metrics for the Dokochan tomari-guruguru assets."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "assets/dokochan_vtuber/tomari_guruguru"
SLICES = ROOT / "public/slices2"
OUT = ROOT / "verification"
SHEETS = "ABCDEF"
ROWS = COLS = 5


def load_rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def alpha_bbox(image: Image.Image, threshold: int = 8) -> tuple[int, int, int, int, int]:
    arr = np.array(image)
    ys, xs = np.where(arr[..., 3] > threshold)
    if xs.size == 0:
        return 0, 0, 0, 0, 0
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, int(xs.size)


def thumb_on_bg(image: Image.Image, size: int, bg: tuple[int, int, int]) -> Image.Image:
    thumb = image.copy()
    thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (size, size), (*bg, 255))
    tile.alpha_composite(thumb, ((size - thumb.width) // 2, (size - thumb.height) // 2))
    return tile.convert("RGB")


def draw_label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str) -> None:
    draw.text(xy, text, fill=(42, 38, 34), font=ImageFont.load_default())


def make_slices_contact(path: Path, bg: tuple[int, int, int]) -> None:
    tile = 160
    label_h = 22
    gap = 10
    width = COLS * tile + gap * (COLS - 1) + 60
    height = len(SHEETS) * (tile + label_h) + gap * (len(SHEETS) - 1)
    canvas = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(canvas)
    for si, sheet in enumerate(SHEETS):
        y = si * (tile + label_h + gap)
        draw_label(draw, (6, y + 4), sheet)
        for col in range(COLS):
            img = load_rgba(SLICES / sheet / f"r2c{col}.png")
            x = 50 + col * (tile + gap)
            canvas.paste(thumb_on_bg(img, tile, bg), (x, y + label_h))
            draw_label(draw, (x + 4, y + 4), f"r2c{col}")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, quality=94)


def make_direction_audit(path: Path) -> None:
    tile = 150
    label_h = 22
    gap = 10
    width = COLS * tile + gap * (COLS - 1) + 72
    height = ROWS * (tile + label_h) + gap * (ROWS - 1) + 24
    bg = (255, 248, 238)
    canvas = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(canvas)
    draw_label(draw, (6, 4), "A direction audit")
    for row in range(ROWS):
        y = 24 + row * (tile + label_h + gap)
        draw_label(draw, (6, y + label_h + tile // 2 - 6), f"r{row}")
        for col in range(COLS):
            img = load_rgba(SLICES / "A" / f"r{row}c{col}.png")
            x = 62 + col * (tile + gap)
            canvas.paste(thumb_on_bg(img, tile, bg), (x, y + label_h))
            draw_label(draw, (x + 4, y + 4), f"r{row}c{col}")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, quality=94)


def write_metrics(path: Path) -> None:
    lines: list[str] = []
    counts = {sheet: len(list((SLICES / sheet).glob("*.png"))) for sheet in SHEETS}
    lines.append("counts " + " ".join(f"{sheet}={counts[sheet]}" for sheet in SHEETS))
    max_delta = 0
    for row in range(ROWS):
        for col in range(COLS):
            boxes = []
            for sheet in SHEETS:
                bbox = alpha_bbox(load_rgba(SLICES / sheet / f"r{row}c{col}.png"))
                boxes.append((sheet, bbox))
            ref = boxes[0][1][:4]
            cell_delta = max(
                max(abs(value - ref_value) for value, ref_value in zip(bbox[:4], ref))
                for _sheet, bbox in boxes
            )
            max_delta = max(max_delta, cell_delta)
            areas = [bbox[4] for _sheet, bbox in boxes]
            lines.append(
                f"r{row}c{col} bbox_delta_vs_A={cell_delta} "
                f"area_range={min(areas)}..{max(areas)}"
            )
    lines.append(f"max_bbox_delta_vs_A={max_delta}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def alpha_centroid(alpha: np.ndarray) -> tuple[float, float]:
    ys, xs = np.where(alpha > 0)
    if xs.size == 0:
        return 0.0, 0.0
    return float(xs.mean()), float(ys.mean())


def bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int, int]:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return 0, 0, 0, 0, 0
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, int(xs.size)


def write_blink_coordinate_metrics(path: Path) -> None:
    pairs = (("A", "D"), ("B", "E"), ("C", "F"))
    lines: list[str] = []
    max_alpha_diff = 0
    max_bbox_delta = 0
    max_centroid_delta = 0.0
    max_rgb_change_area = 0
    for open_sheet, closed_sheet in pairs:
        for row in range(ROWS):
            for col in range(COLS):
                open_arr = np.array(load_rgba(SLICES / open_sheet / f"r{row}c{col}.png"))
                closed_arr = np.array(load_rgba(SLICES / closed_sheet / f"r{row}c{col}.png"))
                open_alpha = open_arr[..., 3]
                closed_alpha = closed_arr[..., 3]
                alpha_diff = int(np.count_nonzero(open_alpha != closed_alpha))
                open_bbox = bbox_from_mask(open_alpha > 0)[:4]
                closed_bbox = bbox_from_mask(closed_alpha > 0)[:4]
                bbox_delta = max(abs(a - b) for a, b in zip(open_bbox, closed_bbox))
                open_centroid = alpha_centroid(open_alpha)
                closed_centroid = alpha_centroid(closed_alpha)
                centroid_delta = max(
                    abs(open_centroid[0] - closed_centroid[0]),
                    abs(open_centroid[1] - closed_centroid[1]),
                )
                rgb_delta = np.max(
                    np.abs(open_arr[..., :3].astype(np.int16) - closed_arr[..., :3].astype(np.int16)),
                    axis=2,
                )
                visible = (open_alpha > 0) | (closed_alpha > 0)
                rgb_bbox = bbox_from_mask((rgb_delta > 2) & visible)
                max_alpha_diff = max(max_alpha_diff, alpha_diff)
                max_bbox_delta = max(max_bbox_delta, bbox_delta)
                max_centroid_delta = max(max_centroid_delta, centroid_delta)
                max_rgb_change_area = max(max_rgb_change_area, rgb_bbox[4])
                lines.append(
                    f"{open_sheet}{closed_sheet} r{row}c{col} "
                    f"alpha_diff={alpha_diff} bbox_delta={bbox_delta} "
                    f"centroid_delta={centroid_delta:.6f} "
                    f"rgb_change_bbox=({rgb_bbox[0]},{rgb_bbox[1]},{rgb_bbox[2]},{rgb_bbox[3]}) "
                    f"rgb_change_area={rgb_bbox[4]}"
                )
    lines.append(f"max_alpha_diff={max_alpha_diff}")
    lines.append(f"max_bbox_delta={max_bbox_delta}")
    lines.append(f"max_centroid_delta={max_centroid_delta:.6f}")
    lines.append(f"max_rgb_change_area={max_rgb_change_area}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    make_slices_contact(OUT / "slices_contact_warm.jpg", (255, 248, 238))
    make_slices_contact(OUT / "slices_dark_edge_check.jpg", (43, 41, 38))
    make_direction_audit(OUT / "direction_audit_A.jpg")
    write_metrics(OUT / "slice_metrics.txt")
    write_blink_coordinate_metrics(OUT / "blink_coordinate_metrics.txt")
    print(OUT / "slices_contact_warm.jpg")
    print(OUT / "slices_dark_edge_check.jpg")
    print(OUT / "direction_audit_A.jpg")
    print(OUT / "slice_metrics.txt")
    print(OUT / "blink_coordinate_metrics.txt")


if __name__ == "__main__":
    main()
