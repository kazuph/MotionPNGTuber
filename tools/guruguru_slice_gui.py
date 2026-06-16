#!/usr/bin/env python3
"""Manual 5x5 cutting-line GUI for Dokochan tomari-guruguru sheets."""
from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "assets/dokochan_vtuber/tomari_guruguru/generated_v2"
SOURCE = ROOT / "upscaled_sheets"
CONFIG = ROOT / "manual_slice_lines.json"
OUT = ROOT / "slices_manual_png"
VERIFY = ROOT / "verification"
CANVAS_SIZE = 1200
SHEETS = [
    {"id": "A", "label": "A: eyes open / mouth closed", "file": "A_目開け_口とじ.png"},
    {"id": "B", "label": "B: eyes open / mouth half-open", "file": "B_目開け_口中間.png"},
    {"id": "C", "label": "C: eyes open / mouth open", "file": "C_目開け_口開け.png"},
    {"id": "D", "label": "D: eyes closed / mouth closed", "file": "D_目閉じ_口とじ.png"},
    {"id": "E", "label": "E: eyes closed / mouth half-open", "file": "E_目閉じ_口中間.png"},
    {"id": "F", "label": "F: eyes closed / mouth open", "file": "F_目閉じ_口開け.png"},
]


HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Guruguru Slice GUI</title>
<style>
:root {
  color-scheme: light;
  --bg: #f4efe6;
  --panel: #fffaf1;
  --panel2: #ffffff;
  --ink: #27231f;
  --muted: #746b61;
  --line: #e02020;
  --blue: #1769e0;
  --border: #d6cdbf;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
  background: var(--bg);
  color: var(--ink);
}
button, input, select {
  font: inherit;
}
.app {
  height: 100vh;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  padding: 12px;
  overflow: hidden;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.left {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-width: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border-bottom: 1px solid var(--border);
  background: #fffdf8;
  min-width: 0;
  flex-wrap: wrap;
}
.toolbar select {
  min-width: 270px;
  padding: 6px 8px;
}
.toolbar button, .right button {
  flex: 0 0 auto;
  border: 1px solid #b8ac9e;
  background: #ffffff;
  border-radius: 6px;
  padding: 6px 10px;
  cursor: pointer;
}
.toolbar button.primary, .right button.primary {
  background: #1f63d6;
  border-color: #1f63d6;
  color: #fff;
}
.sheet-wrap {
  min-height: 0;
  overflow: auto;
  padding: 12px;
  display: grid;
  place-items: start center;
}
.canvas-box {
  position: relative;
  width: min(100%, 860px);
  aspect-ratio: 1 / 1;
  background: #fff8ee;
  border: 1px solid var(--border);
}
canvas {
  display: block;
  width: 100%;
  height: 100%;
}
.hint {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
  min-width: 0;
  overflow-wrap: anywhere;
}
.controls {
  border-top: 1px solid var(--border);
  padding: 10px;
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  background: #fffdf8;
  max-height: 330px;
  overflow: auto;
}
.control-group {
  display: grid;
  gap: 6px;
}
.control-group h2 {
  margin: 0;
  font-size: 14px;
}
.line-row {
  display: grid;
  grid-template-columns: 32px 1fr 72px;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.line-row input[type="range"] {
  width: 100%;
}
.line-row input[type="number"] {
  width: 72px;
  padding: 4px;
}
.right {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) minmax(0, 1fr) auto;
  min-width: 0;
  min-height: 0;
}
.right-head {
  padding: 10px;
  border-bottom: 1px solid var(--border);
  display: grid;
  gap: 8px;
  min-width: 0;
}
.mode {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px;
}
.mode button.active {
  background: #222;
  color: #fff;
}
.stats {
  padding: 10px;
  border-bottom: 1px solid var(--border);
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
  background: #fffdf8;
  min-width: 0;
  overflow-wrap: anywhere;
}
.preview-wrap {
  min-height: 0;
  overflow: auto;
  padding: 10px;
  background: #202124;
  display: flex;
  align-items: flex-start;
  justify-content: center;
}
#previewCanvas {
  width: min(100%, 360px);
  height: auto;
  background: #202124;
  display: block;
  margin: 0;
}
.status {
  padding: 10px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--muted);
  min-height: 52px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
@media (max-width: 980px) {
  .app { grid-template-columns: 1fr; height: auto; overflow: visible; }
}
</style>
</head>
<body>
<div class="app">
  <section class="panel left">
    <div class="toolbar">
      <select id="sheetSelect"></select>
      <button id="resetBtn">900px grid</button>
      <button id="saveBtn">Save lines</button>
      <button id="generateBtn" class="primary">Generate slices</button>
      <span class="hint">赤線をドラッグ、または下の数値/スライダーで調整します。</span>
    </div>
    <div class="sheet-wrap">
      <div class="canvas-box">
        <canvas id="sheetCanvas" width="4500" height="4500"></canvas>
      </div>
    </div>
  </section>
  <section class="panel right">
    <div class="right-head">
      <div class="mode">
        <button id="modeCurrent" class="active">Current sheet</button>
        <button id="modeAll">All 6 sheets</button>
      </div>
      <div class="hint">右側は同じ切断線で切った完成プレビューです。6枚すべて同じ四角で切られます。</div>
    </div>
    <div class="stats mono" id="stats"></div>
    <div class="preview-wrap">
      <canvas id="previewCanvas" width="1100" height="1100"></canvas>
    </div>
    <div class="controls">
      <div class="control-group">
        <h2>Vertical cut lines (X)</h2>
        <div id="xControls"></div>
      </div>
      <div class="control-group">
        <h2>Horizontal cut lines (Y)</h2>
        <div id="yControls"></div>
      </div>
    </div>
    <div class="status mono" id="status">Loading...</div>
  </section>
</div>
<script>
const sheetCanvas = document.getElementById('sheetCanvas');
const sheetCtx = sheetCanvas.getContext('2d');
const previewCanvas = document.getElementById('previewCanvas');
const previewCtx = previewCanvas.getContext('2d');
const sheetSelect = document.getElementById('sheetSelect');
const xControls = document.getElementById('xControls');
const yControls = document.getElementById('yControls');
const statusEl = document.getElementById('status');
const statsEl = document.getElementById('stats');
let config;
let images = new Map();
let activeSheet = 'A';
let drag = null;
let mode = 'current';

const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
const sorted = (arr) => arr.every((v, i) => i === 0 || v > arr[i - 1]);

function setStatus(text) {
  statusEl.textContent = text;
}

async function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

async function init() {
  const res = await fetch('/api/config');
  config = await res.json();
  for (const sheet of config.sheets) {
    const option = document.createElement('option');
    option.value = sheet.id;
    option.textContent = sheet.label;
    sheetSelect.appendChild(option);
    images.set(sheet.id, await loadImage(`/sheet/${encodeURIComponent(sheet.file)}?v=${Date.now()}`));
  }
  activeSheet = config.sheets[0].id;
  sheetSelect.value = activeSheet;
  renderControls();
  draw();
  setStatus('Ready. 共通の赤線を調整してください。');
}

function lineControl(axis, index) {
  const lines = axis === 'x' ? config.xLines : config.yLines;
  const min = index === 0 ? 0 : lines[index - 1] + 1;
  const max = index === lines.length - 1 ? config.size : lines[index + 1] - 1;
  const row = document.createElement('div');
  row.className = 'line-row';
  const label = document.createElement('span');
  label.textContent = `${axis.toUpperCase()}${index}`;
  const range = document.createElement('input');
  range.type = 'range';
  range.min = min;
  range.max = max;
  range.step = 1;
  range.value = lines[index];
  range.disabled = index === 0 || index === lines.length - 1;
  const number = document.createElement('input');
  number.type = 'number';
  number.min = min;
  number.max = max;
  number.step = 1;
  number.value = lines[index];
  number.disabled = index === 0 || index === lines.length - 1;
  const update = (value) => {
    lines[index] = clamp(Math.round(Number(value)), min, max);
    renderControls();
    draw();
  };
  range.addEventListener('input', (e) => update(e.target.value));
  number.addEventListener('change', (e) => update(e.target.value));
  row.append(label, range, number);
  return row;
}

function renderControls() {
  xControls.replaceChildren();
  yControls.replaceChildren();
  config.xLines.forEach((_, i) => xControls.appendChild(lineControl('x', i)));
  config.yLines.forEach((_, i) => yControls.appendChild(lineControl('y', i)));
}

function sheetPoint(evt) {
  const rect = sheetCanvas.getBoundingClientRect();
  return {
    x: clamp((evt.clientX - rect.left) / rect.width * config.size, 0, config.size),
    y: clamp((evt.clientY - rect.top) / rect.height * config.size, 0, config.size),
  };
}

function nearestLine(pt) {
  let best = null;
  for (let i = 1; i < config.xLines.length - 1; i++) {
    const d = Math.abs(pt.x - config.xLines[i]);
    if (d < 45 && (!best || d < best.d)) best = { axis: 'x', index: i, d };
  }
  for (let i = 1; i < config.yLines.length - 1; i++) {
    const d = Math.abs(pt.y - config.yLines[i]);
    if (d < 45 && (!best || d < best.d)) best = { axis: 'y', index: i, d };
  }
  return best;
}

sheetCanvas.addEventListener('pointerdown', (evt) => {
  const hit = nearestLine(sheetPoint(evt));
  if (!hit) return;
  drag = hit;
  sheetCanvas.setPointerCapture(evt.pointerId);
});
sheetCanvas.addEventListener('pointermove', (evt) => {
  const pt = sheetPoint(evt);
  if (!drag) {
    sheetCanvas.style.cursor = nearestLine(pt) ? 'grab' : 'crosshair';
    return;
  }
  const lines = drag.axis === 'x' ? config.xLines : config.yLines;
  const value = drag.axis === 'x' ? pt.x : pt.y;
  lines[drag.index] = clamp(Math.round(value), lines[drag.index - 1] + 1, lines[drag.index + 1] - 1);
  renderControls();
  draw();
});
sheetCanvas.addEventListener('pointerup', () => { drag = null; });
sheetCanvas.addEventListener('pointercancel', () => { drag = null; });

function drawSheet() {
  const img = images.get(activeSheet);
  sheetCtx.clearRect(0, 0, config.size, config.size);
  sheetCtx.fillStyle = '#fff8ee';
  sheetCtx.fillRect(0, 0, config.size, config.size);
  sheetCtx.drawImage(img, 0, 0);
  sheetCtx.lineWidth = 10;
  sheetCtx.strokeStyle = 'rgba(224,32,32,0.9)';
  for (const x of config.xLines) {
    sheetCtx.beginPath();
    sheetCtx.moveTo(x, 0);
    sheetCtx.lineTo(x, config.size);
    sheetCtx.stroke();
  }
  for (const y of config.yLines) {
    sheetCtx.beginPath();
    sheetCtx.moveTo(0, y);
    sheetCtx.lineTo(config.size, y);
    sheetCtx.stroke();
  }
  sheetCtx.fillStyle = 'rgba(224,32,32,0.96)';
  sheetCtx.font = '72px ui-monospace, monospace';
  config.xLines.forEach((x, i) => sheetCtx.fillText(`X${i}:${x}`, clamp(x + 12, 12, 4050), 90));
  config.yLines.forEach((y, i) => sheetCtx.fillText(`Y${i}:${y}`, 20, clamp(y - 18, 90, 4450)));
}

function drawOnePreview(sheetId, x, y, tile, gap, label) {
  const img = images.get(sheetId);
  previewCtx.fillStyle = '#f8f1e8';
  previewCtx.font = '18px ui-monospace, monospace';
  previewCtx.fillText(label, x, y - 8);
  for (let r = 0; r < 5; r++) {
    for (let c = 0; c < 5; c++) {
      const sx = config.xLines[c], sy = config.yLines[r];
      const sw = config.xLines[c + 1] - sx, sh = config.yLines[r + 1] - sy;
      const dx = x + c * (tile + gap), dy = y + r * (tile + gap);
      previewCtx.fillStyle = '#fff8ee';
      previewCtx.fillRect(dx, dy, tile, tile);
      const scale = Math.min(tile / sw, tile / sh);
      const dw = sw * scale, dh = sh * scale;
      previewCtx.drawImage(img, sx, sy, sw, sh, dx + (tile - dw) / 2, dy + (tile - dh) / 2, dw, dh);
      previewCtx.strokeStyle = 'rgba(255,255,255,0.22)';
      previewCtx.lineWidth = 1;
      previewCtx.strokeRect(dx, dy, tile, tile);
    }
  }
}

function drawPreview() {
  const bg = '#202124';
  previewCtx.fillStyle = bg;
  previewCtx.fillRect(0, 0, previewCanvas.width, previewCanvas.height);
  if (mode === 'current') {
    previewCanvas.width = 620;
    previewCanvas.height = 620;
    previewCtx.fillStyle = bg;
    previewCtx.fillRect(0, 0, previewCanvas.width, previewCanvas.height);
    drawOnePreview(activeSheet, 18, 38, 112, 6, `${activeSheet} complete preview`);
  } else {
    previewCanvas.width = 620;
    previewCanvas.height = 2460;
    previewCtx.fillStyle = bg;
    previewCtx.fillRect(0, 0, previewCanvas.width, previewCanvas.height);
    let y = 52;
    for (const sheet of config.sheets) {
      drawOnePreview(sheet.id, 18, y, 92, 5, sheet.label);
      y += 400;
    }
  }
}

function updateStats() {
  const xWidths = config.xLines.slice(1).map((v, i) => v - config.xLines[i]);
  const yHeights = config.yLines.slice(1).map((v, i) => v - config.yLines[i]);
  const ok = sorted(config.xLines) && sorted(config.yLines);
  statsEl.innerHTML = [
    `valid=${ok}`,
    `X widths=${xWidths.join(', ')}`,
    `Y heights=${yHeights.join(', ')}`,
    `output=${config.output}`,
  ].join('<br>');
}

function draw() {
  drawSheet();
  drawPreview();
  updateStats();
}

sheetSelect.addEventListener('change', () => {
  activeSheet = sheetSelect.value;
  draw();
});
document.getElementById('resetBtn').addEventListener('click', () => {
  config.xLines = [0, 900, 1800, 2700, 3600, 4500];
  config.yLines = [0, 900, 1800, 2700, 3600, 4500];
  renderControls();
  draw();
});
document.getElementById('modeCurrent').addEventListener('click', () => {
  mode = 'current';
  document.getElementById('modeCurrent').classList.add('active');
  document.getElementById('modeAll').classList.remove('active');
  draw();
});
document.getElementById('modeAll').addEventListener('click', () => {
  mode = 'all';
  document.getElementById('modeAll').classList.add('active');
  document.getElementById('modeCurrent').classList.remove('active');
  draw();
});

async function post(path) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ xLines: config.xLines, yLines: config.yLines }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

document.getElementById('saveBtn').addEventListener('click', async () => {
  try {
    const data = await post('/api/save');
    setStatus(`Saved: ${data.config}`);
  } catch (err) {
    setStatus(`ERROR: ${err.message}`);
  }
});
document.getElementById('generateBtn').addEventListener('click', async () => {
  try {
    setStatus('Generating slices...');
    const data = await post('/api/generate');
    config.output = data.output;
    draw();
    setStatus(`Generated: ${data.output}\nContact: ${data.contact_dark}\n${data.contact_warm}`);
  } catch (err) {
    setStatus(`ERROR: ${err.message}`);
  }
});

init().catch((err) => setStatus(`ERROR: ${err.message}`));
</script>
</body>
</html>
"""


def load_lines() -> dict[str, object]:
    if CONFIG.exists():
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    else:
        data = {"xLines": [0, 900, 1800, 2700, 3600, 4500], "yLines": [0, 900, 1800, 2700, 3600, 4500]}
    return normalize_lines(data)


def normalize_lines(data: dict[str, object]) -> dict[str, object]:
    x_lines = [int(v) for v in data.get("xLines", [])]
    y_lines = [int(v) for v in data.get("yLines", [])]
    if len(x_lines) != 6 or len(y_lines) != 6:
        raise ValueError("xLines and yLines must each contain exactly 6 numbers")
    if x_lines[0] != 0 or y_lines[0] != 0 or x_lines[-1] != 4500 or y_lines[-1] != 4500:
        raise ValueError("first lines must be 0 and last lines must be 4500")
    if any(x_lines[i] >= x_lines[i + 1] for i in range(5)):
        raise ValueError("xLines must be strictly increasing")
    if any(y_lines[i] >= y_lines[i + 1] for i in range(5)):
        raise ValueError("yLines must be strictly increasing")
    return {"xLines": x_lines, "yLines": y_lines}


def save_lines(data: dict[str, object]) -> dict[str, object]:
    lines = normalize_lines(data)
    payload = {
        "source": str(SOURCE.relative_to(REPO)),
        "output": str(OUT.relative_to(REPO)),
        **lines,
    }
    CONFIG.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def paste_centered(canvas: Image.Image, crop: Image.Image) -> None:
    x = (CANVAS_SIZE - crop.width) // 2
    y = (CANVAS_SIZE - crop.height) // 2
    canvas.alpha_composite(crop, (x, y))


def generate_slices(lines: dict[str, object]) -> dict[str, str]:
    saved = save_lines(lines)
    x_lines = saved["xLines"]
    y_lines = saved["yLines"]
    OUT.mkdir(parents=True, exist_ok=True)
    for sheet in SHEETS:
        image = Image.open(SOURCE / sheet["file"]).convert("RGBA")
        sheet_dir = OUT / sheet["id"]
        sheet_dir.mkdir(parents=True, exist_ok=True)
        for row in range(5):
            for col in range(5):
                crop = image.crop((x_lines[col], y_lines[row], x_lines[col + 1], y_lines[row + 1]))
                frame = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
                paste_centered(frame, crop)
                frame.save(sheet_dir / f"r{row}c{col}.png")
    dark = VERIFY / "slices_manual_contact_dark.jpg"
    warm = VERIFY / "slices_manual_contact_warm.jpg"
    make_contact(dark, (28, 29, 32))
    make_contact(warm, (255, 248, 238))
    return {
        "config": str(CONFIG.relative_to(REPO)),
        "output": str(OUT.relative_to(REPO)),
        "contact_dark": str(dark.relative_to(REPO)),
        "contact_warm": str(warm.relative_to(REPO)),
    }


def make_contact(path: Path, bg: tuple[int, int, int]) -> None:
    VERIFY.mkdir(parents=True, exist_ok=True)
    tile = 94
    gap = 6
    label_h = 20
    sheet_gap = 18
    left = 44
    width = left + 5 * (tile + gap) - gap
    height = len(SHEETS) * (label_h + 5 * (tile + gap) - gap + sheet_gap) - sheet_gap
    canvas = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    fg = (240, 240, 240) if sum(bg) < 200 else (40, 38, 34)
    y = 0
    for sheet in SHEETS:
        draw.text((6, y + 5), sheet["id"], fill=fg, font=font)
        draw.text((left, y + 5), sheet["label"], fill=fg, font=font)
        yy = y + label_h
        for row in range(5):
            draw.text((6, yy + row * (tile + gap) + tile // 2 - 4), f"r{row}", fill=fg, font=font)
            for col in range(5):
                img = Image.open(OUT / sheet["id"] / f"r{row}c{col}.png").convert("RGBA")
                img.thumbnail((tile, tile), Image.Resampling.LANCZOS)
                cell = Image.new("RGBA", (tile, tile), (*bg, 255))
                cell.alpha_composite(img, ((tile - img.width) // 2, (tile - img.height) // 2))
                x = left + col * (tile + gap)
                canvas.paste(cell.convert("RGB"), (x, yy + row * (tile + gap)))
        y += label_h + 5 * (tile + gap) - gap + sheet_gap
    canvas.save(path, quality=94)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", mime)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/config":
            try:
                data = load_lines()
                self.send_json(
                    {
                        **data,
                        "size": 4500,
                        "sheets": SHEETS,
                        "output": str(OUT.relative_to(REPO)),
                    }
                )
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        if parsed.path.startswith("/sheet/"):
            name = unquote(parsed.path.removeprefix("/sheet/"))
            allowed = {sheet["file"] for sheet in SHEETS}
            if name not in allowed:
                self.send_error(403)
                return
            self.send_file(SOURCE / name)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("content-length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/api/save":
                saved = save_lines(data)
                self.send_json({"config": str(CONFIG.relative_to(REPO)), **saved})
                return
            if self.path == "/api/generate":
                self.send_json(generate_slices(data))
                return
            self.send_error(404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5188)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"http://{args.host}:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
