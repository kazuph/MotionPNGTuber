#!/usr/bin/env python3
"""Dokochan Voice Lab server.

irodori TTS (VoiceDesign) が内包する声質空間を、実生成データから作った
「声質アトラス」(tools/voice_atlas_builder.py で構築) として配信し、
マップ上の任意の点での合成・声プロファイル選択を提供する。

- GET  /api/atlas               : アトラス（全サンプル座標+特徴+音声URL）
- POST /api/atlas/synthesize    : サンプル再現 or 近傍ブレンドで任意テキスト合成
- POST /api/select              : 声プロファイルとして保存
- POST /api/synthesize-selected : 選択済みプロファイルで合成（外部ランタイム互換）
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "tools"))
from voice_atlas_builder import extract_features  # noqa: E402

STATIC_DIR = ROOT / "tools" / "voice_lab_static"
OUT_DIR = ROOT / "local" / "voice_lab_outputs"
ATLAS_PATH = OUT_DIR / "atlas" / "atlas.json"
PROFILE_PATH = OUT_DIR / "dokochan_voice_profile.json"
NOTES_PATH = OUT_DIR / "voice_notes.json"
_notes_lock = threading.Lock()
MODEL_ID = os.environ.get(
    "DOKOCHAN_VOICE_MODEL",
    "mlx-community/Irodori-TTS-600M-v3-VoiceDesign-8bit",
)
# 第2エンジン: 1.7BのVoiceDesign。声の母集団がirodoriと別物（多様性の天井が違う）
QWEN_MODEL_ID = os.environ.get(
    "DOKOCHAN_QWEN_MODEL",
    "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit",
)
# リモートGPU生成（SSH PC / qwen-3.5-chatスタック）。空文字でローカルMLXに戻す
REMOTE_IRODORI_URL = os.environ.get(
    "DOKOCHAN_REMOTE_IRODORI_URL", "http://100.80.152.112:8088/api/tts/v1/tts"
)
REMOTE_QWEN_URL = os.environ.get(
    "DOKOCHAN_REMOTE_QWEN_URL", "http://100.80.152.112:8088/api/qwen3-tts/v1/tts"
)

DEFAULT_NUM_STEPS = 12
DEFAULT_CFG = 4.5


class BlendComponent(BaseModel):
    caption: str = Field(min_length=1, max_length=300)
    weight: float = Field(ge=0.0, le=1.0)
    sample_id: str | None = None
    distance: float | None = None


class AtlasSynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=220)
    sample_id: str | None = None
    x: float | None = Field(default=None, ge=0.0, le=1.0)
    y: float | None = Field(default=None, ge=0.0, le=1.0)
    projection: str = Field(default="umap", pattern="^(umap|pca)$")
    k: int = Field(default=4, ge=1, le=8)
    seed: int | None = Field(default=None, ge=0, le=999_999)
    num_steps: int = Field(default=DEFAULT_NUM_STEPS, ge=8, le=40)
    # 既存ブレンドの再合成用: 指定時は座標ではなくこの構成をそのまま使う
    blend: list[BlendComponent] | None = None
    # caption直打ち合成（シードガチャ対応）。指定時は最優先
    caption: str | None = Field(default=None, max_length=300)
    engine: str = Field(default="irodori", pattern="^(irodori|qwen3)$")


class SelectRequest(BaseModel):
    generation_id: str


class SynthesizeSelectedRequest(BaseModel):
    text: str = Field(min_length=1, max_length=220)


class NoteRequest(BaseModel):
    """任意のボイス（アトラス点/ブレンド/★）へのお気に入り・命名。"""

    id: str = Field(min_length=1, max_length=64)
    favorite: bool | None = None
    name: str | None = Field(default=None, max_length=60)


app = FastAPI(title="Dokochan Voice Lab")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    """UI更新のたびに古いJS/CSSが残って挙動が食い違う事故を防ぐ。"""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

_model: Any | None = None
_model_lock = threading.Lock()
_history_lock = threading.Lock()
_caption_cache: dict[str, tuple[Any, Any]] = {}
_atlas_cache: dict[str, Any] = {"mtime": None, "data": None}
_started_at = time.time()


def get_model() -> Any:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from mlx_audio.tts import load

                _model = load(MODEL_ID, lazy=True, strict=False)
    return _model


_qwen_model: Any | None = None


def get_qwen_model() -> Any:
    global _qwen_model
    if _qwen_model is None:
        with _model_lock:
            if _qwen_model is None:
                from mlx_audio.tts import load

                _qwen_model = load(QWEN_MODEL_ID, lazy=True, strict=False)
    return _qwen_model


def synthesize_remote(
    url: str, text: str, caption: str, seed: int, num_steps: int = 16
) -> tuple[np.ndarray, int]:
    """リモートGPU（SSH PC）のVoiceDesignサーバーで合成。WAVバイト列を受け取る。"""
    import io
    import urllib.request

    body = json.dumps(
        {
            "text": text,
            "instruct": caption,
            "seed": int(seed),
            "num_steps": int(num_steps),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        wav_bytes = resp.read()
    audio, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.99:
        audio = audio * (0.99 / peak)
    return audio.astype(np.float32), int(sample_rate)


def synthesize_qwen_caption(
    model: Any, text: str, caption: str, seed: int
) -> tuple[np.ndarray, int]:
    """Qwen3-TTS VoiceDesignで合成。seedはMLXグローバルseedで再現性を確保。"""
    import mlx.core as mx

    mx.random.seed(int(seed))
    # max_tokens=150 ≈ 12秒上限（qwen-tts skill実測: デフォルト4096は327秒生成事故）
    result = next(
        model.generate_voice_design(
            text=text,
            instruct=caption,
            language="Japanese",
            temperature=0.65,
            max_tokens=150,
        )
    )
    audio = np.array(result.audio, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.99:
        audio = audio * (0.99 / peak)
    return audio, int(result.sample_rate)


def load_atlas() -> dict[str, Any] | None:
    if not ATLAS_PATH.is_file():
        return None
    mtime = ATLAS_PATH.stat().st_mtime
    if _atlas_cache["mtime"] != mtime:
        _atlas_cache["data"] = json.loads(ATLAS_PATH.read_text(encoding="utf-8"))
        _atlas_cache["mtime"] = mtime
    return _atlas_cache["data"]


def get_caption_context(model: Any, caption: str) -> tuple[Any, Any]:
    """caption → (hidden state, mask)。キャプション単位でキャッシュする。"""
    import mlx.core as mx

    cached = _caption_cache.get(caption)
    if cached is not None:
        return cached
    caption_ids, caption_mask = model._prepare_caption(caption)
    state = model.model.caption_norm(model.model.caption_encoder(caption_ids, caption_mask))
    mx.eval(state, caption_mask)
    if len(_caption_cache) > 400:
        _caption_cache.clear()
    _caption_cache[caption] = (state, caption_mask)
    return state, caption_mask


def blend_caption_contexts(
    model: Any, weighted_captions: list[tuple[str, float]]
) -> tuple[Any, Any]:
    """複数キャプションの隠れ状態を重み付き合成する。

    エンコーダはパディング位置をゼロ化するため、固定長パディング済み状態の
    重み付き和は有効プレフィックスのブレンドになる。マスクは最大重みの
    キャプションのものを使う。
    """
    import mlx.core as mx

    state = None
    best_mask = None
    best_weight = -1.0
    for caption, weight in weighted_captions:
        cap_state, cap_mask = get_caption_context(model, caption)
        weighted = cap_state * float(weight)
        state = weighted if state is None else state + weighted
        if weight > best_weight:
            best_weight = weight
            best_mask = cap_mask
    mx.eval(state, best_mask)
    return state, best_mask


def sample_with_context(
    model: Any,
    text: str,
    context_state: Any,
    context_mask: Any,
    rng_seed: int,
    sequence_length: int,
    num_steps: int,
    cfg_scale: float,
) -> Any:
    """事前計算済みのcaption隠れ状態を条件に rectified flow をサンプリングする。"""
    import mlx.core as mx

    text_input_ids, text_mask = model._prepare_text(text)
    text_state = model.model.text_norm(model.model.text_encoder(text_input_ids, text_mask))
    text_state_uncond = mx.zeros_like(text_state)
    text_mask_uncond = mx.zeros_like(text_mask)
    context_state_uncond = mx.zeros_like(context_state)
    context_mask_uncond = mx.zeros_like(context_mask)

    kv_text_cond, kv_context_cond = model.model.build_kv_cache(text_state, context_state)
    kv_text_uncond, kv_context_uncond = model.model.build_kv_cache(
        text_state_uncond, context_state_uncond
    )
    mx.eval(kv_text_cond, kv_context_cond, kv_text_uncond, kv_context_uncond)

    mx.random.seed(int(rng_seed))
    latent_dim = int(model.config.dit.patched_latent_dim)
    x_t = mx.random.normal((1, int(sequence_length), latent_dim)) * 0.999
    t_schedule = np.linspace(0.999, 0.0, int(num_steps) + 1, dtype=np.float32)

    for idx in range(int(num_steps)):
        t = float(t_schedule[idx])
        t_next = float(t_schedule[idx + 1])
        t_arr = mx.full((1,), t, dtype=mx.float32)
        v_cond = model.model.forward_with_conditions(
            x_t=x_t,
            t=t_arr,
            text_state=text_state,
            text_mask=text_mask,
            speaker_state=context_state,
            speaker_mask=context_mask,
            kv_text=kv_text_cond,
            kv_speaker=kv_context_cond,
        )
        v_uncond = model.model.forward_with_conditions(
            x_t=x_t,
            t=t_arr,
            text_state=text_state_uncond,
            text_mask=text_mask_uncond,
            speaker_state=context_state_uncond,
            speaker_mask=context_mask_uncond,
            kv_text=kv_text_uncond,
            kv_speaker=kv_context_uncond,
        )
        v_pred = v_cond + float(cfg_scale) * (v_cond - v_uncond)
        x_t = x_t + v_pred * (t_next - t)
        mx.eval(x_t)
    return x_t


def decode_latents(model: Any, latents: Any) -> tuple[np.ndarray, int]:
    import mlx.core as mx
    from mlx_audio.tts.models.irodori_tts.irodori_tts import _find_silence_point

    latent_for_decode = mx.transpose(latents, (0, 2, 1))
    audio_out = model.dacvae.decode(latent_for_decode, chunk_size=50)[:, :, 0]
    mx.eval(audio_out)
    silence_t = max(8, _find_silence_point(latents[0]))
    trim_samples = silence_t * int(model.config.audio_downsample_factor)
    audio = np.array(audio_out[0][:trim_samples], dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.99:
        audio = audio * (0.99 / peak)
    return audio, int(model.sample_rate)


def sequence_length_for(text: str) -> int:
    return int(np.clip(40 + 3.2 * len(text), 90, 260))


def history_path() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR / "history.jsonl"


def append_history(item: dict[str, Any]) -> None:
    with _history_lock:
        with history_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load_history(limit: int = 60) -> list[dict[str, Any]]:
    path = history_path()
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit * 3 :]:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return items[-limit:]


def find_atlas_sample(sample_id: str) -> dict[str, Any] | None:
    atlas = load_atlas()
    if not atlas:
        return None
    return next((s for s in atlas["samples"] if s["id"] == sample_id), None)


def nearest_samples(x: float, y: float, projection: str, k: int) -> list[tuple[dict[str, Any], float]]:
    atlas = load_atlas()
    if not atlas:
        raise HTTPException(status_code=409, detail="atlas not built yet")
    scored = []
    for s in atlas["samples"]:
        sx, sy = s[projection]
        scored.append((s, float(np.hypot(sx - x, sy - y))))
    scored.sort(key=lambda t: t[1])
    return scored[:k]


def blend_spec_from_point(x: float, y: float, projection: str, k: int) -> dict[str, Any]:
    neighbors = nearest_samples(x, y, projection, k)
    eps = 1e-4
    raw = [1.0 / ((dist + eps) ** 2) for _, dist in neighbors]
    total = sum(raw)
    weights = [w / total for w in raw]
    return {
        "captions": [
            {
                "sample_id": s["id"],
                "caption": s["caption"],
                "weight": round(w, 4),
                "distance": round(dist, 4),
            }
            for (s, dist), w in zip(neighbors, weights)
        ],
        "seed": int(neighbors[0][0]["seed"]),
    }


def synthesize_blend(
    model: Any,
    text: str,
    blend: dict[str, Any],
    seed: int,
    num_steps: int,
) -> tuple[np.ndarray, int]:
    weighted = [(c["caption"], float(c["weight"])) for c in blend["captions"]]
    context_state, context_mask = blend_caption_contexts(model, weighted)
    latents = sample_with_context(
        model=model,
        text=text,
        context_state=context_state,
        context_mask=context_mask,
        rng_seed=seed,
        sequence_length=sequence_length_for(text),
        num_steps=num_steps,
        cfg_scale=DEFAULT_CFG,
    )
    return decode_latents(model, latents)


def synthesize_single_caption(
    model: Any,
    text: str,
    caption: str,
    seed: int,
    num_steps: int,
    cfg_scale: float = DEFAULT_CFG,
    sequence_length: int | None = None,
) -> tuple[np.ndarray, int]:
    result = next(
        model.generate(
            text=text,
            caption=caption,
            rng_seed=int(seed),
            num_steps=int(num_steps),
            sequence_length=int(sequence_length or sequence_length_for(text)),
            cfg_scale_caption=float(cfg_scale),
        )
    )
    audio = np.array(result.audio, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.99:
        audio = audio * (0.99 / peak)
    return audio, int(result.sample_rate)


def write_generation(prefix: str, audio: np.ndarray, sample_rate: int, extra: dict[str, Any]) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{prefix}-{time.time()}-{json.dumps(extra.get('text',''), ensure_ascii=False)}".encode("utf-8")
    generation_id = hashlib.sha1(key).hexdigest()[:12]
    wav_name = f"{prefix}_{generation_id}.wav"
    wav_path = OUT_DIR / wav_name
    sf.write(str(wav_path), audio, sample_rate)
    features = extract_features(audio, sample_rate)
    item = {
        "id": generation_id,
        "audio_url": f"/audio/{wav_name}",
        "wav_path": str(wav_path),
        "sample_rate": sample_rate,
        "duration_sec": round(float(len(audio) / sample_rate), 3),
        "features": {k: round(v, 4) for k, v in features.items()},
        "created_at": time.time(),
        **extra,
    }
    append_history(item)
    return item


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    atlas = load_atlas()
    return {
        "ok": True,
        "model": MODEL_ID,
        "model_loaded": _model is not None,
        "atlas_ready": atlas is not None,
        "atlas_samples": atlas["n_samples"] if atlas else 0,
        "outputs_dir": str(OUT_DIR),
        "profile_path": str(PROFILE_PATH),
        "profile_selected": PROFILE_PATH.is_file(),
        "uptime_sec": round(time.time() - _started_at, 1),
    }


@app.get("/api/atlas")
def atlas() -> dict[str, Any]:
    data = load_atlas()
    if data is None:
        return {"ready": False, "hint": "run tools/voice_atlas_builder.py generate && project"}
    return {"ready": True, **data}


@app.get("/api/history")
def history() -> dict[str, Any]:
    return {"history": load_history(400)}


def load_notes() -> dict[str, Any]:
    if not NOTES_PATH.is_file():
        return {}
    try:
        return json.loads(NOTES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


@app.get("/api/notes")
def notes() -> dict[str, Any]:
    return {"notes": load_notes()}


@app.post("/api/notes")
def update_note(req: NoteRequest) -> dict[str, Any]:
    with _notes_lock:
        data = load_notes()
        note = data.get(req.id, {})
        if req.favorite is not None:
            note["favorite"] = bool(req.favorite)
        if req.name is not None:
            note["name"] = req.name.strip()
        note["updated_at"] = time.time()
        if not note.get("favorite") and not note.get("name"):
            data.pop(req.id, None)
        else:
            data[req.id] = note
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        NOTES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "id": req.id, "note": data.get(req.id)}


_spk_matrix_cache: dict[str, Any] = {}


def map_position_for_wav(wav_path: str) -> dict[str, Any] | None:
    """合成wavをECAPAで埋め込み、アトラスのkNN加重平均でマップ座標を推定する。"""
    import subprocess

    spk_path = ATLAS_PATH.parent / "speaker_embeddings.npz"
    if not spk_path.exists():
        return None
    atlas_data = load_atlas()
    if not atlas_data:
        return None
    vec = None
    # 常駐エンベッダー優先（毎回のモデルロード3-5秒を回避）
    try:
        import urllib.request

        req = urllib.request.Request(
            "http://127.0.0.1:8767/embed",
            data=json.dumps({"path": wav_path}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            vec = np.asarray(json.loads(resp.read())["vector"], dtype=np.float64)
    except Exception:
        vec = None
    if vec is None:
        try:
            proc = subprocess.run(
                [str(ROOT / ".venv-spk" / "bin" / "python"), str(ROOT / "tools" / "voice_speaker_embed.py"), "--embed-wav", wav_path],
                capture_output=True,
                text=True,
                timeout=120,
            )
            vec = np.asarray(json.loads(proc.stdout.strip().splitlines()[-1]), dtype=np.float64)
        except Exception:
            return None

    if "Z" not in _spk_matrix_cache or _spk_matrix_cache.get("mtime") != spk_path.stat().st_mtime:
        data = np.load(spk_path, allow_pickle=False)
        ids = [str(i) for i in data["ids"]]
        Z = data["vectors"].astype(np.float64)
        Z /= np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9
        coords = {s["id"]: s for s in atlas_data["samples"]}
        keep = [i for i, sid in enumerate(ids) if sid in coords]
        _spk_matrix_cache.update(
            {
                "Z": Z[keep],
                "ids": [ids[i] for i in keep],
                "coords": coords,
                "mtime": spk_path.stat().st_mtime,
            }
        )
    Z = _spk_matrix_cache["Z"]
    ids = _spk_matrix_cache["ids"]
    coords = _spk_matrix_cache["coords"]
    vec_n = vec / (np.linalg.norm(vec) + 1e-9)
    # 音響性別分類（ECAPAセントロイド）: ガチャ生成物のフィルタに使う
    centroids_path = ATLAS_PATH.parent / "gender_centroids.npz"
    if centroids_path.exists():
        cents = np.load(centroids_path, allow_pickle=False)
        score = float(vec_n @ cents["female"] - vec_n @ cents["male"])
        prob_f = 1.0 / (1.0 + np.exp(-score * 12.0))
        out_gender = {
            "acoustic_gender": "female" if prob_f >= 0.5 else "male",
            "gender_prob": round(float(prob_f if prob_f >= 0.5 else 1.0 - prob_f), 4),
        }
    else:
        out_gender = {}

    sims = Z @ vec_n
    top = np.argsort(sims)[::-1][:5]
    w = np.maximum(sims[top], 0.0) ** 4 + 1e-6
    w /= w.sum()
    out: dict[str, Any] = {"nearest_sim": round(float(sims[top[0]]), 4), **out_gender}
    for proj in ("umap", "pca"):
        xy = np.zeros(2)
        for weight, idx in zip(w, top):
            xy += weight * np.asarray(coords[ids[idx]][proj], dtype=np.float64)
        out[proj] = [round(float(xy[0]), 4), round(float(xy[1]), 4)]
    return out


@app.post("/api/atlas/synthesize")
def atlas_synthesize(req: AtlasSynthesizeRequest) -> dict[str, Any]:
    import random as _random

    start = time.perf_counter()

    if req.caption:
        seed = req.seed if req.seed is not None else _random.randint(0, 9999)
        # 生成はリモートGPU優先（macOSのMLXはフォールバック）
        audio = None
        if req.engine == "qwen3":
            if REMOTE_QWEN_URL:
                try:
                    audio, sample_rate = synthesize_remote(
                        REMOTE_QWEN_URL, req.text, req.caption, seed
                    )
                except Exception:
                    audio = None
            if audio is None:
                qwen = get_qwen_model()
                with _model_lock:
                    audio, sample_rate = synthesize_qwen_caption(qwen, req.text, req.caption, seed)
        else:
            if REMOTE_IRODORI_URL:
                try:
                    audio, sample_rate = synthesize_remote(
                        REMOTE_IRODORI_URL, req.text, req.caption, seed, req.num_steps
                    )
                except Exception:
                    audio = None
            if audio is None:
                model = get_model()
                with _model_lock:
                    audio, sample_rate = synthesize_single_caption(
                        model, req.text, req.caption, seed, req.num_steps
                    )
        # 配置を先に計算してから履歴に含める（履歴の二重書き込みを避ける）
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = OUT_DIR / "tmp_custom_probe.wav"
        sf.write(str(tmp_path), audio, sample_rate)
        placement = map_position_for_wav(str(tmp_path)) or {}
        item = write_generation(
            "atlas",
            audio,
            sample_rate,
            {
                "mode": "custom_caption",
                "text": req.text,
                "caption": req.caption,
                "seed": seed,
                "engine": req.engine,
                "backend": "remote" if (REMOTE_QWEN_URL if req.engine == "qwen3" else REMOTE_IRODORI_URL) else "local",
                "num_steps": req.num_steps,
                **placement,
            },
        )
        item["elapsed_sec"] = round(time.perf_counter() - start, 2)
        return item

    if req.sample_id:
        sample = find_atlas_sample(req.sample_id)
        if sample is None:
            raise HTTPException(status_code=404, detail=f"atlas sample not found: {req.sample_id}")
        seed = req.seed if req.seed is not None else int(sample["seed"])
        # 既存アトラス声はローカルv3モデル産。声の再現性のためローカルで再合成する
        model = get_model()
        with _model_lock:
            audio, sample_rate = synthesize_single_caption(
                model, req.text, sample["caption"], seed, req.num_steps
            )
        item = write_generation(
            "atlas",
            audio,
            sample_rate,
            {
                "mode": "atlas_sample",
                "text": req.text,
                "sample_id": sample["id"],
                "caption": sample["caption"],
                "tags": sample.get("tags"),
                "seed": seed,
                "num_steps": req.num_steps,
                "projection": req.projection,
                "x": sample[req.projection][0],
                "y": sample[req.projection][1],
            },
        )
    else:
        if req.blend:
            total = sum(c.weight for c in req.blend) or 1.0
            blend = {
                "captions": [
                    {
                        "sample_id": c.sample_id,
                        "caption": c.caption,
                        "weight": round(c.weight / total, 4),
                        "distance": c.distance,
                    }
                    for c in req.blend
                ],
                "seed": int(req.seed if req.seed is not None else 7),
            }
        elif req.x is not None and req.y is not None:
            blend = blend_spec_from_point(req.x, req.y, req.projection, req.k)
        else:
            raise HTTPException(status_code=422, detail="x/y, blend, or sample_id is required")
        seed = req.seed if req.seed is not None else int(blend["seed"])
        model = get_model()
        with _model_lock:
            audio, sample_rate = synthesize_blend(model, req.text, blend, seed, req.num_steps)
        item = write_generation(
            "atlas",
            audio,
            sample_rate,
            {
                "mode": "atlas_blend",
                "text": req.text,
                "blend": blend["captions"],
                "seed": seed,
                "num_steps": req.num_steps,
                "projection": req.projection,
                "x": req.x,
                "y": req.y,
            },
        )

    item["elapsed_sec"] = round(time.perf_counter() - start, 2)
    return item


@app.post("/api/select")
def select(req: SelectRequest) -> dict[str, Any]:
    generation: dict[str, Any] | None = None

    sample = find_atlas_sample(req.generation_id)
    if sample is not None:
        generation = {
            "id": sample["id"],
            "mode": "atlas_sample",
            "sample_id": sample["id"],
            "caption": sample["caption"],
            "tags": sample.get("tags"),
            "seed": int(sample["seed"]),
            "num_steps": DEFAULT_NUM_STEPS,
            "audio_url": sample["audio_url"],
            "projection": "umap",
            "x": sample["umap"][0],
            "y": sample["umap"][1],
        }
    else:
        generation = next(
            (item for item in reversed(load_history(200)) if item.get("id") == req.generation_id),
            None,
        )
    if generation is None:
        raise HTTPException(status_code=404, detail="voice not found")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    profile = {
        "selected_at": time.time(),
        "model": MODEL_ID,
        "generation": generation,
        "note": "Repo-local Dokochan VoiceDesign profile. No personal speaker identifier is stored.",
    }
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "profile_path": str(PROFILE_PATH), "profile": profile}


@app.post("/api/synthesize-selected")
def synthesize_selected(req: SynthesizeSelectedRequest) -> dict[str, Any]:
    if not PROFILE_PATH.is_file():
        raise HTTPException(status_code=404, detail="Dokochan voice profile is not selected")

    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    generation = profile.get("generation") or {}
    if not generation:
        raise HTTPException(status_code=400, detail="Dokochan voice profile is invalid")

    mode = str(generation.get("mode") or "")
    start = time.perf_counter()

    # リモートGPU産の声はリモートで再合成（モデル差で声が変わるのを防ぐ）
    if generation.get("backend") == "remote" and generation.get("caption"):
        url = REMOTE_QWEN_URL if generation.get("engine") == "qwen3" else REMOTE_IRODORI_URL
        try:
            audio, sample_rate = synthesize_remote(
                url, req.text, str(generation["caption"]), int(generation.get("seed", 7))
            )
            item = write_generation(
                "runtime",
                audio,
                sample_rate,
                {
                    "mode": "runtime_selected_profile",
                    "text": req.text,
                    "source_generation_id": generation.get("id"),
                },
            )
            item["elapsed_sec"] = round(time.perf_counter() - start, 2)
            return item
        except Exception:
            pass  # リモート不通時はローカルへフォールバック

    model = get_model()
    # ロックは再入不可なので、qwenモデルのロードはロック取得前に済ませる
    qwen = get_qwen_model() if generation.get("engine") == "qwen3" else None

    with _model_lock:
        if mode == "atlas_blend":
            blend = {"captions": generation.get("blend") or []}
            if not blend["captions"]:
                raise HTTPException(status_code=400, detail="blend profile is empty")
            audio, sample_rate = synthesize_blend(
                model,
                req.text,
                blend,
                int(generation.get("seed", 7)),
                int(generation.get("num_steps", DEFAULT_NUM_STEPS)),
            )
        elif qwen is not None and generation.get("caption"):
            audio, sample_rate = synthesize_qwen_caption(
                qwen, req.text, str(generation["caption"]), int(generation.get("seed", 7))
            )
        elif mode == "atlas_sample" or generation.get("caption"):
            # atlas_sample と旧capitonプロファイルの両対応
            traits = generation.get("traits") or {}
            audio, sample_rate = synthesize_single_caption(
                model,
                req.text,
                str(generation.get("caption")),
                int(generation.get("seed", traits.get("seed", 7))),
                int(generation.get("num_steps", traits.get("num_steps", DEFAULT_NUM_STEPS))),
                cfg_scale=float(traits.get("cfg_scale_caption", DEFAULT_CFG)),
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported legacy profile mode: {mode or 'unknown'}. Re-select a voice in Voice Lab.",
            )

    item = write_generation(
        "runtime",
        audio,
        sample_rate,
        {
            "mode": "runtime_selected_profile",
            "text": req.text,
            "source_generation_id": generation.get("id"),
        },
    )
    item["elapsed_sec"] = round(time.perf_counter() - start, 2)
    return item


@app.get("/audio/{name:path}")
def audio(name: str) -> FileResponse:
    safe = Path(name)
    if safe.is_absolute() or ".." in safe.parts:
        raise HTTPException(status_code=400, detail="invalid audio path")
    path = OUT_DIR / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path, media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("DOKOCHAN_VOICE_LAB_PORT", "8765")))
