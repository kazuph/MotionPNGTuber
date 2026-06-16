#!/usr/bin/env python3
"""GUI to align closed-eye guruguru frames over open-eye frames."""
from __future__ import annotations

import argparse
import json
import mimetypes
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parents[1]
DEFAULT_BASE = REPO / "assets/dokochan_vtuber/tomari_guruguru/generated_v3/slices_refedit_png"
DEFAULT_CONFIG = REPO / "assets/dokochan_vtuber/tomari_guruguru/generated_v3/blink_adjustments.json"
DEFAULT_CONTACT = REPO / "assets/dokochan_vtuber/tomari_guruguru/generated_v3/verification/blink_adjust_contact.jpg"
PAIRS = {
    "A-D": ("A", "D", "closed mouth"),
    "B-E": ("B", "E", "half mouth"),
    "C-F": ("C", "F", "open mouth"),
}


HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dokochan Blink Adjust</title>
<style>
:root {
  --bg: #f4efe6;
  --panel: #fffaf1;
  --ink: #26211d;
  --muted: #746b61;
  --line: #d6cdbf;
  --accent: #1f63d6;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
}
button, select, input { font: inherit; }
.app {
  height: 100vh;
  display: grid;
  grid-template-columns: minmax(360px, 460px) minmax(0, 1fr);
  gap: 12px;
  padding: 12px;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  min-width: 0;
}
.controls {
  padding: 14px;
  display: grid;
  gap: 14px;
  align-content: start;
  overflow: auto;
}
h1 {
  margin: 0;
  font-size: 20px;
}
.hint {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
}
.row {
  display: grid;
  grid-template-columns: 92px 1fr;
  align-items: center;
  gap: 8px;
}
.row label {
  color: var(--muted);
  font-size: 13px;
}
.row select, .row input[type="number"] {
  width: 100%;
  padding: 7px 8px;
  border: 1px solid #b8ac9e;
  border-radius: 6px;
  background: #fff;
}
.slider {
  display: grid;
  grid-template-columns: 92px 1fr 78px;
  align-items: center;
  gap: 8px;
}
.slider label {
  color: var(--muted);
  font-size: 13px;
}
.slider input[type="range"] { width: 100%; }
.slider input[type="number"] {
  width: 78px;
  padding: 6px;
  border: 1px solid #b8ac9e;
  border-radius: 6px;
}
.buttons {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}
button {
  border: 1px solid #b8ac9e;
  background: #fff;
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
}
button.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.section-label {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
}
.segmented {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.segmented.view {
  grid-template-columns: repeat(4, 1fr);
}
.segmented button,
.cell-button {
  min-height: 40px;
  font-weight: 700;
}
.segmented button.active,
.cell-button.active {
  background: var(--ink);
  border-color: var(--ink);
  color: #fff;
}
.cell-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
}
.cell-button {
  position: relative;
  min-height: 46px;
  padding: 0;
  font-size: 13px;
}
.cell-button.adjusted::after {
  content: "";
  position: absolute;
  top: 5px;
  right: 5px;
  width: 8px;
  height: 8px;
  border-radius: 99px;
  background: #17a565;
}
.kbd {
  display: grid;
  gap: 3px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.35;
}
.blink-test {
  min-height: 54px;
  background: #1f63d6;
  border-color: #1f63d6;
  color: #fff;
  font-size: 17px;
  font-weight: 800;
}
.blink-test:active,
.blink-test.active {
  background: #0f3f95;
  border-color: #0f3f95;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.status {
  min-height: 76px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fffdf8;
  color: var(--muted);
  font-size: 12px;
}
.preview {
  height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
}
.preview-head {
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.canvas-wrap {
  min-height: 0;
  overflow: auto;
  background: #202124;
  display: grid;
  place-items: center;
  padding: 14px;
}
canvas {
  width: min(100%, 880px);
  height: auto;
  background: #fff8ee;
  border: 1px solid rgba(255,255,255,0.2);
}
.swatches {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.swatches button.active {
  outline: 3px solid rgba(31,99,214,0.35);
  border-color: var(--accent);
}
@media (max-width: 920px) {
  .app { height: auto; grid-template-columns: 1fr; }
  .preview { min-height: 70vh; }
}
</style>
</head>
<body>
<div class="app">
  <section class="panel controls">
    <div>
      <h1>Blink Adjust</h1>
      <div class="hint">開き目フレームを土台に、閉じ目フレームの座標と拡大率を調整します。期待値は「目以外が動かない」です。</div>
    </div>

    <div class="section-label">
      <span>口の状態</span>
      <span id="pairProgress" class="mono">0 / 25 adjusted</span>
    </div>
    <div class="segmented" id="pairTabs">
      <button data-pair="A-D" class="active">閉じ口<br>A-D</button>
      <button data-pair="B-E">半開き<br>B-E</button>
      <button data-pair="C-F">開き口<br>C-F</button>
    </div>

    <div class="section-label">
      <span>セル位置</span>
      <span id="activeCell" class="mono">r2c2</span>
    </div>
    <div id="cellGrid" class="cell-grid"></div>

    <div class="section-label">
      <span>確認方法</span>
      <span class="mono">Spaceで瞬き確認</span>
    </div>
    <div class="segmented view" id="viewTabs">
      <button data-view="overlay" class="active">重ねる</button>
      <button data-view="blink">点滅</button>
      <button data-view="diff">差分</button>
      <button data-view="side">左右</button>
    </div>
    <button id="blinkTest" class="blink-test" type="button">瞬きテスト: 押している間だけ閉じ目</button>

    <div class="slider">
      <label>左右</label>
      <input id="dx" type="range" min="-120" max="120" step="1">
      <input id="dxNum" type="number" min="-120" max="120" step="1">
    </div>
    <div class="slider">
      <label>上下</label>
      <input id="dy" type="range" min="-120" max="120" step="1">
      <input id="dyNum" type="number" min="-120" max="120" step="1">
    </div>
    <div class="slider">
      <label>拡大率</label>
      <input id="scale" type="range" min="0.88" max="1.16" step="0.001">
      <input id="scaleNum" type="number" min="0.88" max="1.16" step="0.001">
    </div>
    <div class="slider">
      <label>透明度</label>
      <input id="opacity" type="range" min="0" max="1" step="0.01">
      <input id="opacityNum" type="number" min="0" max="1" step="0.01">
    </div>

    <div class="buttons">
      <button id="copyPair">この口の25セルへコピー</button>
      <button id="copyAll">全ペアへコピー</button>
      <button id="reset">このセルをリセット</button>
      <button id="save" class="primary">調整値を保存</button>
      <button id="exportContact" class="primary">全体確認を開く</button>
    </div>
    <div class="swatches">
      <button data-bg="#fff8ee" class="active">warm</button>
      <button data-bg="#202124">dark</button>
      <button data-bg="#75d7ff">cyan</button>
    </div>
    <div class="kbd mono">
      <span>矢印: 1px移動 / Shift+矢印: 10px移動</span>
      <span>[ ]: 拡大率0.001 / Shift+[ ]: 0.01</span>
      <span>Tab: 次セル / Shift+Tab: 前セル / Space: 押している間だけ瞬き</span>
    </div>
    <div id="status" class="status mono">Loading...</div>
  </section>

  <section class="panel preview">
    <div class="preview-head">
      <div class="mono" id="title"></div>
      <div class="hint">閉じ目側だけ transform</div>
    </div>
    <div class="canvas-wrap">
      <canvas id="canvas" width="1200" height="1200"></canvas>
    </div>
  </section>
</div>

<script>
const els = {
  pairTabs: document.getElementById('pairTabs'),
  viewTabs: document.getElementById('viewTabs'),
  cellGrid: document.getElementById('cellGrid'),
  pairProgress: document.getElementById('pairProgress'),
  activeCell: document.getElementById('activeCell'),
  dx: document.getElementById('dx'),
  dxNum: document.getElementById('dxNum'),
  dy: document.getElementById('dy'),
  dyNum: document.getElementById('dyNum'),
  scale: document.getElementById('scale'),
  scaleNum: document.getElementById('scaleNum'),
  opacity: document.getElementById('opacity'),
  opacityNum: document.getElementById('opacityNum'),
  blinkTest: document.getElementById('blinkTest'),
  status: document.getElementById('status'),
  title: document.getElementById('title'),
  canvas: document.getElementById('canvas'),
};
const ctx = els.canvas.getContext('2d', { willReadFrequently: true });
let config;
let images = new Map();
let bg = '#fff8ee';
let blinkOn = false;
let holdBlink = false;
let pairValue = 'A-D';
let cellValue = '2-2';
let viewMode = 'overlay';

for (let r = 0; r < 5; r++) {
  for (let c = 0; c < 5; c++) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'cell-button';
    button.dataset.cell = `${r}-${c}`;
    button.textContent = `r${r}c${c}`;
    button.addEventListener('click', () => setCell(`${r}-${c}`));
    els.cellGrid.appendChild(button);
  }
}

function key() {
  return `${pairValue}:${cellValue}`;
}

function defaultAdjust() {
  return { dx: 0, dy: 0, scale: 1, opacity: 1 };
}

function adjust(k = key()) {
  config.adjustments[k] ||= defaultAdjust();
  return config.adjustments[k];
}

function setStatus(text) {
  els.status.textContent = text;
}

function isDefaultAdjust(a) {
  return !a || (
    Number(a.dx || 0) === 0 &&
    Number(a.dy || 0) === 0 &&
    Number(a.scale || 1) === 1 &&
    Number(a.opacity ?? 1) === 1
  );
}

function renderNav() {
  document.querySelectorAll('[data-pair]').forEach((button) => {
    button.classList.toggle('active', button.dataset.pair === pairValue);
  });
  document.querySelectorAll('[data-view]').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === viewMode);
  });
  let adjusted = 0;
  document.querySelectorAll('[data-cell]').forEach((button) => {
    const cell = button.dataset.cell;
    const isActive = cell === cellValue;
    const hasAdjust = !isDefaultAdjust(config?.adjustments?.[`${pairValue}:${cell}`]);
    button.classList.toggle('active', isActive);
    button.classList.toggle('adjusted', hasAdjust);
    if (hasAdjust) adjusted++;
  });
  const [r, c] = cellValue.split('-');
  els.activeCell.textContent = `r${r}c${c}`;
  els.pairProgress.textContent = `${adjusted} / 25 adjusted`;
}

function setPair(value) {
  pairValue = value;
  syncControls();
  draw();
}

function setCell(value) {
  cellValue = value;
  syncControls();
  draw();
}

function setView(value) {
  viewMode = value;
  draw();
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
  const needed = new Set();
  for (const [openSheet, closedSheet] of Object.values(config.pairs)) {
    needed.add(openSheet); needed.add(closedSheet);
  }
  for (const sheet of needed) {
    for (let r = 0; r < 5; r++) {
      for (let c = 0; c < 5; c++) {
        const id = `${sheet}:${r}-${c}`;
        images.set(id, await loadImage(`/img/${sheet}/r${r}c${c}.png?v=${Date.now()}`));
      }
    }
  }
  syncControls();
  draw();
  setInterval(() => {
    if (viewMode === 'blink' && !holdBlink) {
      blinkOn = !blinkOn;
      draw();
    }
  }, 420);
  setStatus(`Ready\nbase=${config.base}\nconfig=${config.config}`);
}

function syncControls() {
  const a = adjust();
  for (const [rangeId, numId, value] of [
    ['dx', 'dxNum', a.dx],
    ['dy', 'dyNum', a.dy],
    ['scale', 'scaleNum', a.scale],
    ['opacity', 'opacityNum', a.opacity],
  ]) {
    els[rangeId].value = value;
    els[numId].value = value;
  }
  renderNav();
}

function update(name, value) {
  const a = adjust();
  a[name] = Number(value);
  syncControls();
  draw();
}

function bindPair(rangeId, numId, name) {
  els[rangeId].addEventListener('input', (e) => update(name, e.target.value));
  els[numId].addEventListener('change', (e) => update(name, e.target.value));
}
bindPair('dx', 'dxNum', 'dx');
bindPair('dy', 'dyNum', 'dy');
bindPair('scale', 'scaleNum', 'scale');
bindPair('opacity', 'opacityNum', 'opacity');

document.querySelectorAll('[data-pair]').forEach((button) => {
  button.addEventListener('click', () => setPair(button.dataset.pair));
});
document.querySelectorAll('[data-view]').forEach((button) => {
  button.addEventListener('click', () => setView(button.dataset.view));
});
document.querySelectorAll('[data-bg]').forEach((button) => {
  button.addEventListener('click', () => {
    bg = button.dataset.bg;
    document.querySelectorAll('[data-bg]').forEach((b) => b.classList.toggle('active', b === button));
    draw();
  });
});

document.getElementById('reset').addEventListener('click', () => {
  config.adjustments[key()] = defaultAdjust();
  syncControls();
  draw();
});
document.getElementById('copyPair').addEventListener('click', () => {
  const source = { ...adjust() };
  for (let r = 0; r < 5; r++) for (let c = 0; c < 5; c++) {
    config.adjustments[`${pairValue}:${r}-${c}`] = { ...source };
  }
  syncControls();
  draw();
});
document.getElementById('copyAll').addEventListener('click', () => {
  const source = { ...adjust() };
  for (const pair of Object.keys(config.pairs)) {
    for (let r = 0; r < 5; r++) for (let c = 0; c < 5; c++) {
      config.adjustments[`${pair}:${r}-${c}`] = { ...source };
    }
  }
  syncControls();
  draw();
});
document.getElementById('save').addEventListener('click', async () => {
  const res = await fetch('/api/save', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(config.adjustments),
  });
  const data = await res.json();
  setStatus(res.ok ? `Saved\n${data.config}` : `ERROR\n${data.error}`);
});
document.getElementById('exportContact').addEventListener('click', async () => {
  const res = await fetch('/api/export-contact', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(config.adjustments),
  });
  const data = await res.json();
  setStatus(res.ok ? `Exported and opened\n${data.output}` : `ERROR\n${data.error}`);
});

function setHoldBlink(value) {
  holdBlink = value;
  els.blinkTest.classList.toggle('active', holdBlink);
  draw();
}

els.blinkTest.addEventListener('pointerdown', (event) => {
  event.preventDefault();
  els.blinkTest.setPointerCapture(event.pointerId);
  setHoldBlink(true);
});
els.blinkTest.addEventListener('pointerup', (event) => {
  event.preventDefault();
  setHoldBlink(false);
});
els.blinkTest.addEventListener('pointercancel', () => setHoldBlink(false));
els.blinkTest.addEventListener('pointerleave', () => setHoldBlink(false));

function moveCell(delta) {
  const [r, c] = cellValue.split('-').map(Number);
  const idx = r * 5 + c + delta;
  const next = ((idx % 25) + 25) % 25;
  setCell(`${Math.floor(next / 5)}-${next % 5}`);
}

document.addEventListener('keydown', (event) => {
  if (event.target && ['INPUT', 'TEXTAREA'].includes(event.target.tagName)) return;
  const nudge = event.shiftKey ? 10 : 1;
  if (event.key === 'ArrowLeft') { event.preventDefault(); update('dx', adjust().dx - nudge); return; }
  if (event.key === 'ArrowRight') { event.preventDefault(); update('dx', adjust().dx + nudge); return; }
  if (event.key === 'ArrowUp') { event.preventDefault(); update('dy', adjust().dy - nudge); return; }
  if (event.key === 'ArrowDown') { event.preventDefault(); update('dy', adjust().dy + nudge); return; }
  if (event.key === '[') { event.preventDefault(); update('scale', Math.max(0.88, adjust().scale - (event.shiftKey ? 0.01 : 0.001))); return; }
  if (event.key === ']') { event.preventDefault(); update('scale', Math.min(1.16, adjust().scale + (event.shiftKey ? 0.01 : 0.001))); return; }
  if (event.key === 'Tab') { event.preventDefault(); moveCell(event.shiftKey ? -1 : 1); return; }
  if (event.key === ' ') { event.preventDefault(); setHoldBlink(true); return; }
  if (event.key === '1') { setPair('A-D'); return; }
  if (event.key === '2') { setPair('B-E'); return; }
  if (event.key === '3') { setPair('C-F'); return; }
});
document.addEventListener('keyup', (event) => {
  if (event.key === ' ') {
    event.preventDefault();
    setHoldBlink(false);
  }
});

function drawImageTransformedAt(img, a, cx, cy, baseScale = 1, alpha = 1) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(cx + a.dx, cy + a.dy);
  ctx.scale(baseScale * a.scale, baseScale * a.scale);
  ctx.drawImage(img, -img.width / 2, -img.height / 2);
  ctx.restore();
}

function drawImageTransformed(img, a, alpha = 1) {
  drawImageTransformedAt(img, a, els.canvas.width / 2, els.canvas.height / 2, 1, alpha);
}

function drawDiff(openImg, closedImg, a) {
  const tmp = document.createElement('canvas');
  tmp.width = 1200; tmp.height = 1200;
  const tctx = tmp.getContext('2d');
  tctx.drawImage(openImg, 0, 0);
  const openData = tctx.getImageData(0, 0, 1200, 1200);
  tctx.clearRect(0, 0, 1200, 1200);
  tctx.translate(600 + a.dx, 600 + a.dy);
  tctx.scale(a.scale, a.scale);
  tctx.drawImage(closedImg, -600, -600);
  const closedData = tctx.getImageData(0, 0, 1200, 1200);
  const out = ctx.createImageData(1200, 1200);
  for (let i = 0; i < out.data.length; i += 4) {
    const da = Math.abs(openData.data[i + 3] - closedData.data[i + 3]);
    const dr = Math.abs(openData.data[i] - closedData.data[i]);
    const dg = Math.abs(openData.data[i + 1] - closedData.data[i + 1]);
    const db = Math.abs(openData.data[i + 2] - closedData.data[i + 2]);
    const d = Math.min(255, da + dr + dg + db);
    out.data[i] = d;
    out.data[i + 1] = da ? 40 : d;
    out.data[i + 2] = da ? 255 : 40;
    out.data[i + 3] = d > 18 ? 255 : 0;
  }
  ctx.putImageData(out, 0, 0);
}

function draw() {
  if (!config) return;
  renderNav();
  const [r, c] = cellValue.split('-').map(Number);
  const [openSheet, closedSheet] = config.pairs[pairValue];
  const openImg = images.get(`${openSheet}:${r}-${c}`);
  const closedImg = images.get(`${closedSheet}:${r}-${c}`);
  const a = adjust();
  if (!openImg || !closedImg) return;
  els.title.textContent = `${pairValue} r${r}c${c} dx=${a.dx} dy=${a.dy} scale=${a.scale.toFixed(3)}`;
  ctx.clearRect(0, 0, 1200, 1200);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, 1200, 1200);

  if (holdBlink) {
    drawImageTransformed(closedImg, a, 1);
    return;
  }
  const mode = viewMode;
  if (mode === 'side') {
    ctx.drawImage(openImg, 0, 300, 600, 600);
    drawImageTransformedAt(closedImg, a, 900, 600, 0.5, 1);
    ctx.fillStyle = 'rgba(0,0,0,0.65)';
    ctx.fillRect(0, 250, 600, 38);
    ctx.fillRect(600, 250, 600, 38);
    ctx.fillStyle = '#fff';
    ctx.font = '24px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
    ctx.fillText('open source', 18, 276);
    ctx.fillText('closed transformed', 618, 276);
    return;
  }
  if (mode === 'diff') {
    drawDiff(openImg, closedImg, a);
    return;
  }
  if (mode === 'blink') {
    if (blinkOn) drawImageTransformed(closedImg, a, 1);
    else ctx.drawImage(openImg, 0, 0);
    return;
  }
  ctx.drawImage(openImg, 0, 0);
  drawImageTransformed(closedImg, a, a.opacity);
}

init().catch((err) => setStatus(`ERROR\n${err.stack || err.message}`));
</script>
</body>
</html>
"""


def load_config(base: Path, config_path: Path) -> dict[str, object]:
    if config_path.exists():
        try:
            adjustments = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            adjustments = {}
    else:
        adjustments = {}
    return {
        "base": str(base.relative_to(REPO)),
        "config": str(config_path.relative_to(REPO)),
        "pairs": {k: [v[0], v[1]] for k, v in PAIRS.items()},
        "adjustments": adjustments,
    }


def adjusted_closed_over_open(open_img: Image.Image, closed_img: Image.Image, adjust: dict[str, object]) -> Image.Image:
    cell = Image.new("RGBA", (1200, 1200), (255, 248, 238, 255))
    open_layer = open_img.copy().convert("RGBA")
    open_alpha = open_layer.getchannel("A").point(lambda v: int(v * 0.35))
    open_layer.putalpha(open_alpha)
    cell.alpha_composite(open_layer, (0, 0))

    dx = int(round(float(adjust.get("dx", 0) or 0)))
    dy = int(round(float(adjust.get("dy", 0) or 0)))
    scale = float(adjust.get("scale", 1) or 1)
    compare_opacity = 0.75
    closed_layer = closed_img.copy().convert("RGBA")
    if scale != 1:
        size = max(1, int(round(1200 * scale)))
        closed_layer = closed_layer.resize((size, size), Image.Resampling.LANCZOS)
    alpha = closed_layer.getchannel("A").point(lambda v: int(v * compare_opacity))
    closed_layer.putalpha(alpha)
    x = 600 + dx - closed_layer.width // 2
    y = 600 + dy - closed_layer.height // 2
    cell.alpha_composite(closed_layer, (x, y))
    return cell


def export_contact(base: Path, output: Path, adjustments: dict[str, object]) -> Path:
    thumb = 220
    gap = 8
    header = 42
    pair_gap = 24
    label_h = 20
    pair_w = 5 * thumb + 4 * gap
    pair_h = header + 5 * thumb + 4 * gap + label_h
    width = pair_w + 40
    height = 3 * pair_h + 2 * pair_gap + 40
    canvas = Image.new("RGB", (width, height), (32, 33, 36))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    y0 = 20
    for pair_name, (open_sheet, closed_sheet, label) in PAIRS.items():
        draw.text((20, y0), f"{pair_name}: {label}  open 35% + closed adjusted 75%", fill=(255, 248, 238), font=font)
        grid_y = y0 + header
        for r in range(5):
            for c in range(5):
                open_path = base / open_sheet / f"r{r}c{c}.png"
                closed_path = base / closed_sheet / f"r{r}c{c}.png"
                open_img = Image.open(open_path)
                closed_img = Image.open(closed_path)
                key = f"{pair_name}:{r}-{c}"
                cell = adjusted_closed_over_open(open_img, closed_img, adjustments.get(key, {}))
                cell = cell.resize((thumb, thumb), Image.Resampling.LANCZOS).convert("RGB")
                x = 20 + c * (thumb + gap)
                y = grid_y + r * (thumb + gap)
                canvas.paste(cell, (x, y))
                draw.text((x + 4, y + 4), f"r{r}c{c}", fill=(20, 20, 20), font=font)
        y0 += pair_h + pair_gap

    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=92)
    return output


class Handler(BaseHTTPRequestHandler):
    base: Path
    config_path: Path

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[blink-adjust] {self.address_string()} {fmt % args}")

    def send_json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/" or path == "/index.html":
            data = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/config":
            self.send_json(load_config(self.base, self.config_path))
            return
        if path.startswith("/img/"):
            rel = path.removeprefix("/img/")
            target = (self.base / rel).resolve()
            if not str(target).startswith(str(self.base.resolve())):
                self.send_error(403)
                return
            self.send_file(target)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/save", "/api/export-contact"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            adjustments = json.loads(self.rfile.read(length).decode("utf-8"))
            if parsed.path == "/api/export-contact":
                out = export_contact(self.base, DEFAULT_CONTACT, adjustments)
                subprocess.run(["open", "-a", "Google Chrome", str(out)], check=False)
                self.send_json({"output": str(out.relative_to(REPO))})
                return
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(adjustments, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.send_json({"config": str(self.config_path.relative_to(REPO))})
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5190, type=int)
    parser.add_argument("--base", default=DEFAULT_BASE, type=Path)
    parser.add_argument("--config", default=DEFAULT_CONFIG, type=Path)
    args = parser.parse_args()
    if not args.base.exists():
        raise SystemExit(f"base directory not found: {args.base}")
    Handler.base = args.base.resolve()
    Handler.config_path = args.config.resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"http://{args.host}:{args.port}/")
    print(f"base={Handler.base}")
    print(f"config={Handler.config_path}")
    server.serve_forever()


if __name__ == "__main__":
    main()
