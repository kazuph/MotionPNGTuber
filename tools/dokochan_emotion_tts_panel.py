#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import threading
import time
import tkinter as tk

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loop_lipsync_runtime_patched_emotion_auto import (  # noqa: E402
    _emotion_button_label,
    synthesize_irodori_tts,
)


def append_event(path: str, event: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    event = {**event, "ts": time.time()}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.flush()


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

    if args.enable_tts:
        tk.Label(frm, text="Input", **lbl_kwargs).pack(anchor="w")
        mode_var = tk.StringVar(value="audio")
        mode_frame = tk.Frame(frm)
        mode_frame.pack(fill="x", pady=(0, 6))
        tk.Radiobutton(mode_frame, text="macOS audio", variable=mode_var, value="audio", **btn_kwargs).pack(side="left")
        tk.Radiobutton(mode_frame, text="Irodori TTS", variable=mode_var, value="tts", **btn_kwargs).pack(side="left")

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
                    elif kind == "send_state":
                        tts_btn.configure(state=value)
            except queue.Empty:
                pass
            root.after(100, poll_ui_q)

        def send_tts() -> None:
            text = tts_var.get().strip()
            if not text:
                return
            mode_var.set("tts")
            status_var.set("生成中...")
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
        tts_btn.pack(fill="x", pady=2)
        tk.Label(frm, textvariable=status_var, **btn_kwargs).pack(anchor="w")
        poll_ui_q()

    tk.Label(frm, text="Emotion", **lbl_kwargs).pack(anchor="w")

    def push_emotion(value: str) -> None:
        append_event(args.event_path, {"type": "emotion", "value": value})

    for emo in emotions:
        btn = tk.Button(
            frm,
            text=f"{_emotion_button_label(emo)}  {emo}",
            command=lambda v=emo: push_emotion(v),
            anchor="center",
            **btn_kwargs,
        )
        btn.pack(fill="x", pady=2)

    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        ww = max(160, root.winfo_reqwidth())
        wh = max(140, root.winfo_reqheight())
        root.geometry(f"{ww}x{wh}+{max(0, sw - ww - 24)}+{max(0, sh - wh - 80)}")
    except Exception:
        pass

    push_emotion(args.initial)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
