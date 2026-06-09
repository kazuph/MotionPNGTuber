#!/usr/bin/env python3
"""Dokochan layered Seedance 2.0 pipeline.

This script intentionally does not create local pseudo-motion. It only:

1. Requests Seedance 2.0 videos for background and green-screen characters.
2. Chroma-key composites already-generated videos.
3. Runs the existing MotionPNGTuber mouth tracking / mouthless pipeline.
4. Launches the existing runtime with microphone input.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


REPO = Path(__file__).resolve().parents[1]
ASSET_ROOT = REPO / "assets" / "dokochan_vtuber"
LAYER_ROOT = ASSET_ROOT / "seedance_layers"
REFERENCE_ROOT = LAYER_ROOT / "references_v2"
EMOTIONS = ("joy", "anger", "sad", "surprise")
SEEDANCE_I2V = "bytedance/seedance-2.0/image-to-video"


@dataclass(frozen=True)
class LayerPaths:
    root: Path

    @property
    def background_video(self) -> Path:
        return self.root / "background" / "background_loop.mp4"

    @property
    def background_request(self) -> Path:
        return self.root / "background" / "background_request.json"

    @property
    def background_result(self) -> Path:
        return self.root / "background" / "background_result.json"

    def character_video(self, emotion: str) -> Path:
        return self.root / "characters" / emotion / f"character_{emotion}_green.mp4"

    def character_request(self, emotion: str) -> Path:
        return self.root / "characters" / emotion / f"character_{emotion}_request.json"

    def character_result(self, emotion: str) -> Path:
        return self.root / "characters" / emotion / f"character_{emotion}_result.json"

    def composite_video(self, emotion: str) -> Path:
        return self.root / "composited" / f"loop_{emotion}.mp4"

    def mouthless_video(self, emotion: str) -> Path:
        return self.root / "composited" / f"loop_{emotion}_mouthless.mp4"

    def track(self, emotion: str) -> Path:
        return self.root / "composited" / f"mouth_track_{emotion}.npz"

    def track_calibrated(self, emotion: str) -> Path:
        return self.root / "composited" / f"mouth_track_{emotion}_calibrated.npz"

    def debug_track(self, emotion: str) -> Path:
        return self.root / "composited" / f"debug_track_{emotion}.mp4"


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(cmd))
    return subprocess.run(
        cmd,
        cwd=REPO,
        text=True,
        check=check,
    )


def ensure_dirs(paths: LayerPaths) -> None:
    for d in [
        paths.root / "background",
        paths.root / "characters",
        paths.root / "composited",
    ]:
        d.mkdir(parents=True, exist_ok=True)
    for emotion in EMOTIONS:
        (paths.root / "characters" / emotion).mkdir(parents=True, exist_ok=True)


def default_character_source(emotion: str) -> Path:
    return REFERENCE_ROOT / f"character_{emotion}_green_ref_v2.png"


def resolve_character_sources(args: argparse.Namespace) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    source_dir = Path(args.character_source_dir) if args.character_source_dir else None
    for emotion in EMOTIONS:
        explicit = getattr(args, f"{emotion}_source", None)
        if explicit:
            path = Path(explicit)
        elif source_dir is not None:
            path = source_dir / f"character_{emotion}_green_ref_v2.png"
        else:
            path = default_character_source(emotion)
        if not path.is_file():
            raise FileNotFoundError(f"{emotion} character reference is missing: {path}")
        sources[emotion] = path
    return sources


def load_fal_client():
    try:
        import fal_client  # type: ignore
    except ImportError as exc:
        raise SystemExit("fal-client is not installed in this environment") from exc
    if not os.environ.get("FAL_KEY") and os.environ.get("FAL_API_KEY"):
        os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
    if not os.environ.get("FAL_KEY"):
        raise SystemExit("FAL_KEY or FAL_API_KEY is required")
    return fal_client


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def download_video(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".download")
    with urllib.request.urlopen(url) as res, tmp.open("wb") as f:
        shutil.copyfileobj(res, f)
    tmp.replace(out_path)


def submit_seedance_i2v(
    *,
    prompt: str,
    image_path: Path,
    out_video: Path,
    request_path: Path,
    result_path: Path,
    request_id_path: Path | None = None,
    wait_seconds: int = 900,
) -> None:
    fal_client = load_fal_client()
    try:
        image_url = fal_client.upload_file(image_path)
        redacted_image_ref = "<uploaded>"
    except Exception as exc:
        text = str(exc)
        if "403" in text or "Forbidden" in text:
            from PIL import Image

            with Image.open(image_path) as img:
                image_url = fal_client.encode_image(img.convert("RGB"), format="jpeg")
            redacted_image_ref = "<embedded-data-url>"
        else:
            raise SystemExit(
                "FAL image upload failed before Seedance generation. "
                "Do not substitute another model or local pseudo-motion."
            ) from exc
    request = {
        "prompt": prompt,
        "image_url": image_url,
        "end_image_url": image_url,
        "duration": "5",
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "generate_audio": False,
    }
    write_json(request_path, {**request, "image_url": redacted_image_ref, "end_image_url": redacted_image_ref})
    try:
        handle = fal_client.submit(SEEDANCE_I2V, arguments=request)
        request_id = str(handle.request_id)
        if request_id_path is not None:
            request_id_path.parent.mkdir(parents=True, exist_ok=True)
            request_id_path.write_text(request_id + "\n")
        print(f"[seedance] submitted {request_id}")

        deadline = time.monotonic() + float(wait_seconds)
        last_status = ""
        while True:
            status = fal_client.status(SEEDANCE_I2V, request_id, with_logs=True)
            status_name = status.__class__.__name__
            if status_name != last_status:
                print(f"[seedance] status={status_name}")
                last_status = status_name
            if status_name == "Completed":
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"Seedance request did not finish within {wait_seconds}s: {request_id}")
            time.sleep(10)
        result = fal_client.result(SEEDANCE_I2V, request_id)
    except Exception as exc:
        text = str(exc)
        if "Exhausted balance" in text or "locked" in text.lower():
            raise SystemExit(
                "Seedance 2.0 is unavailable: FAL account is locked or balance is exhausted. "
                "Do not substitute another model or local pseudo-motion."
            ) from exc
        raise
    write_json(result_path, result)
    video = result.get("video", {})
    url = video.get("url")
    if not url:
        raise RuntimeError(f"Seedance result did not contain video.url: {result_path}")
    download_video(url, out_video)


def background_prompt() -> str:
    return (
        "Create a seamless 5 second looping VTuber streaming room BACKGROUND ONLY. "
        "Absolutely no character, no person, no face, no body, no silhouette. "
        "The background must clearly be a video, not a still image. Required visible motion: "
        "the left monitor shows an animated GPS/map interface with moving route dots, pulsing location pins, "
        "and faint scanning rings; the right wall hex lights slowly pulse in brightness and color; "
        "the glowing globe on the lower right visibly rotates with moving latitude/longitude lines; "
        "small desk LEDs breathe softly. Preserve the approved Dokochan room style: warm softly lit desk, "
        "left monitor, plants, right wall hex lights, small glowing globe. Locked camera, no zoom, no pan, "
        "no cuts, no scene transition, no text, no logo, no watermark. Loop requirement: first and final "
        "frames match closely while the internal monitor, lights, and globe animation cycles smoothly."
    )


def character_prompt(emotion: str) -> str:
    emotion_specs = {
        "joy": "joy / delighted smile, bright relieved eyes, cute cheerful expression, smiling eyelids",
        "anger": "anger / annoyed determination, strongly furrowed brows, sharper focused eyes, cute angry pout",
        "sad": "strong sadness / crying worry, visibly lowered eyebrows, watery eyes, tears collecting and sliding, trembling sad mouth",
        "surprise": "strong surprise / startled shock, very wide open eyes, raised eyebrows, tiny pupils, open surprised mouth, sudden alert gaze",
    }
    return (
        f"Create a seamless 5 second looping character-only VTuber clip on a pure flat chroma-key "
        f"green background (#00ff00). Emotion: {emotion_specs[emotion]}. Preserve Dokochan exactly: "
        "warm light-brown bob hair, gray-blue eyes, pale skin, muted sage-green hoodie with drawstrings, "
        "soft semi-3D anime VTuber rendering, centered bust-up composition. No room background, no props, "
        "no chair, no desk, no text, no logo, no watermark. Keep face and mouth unobstructed for later lip sync. "
        "Locked camera, no zoom, no pan, no cuts. The eyes must visibly animate: natural blinking, small gaze shifts, "
        "moving eye highlights, and emotion-specific eyelid/brow movement. Motion must be generated by the video model "
        "and must be cyclic: breathing, small hair sway, hoodie fabric movement, eye sparkle motion, and gentle head "
        "micro-motion. First and final frames must match as closely as possible for a clean loop."
    )


def generate(args: argparse.Namespace) -> None:
    paths = LayerPaths(Path(args.root))
    ensure_dirs(paths)
    background_source = Path(args.background_source)
    if not background_source.is_file():
        raise FileNotFoundError(f"background reference is missing: {background_source}")
    character_sources = resolve_character_sources(args)
    emotions = tuple(args.emotions or EMOTIONS)
    if args.only in ("all", "background"):
        if paths.background_video.exists() and not args.force:
            print(f"[skip] background exists: {paths.background_video}")
        else:
            submit_seedance_i2v(
                prompt=background_prompt(),
                image_path=background_source,
                out_video=paths.background_video,
                request_path=paths.background_request,
                result_path=paths.background_result,
                request_id_path=paths.root / "background" / "background_request_id.txt",
                wait_seconds=int(args.wait_seconds),
            )
    if args.only in ("all", "characters"):
        for emotion in emotions:
            if paths.character_video(emotion).exists() and not args.force:
                print(f"[skip] character exists: {emotion} {paths.character_video(emotion)}")
                continue
            submit_seedance_i2v(
                prompt=character_prompt(emotion),
                image_path=character_sources[emotion],
                out_video=paths.character_video(emotion),
                request_path=paths.character_request(emotion),
                result_path=paths.character_result(emotion),
                request_id_path=paths.root / "characters" / emotion / f"character_{emotion}_request_id.txt",
                wait_seconds=int(args.wait_seconds),
            )


def chroma_mask(frame_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (35, 50, 50), (95, 255, 255))
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return cv2.GaussianBlur(mask, (5, 5), 0)


def composite_one(bg_path: Path, char_path: Path, out_path: Path) -> None:
    if not bg_path.is_file():
        raise FileNotFoundError(bg_path)
    if not char_path.is_file():
        raise FileNotFoundError(char_path)
    bg = cv2.VideoCapture(str(bg_path))
    ch = cv2.VideoCapture(str(char_path))
    fps = float(bg.get(cv2.CAP_PROP_FPS) or 24.0)
    w = int(bg.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(bg.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_bg = int(bg.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    n_ch = int(ch.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    n = min(n_bg, n_ch)
    if w <= 0 or h <= 0 or n <= 0:
        raise RuntimeError(f"invalid videos: {bg_path}, {char_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"failed to open writer: {tmp}")
    for _ in range(n):
        ok_bg, bg_frame = bg.read()
        ok_ch, ch_frame = ch.read()
        if not ok_bg or not ok_ch:
            break
        if ch_frame.shape[:2] != (h, w):
            ch_frame = cv2.resize(ch_frame, (w, h), interpolation=cv2.INTER_AREA)
        mask = chroma_mask(ch_frame).astype(np.float32) / 255.0
        alpha = 1.0 - mask[..., None]
        frame = (ch_frame.astype(np.float32) * alpha + bg_frame.astype(np.float32) * (1.0 - alpha)).astype(np.uint8)
        writer.write(frame)
    bg.release()
    ch.release()
    writer.release()
    tmp.replace(out_path)


def composite(args: argparse.Namespace) -> None:
    paths = LayerPaths(Path(args.root))
    emotions = tuple(args.emotions or EMOTIONS)
    for emotion in emotions:
        composite_one(paths.background_video, paths.character_video(emotion), paths.composite_video(emotion))


def analyze(args: argparse.Namespace) -> None:
    paths = LayerPaths(Path(args.root))
    emotions = tuple(args.emotions or EMOTIONS)
    for emotion in emotions:
        video = paths.composite_video(emotion)
        if not video.is_file():
            raise FileNotFoundError(video)
        run([
            "uv", "run", "python", "face_track_anime_detector.py",
            "--video", str(video),
            "--out", str(paths.track(emotion)),
            "--quality", "custom",
            "--det-scale", "1.0",
            "--stride", "1",
            "--device", "auto",
            "--debug", str(paths.debug_track(emotion)),
        ])
        shutil.copyfile(paths.track(emotion), paths.track_calibrated(emotion))
        if emotion == "surprise":
            tight_track = paths.root / "composited" / "mouth_track_surprise_mouth_only_bottom95_calibrated.npz"
            run([
                "uv", "run", "python", "tools/erase_surprise_mouth_fullframe.py",
                "--video", str(video),
                "--track", str(paths.track_calibrated(emotion)),
                "--out", str(paths.mouthless_video(emotion)),
                "--out-track", str(tight_track),
            ])
            shutil.copyfile(tight_track, paths.track_calibrated(emotion))
        else:
            run([
                "uv", "run", "python", "auto_erase_mouth.py",
                "--video", str(video),
                "--track", str(paths.track_calibrated(emotion)),
                "--out", str(paths.mouthless_video(emotion)),
            ])


def launch(args: argparse.Namespace) -> None:
    paths = LayerPaths(Path(args.root))
    for emotion in EMOTIONS:
        for path in [paths.mouthless_video(emotion), paths.track(emotion), paths.track_calibrated(emotion)]:
            if not path.is_file():
                raise FileNotFoundError(path)
    run([
        "uv", "run", "python", "loop_lipsync_runtime_patched_emotion_auto.py",
        "--loop-video", str(paths.mouthless_video("joy")),
        "--mouth-dir", str(ASSET_ROOT / "mouth"),
        "--track", str(paths.track("joy")),
        "--track-calibrated", str(paths.track_calibrated("joy")),
        "--emotion-video-dir", str(paths.root / "composited"),
        "--emotion", "joy",
        "--device", str(args.device),
        "--preview-scale", "1.0",
        "--full-w", "1280",
        "--full-h", "720",
        "--render-fps", "24",
        "--window-name", "Dokochan Seedance Layered Lipsync",
        "--no-emotion-hud",
        "--irodori-tts-ui",
        "--irodori-tts-dir", str(paths.root / "irodori"),
    ])


def verify(args: argparse.Namespace) -> None:
    paths = LayerPaths(Path(args.root))
    missing: list[str] = []
    checks = [paths.background_video]
    for emotion in EMOTIONS:
        checks.extend([
            paths.character_video(emotion),
            paths.composite_video(emotion),
            paths.mouthless_video(emotion),
            paths.track(emotion),
            paths.track_calibrated(emotion),
        ])
    for path in checks:
        if not path.is_file():
            missing.append(str(path))
    if missing:
        print("missing:")
        for item in missing:
            print(f"  {item}")
        raise SystemExit(1)
    print("all required layered Seedance artifacts exist")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(LAYER_ROOT), help="layer artifact root")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate", help="request Seedance 2.0 layer videos")
    p_gen.add_argument("--background-source", default=str(REFERENCE_ROOT / "background_only_ref_v2.png"))
    p_gen.add_argument("--character-source-dir", default=str(REFERENCE_ROOT))
    p_gen.add_argument("--joy-source")
    p_gen.add_argument("--anger-source")
    p_gen.add_argument("--sad-source")
    p_gen.add_argument("--surprise-source")
    p_gen.add_argument("--only", choices=("all", "background", "characters"), default="all")
    p_gen.add_argument("--emotions", nargs="*", choices=EMOTIONS)
    p_gen.add_argument("--wait-seconds", type=int, default=900)
    p_gen.add_argument("--force", action="store_true", help="regenerate even if output video already exists")
    p_gen.set_defaults(func=generate)

    p_comp = sub.add_parser("composite", help="chroma-key merge generated videos")
    p_comp.add_argument("--emotions", nargs="*", choices=EMOTIONS)
    p_comp.set_defaults(func=composite)

    p_analyze = sub.add_parser("analyze", help="track mouth and create mouthless videos")
    p_analyze.add_argument("--emotions", nargs="*", choices=EMOTIONS)
    p_analyze.set_defaults(func=analyze)

    p_launch = sub.add_parser("launch", help="launch runtime with microphone input")
    p_launch.add_argument("--device", default="0")
    p_launch.set_defaults(func=launch)

    p_verify = sub.add_parser("verify", help="verify required artifacts exist")
    p_verify.set_defaults(func=verify)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
