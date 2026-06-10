#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import queue
import threading
import time
import tkinter as tk
import urllib.request
import wave

from pathlib import Path
import sys

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loop_lipsync_runtime_patched_emotion_auto import (  # noqa: E402
    _emotion_button_label,
    synthesize_irodori_tts,
)


GEMMA_ENDPOINT = os.environ.get(
    "DOKOCHAN_GEMMA_ENDPOINT",
    "https://ubuntu-3090.tail5f04b.ts.net:8443/api/llm/v1/chat/completions",
)
GEMMA_MODEL = os.environ.get(
    "DOKOCHAN_GEMMA_MODEL",
    "gemma-4-12b-it-qat-grapev-mtp-q4-n3-vision-ctx200k-q8_0-q8_0",
)
GEMMA_SYSTEM_PROMPT = (
    "あなたはVTuberとして配信中のどこちゃんです。短く自然な日本語で返答してください。"
    "相手は家族やおじいちゃんではなく、配信を見ている視聴者さんです。"
    "どこちゃんはおばあちゃんを探す孫娘という属性を持っていますが、今夜はもうおばあちゃんを見つけて一緒に家へ帰ってきています。"
    "おばあちゃんは今は寝ているので、現在進行形で心配しすぎたり、探し続けているとは言わないでください。"
    "おばあちゃんはいつもすぐいなくなっちゃう、という軽い持ちネタとして扱ってください。"
    "返答は音声で読み上げるので、記号や箇条書きは避けてください。"
)


def append_event(path: str, event: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    event = {**event, "ts": time.time()}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()


def write_wav(path: str, audio: np.ndarray, samplerate: int) -> None:
    audio = np.asarray(audio, dtype=np.float32)
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767.0).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(samplerate))
        wf.writeframes(pcm.tobytes())


def ask_gemma_with_audio(wav_path: str) -> str:
    with open(wav_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("ascii")
    body = {
        "model": GEMMA_MODEL,
        "messages": [
            {"role": "system", "content": GEMMA_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "この音声を聞き取って、どこちゃんとして返答して。"},
                    {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}},
                ],
            },
        ],
        "max_tokens": 220,
    }
    req = urllib.request.Request(
        GEMMA_ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as res:
        data = json.loads(res.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        content = "".join(parts)
    return str(content).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-path", required=True)
    parser.add_argument("--title", default="Dokochan Emotion")
    parser.add_argument("--initial", default="joy")
    parser.add_argument("--emotions", default="joy,anger,sad,surprise")
    parser.add_argument("--tts-dir", default="")
    parser.add_argument("--enable-tts", action="store_true")
    args = parser.parse_args()

    emotions = [x.strip() for x in args.emotions.split(",") if x.strip()]
    root = tk.Tk()
    root.title(args.title)
    frm = tk.Frame(root, padx=10, pady=10)
    frm.pack(fill="both", expand=True)

    if platform.system() == "Windows":
        font_bold = ("Meiryo", 12, "bold")
        font_norm = ("Meiryo", 11)
    else:
        font_bold = None
        font_norm = None

    lbl_kwargs = {"font": font_bold} if font_bold else {}
    btn_kwargs = {"font": font_norm} if font_norm else {}
    ui_q: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def fit_window_to_content() -> None:
        try:
            root.update_idletasks()
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            ww = min(max(520, root.winfo_reqwidth()), max(520, sw - 48))
            wh = min(max(220, root.winfo_reqheight()), max(320, sh - 120))
            root.geometry(f"{ww}x{wh}+{max(0, sw - ww - 24)}+{max(0, sh - wh - 80)}")
        except Exception:
            pass

    tk.Label(frm, text="Emotion", **lbl_kwargs).pack(anchor="w")

    def push_emotion(value: str) -> None:
        append_event(args.event_path, {"type": "emotion", "value": value})

    emotion_frame = tk.Frame(frm)
    emotion_frame.pack(fill="x", pady=(0, 10))
    for idx, emo in enumerate(emotions):
        btn = tk.Button(
            emotion_frame,
            text=f"{_emotion_button_label(emo)}  {emo}",
            command=lambda v=emo: push_emotion(v),
            anchor="center",
            **btn_kwargs,
        )
        btn.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=2, pady=2)
    emotion_frame.grid_columnconfigure(0, weight=1)
    emotion_frame.grid_columnconfigure(1, weight=1)

    if args.enable_tts:
        tk.Label(frm, text="Input", **lbl_kwargs).pack(anchor="w")
        mode_var = tk.StringVar(value="audio")
        mode_frame = tk.Frame(frm)
        mode_frame.pack(fill="x", pady=(0, 6))

        def set_input_mode(value: str) -> None:
            mode_var.set(value)
            append_event(args.event_path, {"type": "input_mode", "value": value})

        tk.Radiobutton(
            mode_frame,
            text="macOS audio",
            variable=mode_var,
            value="audio",
            command=lambda: set_input_mode("audio"),
            **btn_kwargs,
        ).pack(side="left")
        tk.Radiobutton(
            mode_frame,
            text="Irodori TTS",
            variable=mode_var,
            value="tts",
            command=lambda: set_input_mode("tts"),
            **btn_kwargs,
        ).pack(side="left")
        tk.Radiobutton(
            mode_frame,
            text="Gemma Voice",
            variable=mode_var,
            value="gemma",
            command=lambda: set_input_mode("gemma"),
            **btn_kwargs,
        ).pack(side="left")

        tts_var = tk.StringVar()
        tts_entry = tk.Entry(
            frm,
            textvariable=tts_var,
            bg="white",
            fg="black",
            insertbackground="black",
            highlightthickness=1,
            highlightbackground="#aaaaaa",
            **btn_kwargs,
        )
        tts_entry.pack(fill="x", pady=2)
        status_var = tk.StringVar(value="")

        def poll_ui_q() -> None:
            try:
                while True:
                    kind, value = ui_q.get_nowait()
                    if kind == "status":
                        status_var.set(value)
                        if value:
                            tts_status_label.pack(fill="x", pady=(2, 0))
                        else:
                            tts_status_label.pack_forget()
                        root.after_idle(fit_window_to_content)
                    elif kind == "send_state":
                        tts_btn.configure(state=value)
                    elif kind == "gemma_status":
                        gemma_status_var.set(value)
                        root.after_idle(fit_window_to_content)
                    elif kind == "gemma_state":
                        gemma_btn.configure(state=value)
            except queue.Empty:
                pass
            root.after(100, poll_ui_q)

        def send_tts() -> None:
            text = tts_var.get().strip()
            if not text:
                return
            set_input_mode("tts")
            status_var.set("生成中...")
            tts_status_label.pack(fill="x", pady=(2, 0))
            root.after_idle(fit_window_to_content)
            tts_btn.configure(state="disabled")

            def worker() -> None:
                try:
                    wav_path = synthesize_irodori_tts(text, args.tts_dir)
                    append_event(args.event_path, {"type": "tts", "path": wav_path})
                    ui_q.put_nowait(("status", "再生中"))
                except Exception as e:
                    ui_q.put_nowait(("status", f"TTS失敗: {e}"))
                finally:
                    ui_q.put_nowait(("send_state", "normal"))

            threading.Thread(target=worker, name="irodori-tts", daemon=True).start()

        tts_btn = tk.Button(frm, text="送信", command=send_tts, anchor="center", **btn_kwargs)
        tts_btn.pack(fill="x", pady=(2, 2))
        tts_status_label = tk.Label(
            frm,
            textvariable=status_var,
            anchor="w",
            justify="left",
            **btn_kwargs,
        )

        gemma_status_var = tk.StringVar(value="録音ボタンで会話")
        recording = {
            "active": False,
            "stream": None,
            "chunks": [],
            "samplerate": 16000,
        }

        def stop_recording_to_wav() -> str:
            stream = recording.get("stream")
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            recording["stream"] = None
            recording["active"] = False
            chunks = recording.get("chunks") or []
            if not chunks:
                raise RuntimeError("録音データが空です")
            audio = np.concatenate(chunks).astype(np.float32, copy=False)
            out_dir = args.tts_dir or str(ROOT / ".runtime_logs" / "irodori")
            os.makedirs(out_dir, exist_ok=True)
            wav_path = os.path.join(out_dir, f"gemma_input_{int(time.time() * 1000)}.wav")
            write_wav(wav_path, audio, int(recording["samplerate"]))
            return wav_path

        def start_recording() -> None:
            recording["chunks"] = []
            samplerate = 16000

            def audio_cb(indata, frames, time_info, status) -> None:
                x = indata.astype(np.float32)
                if x.ndim == 2:
                    x = x.mean(axis=1)
                recording["chunks"].append(x.copy())

            stream = sd.InputStream(
                samplerate=samplerate,
                channels=1,
                dtype="float32",
                callback=audio_cb,
                blocksize=1024,
            )
            stream.start()
            recording["samplerate"] = samplerate
            recording["stream"] = stream
            recording["active"] = True

        def finish_gemma_voice(wav_path: str) -> None:
            try:
                ui_q.put_nowait(("gemma_status", "Gemma応答待ち..."))
                reply = ask_gemma_with_audio(wav_path)
                if not reply:
                    raise RuntimeError("Gemmaの返答が空です")
                ui_q.put_nowait(("gemma_status", reply))
                wav_reply = synthesize_irodori_tts(reply, args.tts_dir)
                append_event(args.event_path, {"type": "tts", "path": wav_reply})
            except Exception as e:
                ui_q.put_nowait(("gemma_status", f"Gemma失敗: {e}"))
            finally:
                ui_q.put_nowait(("gemma_state", "normal"))

        def toggle_gemma_recording() -> None:
            if not recording["active"]:
                try:
                    set_input_mode("gemma")
                    start_recording()
                    gemma_status_var.set("録音中...")
                    gemma_btn.configure(text="停止して会話")
                except Exception as e:
                    gemma_status_var.set(f"録音失敗: {e}")
                return
            try:
                wav_path = stop_recording_to_wav()
                gemma_status_var.set("送信中...")
                gemma_btn.configure(state="disabled", text="録音開始")
                threading.Thread(target=finish_gemma_voice, args=(wav_path,), name="gemma-voice", daemon=True).start()
            except Exception as e:
                gemma_status_var.set(f"録音停止失敗: {e}")
                gemma_btn.configure(text="録音開始")

        gemma_btn = tk.Button(frm, text="録音開始", command=toggle_gemma_recording, anchor="center", **btn_kwargs)
        gemma_btn.pack(fill="x", pady=(2, 2))
        gemma_status_label = tk.Label(
            frm,
            textvariable=gemma_status_var,
            anchor="w",
            justify="left",
            wraplength=520,
            **btn_kwargs,
        )
        gemma_status_label.pack(fill="x", pady=(2, 0))

        def update_status_wrap(event) -> None:
            width = max(240, int(event.width) - 20)
            tts_status_label.configure(wraplength=width)
            gemma_status_label.configure(wraplength=width)

        frm.bind("<Configure>", update_status_wrap)
        poll_ui_q()

    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        root.update_idletasks()
        fit_window_to_content()
    except Exception:
        pass

    push_emotion(args.initial)
    if args.enable_tts:
        append_event(args.event_path, {"type": "input_mode", "value": mode_var.get()})
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
