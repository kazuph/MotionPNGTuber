#!/usr/bin/env python3
"""Slice GPT-generated 5x5 guruguru sheets into transparent runtime frames."""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


SHEETS = {
    "A": "A_open_closed_gpt_candidate_01.png",
    "B": "B_open_half_gpt_candidate_01.png",
    "C": "C_open_open_gpt_candidate_01.png",
    "D": "D_closed_closed_gpt_candidate_01.png",
    "E": "E_closed_half_gpt_candidate_01.png",
    "F": "F_closed_open_gpt_candidate_01.png",
}


def background_color(image: Image.Image) -> np.ndarray:
    rgb = np.array(image.convert("RGB"))
    h, w, _ = rgb.shape
    border = np.concatenate([
        rgb[: max(2, h // 50), :, :].reshape(-1, 3),
        rgb[-max(2, h // 50) :, :, :].reshape(-1, 3),
        rgb[:, : max(2, w // 50), :].reshape(-1, 3),
        rgb[:, -max(2, w // 50) :, :].reshape(-1, 3),
    ])
    return np.median(border, axis=0)


def remove_light_background(image: Image.Image) -> Image.Image:
    rgb = np.array(image.convert("RGB")).astype(np.int32)
    bg = background_color(image).astype(np.int32)
    dist = np.sqrt(np.sum((rgb - bg) ** 2, axis=2))
    background_like = dist < 46
    h, w = background_like.shape
    visited = np.zeros((h, w), dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    for x in range(w):
        if background_like[0, x]:
            visited[0, x] = True
            queue.append((0, x))
        if background_like[h - 1, x]:
            visited[h - 1, x] = True
            queue.append((h - 1, x))
    for y in range(h):
        if background_like[y, 0] and not visited[y, 0]:
            visited[y, 0] = True
            queue.append((y, 0))
        if background_like[y, w - 1] and not visited[y, w - 1]:
            visited[y, w - 1] = True
            queue.append((y, w - 1))

    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if ny < 0 or ny >= h or nx < 0 or nx >= w or visited[ny, nx]:
                continue
            if background_like[ny, nx]:
                visited[ny, nx] = True
                queue.append((ny, nx))

    alpha = np.where(visited, 0, 255).astype(np.uint8)
    rgba = np.dstack([rgb.astype(np.uint8), alpha])
    return Image.fromarray(rgba, "RGBA")


def bbox_from_alpha(image: Image.Image, threshold: int) -> tuple[int, int, int, int] | None:
    alpha = np.array(image.getchannel("A"))
    ys, xs = np.nonzero(alpha > threshold)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def connected_components(alpha: np.ndarray, threshold: int) -> list[tuple[int, int, int, int, int]]:
    fg = alpha > threshold
    h, w = fg.shape
    seen = np.zeros((h, w), dtype=bool)
    comps: list[tuple[int, int, int, int, int]] = []
    ys, xs = np.nonzero(fg)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if seen[sy, sx]:
            continue
        queue: deque[tuple[int, int]] = deque([(sy, sx)])
        seen[sy, sx] = True
        min_x = max_x = sx
        min_y = max_y = sy
        area = 0
        while queue:
            y, x = queue.popleft()
            area += 1
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if ny < 0 or ny >= h or nx < 0 or nx >= w or seen[ny, nx] or not fg[ny, nx]:
                    continue
                seen[ny, nx] = True
                queue.append((ny, nx))
        comps.append((min_x, min_y, max_x + 1, max_y + 1, area))
    return comps


def nearest_cell(cx: float, cy: float, width: int, height: int) -> tuple[int, int]:
    col = min(4, max(0, round(cx / width * 5 - 0.5)))
    row = min(4, max(0, round(cy / height * 5 - 0.5)))
    return row, col


def slice_sheet(source: Path, out_dir: Path, canvas: int, alpha_threshold: int) -> None:
    sheet = remove_light_background(Image.open(source))
    alpha = np.array(sheet.getchannel("A"))
    components = [
        comp for comp in connected_components(alpha, alpha_threshold)
        if comp[4] >= 12
    ]
    assigned: dict[tuple[int, int], list[tuple[int, int, int, int, int]]] = {
        (r, c): [] for r in range(5) for c in range(5)
    }
    for comp in components:
        x0, y0, x1, y1, _area = comp
        row, col = nearest_cell((x0 + x1) / 2, (y0 + y1) / 2, sheet.width, sheet.height)
        assigned[(row, col)].append(comp)

    out_dir.mkdir(parents=True, exist_ok=True)

    for r in range(5):
        for c in range(5):
            comps = assigned[(r, c)]
            if not comps:
                raise ValueError(f"empty cell: {source.name} r{r}c{c}")
            x0 = max(0, min(comp[0] for comp in comps) - 2)
            y0 = max(0, min(comp[1] for comp in comps) - 2)
            x1 = min(sheet.width, max(comp[2] for comp in comps) + 2)
            y1 = min(sheet.height, max(comp[3] for comp in comps) + 2)
            sprite = sheet.crop((x0, y0, x1, y1))

            target_h = int(canvas * 0.43)
            scale = min(target_h / sprite.height, (canvas * 0.56) / sprite.width)
            new_size = (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale)))
            sprite = sprite.resize(new_size, Image.Resampling.LANCZOS)

            frame = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
            x = (canvas - sprite.width) // 2
            y = int(canvas * 0.77) - sprite.height
            frame.alpha_composite(sprite, (x, y))
            frame.save(out_dir / f"r{r}c{c}.png", compress_level=1)


def make_contact(slices_root: Path, out_path: Path) -> None:
    cell = 190
    label_h = 26
    gap = 10
    block_w = 5 * cell
    block_h = label_h + 5 * cell
    canvas = Image.new("RGB", (3 * block_w + 2 * gap, 2 * block_h + gap), (245, 240, 232))
    draw = ImageDraw.Draw(canvas)
    labels = {
        "A": "A GPT open/closed",
        "B": "B GPT open/half",
        "C": "C GPT open/open",
        "D": "D GPT closed/closed",
        "E": "E GPT closed/half",
        "F": "F GPT closed/open",
    }
    for idx, sheet in enumerate("ABCDEF"):
        bx = (idx % 3) * (block_w + gap)
        by = (idx // 3) * (block_h + gap)
        draw.text((bx + 8, by + 6), labels[sheet], fill=(30, 26, 22))
        for r in range(5):
            for c in range(5):
                image = Image.open(slices_root / sheet / f"r{r}c{c}.png").convert("RGBA")
                thumb = Image.new("RGBA", (cell, cell), (255, 250, 242, 255))
                image.thumbnail((cell, cell), Image.Resampling.LANCZOS)
                thumb.alpha_composite(image, ((cell - image.width) // 2, (cell - image.height) // 2))
                canvas.paste(thumb.convert("RGB"), (bx + c * cell, by + label_h + r * cell))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, quality=92)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--canvas", type=int, default=1200)
    parser.add_argument("--alpha-threshold", type=int, default=40)
    args = parser.parse_args()

    for sheet, filename in SHEETS.items():
        slice_sheet(args.source / filename, args.out / sheet, args.canvas, args.alpha_threshold)
    make_contact(args.out, args.out.parent / "gpt_image_runtime_contact.jpg")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
