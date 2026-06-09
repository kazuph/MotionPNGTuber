#!/usr/bin/env python3
"""Build a visual debug report for Dokochan surprise mouth erasing.

This does not change runtime behavior. It extracts real frames, the tracked mouth
quad, normalized mouth patches, the erase mask, and the clean plate used for the
surprise mouthless candidate.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


REPO = Path(__file__).resolve().parents[1]
BASE = REPO / "assets" / "dokochan_vtuber" / "seedance_layers" / "composited"
VIDEO = BASE / "loop_surprise.mp4"
TRACK = BASE / "mouth_track_surprise_mouth_only_bottom95_calibrated.npz"
OUT = BASE / "surprise_mouth_debug"
REF_FRAME = 1


def ensure_even_ge2(n: int) -> int:
    n = int(n)
    if n < 2:
        return 2
    return n if n % 2 == 0 else n - 1


def quad_wh(quad: np.ndarray) -> tuple[float, float]:
    q = np.asarray(quad, dtype=np.float32).reshape(4, 2)
    w = float(np.linalg.norm(q[1] - q[0]))
    h = float(np.linalg.norm(q[3] - q[0]))
    return w, h


def warp_frame_to_norm(frame_bgr: np.ndarray, quad: np.ndarray, norm_w: int, norm_h: int) -> np.ndarray:
    src = np.asarray(quad, dtype=np.float32).reshape(4, 2)
    dst = np.array([[0, 0], [norm_w - 1, 0], [norm_w - 1, norm_h - 1], [0, norm_h - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        frame_bgr,
        matrix,
        (int(norm_w), int(norm_h)),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def make_mouth_mask(
    w: int,
    h: int,
    *,
    mask_scale_x: float,
    mask_scale_y: float,
    center_y_offset_px: int,
    top_clip_frac: float,
) -> np.ndarray:
    mask = np.zeros((h, w), dtype=np.uint8)
    cx, cy0 = w // 2, h // 2
    cy = int(np.clip(cy0 + int(center_y_offset_px), 0, h - 1))
    rx = int(max(1, min(int((w * mask_scale_x) * 0.5), w // 2 - 1)))
    ry = int(max(1, min(int((h * mask_scale_y) * 0.5), h // 2 - 1)))
    cv2.ellipse(mask, (cx, cy), (rx, ry), 0.0, 0.0, 360.0, 255, -1)
    clip_y = int(round(cy - ry * float(np.clip(top_clip_frac, 0.6, 1.0))))
    clip_y = int(np.clip(clip_y, 0, h))
    if clip_y > 0:
        mask[:clip_y, :] = 0
    return mask


def read_frame(cap: cv2.VideoCapture, idx: int) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_FRAMES, float(idx))
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError(f"failed to read frame {idx}")
    return frame


def save_bgr(path: Path, bgr: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), bgr)


def draw_quad(frame_bgr: np.ndarray, quad: np.ndarray, label: str) -> np.ndarray:
    out = frame_bgr.copy()
    q = np.asarray(quad, dtype=np.int32).reshape(4, 2)
    cv2.polylines(out, [q], isClosed=True, color=(0, 0, 255), thickness=3, lineType=cv2.LINE_AA)
    for i, (x, y) in enumerate(q):
        cv2.circle(out, (int(x), int(y)), 6, (255, 255, 0), -1, lineType=cv2.LINE_AA)
        cv2.putText(out, str(i), (int(x) + 8, int(y) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
    cv2.putText(out, label, (24, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
    return out


def make_patch_contact(items: list[tuple[str, Path]], out_path: Path) -> None:
    thumb_w, thumb_h = 220, 220
    label_h = 30
    sheet = Image.new("RGB", (len(items) * thumb_w, thumb_h + label_h), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (label, path) in enumerate(items):
        img = Image.open(path).convert("RGB")
        img.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        x = i * thumb_w + (thumb_w - img.width) // 2
        y = label_h + (thumb_h - img.height) // 2
        draw.text((i * thumb_w + 8, 8), label, fill=(0, 0, 0))
        sheet.paste(img, (x, y))
    sheet.save(out_path, quality=94)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    npz = np.load(TRACK, allow_pickle=False)
    quads = np.asarray(npz["quad"], dtype=np.float32)
    valid = np.asarray(npz["valid"], dtype=np.uint8) > 0

    cap = cv2.VideoCapture(str(VIDEO))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open {VIDEO}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    sample_frames = [0, 1, frame_count // 2, max(0, frame_count - 1)]

    ws = np.array([quad_wh(q)[0] for q in quads], dtype=np.float32)
    hs = np.array([quad_wh(q)[1] for q in quads], dtype=np.float32)
    ratio = float(np.median(ws / np.maximum(1e-6, hs)))
    norm_w = ensure_even_ge2(max(96, int(round(float(np.percentile(ws, 95)) * 1.2))))
    norm_h = ensure_even_ge2(max(64, int(round(norm_w / max(0.25, min(4.0, ratio))))))

    # Tighter mouth-only quad, bottom side moved upward 5% to avoid the chin.
    mask_scale_x = 0.76
    mask_scale_y = 0.78
    center_y_offset_px = 0
    top_clip_frac = 0.82
    mask = make_mouth_mask(
        norm_w,
        norm_h,
        mask_scale_x=mask_scale_x,
        mask_scale_y=mask_scale_y,
        center_y_offset_px=center_y_offset_px,
        top_clip_frac=top_clip_frac,
    )
    mask_path = OUT / "mask_bottom95.png"
    cv2.imwrite(str(mask_path), mask)

    ref_frame = read_frame(cap, REF_FRAME)
    ref_patch = warp_frame_to_norm(ref_frame, quads[REF_FRAME], norm_w, norm_h)
    clean_plate = cv2.inpaint(ref_patch, mask, inpaintRadius=8.0, flags=cv2.INPAINT_TELEA)
    save_bgr(OUT / "ref_frame_001.png", ref_frame)
    save_bgr(OUT / "ref_patch_001.png", ref_patch)
    save_bgr(OUT / "clean_plate_bottom95.png", clean_plate)

    rows: list[dict[str, object]] = []
    patch_items: list[tuple[str, Path]] = []
    for idx in sample_frames:
        frame = read_frame(cap, idx)
        quad = quads[idx]
        overlay = draw_quad(frame, quad, f"frame {idx} mouth quad")
        patch = warp_frame_to_norm(frame, quad, norm_w, norm_h)
        overlay_path = OUT / f"frame_{idx:03d}_quad_overlay.png"
        patch_path = OUT / f"frame_{idx:03d}_norm_patch.png"
        save_bgr(overlay_path, overlay)
        save_bgr(patch_path, patch)
        patch_items.append((f"f{idx}", patch_path))
        x0, y0 = quad[:, 0].min(), quad[:, 1].min()
        x1, y1 = quad[:, 0].max(), quad[:, 1].max()
        rows.append(
            {
                "frame": idx,
                "valid": bool(valid[idx]),
                "bbox": [round(float(x0), 1), round(float(y0), 1), round(float(x1), 1), round(float(y1), 1)],
                "quad": [[round(float(x), 1), round(float(y), 1)] for x, y in quad],
            }
        )
    cap.release()

    make_patch_contact(patch_items, OUT / "normalized_patch_contact.jpg")

    rel = lambda p: html.escape(p.name if p.parent == OUT else str(p.relative_to(OUT)))
    overlay_figures: list[str] = []
    for r in rows:
        frame_idx = int(r["frame"])
        overlay_src = rel(OUT / f"frame_{frame_idx:03d}_quad_overlay.png")
        overlay_figures.append(
            f'<figure><img src="{overlay_src}" /><figcaption>frame {frame_idx}: estimated mouth quad</figcaption></figure>'
        )
    overlay_imgs = "\n".join(overlay_figures)
    table_rows = "\n".join(
        "<tr>"
        f"<td>{r['frame']}</td>"
        f"<td>{r['valid']}</td>"
        f"<td>{html.escape(json.dumps(r['bbox']))}</td>"
        f"<td><code>{html.escape(json.dumps(r['quad'], ensure_ascii=False))}</code></td>"
        "</tr>"
        for r in rows
    )

    report = OUT / "surprise_mouth_debug_report.html"
    report.write_text(
        f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>Dokochan Surprise Mouth Erase Debug</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; line-height: 1.55; color: #222; }}
    h1, h2 {{ margin: 24px 0 8px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(320px, 1fr)); gap: 16px; }}
    figure {{ margin: 0; border: 1px solid #ddd; padding: 8px; background: #fafafa; }}
    img {{ max-width: 100%; height: auto; display: block; }}
    figcaption {{ font-size: 13px; color: #555; margin-top: 6px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px; vertical-align: top; }}
    code {{ white-space: pre-wrap; }}
    .note {{ background: #fff7d6; padding: 12px; border: 1px solid #e4d28a; }}
  </style>
</head>
<body>
  <h1>Dokochan Surprise Mouth Erase Debug</h1>
  <p class="note">目的: 口の矩形推定が悪いのか、矩形後の clean plate / mask が悪いのかを、実データで見るためのレポートです。</p>

  <h2>Summary</h2>
  <ul>
    <li>video: <code>{html.escape(str(VIDEO.relative_to(REPO)))}</code></li>
    <li>track: <code>{html.escape(str(TRACK.relative_to(REPO)))}</code></li>
    <li>size/fps/frames: <code>{width}x{height} @ {fps:.2f}fps, {frame_count} frames</code></li>
    <li>normalized mouth patch: <code>{norm_w}x{norm_h}</code></li>
    <li>clean plate ref frame: <code>{REF_FRAME}</code></li>
    <li>mask candidate: <code>bottom95 tight quad, scale_x={mask_scale_x}, scale_y={mask_scale_y}, inpaint=8</code></li>
  </ul>

  <h2>1. Mouth Quad Overlay</h2>
  <p>赤い四角がモデル推定した口矩形です。ここが開いた口全体を十分に覆っていなければ、矩形選択側の問題です。</p>
  <div class="grid">{overlay_imgs}</div>

  <h2>2. Normalized Mouth Patches</h2>
  <p>上のquadを正面化した口パッチです。ここに口だけでなく鼻・顎が大きく入る場合、clean plate が難しくなります。</p>
  <figure><img src="normalized_patch_contact.jpg" /><figcaption>normalized patches from sampled frames</figcaption></figure>

  <h2>3. Ref Patch, Mask, Clean Plate</h2>
  <p>clean plate は ref frame の正規化口パッチを、mask部分だけ inpaint して作っています。ここが肌として不自然なら、矩形が正しくても口消しは破綻します。</p>
  <div class="grid">
    <figure><img src="ref_patch_001.png" /><figcaption>reference normalized patch, frame 1</figcaption></figure>
    <figure><img src="mask_bottom95.png" /><figcaption>erase mask with bottom -5% mouth-only quad</figcaption></figure>
    <figure><img src="clean_plate_bottom95.png" /><figcaption>clean plate generated by inpaint</figcaption></figure>
    <figure><img src="ref_frame_001.png" /><figcaption>original reference frame 1</figcaption></figure>
  </div>

  <h2>4. Quad Numeric Data</h2>
  <table>
    <thead><tr><th>frame</th><th>valid</th><th>bbox</th><th>quad points</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
