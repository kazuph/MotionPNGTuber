#!/usr/bin/env python3
"""話者性埋め込みの抽出（ECAPA-TDNN）。

経緯:
- DACVAE潜在 mean+std → 平均スペクトル統計で話者性を保存しない（音響性別純度74%）
- WavLM-base-plus-sv → 性別は分離(99.7%)するが判別力が圧縮されすぎ
  （別人のユナvs聡子で cos 0.924）で「似た声」の解像度がない
- ECAPA-TDNN (speechbrain/spkrec-ecapa-voxceleb) → 別人0.487 / 同一人物0.64-0.78 と
  ダイナミックレンジが広く、話者の聞き分けに一致する

実行（speechbrain入りvenv）:

  .venv-spk/bin/python tools/voice_speaker_embed.py

出力:
  local/voice_lab_outputs/atlas/speaker_embeddings.npz       (atlas samples)
  local/voice_lab_outputs/atlas/refs/refs_speaker_embeddings.npz (references)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ATLAS_DIR = ROOT / "local" / "voice_lab_outputs" / "atlas"
WAV_DIR = ATLAS_DIR / "wav"
REFS_DIR = ATLAS_DIR / "refs"
OUT_PATH = ATLAS_DIR / "speaker_embeddings.npz"
REFS_OUT_PATH = REFS_DIR / "refs_speaker_embeddings.npz"

MODEL_ID = "speechbrain/spkrec-ecapa-voxceleb"
TARGET_SR = 16_000


def read_wav_16k(path: Path) -> np.ndarray:
    from scipy.io import wavfile
    from scipy.signal import resample_poly

    sr, audio = wavfile.read(str(path))
    audio = np.asarray(audio, dtype=np.float32)
    if audio.dtype.kind == "i":
        audio = audio / np.iinfo(audio.dtype).max
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != TARGET_SR:
        from math import gcd

        g = gcd(sr, TARGET_SR)
        audio = resample_poly(audio, TARGET_SR // g, sr // g).astype(np.float32)
    return audio


def embed_one(path: Path) -> None:
    """単一wavのECAPA埋め込みをJSONで標準出力（サーバーからのsubprocess用）。"""
    import json as _json

    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    encoder = EncoderClassifier.from_hparams(source=MODEL_ID, run_opts={"device": "cpu"})
    audio = read_wav_16k(path)
    with torch.no_grad():
        vec = encoder.encode_batch(torch.from_numpy(audio)[None, :]).squeeze().numpy()
    vec = vec.astype(np.float32)
    vec = vec / max(1e-9, float(np.linalg.norm(vec)))
    print(_json.dumps([round(float(x), 6) for x in vec]))


def main() -> None:
    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    encoder = EncoderClassifier.from_hparams(source=MODEL_ID, run_opts={"device": "cpu"})

    def embed(path: Path) -> np.ndarray:
        audio = read_wav_16k(path)
        with torch.no_grad():
            vec = encoder.encode_batch(torch.from_numpy(audio)[None, :]).squeeze().numpy()
        vec = vec.astype(np.float32)
        return vec / max(1e-9, float(np.linalg.norm(vec)))

    wavs = sorted(WAV_DIR.glob("*.wav"))
    print(f"[spk] embedding {len(wavs)} atlas wavs with {MODEL_ID} on cpu")
    ids: list[str] = []
    vectors: list[np.ndarray] = []
    started = time.perf_counter()
    for i, wav in enumerate(wavs):
        ids.append(wav.stem)
        vectors.append(embed(wav))
        if (i + 1) % 100 == 0:
            rate = (time.perf_counter() - started) / (i + 1)
            print(f"[spk] {i + 1}/{len(wavs)} eta={(len(wavs) - i - 1) * rate / 60:.1f}min", flush=True)
    np.savez_compressed(OUT_PATH, ids=np.array(ids), vectors=np.stack(vectors))
    print(f"[spk] wrote {OUT_PATH}")

    refs_meta_path = REFS_DIR / "refs.json"
    if refs_meta_path.exists():
        meta = json.loads(refs_meta_path.read_text(encoding="utf-8"))
        rids: list[str] = []
        rvecs: list[np.ndarray] = []
        for ref in meta["references"]:
            rids.append(ref["id"])
            rvecs.append(embed(REFS_DIR / ref["wav"]))
            print(f"[spk] ref {ref['id']} ({ref['label']}) embedded")
        np.savez_compressed(REFS_OUT_PATH, ids=np.array(rids), vectors=np.stack(rvecs))
        print(f"[spk] wrote {REFS_OUT_PATH}")

    print(f"[spk] done in {(time.perf_counter() - started) / 60:.1f}min")


def serve(port: int) -> None:
    """常駐エンベッダー。毎回のモデルロード（3-5秒）を排除する。"""
    import torch
    import uvicorn
    from fastapi import FastAPI
    from pydantic import BaseModel
    from speechbrain.inference.speaker import EncoderClassifier

    encoder = EncoderClassifier.from_hparams(source=MODEL_ID, run_opts={"device": "cpu"})
    app = FastAPI(title="ECAPA embed daemon")

    class EmbedRequest(BaseModel):
        path: str

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "model": MODEL_ID}

    @app.post("/embed")
    def embed(req: EmbedRequest) -> dict:
        audio = read_wav_16k(Path(req.path))
        with torch.no_grad():
            vec = encoder.encode_batch(torch.from_numpy(audio)[None, :]).squeeze().numpy()
        vec = vec.astype(np.float32)
        vec = vec / max(1e-9, float(np.linalg.norm(vec)))
        return {"vector": [round(float(x), 6) for x in vec]}

    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--embed-wav", type=Path, default=None)
    parser.add_argument("--server", type=int, default=None, help="常駐エンベッダーとして起動するポート")
    args = parser.parse_args()
    if args.server:
        serve(args.server)
    elif args.embed_wav:
        embed_one(args.embed_wav)
    else:
        main()
