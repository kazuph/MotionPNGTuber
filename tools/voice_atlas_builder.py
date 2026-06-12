#!/usr/bin/env python3
"""Voice Atlas builder for Dokochan Voice Lab.

irodori TTS (VoiceDesign) が内包する声質空間を実データでマッピングする。

  generate : 多様なキャプション×seedで音声を実生成し、wav・DACVAE潜在統計・
             音響特徴を保存する（再開可能）。
  project  : 潜在統計の埋め込みを UMAP / PCA で2Dに射影し、軸の音響的解釈と
             検証指標を含む atlas.json を出力する。

座標は手書きアンカーの配置ではなく「モデルが実際に生成した音声の分布」から
導出されるため、マップ＝モデルの声質空間のデータ駆動な地図になる。
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ATLAS_DIR = ROOT / "local" / "voice_lab_outputs" / "atlas"
WAV_DIR = ATLAS_DIR / "wav"
SAMPLES_PATH = ATLAS_DIR / "samples.jsonl"
EMBED_PATH = ATLAS_DIR / "embeddings.npz"
ATLAS_PATH = ATLAS_DIR / "atlas.json"
REFS_DIR = ATLAS_DIR / "refs"
REFS_META_PATH = REFS_DIR / "refs.json"
REFS_EMBED_PATH = REFS_DIR / "refs_embeddings.npz"
# 話者性埋め込み（tools/voice_speaker_embed.py が生成）。存在すればこちらを優先。
SPK_EMBED_PATH = ATLAS_DIR / "speaker_embeddings.npz"
REFS_SPK_EMBED_PATH = REFS_DIR / "refs_speaker_embeddings.npz"

MODEL_ID = "mlx-community/Irodori-TTS-600M-v3-VoiceDesign-8bit"
FIXED_TEXT = "こんにちは、今日はとてもいい天気ですね。"

SPEAKERS = [
    ("girl_child", "幼い女の子の声", "female"),
    ("girl", "少女の声", "female"),
    ("female_young", "若い女性の声", "female"),
    ("female_adult", "大人の女性の声", "female"),
    ("female_elder", "年配の女性の声", "female"),
    ("grandma", "おばあさんの声", "female"),
    ("boy", "少年の声", "male"),
    ("male_young", "若い男性の声", "male"),
    ("male_adult", "大人の男性の声", "male"),
    ("male_deep", "低く渋い男性の声", "male"),
    ("male_elder", "年配の男性の声", "male"),
    ("grandpa", "おじいさんの声", "male"),
    ("neutral", "中性的な声", "neutral"),
]

PITCH = ["とても高い声", "高めの声", "低めの声", "とても低い声"]

# 声の質感（テクスチャ）
TEXTURE = [
    "少しハスキーな声",
    "しゃがれた声",
    "かすれた声",
    "息混じりの声",
    "鼻にかかった声",
    "こもった声",
    "透き通った声",
    "芯のある声",
    "太い声",
    "細い声",
    "柔らかな声",
    "硬質な声",
    "艶のある声",
    "ざらついた声",
    "甘い声",
    "乾いた声",
    "よく響く声",
    "弱々しい声",
    "張りのある声",
    "丸みのある声",
]

# 感情・心理状態（複雑な状態を含む）
EMOTION = [
    "打ちひしがれているが、芯に力強さがある",
    "悲しみをこらえている",
    "静かな怒りを含んでいる",
    "涙をこらえて少し震えている",
    "喜びで弾んでいる",
    "退屈そうでけだるい",
    "緊張して硬くなっている",
    "安堵して緩んでいる",
    "誇らしげで自信に満ちている",
    "疲れ切っている",
    "夢見心地でぼんやりしている",
    "高揚して熱っぽい",
    "恥ずかしそうに小さくなっている",
    "不敵で余裕がある",
    "慈しむように優しい",
    "突き放すように冷たい",
    "焦って慌てている",
    "厳かで重々しい",
]

# アーキタイプ（役柄・語りの型）
ARCHETYPE = [
    "執事のように恭しい話し方",
    "魔女のように妖しい話し方",
    "武士のように凛々しい話し方",
    "お姫様のように上品な話し方",
    "女王のように威厳のある話し方",
    "海賊のように豪快な話し方",
    "深夜ラジオのDJのように落ち着いた話し方",
    "子守唄を歌うように穏やかな話し方",
    "指揮官のように号令する話し方",
    "老師のように悟った話し方",
    "探偵のように冷静な話し方",
    "アイドルのように華やかな話し方",
    "巫女のように静謐な話し方",
    "吟遊詩人のように歌うような話し方",
    "商人のように調子のいい話し方",
    "教師のように諭す話し方",
    "ニュースキャスターのように明瞭な話し方",
    "占い師のように思わせぶりな話し方",
    "体育会系のように威勢のいい話し方",
    "図書館司書のように物静かな話し方",
]

# 話法（テンポ・抑揚）
DELIVERY = [
    "早口で話す",
    "とてもゆっくり話す",
    "抑揚豊かに話す",
    "平坦に淡々と話す",
    "ささやくように話す",
    "朗々と話す",
    "ぼそぼそと話す",
    "ハキハキと話す",
    "間延びした話し方",
]

# ユーザー要望を直接反映した手書きキャプション（speaker合成なしの完全文）
CURATED = [
    ("日本語を話す少しハスキーな大人の女性の声。低めで艶があり、落ち着いている。", "female"),
    ("日本語を話す若い女性の声。打ちひしがれているが、芯に力強さがある。少しかすれている。", "female"),
    ("日本語を話すハスキーな若い女性の声。気だるげだが親密に語りかける。", "female"),
    ("日本語を話す大人の女性の声。涙をこらえながらも、毅然と言い切る。", "female"),
    ("日本語を話す低い男性の声。疲れ果てているが、最後の気力を振り絞っている。", "male"),
    ("日本語を話すおばあさんの声。昔話を聞かせるように、ゆったりと温かい。", "female"),
    ("日本語を話すおじいさんの声。しわがれているが、目尻の笑みが伝わる。", "male"),
    ("日本語を話す少女の声。強がっているが、語尾が少し震えている。", "female"),
    ("日本語を話す若い男性の声。皮肉っぽく笑いを含んでいる。", "male"),
    ("日本語を話す中性的な声。感情を排した機械のような均質さ。", "neutral"),
    ("日本語を話す大人の女性の声。酔ったように陽気で、呂律がゆるい。", "female"),
    ("日本語を話す若い女性の声。囁き声だが、情熱がこもっている。", "female"),
    ("日本語を話す大人の男性の声。深夜の電話のように静かで親密。", "male"),
    ("日本語を話す少年の声。声変わり途中のかすれと不安定さがある。", "male"),
    ("日本語を話す大人の女性の声。教会で祈るように敬虔で静か。", "female"),
    ("日本語を話す若い女性の声。鼻声で、風邪をひいているように甘ったるい。", "female"),
]

GEN_PARAMS = {
    "sequence_length": 95,
    "num_steps": 10,
    "cfg_scale_caption": 4.5,
}


def build_caption_plan(n_captions: int, rng_seed: int = 42) -> list[dict[str, Any]]:
    """声質バリエーションを最大化するキャプション計画。

    狙い: 点の数 ≒ 声の種類数。seedの重複生成は検証用サブセットに限定し、
    質感×感情×アーキタイプ×話法×ピッチの組合せで声質空間を広く張る。
    """
    rng = random.Random(rng_seed)
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(caption: str, tags: dict[str, Any], sample_seeds: list[int]) -> None:
        if caption in seen:
            return
        seen.add(caption)
        plan.append({"caption": caption, "tags": tags, "seeds": sample_seeds})

    # 手書きの濃いキャプション（ユーザー要望の声を直接含む）
    for caption, gender in CURATED:
        add(caption, {"speaker": "curated", "gender": gender, "mods": ["curated"]}, [101])

    # 素のキャプション: speaker単体（seed由来の声の揺れの観測用に3seed）
    for key, phrase, gender in SPEAKERS:
        add(
            f"日本語を話す{phrase}。",
            {"speaker": key, "gender": gender, "mods": []},
            [101, 202, 404],
        )

    # 組合せキャプション: speakerラウンドロビン × 修飾パターン
    pools = {
        "texture": TEXTURE,
        "emotion": EMOTION,
        "archetype": ARCHETYPE,
        "delivery": DELIVERY,
        "pitch": PITCH,
    }
    patterns = [
        ("texture",),
        ("emotion",),
        ("archetype",),
        ("texture", "emotion"),
        ("texture", "delivery"),
        ("emotion", "delivery"),
        ("archetype", "texture"),
        ("pitch", "texture"),
        ("pitch", "emotion"),
        ("pitch", "archetype"),
        ("texture", "emotion", "delivery"),
        ("pitch", "texture", "archetype"),
    ]
    idx = 0
    while len(plan) < n_captions and idx < n_captions * 20:
        key, phrase, gender = SPEAKERS[idx % len(SPEAKERS)]
        pattern = patterns[(idx // len(SPEAKERS)) % len(patterns)]
        idx += 1
        mods = [rng.choice(pools[p]) for p in pattern]
        caption = f"日本語を話す{phrase}。" + "。".join(mods) + "。"
        # 12キャプションに1つは2seed生成して「同じ声質は近接する」検証に使う
        seeds = [101, 202] if len(plan) % 12 == 0 else [100 + rng.randrange(900)]
        add(caption, {"speaker": key, "gender": gender, "mods": list(mods)}, seeds)

    return plan


def extract_features(audio: np.ndarray, sample_rate: int) -> dict[str, float]:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=0)
    out = {
        "f0_med": 0.0,
        "f0_iqr": 0.0,
        "centroid": 0.0,
        "rolloff": 0.0,
        "rms": 0.0,
        "zcr": 0.0,
        "voiced": 0.0,
        "dur": float(len(audio) / sample_rate) if sample_rate else 0.0,
    }
    if audio.size == 0:
        return out

    audio = audio - float(np.mean(audio))
    frame = 2048
    hop = 960
    if audio.size < frame:
        audio = np.pad(audio, (0, frame - audio.size))

    rms_values: list[float] = []
    zcr_values: list[float] = []
    centroid_values: list[float] = []
    rolloff_values: list[float] = []
    f0_values: list[float] = []
    freqs = np.fft.rfftfreq(frame, 1.0 / sample_rate)
    window = np.hanning(frame).astype(np.float32)
    min_lag = max(1, int(sample_rate / 420))
    max_lag = min(frame - 1, int(sample_rate / 70))

    for start in range(0, audio.size - frame + 1, hop):
        chunk = audio[start : start + frame] * window
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        rms_values.append(rms)
        if rms < 0.002:
            continue
        zcr_values.append(float(np.mean(np.abs(np.diff(np.signbit(chunk))))))
        spectrum = np.abs(np.fft.rfft(chunk))
        total = float(spectrum.sum())
        if total > 1e-8:
            centroid_values.append(float((freqs * spectrum).sum() / total))
            cumsum = np.cumsum(spectrum)
            roll_idx = int(np.searchsorted(cumsum, 0.85 * total))
            rolloff_values.append(float(freqs[min(roll_idx, len(freqs) - 1)]))
        corr = np.correlate(chunk, chunk, mode="full")[frame - 1 :]
        if corr[0] > 1e-8 and max_lag > min_lag:
            lag = int(np.argmax(corr[min_lag:max_lag]) + min_lag)
            confidence = float(corr[lag] / corr[0])
            if confidence > 0.24:
                f0_values.append(float(sample_rate / lag))

    voiced_rms = [r for r in rms_values if r >= 0.002]
    if f0_values:
        out["f0_med"] = float(np.median(f0_values))
        q75, q25 = np.percentile(f0_values, [75, 25])
        out["f0_iqr"] = float(q75 - q25)
    if centroid_values:
        out["centroid"] = float(np.mean(centroid_values))
    if rolloff_values:
        out["rolloff"] = float(np.mean(rolloff_values))
    if voiced_rms:
        out["rms"] = float(np.mean(voiced_rms))
    if zcr_values:
        out["zcr"] = float(np.mean(zcr_values))
    out["voiced"] = float(len(f0_values) / max(1, len(rms_values)))
    return out


def load_samples() -> list[dict[str, Any]]:
    if not SAMPLES_PATH.exists():
        return []
    items = []
    for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines():
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return items


def load_embeddings() -> dict[str, np.ndarray]:
    if not EMBED_PATH.exists():
        return {}
    data = np.load(EMBED_PATH, allow_pickle=False)
    ids = [str(s) for s in data["ids"]]
    vecs = data["vectors"]
    return {i: v for i, v in zip(ids, vecs)}


def save_embeddings(store: dict[str, np.ndarray]) -> None:
    ids = sorted(store.keys())
    np.savez_compressed(
        EMBED_PATH,
        ids=np.array(ids),
        vectors=np.stack([store[i] for i in ids]).astype(np.float32),
    )


def cmd_generate(args: argparse.Namespace) -> None:
    import mlx.core as mx
    import soundfile as sf
    from mlx_audio.tts import load
    from mlx_audio.tts.models.irodori_tts.irodori_tts import _find_silence_point

    ATLAS_DIR.mkdir(parents=True, exist_ok=True)
    WAV_DIR.mkdir(parents=True, exist_ok=True)

    plan = build_caption_plan(args.captions)
    jobs: list[dict[str, Any]] = []
    for entry in plan:
        for seed in entry["seeds"]:
            jobs.append({"caption": entry["caption"], "tags": entry["tags"], "seed": seed})
    if args.limit:
        jobs = jobs[: args.limit]

    existing = load_samples()
    done_pairs = {(item["caption"], int(item["seed"])) for item in existing}
    embed_store = load_embeddings()

    print(f"[atlas] total jobs: {len(jobs)} (already generated: {len(done_pairs)})")
    model = load(args.model, lazy=True, strict=False)

    import hashlib

    started = time.perf_counter()
    completed = 0
    for idx, job in enumerate(jobs):
        # id は (caption, seed) のハッシュ: 計画の並び替えに対して安定
        digest = hashlib.sha1(f"{job['caption']}|{job['seed']}".encode("utf-8")).hexdigest()[:8]
        sample_id = f"v{digest}"
        wav_path = WAV_DIR / f"{sample_id}.wav"
        if (job["caption"], int(job["seed"])) in done_pairs:
            continue
        if wav_path.exists() and sample_id in embed_store:
            continue

        t0 = time.perf_counter()
        latents = model.generate_latents(
            text=args.text,
            caption=job["caption"],
            rng_seed=int(job["seed"]),
            **GEN_PARAMS,
        )
        latent_for_decode = mx.transpose(latents, (0, 2, 1))
        audio_out = model.dacvae.decode(latent_for_decode, chunk_size=50)[:, :, 0]
        mx.eval(audio_out)
        silence_t = max(8, _find_silence_point(latents[0]))
        trim_samples = silence_t * int(model.config.audio_downsample_factor)
        audio = np.array(audio_out[0][:trim_samples], dtype=np.float32)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0.99:
            audio = audio * (0.99 / peak)
        sample_rate = int(model.sample_rate)
        sf.write(str(wav_path), audio, sample_rate)

        lat = np.array(latents[0][:silence_t], dtype=np.float32)
        emb = np.concatenate([lat.mean(axis=0), lat.std(axis=0)])
        embed_store[sample_id] = emb.astype(np.float32)

        features = extract_features(audio, sample_rate)
        item = {
            "id": sample_id,
            "caption": job["caption"],
            "tags": job["tags"],
            "seed": int(job["seed"]),
            "text": args.text,
            "params": GEN_PARAMS,
            "wav": f"atlas/wav/{sample_id}.wav",
            "sample_rate": sample_rate,
            "duration_sec": round(float(len(audio) / sample_rate), 3),
            "features": {k: round(v, 4) for k, v in features.items()},
            "elapsed_sec": round(time.perf_counter() - t0, 2),
        }
        with SAMPLES_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        completed += 1
        if completed % 10 == 0:
            save_embeddings(embed_store)
            rate = (time.perf_counter() - started) / completed
            remaining = (len(jobs) - idx - 1) * rate
            print(
                f"[atlas] {idx + 1}/{len(jobs)} f0={features['f0_med']:.0f}Hz "
                f"dur={item['duration_sec']:.1f}s eta={remaining / 60:.1f}min",
                flush=True,
            )

    save_embeddings(embed_store)
    print(f"[atlas] generate done: {completed} new samples in {(time.perf_counter() - started) / 60:.1f}min")


def cmd_embed_refs(args: argparse.Namespace) -> None:
    """リファレンス声（voice_lock生成wav）をアトラスと同じDACVAE潜在統計で埋め込む。"""
    import json as _json

    import mlx.core as mx
    import soundfile as sf
    from mlx_audio.tts import load

    if not REFS_META_PATH.exists():
        raise SystemExit(f"refs metadata not found: {REFS_META_PATH}")
    meta = _json.loads(REFS_META_PATH.read_text(encoding="utf-8"))
    model = load(args.model, lazy=True, strict=False)
    target_sr = int(model.sample_rate)

    ids: list[str] = []
    vectors: list[np.ndarray] = []
    for ref in meta["references"]:
        wav_path = REFS_DIR / ref["wav"]
        audio, sr = sf.read(str(wav_path), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != target_sr:
            # 線形補間で十分（DACVAEのmean/std統計が目的のため）
            idx = np.linspace(0, len(audio) - 1, int(len(audio) * target_sr / sr))
            audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
        audio_mx = mx.array(audio)[None, :, None]  # (1, L, 1)
        latent = model.dacvae.encode(audio_mx)  # (1, 128, T)
        mx.eval(latent)
        lat = np.array(mx.transpose(latent, (0, 2, 1))[0], dtype=np.float32)  # (T, 128)
        emb = np.concatenate([lat.mean(axis=0), lat.std(axis=0)])
        ids.append(ref["id"])
        vectors.append(emb.astype(np.float32))
        feats = extract_features(audio, target_sr)
        print(f"[refs] {ref['id']} ({ref['label']}) f0={feats['f0_med']:.0f}Hz dur={feats['dur']:.1f}s")

    np.savez_compressed(REFS_EMBED_PATH, ids=np.array(ids), vectors=np.stack(vectors))
    print(f"[refs] wrote {REFS_EMBED_PATH}")


FEATURE_KEYS = ["f0_med", "f0_iqr", "centroid", "rolloff", "rms", "zcr", "voiced", "dur"]
FEATURE_LABELS = {
    "f0_med": "声の高さ (F0)",
    "f0_iqr": "抑揚の幅",
    "centroid": "明るさ (重心周波数)",
    "rolloff": "高域成分",
    "rms": "声量",
    "zcr": "硬さ (ZCR)",
    "voiced": "有声率",
    "dur": "話す速さ (長さ)",
}


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ar = np.argsort(np.argsort(a)).astype(np.float64)
    br = np.argsort(np.argsort(b)).astype(np.float64)
    ar -= ar.mean()
    br -= br.mean()
    denom = float(np.sqrt((ar * ar).sum() * (br * br).sum()))
    if denom < 1e-9:
        return 0.0
    return float((ar * br).sum() / denom)


def axis_hints(coords: np.ndarray, feats: np.ndarray) -> dict[str, Any]:
    hints: dict[str, Any] = {}
    for ax, name in ((0, "x"), (1, "y")):
        rows = []
        for fi, key in enumerate(FEATURE_KEYS):
            rho = spearman(coords[:, ax], feats[:, fi])
            rows.append({"feature": key, "label": FEATURE_LABELS[key], "rho": round(rho, 3)})
        rows.sort(key=lambda r: -abs(r["rho"]))
        hints[name] = rows
    return hints


def normalize_coords(coords: np.ndarray, margin: float = 0.05) -> np.ndarray:
    mins = coords.min(axis=0)
    spans = np.maximum(coords.max(axis=0) - mins, 1e-9)
    unit = (coords - mins) / spans
    return margin + unit * (1.0 - 2.0 * margin)


def coord_scaler(coords: np.ndarray, margin: float = 0.05):
    """アトラス座標で正規化パラメータを固定し、リファレンスにも同一変換を適用する。"""
    mins = coords.min(axis=0)
    spans = np.maximum(coords.max(axis=0) - mins, 1e-9)

    def apply(c: np.ndarray) -> np.ndarray:
        unit = (c - mins) / spans
        return np.clip(margin + unit * (1.0 - 2.0 * margin), 0.01, 0.99)

    return apply


def f0_gender_flip(gender: str, f0: float) -> bool:
    """指示タグと実音声のF0が明確に逆転しているか（保守的しきい値）。

    例: 「おじいさんの声。お姫様のように上品な話し方。」→ F0 328Hz。
    属性が衝突したcaptionではモデルがアーキタイプ側に引っ張られることがあり、
    その点はタグ（色分け・検索）が実音声と食い違う。削除せず⚠️として可視化する。
    """
    if gender == "male" and f0 > 250.0:
        return True
    if gender == "female" and 0.0 < f0 < 135.0:
        return True
    return False


def knn_gender_purity(coords: np.ndarray, genders: list[str], k: int = 10) -> float:
    n = len(genders)
    if n <= k:
        return 0.0
    d2 = ((coords[:, None, :] - coords[None, :, :]) ** 2).sum(axis=2)
    np.fill_diagonal(d2, np.inf)
    hits = 0
    for i in range(n):
        nn = np.argsort(d2[i])[:k]
        hits += sum(1 for j in nn if genders[j] == genders[i])
    return float(hits / (n * k))


def cmd_project(args: argparse.Namespace) -> None:
    samples = load_samples()
    if SPK_EMBED_PATH.exists():
        # 話者照合用 WavLM x-vector（L2正規化済み）: 「同じ人の声」を保存する空間
        data = np.load(SPK_EMBED_PATH, allow_pickle=False)
        embed_store = {str(i): v for i, v in zip(data["ids"], data["vectors"])}
        embedding_mode = "speaker"
    else:
        embed_store = load_embeddings()
        embedding_mode = "dacvae_stats"
    samples = [s for s in samples if s["id"] in embed_store and (WAV_DIR / f"{s['id']}.wav").exists()]
    # 同一idの重複行は後勝ち
    dedup: dict[str, dict[str, Any]] = {s["id"]: s for s in samples}
    samples = sorted(dedup.values(), key=lambda s: s["id"])
    if len(samples) < 8:
        raise SystemExit(f"not enough samples to project: {len(samples)}")

    matrix = np.stack([embed_store[s["id"]] for s in samples]).astype(np.float64)
    matrix = np.nan_to_num(matrix)
    if embedding_mode == "speaker":
        # 正規化済み話者ベクトルはそのままcosine空間として使う
        mean = np.zeros(matrix.shape[1])
        std = np.ones(matrix.shape[1])
        z = matrix
    else:
        mean = matrix.mean(axis=0)
        std = matrix.std(axis=0) + 1e-9
        z = (matrix - mean) / std

    feats = np.array(
        [[float(s["features"].get(k, 0.0)) for k in FEATURE_KEYS] for s in samples], dtype=np.float64
    )
    genders = [s["tags"].get("gender", "?") for s in samples]

    # PCA
    z_mean = z.mean(axis=0)
    u, sv, vt = np.linalg.svd(z - z_mean, full_matrices=False)
    pca_raw = (z - z_mean) @ vt[:2].T
    pca_scale = coord_scaler(pca_raw)
    pca_coords = pca_scale(pca_raw)
    pca_var = (sv**2) / float((sv**2).sum())

    # UMAP
    import umap

    # n_neighbors/min_dist は埋め込みごとにスイープ済み:
    # 同caption(別seed)距離比（声質保存性）と空間利用率（探索体験）のバランス点
    nn_, md_ = (50, 0.5) if embedding_mode == "speaker" else (50, 0.8)
    reducer = umap.UMAP(
        n_neighbors=min(nn_, len(samples) - 1),
        min_dist=md_,
        metric="cosine",
        random_state=42,
    )
    umap_raw = np.asarray(reducer.fit_transform(z), dtype=np.float64)
    umap_scale = coord_scaler(umap_raw)
    umap_coords = umap_scale(umap_raw)

    # リファレンス声（voice_lock生成wav）を同一空間へ射影
    references: list[dict[str, Any]] = []
    ref_sims: dict[str, np.ndarray] = {}
    refs_embed_path = (
        REFS_SPK_EMBED_PATH
        if (embedding_mode == "speaker" and REFS_SPK_EMBED_PATH.exists())
        else REFS_EMBED_PATH
    )
    if REFS_META_PATH.exists() and refs_embed_path.exists():
        import json as _json

        import soundfile as sf

        refs_meta = _json.loads(REFS_META_PATH.read_text(encoding="utf-8"))
        refs_npz = np.load(refs_embed_path, allow_pickle=False)
        ref_vec = {str(i): v for i, v in zip(refs_npz["ids"], refs_npz["vectors"])}
        for ref in refs_meta["references"]:
            vec = ref_vec.get(ref["id"])
            if vec is None:
                continue
            rz = (np.asarray(vec, dtype=np.float64) - mean) / std
            r_umap = umap_scale(np.asarray(reducer.transform(rz[None, :]), dtype=np.float64))[0]
            r_pca = pca_scale((rz[None, :] - z_mean) @ vt[:2].T)[0]
            # 各アトラス点との話者類似度（cosine）。UIの「★類似度」色分けに使う
            z_norm = z / (np.linalg.norm(z, axis=1, keepdims=True) + 1e-9)
            rz_norm = rz / (np.linalg.norm(rz) + 1e-9)
            ref_sims[ref["id"]] = z_norm @ rz_norm
            audio, sr = sf.read(str(REFS_DIR / ref["wav"]), dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            ref_feats = extract_features(audio, sr)
            references.append(
                {
                    "id": ref["id"],
                    "label": ref["label"],
                    "voice_lock_id": ref["voice_lock_id"],
                    "note": ref.get("note", ""),
                    "audio_url": f"/audio/atlas/refs/{ref['wav']}",
                    "duration_sec": round(float(len(audio) / sr), 3),
                    "features": {k: round(v, 4) for k, v in ref_feats.items()},
                    "umap": [round(float(r_umap[0]), 4), round(float(r_umap[1]), 4)],
                    "pca": [round(float(r_pca[0]), 4), round(float(r_pca[1]), 4)],
                }
            )
        print(f"[atlas] embedded {len(references)} reference voices")

    # 検証: 同一キャプション(別seed)の距離 vs 全体平均距離
    by_caption: dict[str, list[int]] = {}
    for i, s in enumerate(samples):
        by_caption.setdefault(s["caption"], []).append(i)

    def pair_ratio(coords: np.ndarray) -> dict[str, float]:
        intra = []
        for idxs in by_caption.values():
            for a in range(len(idxs)):
                for b in range(a + 1, len(idxs)):
                    intra.append(float(np.linalg.norm(coords[idxs[a]] - coords[idxs[b]])))
        rng = np.random.default_rng(0)
        n = len(samples)
        rand = [
            float(np.linalg.norm(coords[i] - coords[j]))
            for i, j in zip(rng.integers(0, n, 4000), rng.integers(0, n, 4000))
            if i != j
        ]
        return {
            "intra_caption_mean": round(float(np.mean(intra)), 4) if intra else 0.0,
            "random_pair_mean": round(float(np.mean(rand)), 4) if rand else 0.0,
            "ratio": round(float(np.mean(intra) / max(1e-9, np.mean(rand))), 4) if intra and rand else 0.0,
        }

    # 音響ラベル（F0由来）での純度: captionタグはモデルが指示を守らない分ノイズが乗るため、
    # 「実際の出音」基準の純度も併記する（埋め込みの真の品質指標）
    f0_arr = feats[:, FEATURE_KEYS.index("f0_med")]
    acoustic_labels = ["male" if f < 155 else "female" if f > 215 else "ambig" for f in f0_arr]
    ac_idx = [i for i, a in enumerate(acoustic_labels) if a != "ambig"]
    ac_genders = [acoustic_labels[i] for i in ac_idx]

    validation = {
        "n_samples": len(samples),
        "umap": {
            "same_caption_distance": pair_ratio(umap_coords),
            "knn_gender_purity@10": round(knn_gender_purity(umap_coords, genders), 4),
            "knn_acoustic_gender_purity@10": round(
                knn_gender_purity(umap_coords[ac_idx], ac_genders), 4
            ),
        },
        "pca": {
            "same_caption_distance": pair_ratio(pca_coords),
            "knn_gender_purity@10": round(knn_gender_purity(pca_coords, genders), 4),
            "explained_variance": [round(float(v), 4) for v in pca_var[:2]],
        },
        "embedding": (
            "ECAPA-TDNN speaker embedding (speechbrain/spkrec-ecapa-voxceleb, cosine)"
            if embedding_mode == "speaker"
            else "DACVAE latent mean+std pooling (256d, z-scored, cosine)"
        ),
    }

    # 音響性別分類器: タグとF0が強一致する確実サンプルを教師に、
    # ECAPA埋め込みのセントロイドで全点を分類（F0単独の閾値は男声の高F0で破綻するため）
    zn_deck = z / (np.linalg.norm(z, axis=1, keepdims=True) + 1e-9)
    tag_arr = np.array(genders)
    conf_f = (tag_arr == "female") & (f0_arr > 225)
    conf_m = (tag_arr == "male") & (f0_arr < 145)
    acoustic_gender = ["unknown"] * len(samples)
    gender_prob = [0.5] * len(samples)
    if conf_f.sum() >= 10 and conf_m.sum() >= 10:
        cf = zn_deck[conf_f].mean(axis=0)
        cf /= np.linalg.norm(cf) + 1e-9
        cm = zn_deck[conf_m].mean(axis=0)
        cm /= np.linalg.norm(cm) + 1e-9
        score = zn_deck @ cf - zn_deck @ cm
        prob_f = 1.0 / (1.0 + np.exp(-score * 12.0))
        acoustic_gender = ["female" if p >= 0.5 else "male" for p in prob_f]
        gender_prob = [float(p if p >= 0.5 else 1.0 - p) for p in prob_f]
        np.savez(
            ATLAS_DIR / "gender_centroids.npz",
            female=cf.astype(np.float32),
            male=cm.astype(np.float32),
        )
    n_deck = len(samples)
    start = int(np.argmax(np.linalg.norm(z - z.mean(axis=0), axis=1)))
    deck_order = [start]
    dmin = 1.0 - zn_deck @ zn_deck[start]
    for _ in range(n_deck - 1):
        nxt = int(np.argmax(dmin))
        deck_order.append(nxt)
        dmin = np.minimum(dmin, 1.0 - zn_deck @ zn_deck[nxt])
    deck_rank = np.empty(n_deck, dtype=int)
    deck_rank[deck_order] = np.arange(n_deck)

    out_samples = []
    flip_count = 0
    for i, s in enumerate(samples):
        flip = f0_gender_flip(s["tags"].get("gender", ""), float(s["features"].get("f0_med", 0.0)))
        flip_count += int(flip)
        item = {
            "id": s["id"],
            "caption": s["caption"],
            "tags": s["tags"],
            "seed": s["seed"],
            "params": s.get("params", GEN_PARAMS),
            "audio_url": f"/audio/{s['wav']}",
            "duration_sec": s["duration_sec"],
            "features": s["features"],
            "voice_flip": flip,
            "acoustic_gender": acoustic_gender[i],
            "gender_prob": round(gender_prob[i], 4),
            "deck_rank": int(deck_rank[i]),
            "umap": [round(float(umap_coords[i, 0]), 4), round(float(umap_coords[i, 1]), 4)],
            "pca": [round(float(pca_coords[i, 0]), 4), round(float(pca_coords[i, 1]), 4)],
        }
        if ref_sims:
            item["ref_sim"] = {rid: round(float(sims[i]), 4) for rid, sims in ref_sims.items()}
        out_samples.append(item)
    validation["caption_fidelity"] = {
        "voice_flip_count": flip_count,
        "voice_flip_rate": round(flip_count / max(1, len(samples)), 4),
        "note": "指示タグとF0が明確に逆転した点（male>250Hz / female<135Hz）。属性衝突captionでモデルがアーキタイプ側に引っ張られた痕跡。",
    }

    atlas = {
        "built_at": time.time(),
        "model": MODEL_ID,
        "text": samples[0].get("text", FIXED_TEXT),
        "gen_params": GEN_PARAMS,
        "n_samples": len(samples),
        "projections": {
            "umap": {"axis_hints": axis_hints(umap_coords, feats)},
            "pca": {"axis_hints": axis_hints(pca_coords, feats)},
        },
        "validation": validation,
        "feature_labels": FEATURE_LABELS,
        "references": references,
        "samples": out_samples,
    }
    ATLAS_PATH.write_text(json.dumps(atlas, ensure_ascii=False), encoding="utf-8")
    print(f"[atlas] wrote {ATLAS_PATH} ({len(samples)} samples)")
    print(json.dumps(validation, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate")
    g.add_argument("--captions", type=int, default=140)
    g.add_argument("--limit", type=int, default=0, help="先頭N件のみ（スモークテスト用）")
    g.add_argument("--text", default=FIXED_TEXT)
    g.add_argument("--model", default=MODEL_ID)
    g.set_defaults(func=cmd_generate)

    p = sub.add_parser("project")
    p.set_defaults(func=cmd_project)

    r = sub.add_parser("embed-refs", help="リファレンス声(refs/)をDACVAE潜在統計で埋め込む")
    r.add_argument("--model", default=MODEL_ID)
    r.set_defaults(func=cmd_embed_refs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
