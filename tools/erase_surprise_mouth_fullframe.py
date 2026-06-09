#!/usr/bin/env python3
"""Erase Dokochan surprise source mouth with full-frame inpaint.

This is intentionally narrow. The normal mouthless pipeline works for joy,
anger, and sad, but the surprise Seedance clip has a large open mouth baked into
the video. Patch-normalized clean plates left residue there. The accepted fix is
to tighten the detected mouth quad to mouth-only, shrink its bottom edge by 5%,
and inpaint the actual full frame every frame.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def tighten_quad_bottom95(quad: np.ndarray) -> np.ndarray:
    """Convert detector's loose lower-face quad into the accepted mouth-only quad."""
    q = np.asarray(quad, dtype=np.float32).reshape(4, 2)
    x0 = float(q[:, 0].min())
    x1 = float(q[:, 0].max())
    y0 = float(q[:, 1].min())
    y1 = float(q[:, 1].max())
    w = x1 - x0
    h = y1 - y0

    # Values chosen from the user-approved debug report:
    # - remove nose/chin from the detector quad
    # - then move only the bottom edge upward by 5% to avoid the jaw line
    nx0 = x0 + w * 0.17
    nx1 = x1 - w * 0.17
    ny0 = y0 + h * 0.23
    ny1 = y1 - h * 0.12
    ny1 = ny0 + (ny1 - ny0) * 0.95
    return np.array([[nx0, ny0], [nx1, ny0], [nx1, ny1], [nx0, ny1]], dtype=np.float32)


def make_ellipse_mask(shape: tuple[int, int], quad: np.ndarray) -> np.ndarray:
    h, w = shape
    q = np.asarray(quad, dtype=np.float32).reshape(4, 2)
    cx = float(q[:, 0].mean())
    cy = float(q[:, 1].mean())
    qw = float(np.linalg.norm(q[1] - q[0]))
    qh = float(np.linalg.norm(q[3] - q[0]))

    # ff_b_ellipse, the accepted candidate. Keep this boring and explicit.
    sx = 0.96
    sy = 0.90
    yoff = -0.02
    dilate = 5
    rx = max(2, int(round(qw * sx * 0.5)))
    ry = max(2, int(round(qh * sy * 0.5)))
    center = (int(round(cx)), int(round(cy + qh * yoff)))

    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.ellipse(mask, center, (rx, ry), 0.0, 0.0, 360.0, 255, -1)
    if dilate > 0:
        kernel = np.ones((dilate, dilate), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def build_tight_quads(src_track: Path) -> tuple[dict[str, np.ndarray], np.ndarray]:
    src = np.load(src_track, allow_pickle=False)
    data = {key: src[key] for key in src.files}
    tight_quads = np.asarray([tighten_quad_bottom95(q) for q in data["quad"]], dtype=np.float32)
    data["quad"] = tight_quads
    return data, tight_quads


def write_tight_track(src_track: Path, out_track: Path) -> np.ndarray:
    data, tight_quads = build_tight_quads(src_track)
    out_track.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out_track, **data)
    return tight_quads


def erase_video(video: Path, track: Path, out_video: Path, *, out_track: Path | None = None) -> None:
    npz = np.load(track, allow_pickle=False)
    valid = np.asarray(npz["valid"], dtype=np.uint8) > 0
    if out_track is not None:
        quads = write_tight_track(track, out_track)
    else:
        _, quads = build_tight_quads(track)

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open video: {video}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if w <= 0 or h <= 0 or n <= 0:
        raise RuntimeError(f"invalid video metadata: {video}")

    out_video.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_video.with_suffix(out_video.suffix + ".tmp.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"failed to open writer: {tmp}")

    last_quad: np.ndarray | None = None
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if idx < len(quads) and valid[idx]:
            last_quad = quads[idx]
        if last_quad is not None:
            mask = make_ellipse_mask((h, w), last_quad)
            frame = cv2.inpaint(frame, mask, inpaintRadius=6.0, flags=cv2.INPAINT_TELEA)
        writer.write(frame)
        idx += 1

    cap.release()
    writer.release()
    tmp.replace(out_video)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True)
    parser.add_argument("--track", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--out-track", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    erase_video(
        Path(args.video),
        Path(args.track),
        Path(args.out),
        out_track=Path(args.out_track) if args.out_track else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
