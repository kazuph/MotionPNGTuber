"""
loop_lipsync_runtime_patched.py

ベース動画（AI生成ループmp4）を再生しつつ、
リアルタイム音声から推定した口形スプライトを、
フレームごとの口位置トラック（mouth_track.npz / mouth_track_calibrated.npz）に従って
ワープ合成して OpenCVプレビュー / pyvirtualcam(OBS)へ出力する。

更新点:
- mouth_track_calibrated.npz を自動優先（無ければ mouth_track.npz）
- npz 追加キー（confidence/ref_sprite/calib等）があってもOK
- validが0のフレームの扱いを改善（デフォルト: hold=近傍で埋めたquadを使用）
  - 従来挙動に戻す: --valid-policy strict
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import subprocess
import sys
import threading
import time
import queue
import tempfile
import urllib.request
import wave
from dataclasses import dataclass

try:
    import tkinter as tk
except Exception:
    tk = None  # GUI unavailable
from collections import deque

import cv2
import numpy as np
from motionpngtuber.mouth_color_adjust import (
    MouthColorAdjust,
    apply_inspect_boost_3ch,
    apply_mouth_color_adjust_4ch,
    clamp_mouth_color_adjust,
    estimate_auto_mouth_color_adjust,
    sample_background_ring_mean_3ch,
    sample_colored_edge_mean_4ch,
)

# (optional) lightweight audio-only emotion analyzer (numpy only)
try:
    from motionpngtuber.realtime_emotion_audio import RealtimeEmotionAnalyzer  # type: ignore
    HAS_EMOTION_AUDIO = True
except Exception:
    RealtimeEmotionAnalyzer = None  # type: ignore
    HAS_EMOTION_AUDIO = False

import sounddevice as sd
from motionpngtuber.audio_linux import (
    cleanup_audio_device_resolution,
    normalize_audio_device_spec,
    resolve_audio_device_spec,
    apply_audio_resolution_for_current_process,
)

# ========= Import from shared core module =========
from motionpngtuber.lipsync_core import (
    AudioChunkBuffer,
    # Utility functions
    one_pole_beta,
    open_video_capture,
    probe_video_size,
    resolve_preferred_track_path,
    alpha_blit_rgb_safe,
    warp_rgba_to_quad,
    # Classes
    MouthTrack,
    BgVideo,
    # Mouth sprite functions
    load_mouth_sprites,
    discover_mouth_sets,
    # Emotion utilities
    pick_mouth_set_for_label,
    infer_label_from_set_name,
    format_emotion_hud_text,
    EMOJI_BY_LABEL,
)

HERE = os.path.abspath(os.path.dirname(__file__))
LAST_SESSION_FILE = os.path.join(HERE, ".mouth_track_last_session.json")
__VERSION__ = "v7-shared-core"
MOUTH_LEVEL_DEADBAND = 0.04
JOY_MOUTH_SHAPE_STABLE_SEC = 0.08


try:
    import pyvirtualcam

    HAS_VCAM = True
except Exception:
    HAS_VCAM = False


def _parse_device_index(s: str) -> int | None:
    # "31: CABLE Output (...)" のような形式を想定
    try:
        head = str(s).split(":", 1)[0].strip()
        return int(head)
    except Exception:
        return None


def classify_mouth_level_with_hysteresis(
    env: float,
    half_th: float,
    open_th: float,
    prev_level: str = "closed",
    *,
    deadband: float = MOUTH_LEVEL_DEADBAND,
) -> str:
    """しきい値境界の往復で口形が暴れないようにヒステリシスをかける。"""
    env = float(env)
    half_th = float(half_th)
    open_th = float(open_th)
    deadband = max(0.0, float(deadband))
    prev = prev_level if prev_level in {"closed", "half", "open"} else "closed"

    if prev == "closed":
        if env >= half_th + deadband:
            return "half" if env < open_th else "open"
        return "closed"
    if prev == "half":
        if env < half_th - deadband:
            return "closed"
        if env >= open_th + deadband:
            return "open"
        return "half"
    if env < open_th - deadband:
        return "half" if env >= half_th else "closed"
    return "open"


def soften_mouth_shape_for_emotion(
    emotion: str,
    shape: str,
    env: float,
    half_th: float,
    open_th: float,
    mouth_set: dict[str, np.ndarray] | None = None,
) -> str:
    """Joy smiles look jumpy when vowel variants flash between very different silhouettes."""
    if str(emotion).lower() != "joy":
        return shape

    available = mouth_set or {}
    if shape in {"wide", "e", "u"}:
        if env >= min(1.0, open_th + 0.24) and "open" in available:
            return "open"
        if "half" in available:
            return "half"
        return "small" if "small" in available else shape
    if shape == "open" and env < min(1.0, open_th + 0.16):
        return "half" if "half" in available else shape
    if shape == "half" and env < max(open_th, half_th + 0.12):
        return "small" if "small" in available else shape
    return shape


def stabilize_mouth_shape(
    candidate: str,
    current: str,
    pending: str | None,
    pending_since: float,
    now: float,
    stable_sec: float,
) -> tuple[str, str | None, float]:
    if candidate == current:
        return current, None, now
    if current == "closed" or candidate == "closed":
        return candidate, None, now
    if stable_sec <= 0:
        return candidate, None, now
    if candidate != pending:
        return current, candidate, now
    if now - pending_since >= stable_sec:
        return candidate, None, now
    return current, pending, pending_since


def _show_preview_frame(window_name: str, frame_rgb: np.ndarray) -> int:
    """プレビュー描画。macOS などで OpenCV GUI が不安定でもランタイムを止めない。"""
    try:
        cv2.imshow(window_name, cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR))
        return int(cv2.waitKey(1) & 0xFF)
    except cv2.error:
        return -1


def _matches_ipc_token(data: dict, session_token: str) -> bool:
    if not session_token:
        return True
    return str(data.get("session_token", "") or "").strip() == session_token


def _load_live_color_control(path: str, session_token: str = "") -> tuple[float, MouthColorAdjust] | None:
    if not path or (not os.path.isfile(path)):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not _matches_ipc_token(data, session_token):
        return None
    updated_at = float(data.get("updated_at", 0.0) or 0.0)
    cfg = clamp_mouth_color_adjust(
        MouthColorAdjust(
            brightness=float(data.get("mouth_brightness", 0.0)),
            saturation=float(data.get("mouth_saturation", 1.0)),
            warmth=float(data.get("mouth_warmth", 0.0)),
            color_strength=float(data.get("mouth_color_strength", 0.75)),
            edge_priority=float(data.get("mouth_edge_priority", 0.85)),
            edge_width_ratio=float(data.get("mouth_edge_width_ratio", 0.10)),
            inspect_boost=float(data.get("mouth_inspect_boost", 1.0)),
        ),
    )
    return updated_at, cfg


def _load_auto_color_request(path: str, session_token: str = "") -> tuple[str, float] | None:
    if not path or (not os.path.isfile(path)):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not _matches_ipc_token(data, session_token):
        return None
    request_id = str(data.get("request_id", "") or "").strip()
    if not request_id:
        return None
    requested_at = float(data.get("requested_at", 0.0) or 0.0)
    return request_id, requested_at


def _write_json_atomic(path: str, payload: dict) -> None:
    out_path = os.path.abspath(path)
    out_dir = os.path.dirname(out_path) or HERE
    os.makedirs(out_dir, exist_ok=True)
    tmp_path = os.path.join(out_dir, f".tmp_{time.time_ns()}.json")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, out_path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass


def _rebuild_adjusted_mouth_sets(
    mouth_sets_original: dict[str, dict[str, np.ndarray]],
    cfg: MouthColorAdjust,
) -> dict[str, dict[str, np.ndarray]]:
    return {
        emo_name: {
            key: apply_mouth_color_adjust_4ch(val, cfg, color_order="RGBA")
            for key, val in mouth_map.items()
        }
        for emo_name, mouth_map in mouth_sets_original.items()
    }


def _rebuild_runtime_mouth_color_sets(
    mouth_sets_original: dict[str, dict[str, np.ndarray]],
    cfg: MouthColorAdjust,
    *,
    inspect_levels: tuple[float, ...],
) -> tuple[dict[str, dict[str, np.ndarray]], MouthColorAdjust, float, float]:
    reload_t0 = time.perf_counter()
    mouth_sets = _rebuild_adjusted_mouth_sets(mouth_sets_original, cfg)
    inspect_boost = min(inspect_levels, key=lambda x: abs(x - float(cfg.inspect_boost)))
    reload_dt = time.perf_counter() - reload_t0
    return mouth_sets, cfg, inspect_boost, reload_dt


def _select_runtime_mouth_view(
    mouth_sets: dict[str, dict[str, np.ndarray]],
    current_emotion: str,
) -> tuple[str, dict[str, np.ndarray]]:
    if current_emotion in mouth_sets:
        next_emotion = current_emotion
    else:
        next_emotion = sorted(mouth_sets.keys())[0]
    return next_emotion, mouth_sets[next_emotion]


def _emotion_button_label(name: str) -> str:
    lower = name.lower()
    labels = {
        "joy": "喜",
        "happy": "喜",
        "anger": "怒",
        "angry": "怒",
        "sad": "哀",
        "surprise": "驚",
        "surprised": "驚",
    }
    return labels.get(lower, name)


def _sort_emotions_for_ui(emotions: list[str]) -> list[str]:
    order = {
        "joy": 0,
        "happy": 0,
        "anger": 1,
        "angry": 1,
        "sad": 2,
        "surprise": 3,
        "surprised": 3,
    }
    return sorted(emotions, key=lambda x: (order.get(x.lower(), 99), x.lower()))


def _resolve_emotion_asset_paths(emotion_video_dir: str, emotion: str) -> tuple[str, str, str]:
    base = os.path.abspath(emotion_video_dir)
    candidates = [
        os.path.join(base, f"loop_{emotion}_mouthless.mp4"),
        os.path.join(base, f"{emotion}_mouthless.mp4"),
        os.path.join(base, f"loop_{emotion}.mp4"),
    ]
    video = next((p for p in candidates if os.path.isfile(p)), candidates[0])
    track = os.path.join(base, f"mouth_track_{emotion}.npz")
    track_calibrated = os.path.join(base, f"mouth_track_{emotion}_calibrated.npz")
    return video, track, track_calibrated


@dataclass(frozen=True)
class MouthColorRebuildResult:
    updated_at: float
    cfg: MouthColorAdjust
    mouth_sets: dict[str, dict[str, np.ndarray]]
    inspect_boost: float
    reload_dt: float
    reason: str


class AsyncMouthColorRebuilder:
    def __init__(
        self,
        mouth_sets_original: dict[str, dict[str, np.ndarray]],
        inspect_levels: tuple[float, ...],
    ) -> None:
        self._mouth_sets_original = mouth_sets_original
        self._inspect_levels = inspect_levels
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._pending: tuple[float, MouthColorAdjust, str] | None = None
        self._ready: MouthColorRebuildResult | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="mouth-color-rebuilder",
            daemon=True,
        )
        self._thread.start()

    def submit(self, *, updated_at: float, cfg: MouthColorAdjust, reason: str) -> None:
        with self._lock:
            self._pending = (float(updated_at), cfg, str(reason))
            self._wake.set()

    def pop_ready(self) -> MouthColorRebuildResult | None:
        with self._lock:
            ready = self._ready
            self._ready = None
            return ready

    def close(self) -> None:
        with self._lock:
            self._stop = True
            self._wake.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while True:
            self._wake.wait(0.1)
            with self._lock:
                if self._stop and self._pending is None:
                    return
                request = self._pending
                self._pending = None
                self._wake.clear()
            if request is None:
                continue
            updated_at, cfg, reason = request
            mouth_sets, _cfg, inspect_boost, reload_dt = _rebuild_runtime_mouth_color_sets(
                self._mouth_sets_original,
                cfg,
                inspect_levels=self._inspect_levels,
            )
            result = MouthColorRebuildResult(
                updated_at=updated_at,
                cfg=cfg,
                mouth_sets=mouth_sets,
                inspect_boost=inspect_boost,
                reload_dt=reload_dt,
                reason=reason,
            )
            with self._lock:
                pending = self._pending
                if pending is not None and float(pending[0]) > updated_at:
                    self._wake.set()
                    continue
                self._ready = result


class WavAudioInputStream:
    """Feed a WAV file through the same callback path as sounddevice.InputStream."""

    def __init__(
        self,
        wav_path: str,
        blocksize: int,
        callback,
        *,
        play_audio: bool = False,
        target_samplerate: int | None = None,
    ) -> None:
        self.wav_path = wav_path
        self.blocksize = max(1, int(blocksize))
        self.callback = callback
        self.play_audio = bool(play_audio)
        self.latency = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._stream_lock = threading.RLock()
        self._pos = 0
        self._analysis_pos = 0
        playback_audio, playback_samplerate = load_wav_mono_float32(wav_path)
        self.playback_audio = playback_audio
        self.playback_samplerate = int(playback_samplerate)
        self.audio = playback_audio
        self.samplerate = int(playback_samplerate)
        if target_samplerate and int(target_samplerate) > 0 and int(target_samplerate) != int(self.samplerate):
            self.audio = resample_linear_float32(self.audio, int(self.samplerate), int(target_samplerate))
            self.samplerate = int(target_samplerate)

    def __enter__(self):
        self._stop.clear()
        if self.play_audio:
            try:
                default_output = sd.default.device[1] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
                output_info = sd.query_devices(default_output)
                output_samplerate = int(round(float(output_info.get("default_samplerate") or self.playback_samplerate)))
            except Exception:
                output_samplerate = self.playback_samplerate
            if output_samplerate > 0 and output_samplerate != self.playback_samplerate:
                self.playback_audio = resample_linear_float32(
                    self.playback_audio,
                    self.playback_samplerate,
                    output_samplerate,
                )
                self.playback_samplerate = output_samplerate
            self._stream = sd.OutputStream(
                samplerate=self.playback_samplerate,
                channels=1,
                blocksize=max(1, int(round(self.blocksize * float(self.playback_samplerate) / float(max(1, self.samplerate))))),
                dtype="float32",
                callback=self._output_cb,
                latency="low",
            )
            self._stream.start()
            self.latency = float(getattr(self._stream, "latency", 0.0) or 0.0)
        else:
            self._thread = threading.Thread(target=self._run, name="wav-audio-input", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        self._close_output_stream()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _close_output_stream(self) -> None:
        with self._stream_lock:
            if self._stream is None:
                return
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    @staticmethod
    def _read_chunk_looped(audio: np.ndarray, start: int, frames: int) -> np.ndarray:
        n = int(audio.shape[0])
        out = np.zeros((int(frames),), dtype=np.float32)
        if n <= 0:
            return out
        start = int(start) % n
        remaining = int(frames)
        dst = 0
        while remaining > 0:
            take = min(remaining, n - start)
            out[dst:dst + take] = self.audio[start:start + take]
            dst += take
            remaining -= take
            start = 0
        return out

    @staticmethod
    def _read_chunk_once(audio: np.ndarray, start: int, frames: int) -> tuple[np.ndarray, bool]:
        n = int(audio.shape[0])
        out = np.zeros((int(frames),), dtype=np.float32)
        if n <= 0 or start >= n:
            return out, True
        take = min(int(frames), n - int(start))
        out[:take] = audio[int(start):int(start) + take]
        return out, (int(start) + take) >= n

    def _output_cb(self, outdata, frames, time_info, status) -> None:
        chunk, done = self._read_chunk_once(self.playback_audio, self._pos, int(frames))
        chunk_2d = chunk.reshape(-1, 1)
        outdata[:] = chunk_2d
        self._pos += int(frames)
        analysis_frames = max(1, int(round(int(frames) * float(self.samplerate) / float(max(1, self.playback_samplerate)))))
        analysis_chunk, analysis_done = self._read_chunk_once(self.audio, self._analysis_pos, analysis_frames)
        if analysis_frames < self.blocksize:
            analysis_chunk = np.pad(analysis_chunk, (0, self.blocksize - analysis_frames))
        elif analysis_frames > self.blocksize:
            analysis_chunk = resample_linear_float32(analysis_chunk, self.samplerate, int(round(self.samplerate * self.blocksize / analysis_frames)))
            if int(analysis_chunk.shape[0]) < self.blocksize:
                analysis_chunk = np.pad(analysis_chunk, (0, self.blocksize - int(analysis_chunk.shape[0])))
            else:
                analysis_chunk = analysis_chunk[:self.blocksize]
        self.callback(analysis_chunk.reshape(-1, 1), self.blocksize, time_info, status)
        self._analysis_pos += analysis_frames
        if done:
            self._stop.set()

    def _run(self) -> None:
        pos = 0
        n = int(self.audio.shape[0])
        try:
            while not self._stop.is_set():
                chunk, done = self._read_chunk_once(self.audio, pos, self.blocksize)
                self.callback(chunk.reshape(-1, 1), self.blocksize, None, None)
                pos += self.blocksize
                if done or pos >= n:
                    self._stop.set()
                    break
                time.sleep(self.blocksize / float(max(1, self.samplerate)))
        finally:
            self._close_output_stream()


def load_wav_mono_float32(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as wf:
        channels = int(wf.getnchannels())
        sampwidth = int(wf.getsampwidth())
        samplerate = int(wf.getframerate())
        frames = int(wf.getnframes())
        raw = wf.readframes(frames)
    if sampwidth == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sampwidth == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sampwidth == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported WAV sample width: {sampwidth}")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data.astype(np.float32, copy=False), samplerate


def resample_linear_float32(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr <= 0 or dst_sr <= 0 or src_sr == dst_sr or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    src_n = int(audio.shape[0])
    dst_n = max(1, int(round(src_n * float(dst_sr) / float(src_sr))))
    src_x = np.arange(src_n, dtype=np.float64)
    dst_x = np.linspace(0, max(0, src_n - 1), dst_n, dtype=np.float64)
    return np.interp(dst_x, src_x, audio.astype(np.float32)).astype(np.float32)


class SwitchableAudioInputStream:
    """Keep mic input alive and overlay TTS WAV through the same callback path."""

    def __init__(self, initial_stream, blocksize: int, callback, samplerate: int, reopen_current=None) -> None:
        self._lock = threading.RLock()
        self._stream = initial_stream
        self._tts_stream: WavAudioInputStream | None = None
        self._reopen_current = reopen_current
        self.blocksize = max(1, int(blocksize))
        self.callback = callback
        self.samplerate = int(samplerate)
        self.latency = float(getattr(initial_stream, "latency", 0.0) or 0.0)
        self._entered = False

    def __enter__(self):
        with self._lock:
            if not self._entered:
                self._stream.__enter__() if hasattr(self._stream, "__enter__") else self._stream.start()
                self.latency = float(getattr(self._stream, "latency", 0.0) or 0.0)
                self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        with self._lock:
            self._close_tts()
            self._close_current()
            self._entered = False

    def _close_current(self) -> None:
        if self._stream is None:
            return
        try:
            if hasattr(self._stream, "__exit__"):
                self._stream.__exit__(None, None, None)
            else:
                self._stream.stop()
                self._stream.close()
        except Exception as e:
            print(f"[audio warn] failed to close stream: {e}")
        self._stream = None

    def _open_current(self) -> None:
        if self._stream is not None or self._reopen_current is None:
            return
        try:
            self._stream = self._reopen_current()
            self._stream.__enter__() if hasattr(self._stream, "__enter__") else self._stream.start()
            self.latency = float(getattr(self._stream, "latency", 0.0) or 0.0)
            print("[audio] restored live input stream")
        except Exception as e:
            print(f"[audio warn] failed to restore live input stream: {e}")

    def _close_tts(self) -> None:
        if self._tts_stream is None:
            return
        try:
            self._tts_stream.__exit__(None, None, None)
        except Exception as e:
            print(f"[audio warn] failed to close TTS stream: {e}")
        self._tts_stream = None

    def switch_to_wav(self, wav_path: str, *, play_audio: bool = True) -> None:
        next_stream = WavAudioInputStream(
            wav_path,
            self.blocksize,
            self.callback,
            play_audio=play_audio,
            target_samplerate=self.samplerate,
        )
        with self._lock:
            self._close_tts()
            restore_live_input = bool(play_audio and self._reopen_current is not None)
            if restore_live_input:
                self._close_current()
            next_stream.__enter__()
            self._tts_stream = next_stream
            self.latency = max(
                float(getattr(self._stream, "latency", 0.0) or 0.0) if self._stream is not None else 0.0,
                float(getattr(next_stream, "latency", 0.0) or 0.0),
            )
        if restore_live_input:
            threading.Thread(target=self._restore_after_tts, args=(next_stream,), name="restore-live-input", daemon=True).start()
        print(f"[audio] started TTS WAV synced playback: {wav_path}")

    def _restore_after_tts(self, tts_stream: WavAudioInputStream) -> None:
        while not tts_stream._stop.is_set():
            time.sleep(0.02)
        with self._lock:
            if self._tts_stream is tts_stream:
                self._close_tts()
                self._open_current()


def synthesize_irodori_tts(text: str, out_dir: str) -> str:
    voice = os.environ.get("IRODORI_VOICE_LOCK", "").strip()
    if not voice:
        lab_url = os.environ.get("DOKOCHAN_VOICE_LAB_URL", "http://127.0.0.1:8766").rstrip("/")
        body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{lab_url}/api/synthesize-selected",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        wav_path = str(payload.get("wav_path") or "")
        if not wav_path or not os.path.isfile(wav_path):
            raise RuntimeError("Dokochan Voice Lab did not return a usable WAV path.")
        print(f"[tts] generated repo-local Irodori WAV: {wav_path}")
        return wav_path

    url = os.environ.get("IRODORI_TTS_URL", "http://100.80.152.112:8088/api/tts/v1/tts")
    body = json.dumps({"text": text, "voice_lock_id": voice}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    os.makedirs(out_dir, exist_ok=True)
    fd, out_path = tempfile.mkstemp(prefix="irodori_", suffix=".wav", dir=out_dir)
    os.close(fd)
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            data = res.read()
        with open(out_path, "wb") as f:
            f.write(data)
        _probe, sr = load_wav_mono_float32(out_path)
        print(f"[tts] generated Irodori WAV: {out_path} sr:{sr} bytes:{len(data)}")
        return out_path
    except Exception:
        try:
            os.unlink(out_path)
        except OSError:
            pass
        raise


def _apply_runtime_mouth_color_update(
    mouth_sets_original: dict[str, dict[str, np.ndarray]],
    cfg: MouthColorAdjust,
    *,
    current_emotion: str,
    inspect_levels: tuple[float, ...],
) -> tuple[dict[str, dict[str, np.ndarray]], MouthColorAdjust, float, str, dict[str, np.ndarray], float]:
    mouth_sets, cfg, inspect_boost, reload_dt = _rebuild_runtime_mouth_color_sets(
        mouth_sets_original,
        cfg,
        inspect_levels=inspect_levels,
    )
    next_emotion, mouth = _select_runtime_mouth_view(mouth_sets, current_emotion)
    return mouth_sets, cfg, inspect_boost, next_emotion, mouth, reload_dt


def _estimate_auto_color_result(
    frame_rgb: np.ndarray,
    spr_rgba: np.ndarray,
    *,
    x0: int,
    y0: int,
    current_cfg: MouthColorAdjust,
) -> dict | None:
    mouth_sample = sample_colored_edge_mean_4ch(
        spr_rgba,
        edge_width_ratio=current_cfg.edge_width_ratio,
        color_order="RGBA",
        alpha_threshold=24,
    )
    if mouth_sample is None:
        return None
    mouth_mean, mouth_count = mouth_sample
    bg_sample = sample_background_ring_mean_3ch(
        frame_rgb,
        spr_rgba[..., 3],
        x0,
        y0,
        edge_width_ratio=current_cfg.edge_width_ratio,
        color_order="RGB",
        alpha_threshold=24,
    )
    if bg_sample is None:
        return None
    bg_mean, bg_count = bg_sample
    new_cfg, debug = estimate_auto_mouth_color_adjust(
        current_cfg,
        bg_mean=bg_mean,
        mouth_mean=mouth_mean,
        color_order="RGB",
    )
    return {
        "cfg": new_cfg,
        "bg_sample_count": int(bg_count),
        "mouth_sample_count": int(mouth_count),
        "debug": debug,
    }


def _compose_mouth_patch(
    mouth_set: dict[str, np.ndarray],
    mouth_shape: str,
    frame_idx: int,
    track: MouthTrack | None,
    scale: float,
    fixed_x: int,
    fixed_y: int,
) -> dict[str, object]:
    spr = mouth_set.get(mouth_shape, mouth_set["closed"])
    quad = track.get_quad(frame_idx) if track is not None else None
    if quad is None:
        x = int(fixed_x * scale - spr.shape[1] // 2)
        y = int(fixed_y * scale - spr.shape[0] // 2)
        patch = spr
        return {
            "sprite": spr,
            "patch": patch,
            "x0": x,
            "y0": y,
            "quad": None,
        }
    patch, x0, y0 = warp_rgba_to_quad(spr, quad)
    return {
        "sprite": spr,
        "patch": patch,
        "x0": x0,
        "y0": y0,
        "quad": quad,
    }


def start_emotion_selector_gui(
    emotions: list[str],
    initial: str,
    selection_q: "queue.Queue[str]",
    title: str = "Mouth Emotion",
):
    """
    Start a tiny Tk GUI (non-blocking) that lets user switch emotion sprite sets.

    The GUI thread will push the selected emotion name into `selection_q`.
    """
    if tk is None:
        print("[warn] tkinter is not available; emotion GUI is disabled.")
        return None

    emotions = list(emotions)
    if not emotions:
        return None

    def _runner():
        try:
            root = tk.Tk()
            root.title(title)

            frm = tk.Frame(root, padx=10, pady=10)
            frm.pack(fill="both", expand=True)

            # 日本語フォルダ名表示対応: Windowsでは日本語フォントを優先（フォールバック: システムデフォルト）
            if platform.system() == "Windows":
                font_bold = ("Meiryo", 12, "bold")
                font_norm = ("Meiryo", 11)
            else:
                font_bold = None
                font_norm = None

            lbl_kwargs = {"font": font_bold} if font_bold else {}
            tk.Label(frm, text="Emotion", **lbl_kwargs).pack(anchor="w")

            def push_selection(v: str):
                try:
                    selection_q.put_nowait(v)
                except Exception:
                    pass

            btn_kwargs = {"font": font_norm} if font_norm else {}

            for emo in emotions:
                btn = tk.Button(
                    frm,
                    text=f"{_emotion_button_label(emo)}  {emo}",
                    command=lambda v=emo: push_selection(v),
                    anchor="center",
                    **btn_kwargs,
                )
                btn.pack(fill="x", pady=2)

            push_selection(initial)

            try:
                root.update_idletasks()
                sw = root.winfo_screenwidth()
                sh = root.winfo_screenheight()
                ww = max(160, root.winfo_reqwidth())
                wh = max(140, root.winfo_reqheight())
                root.geometry(f"{ww}x{wh}+{max(0, sw - ww - 24)}+{max(0, sh - wh - 80)}")
            except Exception:
                pass

            def _on_close():
                try:
                    root.destroy()
                except Exception:
                    pass

            root.protocol("WM_DELETE_WINDOW", _on_close)
            root.mainloop()
        except Exception as e:
            print(f"[warn] emotion GUI failed: {e}")

    th = threading.Thread(target=_runner, daemon=True)
    th.start()
    return th


def start_emotion_buttons_gui(
    emotions: list[str],
    initial: str,
    selection_q: "queue.Queue[str]",
    tts_q: "queue.Queue[str] | None" = None,
    mode_q: "queue.Queue[str] | None" = None,
    *,
    enable_tts: bool = False,
    tts_dir: str = "",
    title: str = "Mouth Emotion",
):
    """Launch the Tk button panel as a helper process.

    macOS Japanese IME can deadlock when Tk is pumped from OpenCV's render loop,
    and Tk initialization is also fragile from a non-main thread.  Keeping the
    panel in its own Python process isolates UI event handling from lipsync.
    """
    if tk is None:
        print("[warn] tkinter is not available; emotion GUI is disabled.")
        return None
    if not emotions:
        return None

    control_dir = os.path.join(tempfile.gettempdir(), f"dokochan_panel_{os.getpid()}")
    os.makedirs(control_dir, exist_ok=True)
    event_path = os.path.join(control_dir, "events.jsonl")
    open(event_path, "a", encoding="utf-8").close()

    helper = os.path.join(HERE, "tools", "dokochan_emotion_tts_panel.py")
    cmd = [
        sys.executable,
        helper,
        "--event-path",
        event_path,
        "--title",
        title,
        "--initial",
        initial,
        "--emotions",
        ",".join(emotions),
        "--tts-dir",
        tts_dir or tempfile.gettempdir(),
    ]
    if enable_tts and tts_q is not None:
        cmd.append("--enable-tts")
    proc = subprocess.Popen(cmd)
    push_selection = lambda v: selection_q.put_nowait(v)
    try:
        push_selection(initial)
    except Exception:
        pass

    state = {"offset": 0, "stop": False}

    def _poll_events():
        while not state["stop"]:
            try:
                with open(event_path, "r", encoding="utf-8") as f:
                    f.seek(state["offset"])
                    for line in f:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "emotion":
                            try:
                                selection_q.put_nowait(str(event.get("value", "")))
                            except Exception:
                                pass
                        elif event.get("type") == "tts" and tts_q is not None:
                            wav_path = str(event.get("path", ""))
                            if wav_path:
                                try:
                                    tts_q.put_nowait(wav_path)
                                except Exception:
                                    pass
                        elif event.get("type") == "input_mode" and mode_q is not None:
                            mode = str(event.get("value", ""))
                            if mode:
                                try:
                                    mode_q.put_nowait(mode)
                                except Exception:
                                    pass
                    state["offset"] = f.tell()
            except Exception:
                pass
            if proc.poll() is not None:
                break
            time.sleep(0.05)

    th = threading.Thread(target=_poll_events, name="emotion-tts-panel-poll", daemon=True)
    th.start()
    return proc


# ========= emotion auto / HUD =========
# Note: EMOJI_BY_LABEL, pick_mouth_set_for_label, format_emotion_hud_text, infer_label_from_set_name
# are now imported from lipsync_core

EMOTION_PRESET_PARAMS = {
    # stable (配信向け): switch less
    "stable": dict(smooth_alpha=0.18, min_hold_sec=0.75, cand_stable_sec=0.30, switch_margin=0.14),
    # standard
    "standard": dict(smooth_alpha=0.25, min_hold_sec=0.45, cand_stable_sec=0.22, switch_margin=0.10),
    # snappy (ゲーム向け): switch more
    "snappy": dict(smooth_alpha=0.35, min_hold_sec=0.25, cand_stable_sec=0.12, switch_margin=0.06),
}


def start_emotion_hud_gui(
    initial_text: str,
    title: str = "Emotion HUD",
    x: int = 12,
    y: int = 12,
    font_size: int = 28,
    alpha: float = 0.92,
):
    """HUD window (create on MAIN thread; update via root.update() in main loop)."""
    if tk is None:
        print("[warn] tkinter is not available; emotion HUD is disabled.")
        return None, None

    root = tk.Tk()
    root.title(title)

    # Emoji-friendly font
    if platform.system() == "Windows":
        font = ("Segoe UI Emoji", int(font_size), "bold")
    elif platform.system() == "Darwin":
        font = ("Apple Color Emoji", int(font_size), "bold")
    else:
        font = ("Noto Color Emoji", int(font_size), "bold")

    try:
        root.attributes("-alpha", float(alpha))
    except Exception:
        pass

    root.resizable(False, False)

    # 視認性UP: 少し太い枠 + 余白増
    lbl = tk.Label(
        root,
        text=initial_text,
        font=font,
        padx=16,
        pady=10,
        bg="#111",
        fg="#fff",
        relief="solid",
        borderwidth=2,
    )
    lbl.pack()

    root.geometry(f"+{x}+{y}")
    return root, lbl


def resolve_track_path(base_track: str, calibrated_track: str, prefer_calibrated: bool = True) -> str:
    return resolve_preferred_track_path(
        base_track,
        calibrated_track,
        prefer_calibrated=prefer_calibrated,
    )


def resolve_emotion_auto_target(
    lab: object,
    info: dict[str, float],
    emotions: list[str],
    neutral_set: str,
    *,
    silence_db: float,
    min_conf: float,
) -> tuple[str | None, str | None, str]:
    """Resolve the target emotion set for one analyzer output.

    Returns ``(target_label, target_set, reason)`` where ``reason`` is one of
    ``"silence"``, ``"unvoiced"``, ``"low_conf"``, or ``"label"``.
    """
    rms_db = float(info.get("rms_db", -120.0))
    conf = float(info.get("confidence", 0.0))
    voiced = float(info.get("voiced", 0.0)) >= 0.5

    if rms_db < float(silence_db):
        return "neutral", neutral_set, "silence"
    if not voiced:
        return None, None, "unvoiced"
    if conf < float(min_conf):
        return None, None, "low_conf"

    target_label = str(lab).lower()
    target_set = pick_mouth_set_for_label(emotions, target_label) or neutral_set
    return target_label, target_set, "label"


def run(args) -> None:
    try:
        cv2.setNumThreads(1)
    except Exception:
        pass

    if not os.path.isfile(args.loop_video):
        raise FileNotFoundError(f"Loop video not found: {args.loop_video}")

    # mouth_dir が未指定、または存在しない場合は候補を順に探す:
    # 1) loop_video と同じフォルダの mouth/（従来GUI仕様）
    # 2) このスクリプトと同じフォルダの mouth/（単体運用 / 感情フォルダ運用）
    # 3) このスクリプトと同じフォルダの mouth_dir/（旧/別名運用の後方互換）
    if (not args.mouth_dir) or (not os.path.isdir(args.mouth_dir)):
        cand1 = os.path.join(os.path.dirname(os.path.abspath(args.loop_video)), "mouth")
        cand2 = os.path.join(HERE, "mouth")
        cand3 = os.path.join(HERE, "mouth_dir")

        for c in (cand1, cand2, cand3):
            if os.path.isdir(c):
                args.mouth_dir = c
                print(f"[info] auto-detected mouth_dir: {c}")
                break

    if not os.path.isdir(args.mouth_dir):
        raise FileNotFoundError(f"mouth dir not found: {args.mouth_dir}")

    # ---- video sizes (auto-detect) ----
    full_w, full_h = args.full_w, args.full_h
    probed = probe_video_size(args.loop_video)
    if probed is not None:
        vw, vh = probed
        if full_w <= 0 or full_h <= 0:
            full_w, full_h = vw, vh
            print(f"[info] auto-detected video size: {full_w}x{full_h}")
        else:
            # デフォルト値のままなら、実動画サイズに合わせる（解像度違いでも壊れにくく）
            if (full_w, full_h) == (1440, 2560) and (vw, vh) != (1440, 2560):
                full_w, full_h = vw, vh
                print(f"[info] override full size to video size: {full_w}x{full_h}")
            else:
                # デフォルト値でなくても、アスペクト比が大きく異なる場合は動画サイズを優先
                req_aspect = full_w / max(1, full_h)
                vid_aspect = vw / max(1, vh)
                if abs(req_aspect - vid_aspect) > 0.05:
                    full_w, full_h = vw, vh
                    print(f"[info] aspect ratio mismatch, using video size: {full_w}x{full_h}")
    else:
        if full_w <= 0 or full_h <= 0:
            full_w, full_h = 1440, 2560
            print(f"[warn] could not probe video size, using default: {full_w}x{full_h}")

    # ---- audio device ----
    samplerate = 48000
    input_channels = 1
    audio_resolution: dict | None = None
    audio_apply_state: dict | None = None
    allow_default_source_switch = bool(getattr(args, "linux_allow_default_source_switch", False))

    def _resolve_input_device(*, prefer_default_source: bool = False):
        nonlocal audio_resolution, audio_apply_state, samplerate, input_channels
        if audio_resolution is not None:
            cleanup_audio_device_resolution(audio_resolution, audio_apply_state)
        spec = normalize_audio_device_spec(getattr(args, "audio_device_spec", "") or args.device)
        audio_resolution = resolve_audio_device_spec(
            spec,
            sd,
            fallback_index=args.device,
            prefer_default_source=prefer_default_source and allow_default_source_switch,
            allow_default_source_switch=allow_default_source_switch,
        )
        resolved_index = audio_resolution.get("resolved_index")
        if resolved_index is None:
            raise RuntimeError(
                f"audio input device could not be resolved: {audio_resolution.get('effective_spec') or spec}"
            )
        audio_apply_state = apply_audio_resolution_for_current_process(audio_resolution)
        args.device = int(resolved_index)
        dev = sd.query_devices(args.device, "input")
        samplerate = int(dev["default_samplerate"])
        max_in = int(dev.get("max_input_channels", 1) or 1)
        input_channels = 1
        print(
            "[audio] using device:",
            args.device,
            dev["name"],
            "sr:",
            samplerate,
            "max_in:",
            max_in,
            "ch:",
            input_channels,
            "strategy:",
            audio_resolution.get("strategy"),
        )

    audio_file_path = str(getattr(args, "audio_file", "") or "").strip()
    if audio_file_path:
        if not os.path.isfile(audio_file_path):
            raise FileNotFoundError(f"audio file not found: {audio_file_path}")
        _audio_probe, samplerate = load_wav_mono_float32(audio_file_path)
        input_channels = 1
        print(f"[audio] using WAV file: {audio_file_path} sr:{samplerate}")
    else:
        try:
            _resolve_input_device(prefer_default_source=False)
        except Exception as e:
            raw_spec = normalize_audio_device_spec(getattr(args, "audio_device_spec", "") or args.device)
            if raw_spec and str(raw_spec).startswith("pa:") and allow_default_source_switch:
                print(f"[audio] primary resolution failed, retrying fallback: {e}")
                _resolve_input_device(prefer_default_source=True)
            else:
                raise

    # ---- video sources ----
    prev_w = int(full_w * args.preview_scale)
    prev_h = int(full_h * args.preview_scale)
    vid_prev = BgVideo(args.loop_video, prev_w, prev_h)
    vid_full = BgVideo(args.loop_video, full_w, full_h) if (args.use_virtual_cam and HAS_VCAM) else None

    # ---- mouth sprites (emotion sets supported) ----
    print(f"[discover] scanning mouth_dir: {args.mouth_dir}")
    sets_dirs = discover_mouth_sets(args.mouth_dir)

    # もし指定ディレクトリにセットが無い場合は、よくある候補へフォールバック
    if not sets_dirs:
        fallback_candidates = [
            os.path.join(os.path.dirname(os.path.abspath(args.loop_video)), "mouth"),
            os.path.join(HERE, "mouth"),
            os.path.join(HERE, "mouth_dir"),
        ]
        for fb in fallback_candidates:
            if os.path.isdir(fb) and os.path.abspath(fb) != os.path.abspath(args.mouth_dir):
                fb_sets = discover_mouth_sets(fb)
                if fb_sets:
                    print(f"[discover] no sets under {args.mouth_dir}, fallback -> {fb}")
                    args.mouth_dir = fb
                    sets_dirs = fb_sets
                    break

    # GUIセッションから読み込んだ mouth_dir にサブフォルダがない場合（Defaultのみ）、
    # プロジェクトルートの mouth/（感情フォルダ運用）を優先してフォールバックとして試す
    if sets_dirs and len(sets_dirs) == 1 and "Default" in sets_dirs:
        fallback_candidates = [
            os.path.join(HERE, "mouth"),
            os.path.join(HERE, "mouth_dir"),  # 後方互換
        ]
        for fallback_mouth_dir in fallback_candidates:
            if os.path.isdir(fallback_mouth_dir) and os.path.abspath(fallback_mouth_dir) != os.path.abspath(args.mouth_dir):
                print(f"[discover] only 'Default' found, trying fallback: {fallback_mouth_dir}")
                fallback_sets = discover_mouth_sets(fallback_mouth_dir)
                if fallback_sets and len(fallback_sets) > 1:
                    sets_dirs = fallback_sets
                    args.mouth_dir = fallback_mouth_dir
                    print(f"[discover] using fallback mouth_dir with {len(sets_dirs)} emotion sets")
                    break

    if not sets_dirs:
        raise FileNotFoundError(
            f"No mouth sprite sets found under: {args.mouth_dir} (need open.png or subfolders with open.png)"
        )

    print(f"[discover] found {len(sets_dirs)} emotion set(s): {list(sets_dirs.keys())}")
    mouth_color_cfg = clamp_mouth_color_adjust(
        MouthColorAdjust(
            brightness=float(getattr(args, "mouth_brightness", 0.0)),
            saturation=float(getattr(args, "mouth_saturation", 1.0)),
            warmth=float(getattr(args, "mouth_warmth", 0.0)),
            color_strength=float(getattr(args, "mouth_color_strength", 0.75)),
            edge_priority=float(getattr(args, "mouth_edge_priority", 0.85)),
            edge_width_ratio=float(getattr(args, "mouth_edge_width_ratio", 0.10)),
            inspect_boost=float(getattr(args, "mouth_inspect_boost", 1.0)),
        ),
    )
    inspect_levels = (1.0, 2.0, 3.0, 4.0)
    inspect_boost = min(inspect_levels, key=lambda x: abs(x - float(mouth_color_cfg.inspect_boost)))
    mouth_sets_original: dict[str, dict[str, np.ndarray]] = {}
    for name, p in sets_dirs.items():
        try:
            mouth_sets_original[name] = load_mouth_sprites(p, full_w, full_h)
            print(f"[load] successfully loaded emotion set: '{name}'")
        except Exception as e:
            print(f"[warn] failed to load mouth set '{name}': {p} ({e})")

    mouth_sets, mouth_color_cfg, inspect_boost, _initial_reload_dt = _rebuild_runtime_mouth_color_sets(
        mouth_sets_original,
        mouth_color_cfg,
        inspect_levels=inspect_levels,
    )
    if not mouth_sets:
        raise RuntimeError(f"All mouth sprite sets failed to load under: {args.mouth_dir}")
    color_rebuilder = AsyncMouthColorRebuilder(mouth_sets_original, inspect_levels)

    emotions = _sort_emotions_for_ui(list(mouth_sets.keys()))

    # Determine which folder corresponds to "neutral" (fallback to Default/Neutral/first)
    neutral_set = pick_mouth_set_for_label(emotions, "neutral")
    if neutral_set is None:
        if "Neutral" in mouth_sets:
            neutral_set = "Neutral"
        elif "Default" in mouth_sets:
            neutral_set = "Default"
        else:
            neutral_set = emotions[0]

    # Emotion AUTO: audio-only emotion inference (no manual switching allowed)
    emotion_auto_enabled = bool(args.emotion_auto) and (len(mouth_sets) > 1) and HAS_EMOTION_AUDIO and (RealtimeEmotionAnalyzer is not None)
    if args.emotion_auto and not emotion_auto_enabled:
        if len(mouth_sets) <= 1:
            print("[emotion-auto] only one set found; auto switching is disabled.")
        elif not HAS_EMOTION_AUDIO:
            print("[emotion-auto] realtime_emotion_audio.py is missing; auto switching is disabled.")
        else:
            print("[emotion-auto] init failed; auto switching is disabled.")

    if emotion_auto_enabled:
        args.no_emotion_gui = True  # ensure manual GUI is disabled
        current_emotion = neutral_set
    else:
        desired = (args.emotion or "").strip()
        if desired and desired in mouth_sets:
            current_emotion = desired
        elif "Neutral" in mouth_sets:
            current_emotion = "Neutral"
        elif "Default" in mouth_sets:
            current_emotion = "Default"
        else:
            current_emotion = emotions[0]

    current_emotion, mouth = _select_runtime_mouth_view(mouth_sets, current_emotion)
    print(f"[emotion] available sets: {emotions}")
    print(f"[emotion] initial: {current_emotion}")
    print(
        "[mouth-color] "
        f"bri={mouth_color_cfg.brightness:.0f} "
        f"sat={mouth_color_cfg.saturation:.2f} "
        f"warm={mouth_color_cfg.warmth:.0f} "
        f"strength={mouth_color_cfg.color_strength:.2f} "
        f"edge={mouth_color_cfg.edge_priority:.2f} "
        f"width={mouth_color_cfg.edge_width_ratio:.2f} "
        f"inspect={inspect_boost:.1f}"
    )
    ipc_token = str(getattr(args, "mouth_ipc_token", "") or "").strip()
    live_control_path = str(getattr(args, "mouth_live_control", "") or "").strip()
    live_control_last_check = 0.0
    live_control_last_requested = 0.0
    live_control_last_applied = 0.0
    auto_request_path = str(getattr(args, "mouth_auto_request", "") or "").strip()
    auto_result_path = str(getattr(args, "mouth_auto_result", "") or "").strip()
    auto_request_last_check = 0.0
    auto_request_last_id = ""

    def _queue_color_rebuild(cfg: MouthColorAdjust, *, updated_at: float, reason: str) -> None:
        nonlocal live_control_last_requested
        color_rebuilder.submit(updated_at=updated_at, cfg=cfg, reason=reason)
        live_control_last_requested = max(live_control_last_requested, float(updated_at))

    def _apply_ready_color_rebuild() -> None:
        nonlocal mouth_sets, mouth_color_cfg, inspect_boost, current_emotion, mouth, live_control_last_applied
        ready = color_rebuilder.pop_ready()
        if ready is None or ready.updated_at <= live_control_last_applied:
            return
        mouth_sets = ready.mouth_sets
        mouth_color_cfg = ready.cfg
        inspect_boost = ready.inspect_boost
        current_emotion, mouth = _select_runtime_mouth_view(mouth_sets, current_emotion)
        live_control_last_applied = ready.updated_at
        tag = "auto-color" if ready.reason.startswith("auto") else "mouth-color"
        print(
            f"[{tag}] applied "
            f"bri={mouth_color_cfg.brightness:.0f} "
            f"sat={mouth_color_cfg.saturation:.2f} "
            f"warm={mouth_color_cfg.warmth:.0f} "
            f"strength={mouth_color_cfg.color_strength:.2f} "
            f"inspect={inspect_boost:.1f} "
            f"dt={ready.reload_dt*1000.0:.1f}ms"
        )
        if ready.reload_dt > 0.2:
            print(f"[{tag} warn] sprite rebuild exceeded 200ms; async apply prevented render blocking.")

    emotion_q: queue.Queue[str] = queue.Queue()
    tts_q: queue.Queue[str] = queue.Queue()
    input_mode_q: queue.Queue[str] = queue.Queue()
    input_mode = "audio"
    selector_root = None

    # Optional HUD (emoji + label). Default ON, can be disabled by --no-emotion-hud
    hud_q: queue.Queue[str] = queue.Queue()
    hud_root = None
    hud_lbl = None

    if bool(args.emotion_hud):
        init_label = infer_label_from_set_name(current_emotion)
        init_txt = format_emotion_hud_text(init_label)
        hud_root, hud_lbl = start_emotion_hud_gui(
            init_txt,
            title="Emotion HUD",
            font_size=int(getattr(args, "emotion_hud_font", 28)),
            alpha=float(getattr(args, "emotion_hud_alpha", 0.92)),
        )
        print("[hud] started:", init_txt)

    # Manual selector GUI is allowed only when emotion-auto is OFF
    if (not args.no_emotion_gui) and (not emotion_auto_enabled):
        selector_root = start_emotion_buttons_gui(
            emotions,
            current_emotion,
            emotion_q,
            tts_q,
            input_mode_q,
            enable_tts=bool(getattr(args, "irodori_tts_ui", False)),
            tts_dir=str(getattr(args, "irodori_tts_dir", "") or os.path.join(HERE, ".runtime_logs", "irodori")),
            title="Dokochan Emotion",
        )

    # Emotion auto analyzer
    emo_audio_q: queue.Queue[np.ndarray] | None = None
    emo_analyzer = None
    last_auto_label = infer_label_from_set_name(current_emotion)
    emo_buf = AudioChunkBuffer(max_samples=int(samplerate * 1.2))
    emo_window_sec = 0.25      # 0.25秒ぶんまとめて推定
    emo_eval_interval = 0.10   # 10Hzで推定
    emo_window_len = 0
    emo_last_eval = 0.0
    emo_last_debug = 0.0
    if emotion_auto_enabled:
        emo_audio_q = queue.Queue(maxsize=max(8, args.audio_hz * 2))
        emo_window_len = int(samplerate * emo_window_sec)
        preset = str(args.emotion_preset or "standard").strip().lower()
        params = EMOTION_PRESET_PARAMS.get(preset, EMOTION_PRESET_PARAMS["standard"])
        try:
            emo_analyzer = RealtimeEmotionAnalyzer(sr=int(samplerate), **params)  # type: ignore[misc]
            print(f"[emotion-auto] enabled preset={preset} neutral_set={neutral_set}")
        except Exception as e:
            print(f"[emotion-auto] init failed: {e}")
            emotion_auto_enabled = False

    # ---- mouth track (prefer calibrated) ----
    track_path = resolve_track_path(args.track, args.track_calibrated, prefer_calibrated=not args.no_prefer_calibrated)
    print(f"[info] track candidates: base={args.track} calibrated={args.track_calibrated}")
    print(f"[info] resolved track: {track_path}")
    track_prev = MouthTrack.load(track_path, prev_w, prev_h, policy=args.valid_policy)
    track_full = MouthTrack.load(track_path, full_w, full_h, policy=args.valid_policy) if vid_full is not None else None
    vid_full_auto: BgVideo | None = None
    track_full_auto: MouthTrack | None = None
    emotion_video_dir = str(getattr(args, "emotion_video_dir", "") or "").strip()

    def _switch_runtime_emotion(sel: str, *, source: str = "manual") -> None:
        nonlocal current_emotion, mouth, vid_prev, vid_full, track_prev, track_full, vid_full_auto, track_full_auto, track_path
        if sel not in mouth_sets:
            return

        current_emotion = sel
        mouth = mouth_sets[current_emotion]

        if emotion_video_dir:
            video_path, next_track, next_track_calibrated = _resolve_emotion_asset_paths(emotion_video_dir, current_emotion)
            if not os.path.isfile(video_path):
                print(f"[emotion warn] video not found for {current_emotion}: {video_path}")
            else:
                try:
                    old_prev = vid_prev
                    old_full = vid_full
                    old_full_auto = vid_full_auto
                    loop_phase = old_prev.loop_phase() if old_prev is not None else 0.0
                    vid_prev = BgVideo(video_path, prev_w, prev_h)
                    vid_prev.seek_to_phase(loop_phase)
                    vid_full = BgVideo(video_path, full_w, full_h) if (args.use_virtual_cam and HAS_VCAM) else None
                    if vid_full is not None:
                        vid_full.seek_to_phase(loop_phase)
                    if old_prev is not vid_prev:
                        old_prev.close()
                    if old_full is not None and old_full is not vid_full:
                        old_full.close()
                    if old_full_auto is not None:
                        old_full_auto.close()
                    vid_full_auto = None
                    track_full_auto = None
                    track_path = resolve_track_path(
                        next_track,
                        next_track_calibrated,
                        prefer_calibrated=not args.no_prefer_calibrated,
                    )
                    track_prev = MouthTrack.load(track_path, prev_w, prev_h, policy=args.valid_policy)
                    track_full = MouthTrack.load(track_path, full_w, full_h, policy=args.valid_policy) if vid_full is not None else None
                    print(f"[emotion] switched -> {current_emotion} ({source}) video={os.path.basename(video_path)} track={os.path.basename(track_path)}")
                except Exception as e:
                    print(f"[emotion warn] failed to switch video/track for {current_emotion}: {e}")
        else:
            print(f"[emotion] switched -> {current_emotion} ({source})")

        if bool(args.emotion_hud):
            try:
                hud_q.put_nowait(format_emotion_hud_text(infer_label_from_set_name(current_emotion)))
            except Exception:
                pass

    if emotion_video_dir:
        _switch_runtime_emotion(current_emotion, source="initial")

    if track_prev is None:
        print("[warn] mouth_track not found -> fallback to fixed placement")
    else:
        vr = float(track_prev.valid.mean()) if track_prev.total > 0 else 0.0
        print(f"[info] mouth_track loaded: {track_path}")
        print(f"       valid_rate(raw)={vr:.1%} policy={track_prev.policy} calibrated={track_prev.calibrated}")

        # どのトラックが使われているか（更新日時とキャリブ値）を表示
        try:
            mt = datetime.datetime.fromtimestamp(os.path.getmtime(track_path)).strftime("%Y-%m-%d %H:%M:%S")
            print(f"       mtime={mt}")
        except Exception:
            pass
        try:
            npz_dbg = np.load(track_path, allow_pickle=False)
            if any(k in npz_dbg for k in ["calib_offset", "calib_scale", "calib_rotation"]):
                off = npz_dbg["calib_offset"].tolist() if "calib_offset" in npz_dbg else None
                sc = float(npz_dbg["calib_scale"]) if "calib_scale" in npz_dbg else None
                rot = float(npz_dbg["calib_rotation"]) if "calib_rotation" in npz_dbg else None
                print(f"       calib_offset={off} calib_scale={sc} calib_rotation={rot}")
        except Exception:
            pass

    def _ensure_full_auto_sources() -> tuple[BgVideo | None, MouthTrack | None]:
        nonlocal vid_full_auto, track_full_auto
        if vid_full is not None:
            return vid_full, track_full
        if vid_full_auto is None:
            print("[auto-color] opening full-resolution video source...")
            vid_full_auto = BgVideo(args.loop_video, full_w, full_h)
        if track_full_auto is None:
            print("[auto-color] loading full-resolution track...")
            track_full_auto = MouthTrack.load(track_path, full_w, full_h, policy=args.valid_policy)
        return vid_full_auto, track_full_auto

    # ---- audio feature buffers ----
    # Use queue.Queue instead of lock+deque to avoid blocking in audio callback.
    # This prevents potential audio glitches caused by lock contention.
    feat_q: queue.Queue[tuple[float, float]] = queue.Queue(maxsize=args.audio_hz * 2)

    hop = int(samplerate / args.audio_hz)
    hop = max(hop, 256)
    window = np.hanning(hop).astype(np.float32)
    freqs = np.fft.rfftfreq(hop, d=1.0 / samplerate)

    def _scheduled_perf_time(time_info) -> float:
        try:
            current = float(time_info.currentTime)
            output = float(time_info.outputBufferDacTime)
        except Exception:
            return time.perf_counter()
        return time.perf_counter() + max(0.0, output - current)

    def audio_cb_common(indata, frames, time_info, status):
        x = indata.astype(np.float32)
        if x.ndim == 2:
            x = x.mean(axis=1)
        if len(x) < hop:
            x = np.pad(x, (0, hop - len(x)))
        elif len(x) > hop:
            x = x[:hop]
        rms_raw = float(np.sqrt(np.mean(x * x) + 1e-12))
        w = x * window
        mag = np.abs(np.fft.rfft(w)) + 1e-9
        centroid = float((freqs * mag).sum() / mag.sum())
        centroid = float(np.clip(centroid / (samplerate * 0.5), 0.0, 1.0))
        # Non-blocking put: drop if full (better than blocking audio callback)
        try:
            feat_q.put_nowait((_scheduled_perf_time(time_info), rms_raw, centroid))
        except queue.Full:
            pass  # Drop sample if queue is full

        # Emotion-auto analyzer also consumes raw audio chunks (non-blocking)
        if emotion_auto_enabled and (emo_audio_q is not None):
            try:
                emo_audio_q.put_nowait(x)
            except queue.Full:
                pass

    def mic_audio_cb(indata, frames, time_info, status):
        if input_mode != "audio":
            return
        audio_cb_common(indata, frames, time_info, status)

    def tts_audio_cb(indata, frames, time_info, status):
        audio_cb_common(indata, frames, time_info, status)

    def create_live_input_stream():
        return sd.InputStream(
            samplerate=samplerate,
            channels=input_channels,
            blocksize=hop,
            dtype="float32",
            callback=mic_audio_cb,
            device=args.device,
            latency="low",
        )

    reopen_current_stream = None
    if audio_file_path:
        stream = WavAudioInputStream(
            audio_file_path,
            hop,
            tts_audio_cb,
            play_audio=bool(getattr(args, "play_audio_file", False)),
        )
    else:
        try:
            stream = create_live_input_stream()
            reopen_current_stream = create_live_input_stream
        except Exception as e:
            raw_spec = normalize_audio_device_spec(getattr(args, "audio_device_spec", "") or args.device)
            can_retry = (
                raw_spec
                and str(raw_spec).startswith("pa:")
                and audio_resolution is not None
                and allow_default_source_switch
                and audio_resolution.get("strategy") != "set_default_source"
            )
            if not can_retry:
                raise RuntimeError(f"failed to open audio input stream: {e}") from e
            print(f"[audio] stream open failed, retrying fallback: {e}")
            _resolve_input_device(prefer_default_source=True)
            stream = create_live_input_stream()
            reopen_current_stream = create_live_input_stream

    stream = SwitchableAudioInputStream(stream, hop, tts_audio_cb, samplerate, reopen_current=reopen_current_stream)

    # ---- audio state ----
    beta = one_pole_beta(args.cutoff_hz, args.audio_hz)
    noise = 1e-4
    peak = 1e-3
    peak_decay = 0.995
    silence_gate_rms = args.silence_gate  # サイレンスゲート閾値
    rms_smooth_q = deque(maxlen=3)
    env_lp = 0.0
    env_hist = deque(maxlen=args.audio_hz * args.hist_sec)
    cent_hist = deque(maxlen=args.audio_hz * args.hist_sec)
    TALK_TH, HALF_TH, OPEN_TH = 0.06, 0.30, 0.52
    U_TH, E_TH = 0.16, 0.20

    current_open_shape = "open"
    last_vowel_change_t = -999.0
    e_prev2, e_prev1 = 0.0, 0.0
    mouth_shape_now = "closed"
    pending_mouth_shape: str | None = None
    pending_mouth_shape_since = 0.0
    prev_mouth_level = "closed"

    # ---- virtual cam ----
    cam = None
    if vid_full is not None:
        cam = pyvirtualcam.Camera(width=full_w, height=full_h, fps=args.render_fps, print_fps=False)
        print(f"[vcam] Virtual camera started: {cam.device}")

    # ---- render ----
    t0 = time.perf_counter()
    last_stat = time.perf_counter()
    rendered = 0

    window_name = args.window_name
    preview_window_enabled = not bool(getattr(args, "no_preview_window", False))
    if preview_window_enabled:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, vid_prev.w, vid_prev.h)
    print("[info] Press 'q' to quit.")
    print("stream latency:", stream.latency)
    render_writer = None
    render_out = str(getattr(args, "render_out", "") or "").strip()
    if render_out:
        os.makedirs(os.path.dirname(os.path.abspath(render_out)) or ".", exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        render_writer = cv2.VideoWriter(render_out, fourcc, float(args.render_fps), (vid_prev.w, vid_prev.h))
        if not render_writer.isOpened():
            raise RuntimeError(f"failed to open render writer: {render_out}")
        print(f"[render] writing preview video: {render_out}")

    def draw_one(dst_rgb: np.ndarray, frame_idx: int, track: MouthTrack | None, scale: float):
        nonlocal mouth_shape_now
        meta = _compose_mouth_patch(
            mouth,
            mouth_shape_now,
            frame_idx,
            track,
            scale,
            int(args.mouth_fixed_x),
            int(args.mouth_fixed_y),
        )
        alpha_blit_rgb_safe(dst_rgb, meta["patch"], int(meta["x0"]), int(meta["y0"]))

        quad = meta.get("quad")
        if args.draw_quad and quad is not None:
            q = np.asarray(quad, dtype=np.int32).reshape(4, 2)
            cv2.polylines(dst_rgb, [q], isClosed=True, color=(0, 255, 0), thickness=2)
        return meta

    try:
        with stream:
            next_frame_t = time.perf_counter()
            while True:
                now = time.perf_counter()
                t = now - t0
                _apply_ready_color_rebuild()

                if live_control_path and (now - live_control_last_check) >= 0.15:
                    live_control_last_check = now
                    try:
                        loaded = _load_live_color_control(live_control_path, ipc_token)
                    except Exception as e:
                        loaded = None
                        print(f"[mouth-color warn] failed to read live control: {e}")
                    if loaded is not None:
                        updated_at, live_cfg = loaded
                        if updated_at > max(live_control_last_requested, live_control_last_applied):
                            _queue_color_rebuild(live_cfg, updated_at=updated_at, reason="live")
                            print(
                                "[mouth-color] queued live update "
                                f"bri={live_cfg.brightness:.0f} "
                                f"sat={live_cfg.saturation:.2f} "
                                f"warm={live_cfg.warmth:.0f} "
                                f"strength={live_cfg.color_strength:.2f} "
                                f"edge={live_cfg.edge_priority:.2f} "
                                f"width={live_cfg.edge_width_ratio:.2f} "
                                f"inspect={live_cfg.inspect_boost:.1f}"
                            )

                # ---- emotion GUI updates ----
                # Avoid raising queue.Empty every frame when no GUI input is present.
                if not emotion_q.empty():
                    while True:
                        try:
                            sel = emotion_q.get_nowait()
                        except queue.Empty:
                            break
                        if sel in mouth_sets and sel != current_emotion:
                            _switch_runtime_emotion(sel, source="button")

                if not input_mode_q.empty():
                    while True:
                        try:
                            next_mode = input_mode_q.get_nowait()
                        except queue.Empty:
                            break
                        if next_mode in {"audio", "tts", "gemma"} and next_mode != input_mode:
                            input_mode = next_mode
                            noise = 1e-4
                            peak = 1e-3
                            env_lp = 0.0
                            env_hist.clear()
                            cent_hist.clear()
                            rms_smooth_q.clear()
                            print(f"[input] mode -> {input_mode}")

                if not tts_q.empty():
                    while True:
                        try:
                            wav_path = tts_q.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            stream.switch_to_wav(wav_path, play_audio=True)
                            noise = 1e-4
                            peak = 1e-3
                            env_lp = 0.0
                            env_hist.clear()
                            cent_hist.clear()
                            rms_smooth_q.clear()
                        except Exception as e:
                            print(f"[tts warn] failed to switch TTS WAV input: {e}")

                # ---- emotion AUTO updates ----
                if emotion_auto_enabled and (emo_audio_q is not None) and (emo_analyzer is not None):
                    # drain chunks
                    while True:
                        try:
                            emo_buf.append(emo_audio_q.get_nowait())
                        except queue.Empty:
                            break

                    # evaluate at fixed interval using a window
                    if (now - emo_last_eval) >= emo_eval_interval and len(emo_buf) >= emo_window_len:
                        emo_last_eval = now
                        xwin = emo_buf.tail(emo_window_len)

                        try:
                            lab, info = emo_analyzer.update(xwin)  # type: ignore[union-attr]
                        except Exception:
                            lab, info = None, {}

                        if lab is not None:
                            rms_db = float(info.get("rms_db", -120.0))
                            conf = float(info.get("confidence", 0.0))
                            voiced = float(info.get("voiced", 0.0)) >= 0.5

                            # debug (1Hz)
                            if (now - emo_last_debug) >= 1.0:
                                emo_last_debug = now
                                print(f"[emotion-auto dbg] lab={str(lab).lower():8s} conf={conf:.2f} voiced={int(voiced)} rms_db={rms_db:.1f} cur={current_emotion}")

                            target_label, target_set, target_reason = resolve_emotion_auto_target(
                                lab,
                                info,
                                emotions,
                                neutral_set,
                                silence_db=float(args.emotion_silence_db),
                                min_conf=float(args.emotion_min_conf),
                            )

                            if target_reason == "low_conf" and (now - emo_last_debug) < 0.25:
                                print(
                                    f"[emotion-auto dbg] hold current: low confidence "
                                    f"{conf:.2f} < min {float(args.emotion_min_conf):.2f}"
                                )

                            if target_set in mouth_sets and target_set != current_emotion:
                                _switch_runtime_emotion(target_set, source=f"auto:{target_label}, conf={conf:.2f}")

                # ---- audio updates ----
                # Drain all available items from the queue (non-blocking)
                pending_items: list[tuple[float, float, float]] = []
                items: list[tuple[float, float]] = []
                while True:
                    try:
                        due_t, rms_raw, cent = feat_q.get_nowait()
                        if due_t <= now:
                            items.append((rms_raw, cent))
                        else:
                            pending_items.append((due_t, rms_raw, cent))
                    except queue.Empty:
                        break
                for item in pending_items:
                    try:
                        feat_q.put_nowait(item)
                    except queue.Full:
                        break

                for rms_raw, cent in items:
                    if rms_raw < noise + 0.0005:
                        noise = 0.99 * noise + 0.01 * rms_raw
                    else:
                        noise = 0.999 * noise + 0.001 * rms_raw

                    # サイレンスゲート + 正規化の安定化
                    peak = max(rms_raw, peak * peak_decay, noise + silence_gate_rms)
                    denom = max(peak - noise, silence_gate_rms)
                    rms_norm = float(np.clip((rms_raw - noise) / denom, 0.0, 1.0) ** 0.5)

                    # 無音域は強制的に0へ（パクパク防止）
                    if rms_raw < noise + silence_gate_rms:
                        rms_norm = 0.0

                    rms_smooth_q.append(rms_norm)
                    rms_sm = float(np.mean(rms_smooth_q))

                    env_lp = env_lp + beta * (rms_sm - env_lp)
                    env = float(np.clip(0.75 * env_lp + 0.25 * rms_sm, 0.0, 1.0))

                    env_hist.append(env)
                    cent_hist.append(float(cent))

                    if len(env_hist) > args.audio_hz * 3 and (len(env_hist) % args.audio_hz == 0):
                        vals = np.array(env_hist, dtype=np.float32)
                        k = max(1, int(0.2 * len(vals)))
                        noise_floor_env = float(np.median(np.sort(vals)[:k]))
                        TALK_TH = float(np.clip(noise_floor_env + 0.05, 0.03, 0.18))

                        talk_vals = vals[vals > TALK_TH]
                        if len(talk_vals) > 20:
                            HALF_TH = float(np.percentile(talk_vals, 25))
                            OPEN_TH = float(np.percentile(talk_vals, 58))
                            HALF_TH = max(HALF_TH, TALK_TH + 0.02)
                            OPEN_TH = max(OPEN_TH, HALF_TH + 0.05)

                            cents = np.array(cent_hist, dtype=np.float32)
                            open_mask = vals >= OPEN_TH
                            cent_open = cents[open_mask] if open_mask.sum() > 20 else cents[vals > TALK_TH]
                            if len(cent_open) > 20:
                                U_TH = float(np.percentile(cent_open, 20))
                                E_TH = float(np.percentile(cent_open, 80))

                    mouth_level = classify_mouth_level_with_hysteresis(
                        env,
                        HALF_TH,
                        OPEN_TH,
                        prev_mouth_level,
                    )
                    prev_mouth_level = mouth_level

                    # vowel selection on peaks
                    if mouth_level == "open":
                        is_peak = (e_prev2 < e_prev1) and (e_prev1 >= env) and (e_prev1 > OPEN_TH + args.peak_margin)
                        if is_peak and (t - last_vowel_change_t) >= args.min_vowel_interval:
                            if len(cent_hist) >= 5:
                                cm = float(np.mean(list(cent_hist)[-5:]))
                            else:
                                cm = float(cent)
                            if cm < U_TH:
                                current_open_shape = "u"
                            elif cm > E_TH:
                                current_open_shape = "wide" if env > min(1.0, OPEN_TH + 0.18) and "wide" in mouth else "e"
                            else:
                                current_open_shape = "open"
                            last_vowel_change_t = t
                        mouth_shape_candidate = "wide" if env > min(1.0, OPEN_TH + 0.28) and "wide" in mouth else current_open_shape
                    elif mouth_level == "half":
                        mouth_shape_candidate = "small" if env < max(OPEN_TH, HALF_TH + 0.12) and "small" in mouth else "half"
                    else:
                        mouth_shape_candidate = "closed"

                    mouth_shape_candidate = soften_mouth_shape_for_emotion(
                        current_emotion,
                        mouth_shape_candidate,
                        env,
                        HALF_TH,
                        OPEN_TH,
                        mouth,
                    )
                    stable_sec = float(args.joy_mouth_shape_stable_sec) if current_emotion.lower() == "joy" else 0.0
                    mouth_shape_now, pending_mouth_shape, pending_mouth_shape_since = stabilize_mouth_shape(
                        mouth_shape_candidate,
                        mouth_shape_now,
                        pending_mouth_shape,
                        pending_mouth_shape_since,
                        t,
                        stable_sec,
                    )

                    e_prev2, e_prev1 = e_prev1, env

                # ---- HUD update (main thread) ----
                if hud_root is not None and hud_lbl is not None:
                    try:
                        while True:
                            txt = hud_q.get_nowait()
                            hud_lbl.config(text=txt)
                    except queue.Empty:
                        pass
                    try:
                        hud_root.update_idletasks()
                        hud_root.update()
                    except Exception:
                        hud_root = None
                        hud_lbl = None

                # ---- preview ----
                frp_base = vid_prev.get_frame(now).copy()
                frp = frp_base.copy()
                draw_meta = draw_one(frp, vid_prev.frame_idx, track_prev, args.preview_scale)

                if auto_request_path and auto_result_path and (now - auto_request_last_check) >= 0.15:
                    auto_request_last_check = now
                    try:
                        req = _load_auto_color_request(auto_request_path, ipc_token)
                    except Exception as e:
                        req = None
                        print(f"[auto-color warn] failed to read request: {e}")
                    if req is not None:
                        request_id, _requested_at = req
                        if request_id and request_id != auto_request_last_id:
                            auto_request_last_id = request_id
                            estimated = None
                            estimated_source = "preview"
                            full_sampling_error = False
                            try:
                                full_video, full_track_src = _ensure_full_auto_sources()
                                if full_video is not None:
                                    fr_full = full_video.get_frame(now).copy()
                                    full_meta = _compose_mouth_patch(
                                        mouth,
                                        mouth_shape_now,
                                        full_video.frame_idx,
                                        full_track_src,
                                        1.0,
                                        int(args.mouth_fixed_x),
                                        int(args.mouth_fixed_y),
                                    )
                                    estimated = _estimate_auto_color_result(
                                        fr_full,
                                        np.asarray(full_meta["patch"]),
                                        x0=int(full_meta["x0"]),
                                        y0=int(full_meta["y0"]),
                                        current_cfg=mouth_color_cfg,
                                    )
                                    if estimated is not None:
                                        estimated_source = "full"
                            except Exception as e:
                                print(
                                    "[auto-color warn] full-resolution sampling unavailable; "
                                    f"fallback to preview ({e})"
                                )
                                estimated = None
                                full_sampling_error = True
                            if estimated is None:
                                if not full_sampling_error:
                                    print("[auto-color warn] full-resolution sampling failed; fallback to preview")
                                estimated = _estimate_auto_color_result(
                                    frp_base,
                                    np.asarray(draw_meta["patch"]),
                                    x0=int(draw_meta["x0"]),
                                    y0=int(draw_meta["y0"]),
                                    current_cfg=mouth_color_cfg,
                                )
                                estimated_source = "preview"
                            if estimated is None:
                                result_payload = {
                                    "session_token": ipc_token,
                                    "request_id": request_id,
                                    "processed_at": float(time.time()),
                                    "error": "sampling_failed",
                                }
                                print("[auto-color warn] sampling failed")
                            else:
                                new_cfg = estimated["cfg"]
                                apply_updated_at = float(time.time())
                                _queue_color_rebuild(
                                    new_cfg,
                                    updated_at=apply_updated_at,
                                    reason=f"auto:{estimated_source}",
                                )
                                result_payload = {
                                    "session_token": ipc_token,
                                    "request_id": request_id,
                                    "processed_at": float(time.time()),
                                    "apply_updated_at": apply_updated_at,
                                    "mouth_brightness": float(new_cfg.brightness),
                                    "mouth_saturation": float(new_cfg.saturation),
                                    "mouth_warmth": float(new_cfg.warmth),
                                    "mouth_color_strength": float(new_cfg.color_strength),
                                    "bg_sample_count": int(estimated["bg_sample_count"]),
                                    "mouth_sample_count": int(estimated["mouth_sample_count"]),
                                    "debug": estimated["debug"],
                                }
                                print(
                                    "[auto-color] queued "
                                    f"bri={new_cfg.brightness:.0f} "
                                    f"sat={new_cfg.saturation:.2f} "
                                    f"warm={new_cfg.warmth:.0f} "
                                    f"strength={new_cfg.color_strength:.2f} "
                                    f"src={estimated_source}"
                                )
                            try:
                                _write_json_atomic(auto_result_path, result_payload)
                            except Exception as e:
                                print(f"[auto-color warn] failed to write result: {e}")

                frp = apply_inspect_boost_3ch(frp, inspect_boost, color_order="RGB")
                if render_writer is not None:
                    render_writer.write(cv2.cvtColor(frp, cv2.COLOR_RGB2BGR))
                k = _show_preview_frame(window_name, frp) if preview_window_enabled else -1
                if k == ord("q"):
                    break
                if k == ord("v"):
                    cur_idx = inspect_levels.index(inspect_boost) if inspect_boost in inspect_levels else 0
                    inspect_boost = inspect_levels[(cur_idx + 1) % len(inspect_levels)]
                    print(f"[mouth-color] inspect boost -> {inspect_boost:.1f}")

                # ---- virtual cam ----
                if cam is not None and vid_full is not None:
                    frf = vid_full.get_frame(now).copy()
                    draw_one(frf, vid_full.frame_idx, track_full, 1.0)
                    cam.send(frf)
                    cam.sleep_until_next_frame()

                # ---- pacing ----
                next_frame_t += 1.0 / float(args.render_fps)
                sleep_s = next_frame_t - time.perf_counter()
                if sleep_s > 0:
                    time.sleep(sleep_s)
                else:
                    next_frame_t = time.perf_counter()

                # ---- stats ----
                rendered += 1
                if rendered % int(args.render_fps) == 0:
                    now2 = time.perf_counter()
                    fps = float(args.render_fps) / (now2 - last_stat)
                    last_stat = now2
                    print(f"[runtime] fps:{fps:.2f} mouth:{mouth_shape_now} frame:{vid_prev.frame_idx}")
                if args.max_frames > 0 and rendered >= int(args.max_frames):
                    print(f"[runtime] reached max frames: {rendered}")
                    break

    finally:
        color_rebuilder.close()
        if render_writer is not None:
            render_writer.release()
        if cam is not None:
            cam.close()
        vid_prev.close()
        if vid_full is not None:
            vid_full.close()
        if vid_full_auto is not None:
            vid_full_auto.close()
        if selector_root is not None and hasattr(selector_root, "destroy"):
            try:
                selector_root.destroy()
            except Exception:
                pass
        if preview_window_enabled:
            cv2.destroyAllWindows()
        cleanup_audio_device_resolution(audio_resolution or {}, audio_apply_state)


def load_last_session() -> dict:
    """GUIが保存した最後のセッション情報を読み込む"""
    try:
        if os.path.isfile(LAST_SESSION_FILE):
            with open(LAST_SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def parse_args():
    ap = argparse.ArgumentParser()

    ap.add_argument("--use-last-session", action="store_true",
                    help="GUIで最後に使用したファイルを自動的に使用する")
    ap.add_argument("--no-auto-last-session", action="store_true",
                    help="引数省略時の自動セッション復元を無効化（デフォルトは有効）")
    ap.add_argument("--tuber-num", type=int, default=10)
    ap.add_argument("--assets-dir", default="", help="空なら assets/assetsXX を tuber-num から生成")

    ap.add_argument("--loop-video", default="", help="空なら {assets_dir}/loop.mp4")
    ap.add_argument("--mouth-dir", default="", help="空なら {assets_dir}/mouth")

    ap.add_argument("--track", default="", help="空なら {assets_dir}/mouth_track.npz")
    ap.add_argument("--track-calibrated", default="", help="空なら {assets_dir}/mouth_track_calibrated.npz")
    ap.add_argument("--no-prefer-calibrated", action="store_true", help="calibratedがあっても使わない")

    ap.add_argument("--full-w", type=int, default=1440)
    ap.add_argument("--full-h", type=int, default=2560)
    ap.add_argument("--preview-scale", type=float, default=1.0)

    ap.add_argument("--render-fps", type=int, default=30)
    ap.add_argument("--audio-hz", type=int, default=100)
    ap.add_argument("--cutoff-hz", type=float, default=8.0)

    ap.add_argument("--device", type=int, default=31, help="sounddevice input device index")
    ap.add_argument("--audio-device-spec", type=str, default="", help="audio device spec: sd:<index> / pa:<source>")
    ap.add_argument("--audio-file", default="", help="WAV file input for finite/offline runtime verification")
    ap.add_argument("--play-audio-file", action="store_true", help="play --audio-file from the same callback used for lipsync")
    ap.add_argument("--irodori-tts-ui", action="store_true", help="show Irodori TTS input in the emotion control window")
    ap.add_argument("--irodori-tts-dir", default="", help="directory for generated Irodori TTS WAV files")
    ap.add_argument("--linux-allow-default-source-switch", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--use-virtual-cam", action="store_true")

    ap.add_argument("--mouth-fixed-x", type=int, default=int(1440 * 0.50))
    ap.add_argument("--mouth-fixed-y", type=int, default=int(2560 * 0.58))

    ap.add_argument("--valid-policy", choices=["hold", "strict"], default="hold",
                    help="hold: validが無いフレームも近傍で埋めたquadを使う / strict: valid=0は固定貼り")
    ap.add_argument("--draw-quad", action="store_true")

    ap.add_argument("--min-vowel-interval", type=float, default=0.12)
    ap.add_argument("--peak-margin", type=float, default=0.02)
    ap.add_argument("--silence-gate", type=float, default=0.002,
                    help="サイレンスゲート閾値 (0.001〜0.01, 高いほど無音判定厳しい)")
    ap.add_argument("--hist-sec", type=int, default=10)

    ap.add_argument("--emotion", default="", help="起動時に選択する感情フォルダ名（mouth_dir配下）。空なら自動選択")
    ap.add_argument("--no-emotion-gui", action="store_true", help="感情選択GUIを表示しない（CLI指定のみで切替）")
    ap.add_argument("--emotion-video-dir", default="",
                    help="感情名に対応する loop_<emotion>_mouthless.mp4 と mouth_track_<emotion>_calibrated.npz を切り替えるディレクトリ")

    ap.add_argument("--emotion-auto", action="store_true",
                    help="音声から感情を推定して、口パーツ（感情セット）を自動で切り替える")
    ap.add_argument("--emotion-preset", default="standard", choices=("stable", "standard", "snappy"),
                    help="感情AUTOの反応の強さ（stable/standard/snappy）")

    hud = ap.add_mutually_exclusive_group()
    hud.add_argument("--emotion-hud", dest="emotion_hud", action="store_true",
                     help="画面隅に『😊 happy』のように感情表示を出す（デバッグ用）")
    hud.add_argument("--no-emotion-hud", dest="emotion_hud", action="store_false",
                     help="感情表示HUDを出さない")
    ap.set_defaults(emotion_hud=True)

    # Advanced (hidden): tweak thresholds if needed later
    ap.add_argument("--emotion-silence-db", type=float, default=-65.0, help=argparse.SUPPRESS)
    ap.add_argument("--emotion-min-conf", type=float, default=0.45, help=argparse.SUPPRESS)
    ap.add_argument("--emotion-hud-font", type=int, default=28, help=argparse.SUPPRESS)
    ap.add_argument("--emotion-hud-alpha", type=float, default=0.92, help=argparse.SUPPRESS)
    ap.add_argument("--mouth-brightness", type=float, default=0.0, help=argparse.SUPPRESS)
    ap.add_argument("--mouth-saturation", type=float, default=1.0, help=argparse.SUPPRESS)
    ap.add_argument("--mouth-warmth", type=float, default=0.0, help=argparse.SUPPRESS)
    ap.add_argument("--mouth-color-strength", type=float, default=0.75, help=argparse.SUPPRESS)
    ap.add_argument("--mouth-edge-priority", type=float, default=0.85, help=argparse.SUPPRESS)
    ap.add_argument("--mouth-edge-width-ratio", type=float, default=0.10, help=argparse.SUPPRESS)
    ap.add_argument("--mouth-inspect-boost", type=float, default=1.0, help=argparse.SUPPRESS)
    ap.add_argument("--mouth-ipc-token", default="", help=argparse.SUPPRESS)
    ap.add_argument("--mouth-live-control", default="", help=argparse.SUPPRESS)
    ap.add_argument("--mouth-auto-request", default="", help=argparse.SUPPRESS)
    ap.add_argument("--mouth-auto-result", default="", help=argparse.SUPPRESS)
    ap.add_argument("--joy-mouth-shape-stable-sec", type=float, default=JOY_MOUTH_SHAPE_STABLE_SEC, help=argparse.SUPPRESS)

    ap.add_argument("--window-name", default="LoopLipsync Runtime")
    ap.add_argument("--max-frames", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--render-out", default="", help=argparse.SUPPRESS)
    ap.add_argument("--no-preview-window", action="store_true", help=argparse.SUPPRESS)

    args = ap.parse_args()

    # Auto: 引数でパス系を指定していない場合は、最後のGUIセッションを自動復元する
    argv = sys.argv[1:]
    path_flags = {"--loop-video", "--assets-dir", "--tuber-num", "--mouth-dir", "--track", "--track-calibrated"}
    user_specified_paths = any(tok in path_flags for tok in argv)
    auto_use_last = (not args.no_auto_last_session) and (not args.use_last_session) and (not user_specified_paths)
    use_last = args.use_last_session or auto_use_last
    if auto_use_last:
        print("[info] No path args provided; auto-loading last GUI session (disable with --no-auto-last-session).")

    # --use-last-session: GUIで最後に使用したファイルを読み込む
    if use_last:
        session = load_last_session()
        if session:
            print("[info] Loading last session from GUI...")
            # video: 現在の動画（mouthlessの場合もある）
            if session.get("video") and os.path.isfile(session["video"]):
                args.loop_video = session["video"]
                print(f"  video: {args.loop_video}")

            # source_video: 元動画（GUIが保持しているだけ。ここではパス推定に利用）
            source_video = session.get("source_video", "") or ""
            if source_video and os.path.isfile(source_video):
                print(f"  source_video: {source_video}")

            # audio_device: "31: ..." 形式なら index を復元（device未指定 or デフォルトのままなら上書き）
            if session.get("audio_device"):
                idx = _parse_device_index(session.get("audio_device"))
                if idx is not None and (args.device is None or args.device == 31):
                    args.device = idx
                    print(f"  device: {args.device}")

            # --- GUIセッションの明示パスを優先（古いGUI互換: track/calib/mouth_dir） ---
            sess_mouth_dir = (session.get("mouth_dir") or "").strip()
            if sess_mouth_dir and os.path.isdir(sess_mouth_dir):
                args.mouth_dir = sess_mouth_dir
                print(f"  mouth_dir(session): {args.mouth_dir}")

            sess_track = (session.get("track") or session.get("track_path") or "").strip()
            if sess_track and os.path.isfile(sess_track):
                args.track = sess_track
                print(f"  track(session): {args.track}")

            sess_calib = (
                session.get("calib")
                or session.get("track_calibrated")
                or session.get("track_calibrated_path")
                or ""
            )
            sess_calib = str(sess_calib).strip()
            if sess_calib and os.path.isfile(sess_calib):
                args.track_calibrated = sess_calib
                print(f"  track_calibrated(session): {args.track_calibrated}")

            # mouth_dir / track は動画フォルダから推定（GUI仕様）
            video_for_paths = args.loop_video or source_video
            if video_for_paths:
                video_dir = os.path.dirname(os.path.abspath(video_for_paths))

                mouth_dir_cand = os.path.join(video_dir, "mouth")
                if not args.mouth_dir and os.path.isdir(mouth_dir_cand):
                    args.mouth_dir = mouth_dir_cand
                    print(f"  mouth_dir: {args.mouth_dir}")

                track_cand = os.path.join(video_dir, "mouth_track.npz")
                calib_cand = os.path.join(video_dir, "mouth_track_calibrated.npz")
                if not args.track and os.path.isfile(track_cand):
                    args.track = track_cand
                    print(f"  track: {args.track}")
                if not args.track_calibrated and os.path.isfile(calib_cand):
                    args.track_calibrated = calib_cand
                    print(f"  track_calibrated: {args.track_calibrated}")
        else:
            print("[warn] No last session found. Using default paths.")

    # resolve paths
    assets_dir = args.assets_dir.strip()
    if not assets_dir:
        assets_dir = os.path.join("assets", f"assets{args.tuber_num:02d}")
    args.assets_dir = assets_dir

    # loop_video が未指定なら assets_dir/loop.mp4
    if not args.loop_video:
        args.loop_video = os.path.join(assets_dir, "loop.mp4")

    # GUI仕様: 基本は「動画と同じフォルダ」から mouth/ と npz を推定する
    base_dir = os.path.dirname(os.path.abspath(args.loop_video)) if args.loop_video else assets_dir

    if not args.mouth_dir:
        args.mouth_dir = os.path.join(base_dir, "mouth")
    if not args.track:
        args.track = os.path.join(base_dir, "mouth_track.npz")
    if not args.track_calibrated:
        args.track_calibrated = os.path.join(base_dir, "mouth_track_calibrated.npz")

    return args


def main():
    print(f"[info] runtime: {__VERSION__} file={os.path.abspath(__file__)}")
    args = parse_args()
    run(args)


if __name__ == "__main__":
    main()
