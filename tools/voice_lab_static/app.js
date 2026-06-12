/* Dokochan Voice Lab — irodori voice atlas explorer */

// 自動テスト専用フラグ。通常利用では絶対に有効にしないこと（音が出るのが正）。
const MUTED = new URLSearchParams(location.search).has("muted");
if (MUTED) {
  const badge = document.createElement("div");
  badge.className = "muted-badge";
  badge.textContent = "🔇 自動テスト用ミュートモード — 通常はこのURLを使わないでください（/ で開き直すと音が出ます）";
  document.addEventListener("DOMContentLoaded", () => document.body.prepend(badge));
}

const els = {
  modelStatus: document.querySelector("#modelStatus"),
  atlasStatus: document.querySelector("#atlasStatus"),
  projUmap: document.querySelector("#projUmap"),
  projPca: document.querySelector("#projPca"),
  colorBy: document.querySelector("#colorBy"),
  searchBox: document.querySelector("#searchBox"),
  legend: document.querySelector("#legend"),
  canvas: document.querySelector("#mapCanvas"),
  tooltip: document.querySelector("#tooltip"),
  axisX: document.querySelector("#axisX"),
  axisY: document.querySelector("#axisY"),
  mapEmpty: document.querySelector("#mapEmpty"),
  validation: document.querySelector("#validationChips"),
  selTitle: document.querySelector("#selTitle"),
  selSeed: document.querySelector("#selSeed"),
  selCaption: document.querySelector("#selCaption"),
  selTags: document.querySelector("#selTags"),
  selBlend: document.querySelector("#selBlend"),
  featureBars: document.querySelector("#featureBars"),
  player: document.querySelector("#player"),
  synthText: document.querySelector("#synthText"),
  synthBtn: document.querySelector("#synthBtn"),
  useBtn: document.querySelector("#useBtn"),
  synthMeta: document.querySelector("#synthMeta"),
  historyList: document.querySelector("#historyList"),
  favFilter: document.querySelector("#favFilter"),
  resetView: document.querySelector("#resetView"),
  noteRow: document.querySelector("#noteRow"),
  noteFav: document.querySelector("#noteFav"),
  noteName: document.querySelector("#noteName"),
  captionText: document.querySelector("#captionText"),
  gachaBtn: document.querySelector("#gachaBtn"),
  rerollBtn: document.querySelector("#rerollBtn"),
  gachaMeta: document.querySelector("#gachaMeta"),
};

const state = {
  atlas: null,
  projection: "umap",
  colorBy: "f0_med",
  search: "",
  points: [], // {sample, cur:{x,y}, tgt:{x,y}}
  references: [], // voice_lockランドマーク（★）
  blendMarks: [], // {x, y, item, projection}
  hover: null,
  selected: null, // {type:"sample"|"blend", sample?, item?, x?, y?}
  busy: false,
  view: { scale: 1, ox: 0, oy: 0 },
  notes: {},
  favOnly: false,
  customMarks: [], // caption直打ち合成（▲）
  lastCustom: null,
};

const GENDER_COLORS = { female: "#e0408a", male: "#2563eb", neutral: "#d97706" };
const GRADIENT = ["#2c4fd0", "#0ea5e9", "#0d9488", "#ca8a04", "#ea580c", "#dc2626"];

const SIM_PREFIX = "sim:";

const FEATURE_FMT = {
  f0_med: (v) => `${v.toFixed(0)} Hz`,
  f0_iqr: (v) => `${v.toFixed(0)} Hz`,
  centroid: (v) => `${(v / 1000).toFixed(2)} kHz`,
  rolloff: (v) => `${(v / 1000).toFixed(2)} kHz`,
  rms: (v) => v.toFixed(3),
  zcr: (v) => v.toFixed(3),
  voiced: (v) => `${(v * 100).toFixed(0)}%`,
  dur: (v) => `${v.toFixed(1)}s`,
};

/* ---------- utils ---------- */

function toast(message) {
  document.querySelector(".toast")?.remove();
  const box = document.createElement("div");
  box.className = "toast";
  box.textContent = message;
  document.body.append(box);
  window.setTimeout(() => box.remove(), 4200);
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data;
}

function lerpColor(c1, c2, t) {
  const a = c1.match(/\w\w/g).map((h) => parseInt(h, 16));
  const b = c2.match(/\w\w/g).map((h) => parseInt(h, 16));
  const m = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${m[0]},${m[1]},${m[2]})`;
}

function gradientColor(t) {
  const x = Math.max(0, Math.min(1, t)) * (GRADIENT.length - 1);
  const i = Math.min(GRADIENT.length - 2, Math.floor(x));
  return lerpColor(GRADIENT[i], GRADIENT[i + 1], x - i);
}

function percentileDomain(values, lo = 0.05, hi = 0.95) {
  const sorted = values.slice().sort((a, b) => a - b);
  const pick = (p) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))))];
  const min = pick(lo);
  const max = pick(hi);
  return [min, Math.max(max, min + 1e-9)];
}

let colorDomain = [0, 1];

function colorValue(sample) {
  if (state.colorBy.startsWith(SIM_PREFIX)) {
    return Number(sample.ref_sim?.[state.colorBy.slice(SIM_PREFIX.length)] ?? NaN);
  }
  return Number(sample.features?.[state.colorBy] ?? NaN);
}

function pointColor(sample) {
  if (state.colorBy === "gender") {
    return GENDER_COLORS[sample.tags?.gender] || "#9aa4b8";
  }
  const v = colorValue(sample);
  if (!Number.isFinite(v)) return "#b6c0cd";
  return gradientColor((v - colorDomain[0]) / (colorDomain[1] - colorDomain[0]));
}

function noteOf(id) {
  return state.notes[id] || null;
}

function matchesSearch(sample) {
  if (state.favOnly && !noteOf(sample.id)?.favorite) return false;
  if (!state.search) return true;
  const note = noteOf(sample.id);
  const hay = `${sample.caption} ${(sample.tags?.mods || []).join(" ")} ${sample.tags?.speaker || ""} ${note?.name || ""}`;
  return hay.toLowerCase().includes(state.search.toLowerCase());
}

function customVisible(mark) {
  const id = mark.item?.id;
  if (state.favOnly && !noteOf(id)?.favorite) return false;
  if (!state.search) return true;
  const hay = `${mark.item?.caption || ""} ${noteOf(id)?.name || ""}`;
  return hay.toLowerCase().includes(state.search.toLowerCase());
}

function blendVisible(mark) {
  const id = mark.item?.id;
  if (state.favOnly && !noteOf(id)?.favorite) return false;
  if (!state.search) return true;
  return (noteOf(id)?.name || "").toLowerCase().includes(state.search.toLowerCase());
}

/* ---------- canvas ---------- */

const ctx = els.canvas.getContext("2d");
let cssW = 0;
let cssH = 0;

function resizeCanvas() {
  const rect = els.canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  cssW = rect.width;
  cssH = rect.height;
  els.canvas.width = Math.round(rect.width * dpr);
  els.canvas.height = Math.round(rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function dataToScreen(x, y) {
  return [x * cssW * state.view.scale + state.view.ox, y * cssH * state.view.scale + state.view.oy];
}

function screenToData(sx, sy) {
  return [
    (sx - state.view.ox) / (cssW * state.view.scale),
    (sy - state.view.oy) / (cssH * state.view.scale),
  ];
}

function clampView() {
  const v = state.view;
  v.scale = Math.max(1, Math.min(14, v.scale));
  v.ox = Math.max(cssW * (1 - v.scale), Math.min(0, v.ox));
  v.oy = Math.max(cssH * (1 - v.scale), Math.min(0, v.oy));
}

function zoomAt(sx, sy, factor) {
  const v = state.view;
  const next = Math.max(1, Math.min(14, v.scale * factor));
  const k = next / v.scale;
  v.ox = sx - (sx - v.ox) * k;
  v.oy = sy - (sy - v.oy) * k;
  v.scale = next;
  clampView();
  invalidateMap();
}

function resetView() {
  state.view = { scale: 1, ox: 0, oy: 0 };
  invalidateMap();
}

function drawTriangle(cx, cy, r) {
  ctx.beginPath();
  ctx.moveTo(cx, cy - r);
  ctx.lineTo(cx + r * 0.9, cy + r * 0.7);
  ctx.lineTo(cx - r * 0.9, cy + r * 0.7);
  ctx.closePath();
}

function drawStar(cx, cy, R) {
  const r = R * 0.45;
  ctx.beginPath();
  for (let i = 0; i < 10; i += 1) {
    const ang = -Math.PI / 2 + (i * Math.PI) / 5;
    const rad = i % 2 === 0 ? R : r;
    const px = cx + Math.cos(ang) * rad;
    const py = cy + Math.sin(ang) * rad;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
}

function drawFrame() {
  ctx.clearRect(0, 0, cssW, cssH);

  // grid
  ctx.strokeStyle = "rgba(40, 60, 100, 0.08)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 10; i += 1) {
    const gx = (i / 10) * cssW;
    const gy = (i / 10) * cssH;
    ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, cssH); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(cssW, gy); ctx.stroke();
  }

  // density underlay（ライトテーマ: 通常合成の淡いティール）
  for (const p of state.points) {
    if (!matchesSearch(p.sample)) continue;
    const [sx, sy] = dataToScreen(p.cur.x, p.cur.y);
    const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, 30);
    grad.addColorStop(0, "rgba(13, 148, 136, 0.045)");
    grad.addColorStop(1, "rgba(13, 148, 136, 0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(sx, sy, 30, 0, Math.PI * 2);
    ctx.fill();
  }

  // points
  for (const p of state.points) {
    const dim = !matchesSearch(p.sample);
    const isHover = state.hover?.type === "sample" && state.hover.sample.id === p.sample.id;
    const isSel = state.selected?.type === "sample" && state.selected.sample.id === p.sample.id;
    const [sx, sy] = dataToScreen(p.cur.x, p.cur.y);
    const r = isSel ? 7.5 : isHover ? 6.5 : 4.6;

    ctx.globalAlpha = dim ? 0.07 : 0.95;
    ctx.fillStyle = pointColor(p.sample);
    ctx.beginPath();
    ctx.arc(sx, sy, r, 0, Math.PI * 2);
    ctx.fill();

    if (p.sample.voice_flip && !dim) {
      // 指示タグと実音声がズレている点（例: おじいさん×お姫様 → 姫声）
      ctx.globalAlpha = 0.85;
      ctx.strokeStyle = "#b45309";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([2.5, 2.5]);
      ctx.beginPath();
      ctx.arc(sx, sy, r + 2.2, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if ((isHover || isSel) && !dim) {
      ctx.globalAlpha = 0.9;
      ctx.strokeStyle = "#1e2530";
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.arc(sx, sy, r + 2.6, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;

  // お気に入り♥と名前ラベル
  ctx.textAlign = "center";
  for (const p of state.points) {
    const note = noteOf(p.sample.id);
    if (!note || !matchesSearch(p.sample)) continue;
    const [sx, sy] = dataToScreen(p.cur.x, p.cur.y);
    if (note.favorite) {
      ctx.font = "10px sans-serif";
      ctx.fillStyle = "#e0408a";
      ctx.fillText("♥", sx + 7, sy - 6);
    }
    if (note.name) {
      ctx.font = "600 10px sans-serif";
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 3;
      ctx.strokeText(note.name, sx, sy + 16);
      ctx.fillStyle = "#1e2530";
      ctx.fillText(note.name, sx, sy + 16);
    }
  }
  for (const mark of state.blendMarks) {
    if (mark.projection !== state.projection || !blendVisible(mark)) continue;
    const note = noteOf(mark.item?.id);
    if (!note) continue;
    const [sx, sy] = dataToScreen(mark.x, mark.y);
    if (note.favorite) {
      ctx.font = "10px sans-serif";
      ctx.fillStyle = "#e0408a";
      ctx.fillText("♥", sx + 8, sy - 7);
    }
    if (note.name) {
      ctx.font = "600 10px sans-serif";
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 3;
      ctx.strokeText(note.name, sx, sy + 17);
      ctx.fillStyle = "#86198f";
      ctx.fillText(note.name, sx, sy + 17);
    }
  }

  // caption直打ち合成（▲）
  for (const mark of state.customMarks) {
    if (!customVisible(mark)) continue;
    const item = mark.item;
    const [sx, sy] = dataToScreen(item[state.projection][0], item[state.projection][1]);
    const isSel = state.selected?.type === "custom" && state.selected.item?.id === item.id;
    const isHover = state.hover?.type === "custom" && state.hover.item?.id === item.id;
    const r = isSel || isHover ? 8 : 6.5;
    ctx.globalAlpha = 0.95;
    ctx.fillStyle = item.features ? pointColor(item) : "#94a3b8";
    drawTriangle(sx, sy, r);
    ctx.fill();
    ctx.strokeStyle = isSel ? "#1e2530" : "rgba(124, 58, 237, 0.85)";
    ctx.lineWidth = isSel ? 1.8 : 1.2;
    drawTriangle(sx, sy, r + 1.4);
    ctx.stroke();
    const note = noteOf(item.id);
    ctx.textAlign = "center";
    if (note?.favorite) {
      ctx.font = "10px sans-serif";
      ctx.fillStyle = "#e0408a";
      ctx.fillText("♥", sx + 9, sy - 8);
    }
    if (note?.name) {
      ctx.font = "600 10px sans-serif";
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 3;
      ctx.strokeText(note.name, sx, sy + 19);
      ctx.fillStyle = "#5b21b6";
      ctx.fillText(note.name, sx, sy + 19);
    }
  }

  // リファレンス声（voice_lock）= 金の★ランドマーク
  for (const ref of state.references) {
    const [sx, sy] = dataToScreen(ref[state.projection][0], ref[state.projection][1]);
    const isSel = state.selected?.type === "ref" && state.selected.ref.id === ref.id;
    const isHover = state.hover?.type === "ref" && state.hover.ref.id === ref.id;
    const R = isSel || isHover ? 13 : 11;
    ctx.save();
    ctx.globalAlpha = 1;
    ctx.shadowColor = "rgba(245, 158, 11, 0.5)";
    ctx.shadowBlur = 12;
    ctx.fillStyle = "#f59e0b";
    ctx.strokeStyle = "#92670c";
    ctx.lineWidth = 1.6;
    drawStar(sx, sy, R);
    ctx.fill();
    ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.font = "600 11px sans-serif";
    ctx.textAlign = "center";
    ctx.fillStyle = "#92670c";
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.lineWidth = 3;
    ctx.strokeText(ref.label, sx, sy + R + 14);
    ctx.fillText(ref.label, sx, sy + R + 14);
    ctx.restore();
  }

  // blend markers (current projection only) — 色はアトラス点と同じ声質スケール
  for (const mark of state.blendMarks) {
    if (mark.projection !== state.projection || !blendVisible(mark)) continue;
    const [sx, sy] = dataToScreen(mark.x, mark.y);
    const isSel = state.selected?.type === "blend" && state.selected.item?.id === mark.item?.id;
    const isHover = state.hover?.type === "blend" && state.hover.item?.id === mark.item?.id;
    ctx.save();
    ctx.translate(sx, sy);
    ctx.rotate(Math.PI / 4);
    ctx.fillStyle = mark.item?.features ? pointColor(mark.item) : "#94a3b8";
    ctx.globalAlpha = 0.95;
    const s = isSel || isHover ? 7 : 5.4;
    ctx.fillRect(-s, -s, s * 2, s * 2);
    ctx.strokeStyle = isSel ? "#1e2530" : "rgba(192, 38, 211, 0.8)";
    ctx.lineWidth = isSel ? 1.8 : 1.1;
    ctx.strokeRect(-s - 1.6, -s - 1.6, (s + 1.6) * 2, (s + 1.6) * 2);
    ctx.restore();
  }
}

let mapDirty = true;

function invalidateMap() {
  mapDirty = true;
}

function tick() {
  let moving = false;
  for (const p of state.points) {
    const dx = p.tgt.x - p.cur.x;
    const dy = p.tgt.y - p.cur.y;
    if (Math.abs(dx) > 0.0004 || Math.abs(dy) > 0.0004) {
      p.cur.x += dx * 0.14;
      p.cur.y += dy * 0.14;
      moving = true;
    } else {
      p.cur.x = p.tgt.x;
      p.cur.y = p.tgt.y;
    }
  }
  // 静止中かつ変更なしならスキップ（1000点超のgradient描画は毎フレームやらない）
  if (moving || mapDirty) {
    drawFrame();
    mapDirty = false;
  }
  window.requestAnimationFrame(tick);
}

function nearestPoint(mx, my, threshold = 11) {
  // ★リファレンスを最優先（大きいので閾値も広め）
  for (const ref of state.references) {
    const [sx, sy] = dataToScreen(ref[state.projection][0], ref[state.projection][1]);
    if (Math.hypot(sx - mx, sy - my) < 14) {
      return { kind: "ref", ref };
    }
  }
  for (const mark of state.customMarks) {
    if (!customVisible(mark)) continue;
    const [sx, sy] = dataToScreen(mark.item[state.projection][0], mark.item[state.projection][1]);
    if (Math.hypot(sx - mx, sy - my) < 10) {
      return { kind: "custom", mark };
    }
  }
  let best = null;
  let bestDist = threshold;
  for (const p of state.points) {
    if (!matchesSearch(p.sample)) continue;
    const [sx, sy] = dataToScreen(p.cur.x, p.cur.y);
    const d = Math.hypot(sx - mx, sy - my);
    if (d < bestDist) {
      bestDist = d;
      best = p;
    }
  }
  if (best) return { kind: "sample", point: best, dist: bestDist };
  for (const mark of state.blendMarks) {
    if (mark.projection !== state.projection) continue;
    const [sx, sy] = dataToScreen(mark.x, mark.y);
    const d = Math.hypot(sx - mx, sy - my);
    if (d < bestDist) {
      bestDist = d;
      best = mark;
    }
  }
  return best ? { kind: "blend", mark: best, dist: bestDist } : null;
}

/* ---------- legend / axis / validation ---------- */

function renderLegend() {
  els.legend.innerHTML = "";
  if (state.colorBy === "gender") {
    for (const [key, color] of Object.entries(GENDER_COLORS)) {
      const cat = document.createElement("span");
      cat.className = "cat";
      const dot = document.createElement("i");
      dot.style.background = color;
      cat.append(dot, document.createTextNode({ female: "女声", male: "男声", neutral: "中性" }[key]));
      els.legend.append(cat);
    }
    return;
  }
  const fmt = state.colorBy.startsWith(SIM_PREFIX)
    ? (v) => `${(v * 100).toFixed(0)}%`
    : FEATURE_FMT[state.colorBy];
  const lo = document.createElement("span");
  lo.textContent = fmt?.(colorDomain[0]) ?? colorDomain[0];
  const grad = document.createElement("span");
  grad.className = "grad";
  grad.style.background = `linear-gradient(90deg, ${GRADIENT.join(",")})`;
  const hi = document.createElement("span");
  hi.textContent = fmt?.(colorDomain[1]) ?? colorDomain[1];
  els.legend.append(lo, grad, hi);
}

function renderAxisHints() {
  const hints = state.atlas?.projections?.[state.projection]?.axis_hints;
  if (!hints) {
    els.axisX.textContent = "";
    els.axisY.textContent = "";
    return;
  }
  const fmt = (rows, horizontal) => {
    const top = rows[0];
    if (!top || Math.abs(top.rho) < 0.25) return "支配的な音響相関なし";
    const arrow = horizontal ? (top.rho > 0 ? "→" : "←") : top.rho > 0 ? "↓" : "↑";
    return `${arrow} ${top.label}  ρ=${top.rho > 0 ? "+" : ""}${top.rho.toFixed(2)}`;
  };
  els.axisX.textContent = fmt(hints.x, true);
  els.axisY.textContent = fmt(hints.y, false);
}

function renderValidation() {
  els.validation.innerHTML = "";
  const v = state.atlas?.validation;
  if (!v) return;
  const proj = v[state.projection] || {};
  const chips = [
    [`サンプル数`, `${v.n_samples}`],
    [`音響性別純度@10`, proj["knn_acoustic_gender_purity@10"] != null ? `${(proj["knn_acoustic_gender_purity@10"] * 100).toFixed(1)}%` : "—"],
    [`指示⇄出音ズレ(⚠️)`, v.caption_fidelity ? `${v.caption_fidelity.voice_flip_count}件` : "—"],
    [`同caption距離比(seed違い/ランダム)`, `${proj.same_caption_distance?.ratio ?? "—"}`],
    [`kNN性別純度@10`, proj["knn_gender_purity@10"] != null ? `${(proj["knn_gender_purity@10"] * 100).toFixed(0)}%` : "—"],
  ];
  if (state.projection === "pca" && proj.explained_variance) {
    chips.push([`PC1+PC2寄与率`, `${((proj.explained_variance[0] + proj.explained_variance[1]) * 100).toFixed(0)}%`]);
  }
  for (const [label, value] of chips) {
    const chip = document.createElement("span");
    chip.className = "v-chip";
    chip.innerHTML = `${label}: <b>${value}</b>`;
    els.validation.append(chip);
  }
}

/* ---------- inspector ---------- */

function renderFeatureBars(features) {
  els.featureBars.innerHTML = "";
  if (!features) return;
  const keys = ["f0_med", "centroid", "rms", "f0_iqr"];
  const labels = state.atlas?.feature_labels || {};
  for (const key of keys) {
    const v = Number(features[key] ?? 0);
    const values = state.atlas.samples.map((s) => Number(s.features?.[key] ?? 0));
    const [lo, hi] = percentileDomain(values, 0.02, 0.98);
    const ratio = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
    const div = document.createElement("div");
    div.className = "fbar";
    div.innerHTML = `
      <div class="fb-head"><span>${labels[key] || key}</span><span class="fb-val">${FEATURE_FMT[key]?.(v) ?? v}</span></div>
      <div class="fb-track"><i style="width:${(ratio * 100).toFixed(1)}%"></i></div>`;
    els.featureBars.append(div);
  }
}

function renderTags(tags, voiceFlip = false) {
  els.selTags.innerHTML = "";
  if (!tags) return;
  if (voiceFlip) {
    const w = document.createElement("span");
    w.className = "tag tag-warn";
    w.textContent = "⚠️ 指示と出音がズレてる可能性";
    w.title = "captionの属性が衝突しており、実音声のF0がタグの性別と逆転しています";
    els.selTags.append(w);
  }
  const gender = tags.gender || "?";
  const g = document.createElement("span");
  g.className = `tag gender-${gender}`;
  g.textContent = { female: "女声", male: "男声", neutral: "中性" }[gender] || gender;
  els.selTags.append(g);
  for (const mod of tags.mods || []) {
    const t = document.createElement("span");
    t.className = "tag";
    t.textContent = mod;
    els.selTags.append(t);
  }
}

function renderBlend(blend) {
  els.selBlend.innerHTML = "";
  if (!blend) return;
  for (const c of blend) {
    const row = document.createElement("div");
    row.className = "blend-row";
    row.innerHTML = `
      <span class="w">${(c.weight * 100).toFixed(0)}%</span>
      <span class="bar"><i style="width:${(c.weight * 100).toFixed(1)}%"></i></span>
      <span class="cap" title="${c.caption}">${c.caption.replace("日本語を話す", "")}</span>`;
    els.selBlend.append(row);
  }
}

function selectedNoteId() {
  if (!state.selected) return null;
  if (state.selected.type === "sample") return state.selected.sample.id;
  if (state.selected.type === "blend") return state.selected.item?.id;
  if (state.selected.type === "custom") return state.selected.item?.id;
  if (state.selected.type === "ref") return state.selected.ref.id;
  return null;
}

function resolveVoice(id) {
  const sample = state.atlas?.samples?.find((s) => s.id === id);
  if (sample) return { kind: "sample", voice: sample };
  const hist = (state.history || []).find((it) => it.id === id);
  if (hist) return { kind: hist.mode === "atlas_blend" ? "blend" : "custom", voice: hist };
  return null;
}

function renderCollection() {
  const box = document.querySelector("#collectionList");
  if (!box) return;
  box.innerHTML = "";
  const favs = Object.entries(state.notes)
    .filter(([, n]) => n.favorite)
    .sort((a, b) => (b[1].updated_at || 0) - (a[1].updated_at || 0));
  if (!favs.length) {
    const p = document.createElement("p");
    p.className = "collection-empty";
    p.textContent = "まだ保存した声はないよ。スワイプ発掘♥やマップの♥で集めてね。";
    box.append(p);
    return;
  }
  for (const [id, note] of favs) {
    const res = resolveVoice(id);
    const row = document.createElement("div");
    row.className = "history-item";
    const play = document.createElement("button");
    play.className = "h-play";
    play.textContent = "▶";
    play.disabled = !res;
    if (res) play.addEventListener("click", () => playAudio(res.voice.audio_url));
    const body = document.createElement("div");
    body.className = "h-body";
    const caption = res?.voice.caption || "(音源情報なし)";
    body.innerHTML = `
      <div class="h-text"><b>${note.name || "(無名)"}</b></div>
      <div class="h-meta" title="${caption}">${caption.slice(0, 30)}…</div>`;
    body.style.cursor = res ? "pointer" : "default";
    if (res) {
      body.addEventListener("click", () => {
        if (res.kind === "sample") selectSample(res.voice);
        else if (res.kind === "custom") selectCustom(res.voice);
        else playAudio(res.voice.audio_url);
      });
    }
    const use = document.createElement("button");
    use.className = "h-use";
    use.textContent = "⭐ 設定";
    use.title = "既定ボイス（どこちゃんランタイム等）に設定";
    use.addEventListener("click", () => useVoice(id));
    row.append(play, body, use);
    box.append(row);
  }
}

function renderNoteRow() {
  const id = selectedNoteId();
  els.noteRow.hidden = !id;
  if (!id) return;
  const note = noteOf(id);
  els.noteFav.textContent = note?.favorite ? "♥" : "♡";
  els.noteFav.classList.toggle("is-fav", !!note?.favorite);
  // 入力中のテキストをサーバー応答で上書きしない
  if (document.activeElement !== els.noteName) {
    els.noteName.value = note?.name || "";
  }
}

async function saveNoteFor(id, patch) {
  if (!id) return;
  try {
    const data = await fetchJson("/api/notes", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id, ...patch }),
    });
    if (data.note) state.notes[id] = data.note;
    else delete state.notes[id];
    renderCollection();
    invalidateMap();
  } catch (error) {
    toast(`保存失敗: ${error.message}`);
  }
}

async function saveNote(patch) {
  await saveNoteFor(selectedNoteId(), patch);
  renderNoteRow();
}

async function loadNotes() {
  try {
    const data = await fetchJson("/api/notes");
    state.notes = data.notes || {};
    renderCollection();
    invalidateMap();
  } catch {
    /* noop */
  }
}

function playAudio(url) {
  // 通常利用では必ず音を出す（muted解除＋音量最大）。MUTEDは自動テスト専用。
  els.player.muted = MUTED;
  if (!MUTED) {
    els.player.volume = 1.0;
  }
  els.player.src = url;
  els.player.load();
  els.player.play().catch(() => {
    /* 自動再生がブロックされたら手動再生に任せる */
  });
}

function selectSample(sample, { play = true } = {}) {
  state.selected = { type: "sample", sample };
  els.selTitle.textContent = `atlas ${sample.id}`;
  els.selSeed.textContent = `seed ${sample.seed}`;
  els.selCaption.textContent = sample.caption;
  renderTags(sample.tags, !!sample.voice_flip);
  renderBlend(null);
  renderFeatureBars(sample.features);
  els.useBtn.disabled = false;
  els.synthMeta.textContent = "";
  renderNoteRow();
  invalidateMap();
  if (play) playAudio(sample.audio_url);
}

function selectReference(ref, { play = true } = {}) {
  state.selected = { type: "ref", ref };
  els.selTitle.textContent = `★ ${ref.label}`;
  els.selSeed.textContent = `voice_lock ${ref.voice_lock_id.slice(0, 12)}…`;
  els.selCaption.textContent = `マスターのスキルに登録済みのvoice_lock声（リモートIrodoriサーバーでアトラスと同一テキストを生成して同じ潜在空間に射影）。${ref.note || ""} この★の近くの●が、似た系統の声。`;
  renderTags(null);
  renderSimilarList(ref);
  renderFeatureBars(ref.features);
  els.useBtn.disabled = true;
  els.synthMeta.textContent = "★はvoice_lock声。どこちゃんに使う場合は IRODORI_VOICE_LOCK 環境変数で指定してね。";
  renderNoteRow();
  // 色分けを自動でこの★の類似度ヒートマップに切替
  const simValue = `${SIM_PREFIX}${ref.id}`;
  if ([...els.colorBy.options].some((o) => o.value === simValue)) {
    els.colorBy.value = simValue;
    state.colorBy = simValue;
    updateColorDomain();
  }
  invalidateMap();
  if (play) playAudio(ref.audio_url);
}

function renderSimilarList(ref) {
  els.selBlend.innerHTML = "";
  const scored = state.atlas.samples
    .filter((s) => Number.isFinite(Number(s.ref_sim?.[ref.id])))
    .map((s) => ({ s, sim: Number(s.ref_sim[ref.id]) }))
    .sort((a, b) => b.sim - a.sim)
    .slice(0, 10);
  if (!scored.length) return;
  const head = document.createElement("div");
  head.className = "blend-row";
  head.innerHTML = `<span class="cap">この★に近い声 トップ10（クリックで試聴）</span>`;
  els.selBlend.append(head);
  for (const { s, sim } of scored) {
    const row = document.createElement("div");
    row.className = "blend-row sim-row";
    row.innerHTML = `
      <span class="w">${(sim * 100).toFixed(0)}%</span>
      <span class="bar"><i style="width:${(Math.max(0, sim) * 100).toFixed(1)}%"></i></span>
      <span class="cap" title="${s.caption}">${s.caption.replace("日本語を話す", "")}</span>`;
    row.style.cursor = "pointer";
    row.addEventListener("click", () => selectSample(s));
    els.selBlend.append(row);
  }
}

function selectCustom(item, { play = true } = {}) {
  state.selected = { type: "custom", item };
  els.selTitle.textContent = `▲ caption合成 ${item.id}`;
  els.selSeed.textContent = `seed ${item.seed}`;
  els.selCaption.textContent = item.caption;
  renderTags(null);
  renderBlend(null);
  renderFeatureBars(item.features);
  els.useBtn.disabled = false;
  els.rerollBtn.disabled = false;
  state.lastCustom = item;
  els.captionText.value = item.caption;
  renderNoteRow();
  invalidateMap();
  if (play) playAudio(item.audio_url);
}

function selectBlendResult(item, x, y, { play = true } = {}) {
  state.selected = { type: "blend", item, x, y };
  els.selTitle.textContent = `blend ${item.id}`;
  els.selSeed.textContent = `seed ${item.seed}`;
  els.selCaption.textContent = `マップ位置 (${x.toFixed(2)}, ${y.toFixed(2)}) の近傍 ${item.blend.length} 点のキャプション隠れ状態を距離重みでブレンドした声`;
  renderTags(null);
  renderBlend(item.blend);
  renderFeatureBars(null);
  els.useBtn.disabled = false;
  renderNoteRow();
  invalidateMap();
  if (play) playAudio(item.audio_url);
}

/* ---------- history ---------- */

function renderHistory(items) {
  els.historyList.innerHTML = "";
  const usable = items
    .filter((it) => ["atlas_sample", "atlas_blend", "custom_caption", "runtime_selected_profile"].includes(it.mode))
    .slice(-12)
    .reverse();
  if (!usable.length) {
    const p = document.createElement("p");
    p.className = "history-empty";
    p.textContent = "まだ合成履歴はありません。";
    els.historyList.append(p);
    return;
  }
  for (const item of usable) {
    const row = document.createElement("div");
    row.className = "history-item";
    const play = document.createElement("button");
    play.className = "h-play";
    play.textContent = "▶";
    play.addEventListener("click", () => playAudio(item.audio_url));
    const body = document.createElement("div");
    body.className = "h-body";
    const modeLabel = { atlas_sample: "atlas点", atlas_blend: "ブレンド", custom_caption: "caption合成", runtime_selected_profile: "runtime" }[item.mode];
    body.innerHTML = `
      <div class="h-text">${item.text}</div>
      <div class="h-meta">${modeLabel} / ${item.duration_sec?.toFixed?.(1) ?? item.duration_sec}s / seed ${item.seed ?? "—"}</div>`;
    row.append(play, body);
    if (item.mode !== "runtime_selected_profile") {
      const use = document.createElement("button");
      use.className = "h-use";
      use.textContent = "★ 採用";
      use.addEventListener("click", () => useVoice(item.id));
      row.append(use);
    }
    els.historyList.append(row);
  }
}

function rebuildCustomMarks(items) {
  const builtAt = Number(state.atlas?.built_at || 0);
  state.customMarks = items
    .filter(
      (it) =>
        it.mode === "custom_caption" &&
        Number(it.created_at || 0) > builtAt &&
        Array.isArray(it.umap) &&
        Array.isArray(it.pca),
    )
    .map((it) => ({ item: it }));
}

function rebuildBlendMarks(items) {
  // 生成済みブレンド点は履歴から復元する（リロードしても消えない）。
  // ただしアトラス再構築前の◆は座標の意味が失われるため非表示（履歴からは再生可能）
  const builtAt = Number(state.atlas?.built_at || 0);
  state.blendMarks = items
    .filter(
      (it) =>
        it.mode === "atlas_blend" &&
        Number(it.created_at || 0) > builtAt &&
        Number.isFinite(Number(it.x)) &&
        Number.isFinite(Number(it.y)) &&
        (it.projection === "umap" || it.projection === "pca"),
    )
    .map((it) => ({ x: Number(it.x), y: Number(it.y), item: it, projection: it.projection }));
  rebuildCustomMarks(items);
  invalidateMap();
}

async function refreshHistory() {
  try {
    const data = await fetchJson("/api/history");
    state.history = data.history || [];
    renderHistory(state.history);
    rebuildBlendMarks(state.history);
    renderCollection();
  } catch {
    /* noop */
  }
}

/* ---------- API actions ---------- */

function setBusy(busy, message = "") {
  state.busy = busy;
  els.synthBtn.disabled = busy;
  els.synthMeta.textContent = message;
}

async function synthesizeAtPoint(x, y) {
  if (state.busy) return;
  const text = els.synthText.value.trim();
  if (!text) {
    toast("合成するテキストを入力してね");
    return;
  }
  setBusy(true, "近傍キャプションをブレンドして合成中…");
  try {
    const item = await fetchJson("/api/atlas/synthesize", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, x, y, projection: state.projection }),
    });
    selectBlendResult(item, x, y);
    setBusy(false, `${item.id} / ${item.duration_sec}s / ${item.elapsed_sec}s で生成`);
    refreshHistory();
  } catch (error) {
    setBusy(false, `生成失敗: ${error.message}`);
  }
}

async function synthesizeSelectedVoice() {
  if (state.busy || !state.selected) {
    if (!state.selected) toast("先にマップで声を選んでね");
    return;
  }
  if (state.selected.type === "ref") {
    toast("★はvoice_lock声だよ。ローカル合成はできないけど、★の近くの●を試すと似た声が見つかるはず！");
    return;
  }
  const text = els.synthText.value.trim();
  if (!text) {
    toast("合成するテキストを入力してね");
    return;
  }
  setBusy(true, "選択中の声で合成中…");
  try {
    let body;
    if (state.selected.type === "sample") {
      body = { text, sample_id: state.selected.sample.id };
    } else if (state.selected.type === "custom") {
      body = { text, caption: state.selected.item.caption, seed: state.selected.item.seed, engine: state.selected.item.engine || "irodori" };
    } else {
      // 保存済みのブレンド構成で再合成する（投影の変化に影響されない）
      body = {
        text,
        blend: state.selected.item?.blend,
        x: state.selected.x,
        y: state.selected.y,
        projection: state.selected.item?.projection || state.projection,
        seed: state.selected.item?.seed,
      };
    }
    const item = await fetchJson("/api/atlas/synthesize", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (state.selected.type === "blend") {
      state.selected.item = item;
    }
    playAudio(item.audio_url);
    setBusy(false, `${item.id} / ${item.duration_sec}s / ${item.elapsed_sec}s で生成`);
    refreshHistory();
  } catch (error) {
    setBusy(false, `生成失敗: ${error.message}`);
  }
}

async function useVoice(generationId) {
  const id =
    generationId ||
    (state.selected?.type === "sample" ? state.selected.sample.id : state.selected?.item?.id);
  if (!id) {
    toast("先に声を選ぶか、合成してから採用してね");
    return;
  }
  try {
    const data = await fetchJson("/api/select", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ generation_id: id }),
    });
    toast(`⭐ 既定ボイスに設定したよ（ランタイムがこの声で喋る）: ${data.profile.generation.id}`);
    loadStatus();
  } catch (error) {
    toast(`保存失敗: ${error.message}`);
  }
}

/* ---------- load ---------- */

async function loadStatus() {
  try {
    const status = await fetchJson("/api/status");
    els.modelStatus.textContent = `${status.model.split("/").pop()} ${status.model_loaded ? "· loaded" : "· cold"}`;
    els.modelStatus.classList.toggle("ok", status.model_loaded);
    els.atlasStatus.textContent = status.atlas_ready
      ? `atlas: ${status.atlas_samples} voices`
      : "atlas: 未構築";
    els.atlasStatus.classList.toggle("ok", status.atlas_ready);
    els.atlasStatus.classList.toggle("bad", !status.atlas_ready);
  } catch (error) {
    els.modelStatus.textContent = `error: ${error.message}`;
  }
}

function applyProjection() {
  if (!state.atlas) return;
  for (const p of state.points) {
    const [x, y] = p.sample[state.projection];
    p.tgt = { x, y };
  }
  renderAxisHints();
  renderValidation();
  invalidateMap();
}

function updateColorDomain() {
  if (!state.atlas || state.colorBy === "gender") {
    renderLegend();
    return;
  }
  const values = state.atlas.samples
    .map((s) => colorValue(s))
    .filter((v) => Number.isFinite(v));
  colorDomain = percentileDomain(values.length ? values : [0, 1]);
  renderLegend();
  invalidateMap();
}

async function loadAtlas() {
  const data = await fetchJson("/api/atlas");
  if (!data.ready) {
    els.mapEmpty.hidden = false;
    return;
  }
  els.mapEmpty.hidden = true;
  state.atlas = data;
  state.references = data.references || [];
  for (const opt of els.colorBy.querySelectorAll("option[data-sim]")) opt.remove();
  const hasSim = data.samples.some((s) => s.ref_sim);
  if (hasSim) {
    for (const ref of state.references) {
      const opt = document.createElement("option");
      opt.value = `${SIM_PREFIX}${ref.id}`;
      opt.dataset.sim = "1";
      opt.textContent = `★${ref.label} 類似度`;
      els.colorBy.append(opt);
    }
  }
  state.points = data.samples.map((sample) => {
    const [x, y] = sample[state.projection];
    return { sample, cur: { x, y }, tgt: { x, y } };
  });
  updateColorDomain();
  renderAxisHints();
  renderValidation();
  invalidateMap();
  // built_at確定後に◆の鮮度フィルタを適用し直す（refreshHistoryとのレース対策）
  refreshHistory();
}

/* ---------- events ---------- */

els.canvas.addEventListener("mousemove", (event) => {
  const rect = els.canvas.getBoundingClientRect();
  const mx = event.clientX - rect.left;
  const my = event.clientY - rect.top;
  const prevHoverKey = state.hover ? `${state.hover.type}:${state.hover.sample?.id || state.hover.item?.id}` : "";
  const hit = nearestPoint(mx, my);
  if (hit?.kind === "ref") {
    state.hover = { type: "ref", ref: hit.ref };
    showTooltip(
      mx,
      my,
      `★ ${hit.ref.label} — マスターのvoice_lock声`,
      `${hit.ref.note || ""}<br>lock ${hit.ref.voice_lock_id.slice(0, 8)}… · F0 ${hit.ref.features.f0_med.toFixed(0)}Hz · クリックで試聴`,
    );
  } else if (hit?.kind === "custom") {
    const item = hit.mark.item;
    state.hover = { type: "custom", item };
    showTooltip(
      mx,
      my,
      `▲ ${item.caption}`,
      `caption直打ち · seed ${item.seed}${item.nearest_sim != null ? ` · 最近傍sim ${(item.nearest_sim * 100).toFixed(0)}%` : ""}${item.features ? ` · F0 ${Number(item.features.f0_med).toFixed(0)}Hz` : ""}`,
    );
  } else if (hit?.kind === "sample") {
    state.hover = { type: "sample", sample: hit.point.sample };
    const s = hit.point.sample;
    const flipNote = s.voice_flip
      ? `<br>⚠️ 指示と出音がズレてる可能性: タグは${s.tags?.gender === "male" ? "男声" : "女声"}だがF0 ${s.features.f0_med.toFixed(0)}Hz（caption内の属性衝突でモデルが片方に引っ張られた例）`
      : "";
    showTooltip(
      mx,
      my,
      s.caption,
      `${s.id} · seed ${s.seed} · F0 ${s.features.f0_med.toFixed(0)}Hz · ${s.duration_sec.toFixed(1)}s${flipNote}`,
    );
  } else if (hit?.kind === "blend") {
    const item = hit.mark.item || {};
    state.hover = { type: "blend", item };
    const comp = (item.blend || [])
      .slice(0, 3)
      .map((c) => `${(c.weight * 100).toFixed(0)}% ${c.caption.replace("日本語を話す", "").slice(0, 14)}`)
      .join(" + ");
    showTooltip(
      mx,
      my,
      `ブレンド: ${comp}`,
      `${item.id || ""} · seed ${item.seed ?? "—"}${item.features ? ` · F0 ${Number(item.features.f0_med).toFixed(0)}Hz` : ""}`,
    );
  } else {
    state.hover = null;
    els.tooltip.hidden = true;
    els.canvas.style.cursor = "crosshair";
  }
  const nextHoverKey = state.hover ? `${state.hover.type}:${state.hover.sample?.id || state.hover.item?.id}` : "";
  if (prevHoverKey !== nextHoverKey) invalidateMap();
});

function showTooltip(mx, my, captionHtml, metaHtml) {
  els.tooltip.hidden = false;
  els.tooltip.innerHTML = `
    <div class="tt-caption">${captionHtml}</div>
    <div class="tt-meta">${metaHtml}</div>`;
  const tx = Math.min(mx + 16, cssW - 310);
  const ty = Math.min(my + 14, cssH - 80);
  els.tooltip.style.left = `${Math.max(8, tx)}px`;
  els.tooltip.style.top = `${Math.max(8, ty)}px`;
  els.canvas.style.cursor = "pointer";
}

els.canvas.addEventListener("mouseleave", () => {
  state.hover = null;
  els.tooltip.hidden = true;
  invalidateMap();
});

els.canvas.addEventListener("click", (event) => {
  if (!state.atlas || dragState.moved) return;
  const rect = els.canvas.getBoundingClientRect();
  const mx = event.clientX - rect.left;
  const my = event.clientY - rect.top;
  const hit = nearestPoint(mx, my);
  if (hit?.kind === "ref") {
    selectReference(hit.ref);
    return;
  }
  if (hit?.kind === "custom") {
    selectCustom(hit.mark.item);
    return;
  }
  if (hit?.kind === "sample") {
    selectSample(hit.point.sample);
    return;
  }
  if (hit?.kind === "blend") {
    selectBlendResult(hit.mark.item, hit.mark.x, hit.mark.y);
    return;
  }
  const [dx, dy] = screenToData(mx, my);
  if (dx < -0.02 || dx > 1.02 || dy < -0.02 || dy > 1.02) return;
  synthesizeAtPoint(Math.max(0, Math.min(1, dx)), Math.max(0, Math.min(1, dy)));
});

// --- ズーム（ピンチ=wheel+ctrlKey / それ以外のwheel=パン） ---
els.canvas.addEventListener(
  "wheel",
  (event) => {
    event.preventDefault();
    const rect = els.canvas.getBoundingClientRect();
    if (event.ctrlKey) {
      zoomAt(event.clientX - rect.left, event.clientY - rect.top, Math.exp(-event.deltaY * 0.012));
    } else {
      state.view.ox -= event.deltaX;
      state.view.oy -= event.deltaY;
      clampView();
      invalidateMap();
    }
  },
  { passive: false },
);

// --- ドラッグでパン（クリックとは移動量4pxで区別） ---
const dragState = { active: false, moved: false, lx: 0, ly: 0 };
els.canvas.addEventListener("mousedown", (event) => {
  dragState.active = true;
  dragState.moved = false;
  dragState.lx = event.clientX;
  dragState.ly = event.clientY;
});
window.addEventListener("mousemove", (event) => {
  if (!dragState.active) return;
  const dx = event.clientX - dragState.lx;
  const dy = event.clientY - dragState.ly;
  if (!dragState.moved && Math.hypot(dx, dy) < 4) return;
  dragState.moved = true;
  dragState.lx = event.clientX;
  dragState.ly = event.clientY;
  state.view.ox += dx;
  state.view.oy += dy;
  clampView();
  invalidateMap();
});
window.addEventListener("mouseup", () => {
  dragState.active = false;
  window.setTimeout(() => {
    dragState.moved = false;
  }, 0);
});
els.canvas.addEventListener("dblclick", resetView);
els.resetView.addEventListener("click", resetView);

// --- お気に入りフィルタとノート ---
els.favFilter.addEventListener("click", () => {
  state.favOnly = !state.favOnly;
  els.favFilter.classList.toggle("is-active", state.favOnly);
  invalidateMap();
});

els.noteFav.addEventListener("click", () => {
  const id = selectedNoteId();
  saveNote({ favorite: !noteOf(id)?.favorite });
});
let noteNameTimer = 0;
els.noteName.addEventListener("input", () => {
  window.clearTimeout(noteNameTimer);
  noteNameTimer = window.setTimeout(() => saveNote({ name: els.noteName.value }), 600);
});

function setProjection(projection) {
  state.projection = projection;
  els.projUmap.classList.toggle("is-active", projection === "umap");
  els.projPca.classList.toggle("is-active", projection === "pca");
  applyProjection();
}

els.projUmap.addEventListener("click", () => setProjection("umap"));
els.projPca.addEventListener("click", () => setProjection("pca"));

els.colorBy.addEventListener("change", () => {
  state.colorBy = els.colorBy.value;
  updateColorDomain();
});

els.searchBox.addEventListener("input", () => {
  state.search = els.searchBox.value.trim();
  invalidateMap();
});

async function gachaSynthesize(seed = null) {
  if (state.busy) return;
  const caption = els.captionText.value.trim();
  if (!caption) {
    toast("captionを入力してね（例: 日本語を話すハスキーな大人の女性の声。）");
    return;
  }
  const text = els.synthText.value.trim() || "リスナーのみんな、こんにちは！これから配信始めるね。";
  state.busy = true;
  els.gachaBtn.disabled = true;
  els.gachaMeta.textContent = "生成中…（マップ配置のため話者埋め込みも計算するよ）";
  try {
    const body = { text, caption, engine: swipeEls.engine?.value || "irodori" };
    if (seed != null) body.seed = seed;
    const item = await fetchJson("/api/atlas/synthesize", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    els.gachaMeta.textContent = `${item.id} / seed ${item.seed} / ${item.elapsed_sec}s${item.nearest_sim != null ? ` / 既存最近傍sim ${(item.nearest_sim * 100).toFixed(0)}%（低いほど新しい声）` : ""}`;
    await refreshHistory();
    selectCustom(item);
  } catch (error) {
    els.gachaMeta.textContent = `生成失敗: ${error.message}`;
  } finally {
    state.busy = false;
    els.gachaBtn.disabled = false;
  }
}

els.gachaBtn.addEventListener("click", () => gachaSynthesize());
els.rerollBtn.addEventListener("click", () => {
  if (state.lastCustom) gachaSynthesize(state.lastCustom.seed);
});

els.synthBtn.addEventListener("click", synthesizeSelectedVoice);
els.useBtn.addEventListener("click", () => useVoice());

// セリフプリセット: テキスト内容が演技を引っ張るため、キャラの異なる定型文を切替可能に
for (const chip of document.querySelectorAll(".preset-chip")) {
  chip.addEventListener("click", () => {
    els.synthText.value = chip.dataset.text;
    for (const c of document.querySelectorAll(".preset-chip")) {
      c.classList.toggle("is-active", c === chip);
    }
  });
}
els.synthText.addEventListener("input", () => {
  for (const c of document.querySelectorAll(".preset-chip")) {
    c.classList.toggle("is-active", c.dataset.text === els.synthText.value);
  }
});

window.addEventListener("resize", () => {
  resizeCanvas();
  invalidateMap();
});

/* ---------- boot ---------- */

resizeCanvas();
window.requestAnimationFrame(tick);
loadStatus();
loadNotes();
loadAtlas().catch((error) => {
  toast(`atlas読み込み失敗: ${error.message}`);
  els.mapEmpty.hidden = false;
});
refreshHistory();


/* ---------- swipe discovery mode (Tinder風発掘) ---------- */

const swipeEls = {
  btn: document.querySelector("#swipeBtn"),
  overlay: document.querySelector("#swipeOverlay"),
  card: document.querySelector("#swipeCard"),
  next: document.querySelector("#swipeCardNext"),
  empty: document.querySelector("#swipeEmpty"),
  count: document.querySelector("#swipeCount"),
  gacha: document.querySelector("#swipeGacha"),
  close: document.querySelector("#swipeClose"),
  nope: document.querySelector("#swipeNope"),
  like: document.querySelector("#swipeLike"),
  replay: document.querySelector("#swipeReplay"),
  name: document.querySelector("#swipeName"),
};

swipeEls.gender = document.querySelector("#swipeGender");

const SWIPED_KEY = "vl_swiped_v2"; // 2026-06-12 大掃除でリセット
const SWIPE_GENDER_KEY = "vl_swipe_gender";
const swipe = { deck: [], idx: 0, open: false, audio: new Audio(), gachaBusy: false, sinceGacha: 0 };
swipe.audio.muted = MUTED;
swipeEls.gender.value = localStorage.getItem(SWIPE_GENDER_KEY) || "female";

// 性別縛り: ECAPA分類器の音響性別（高信頼）と captionタグ の二重条件。
// F0単独の閾値は男声の高F0で破綻したため廃止（ホールドアウト精度98-100%の分類器に置換）。
function genderBindOk(voice, mode) {
  if (mode === "all") return true;
  const ag = voice.acoustic_gender;
  const prob = Number(voice.gender_prob ?? 0);
  const tagG = voice.tags?.gender;
  if (ag) {
    // 分類器はirodori音声で校正済み。Qwen3の声は分布が違い確信度が圧縮されるため
    // 閾値を下げ、代わりにF0の常識レンジでダブルチェックする
    const isQwen = voice.engine === "qwen3";
    let soundOk = ag === mode && prob >= (isQwen ? 0.55 : 0.75);
    if (isQwen && soundOk) {
      const f0 = Number(voice.features?.f0_med ?? 0);
      if (mode === "female" && f0 > 0 && f0 < 150) soundOk = false;
      if (mode === "male" && f0 > 230) soundOk = false;
    }
    const tagOk = tagG ? tagG === mode : true; // caption表記の興ざめ防止（タグなし=自然文captionは音響のみ）
    return soundOk && tagOk;
  }
  // 分類器情報がない場合（旧データ）は保守的にF0
  const f0 = Number(voice.features?.f0_med ?? 0);
  return mode === "female" ? f0 >= 215 : f0 > 0 && f0 <= 150;
}

function swipedSet() {
  try {
    return new Set(JSON.parse(localStorage.getItem(SWIPED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function recordSwiped(id) {
  const s = swipedSet();
  s.add(id);
  localStorage.setItem(SWIPED_KEY, JSON.stringify([...s]));
}

function buildSwipeDeck() {
  const seen = swipedSet();
  const mode = swipeEls.gender.value;
  swipe.deck = state.atlas.samples
    .filter((s) => !seen.has(s.id) && !noteOf(s.id)?.favorite && genderBindOk(s, mode))
    .sort((a, b) => (a.deck_rank ?? 1e9) - (b.deck_rank ?? 1e9))
    .map((s) => ({ kind: "atlas", voice: s }));
  swipe.idx = 0;
}

// ---- 自然言語captionテーマ（広い表現空間をその場で生成）----
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

// 数字年齢（60代等）はモデルにほぼ効かないため廃止。効く語彙（少女/おばあさん等）で表現する。
// 年配系はデフォルト除外（「👵 年配も含める」チェック時のみ）
function elderOk() {
  return !!swipeEls.elder?.checked;
}

function persona(g, subset) {
  const elder = elderOk();
  const baseF = ["少女", "若い女性", "女性", "大人の女性"].concat(elder ? ["年配の女性", "おばあさん"] : []);
  const baseM = ["少年", "若い男性", "男性", "大人の男性"].concat(elder ? ["おじいさん"] : []);
  const table = {
    young: [["少女", "若い女性"], ["少年", "若い男性"]],
    adult: [["女性", "大人の女性"], ["男性", "大人の男性"]],
    mature: [
      elder ? ["年配の女性", "おばあさん", "大人の女性"] : ["大人の女性", "女性"],
      elder ? ["おじいさん", "大人の男性"] : ["大人の男性", "男性"],
    ],
  };
  const [f, m] = table[subset] || [baseF, baseM];
  if (g === "female") return pick(f);
  if (g === "male") return pick(m);
  return pick(f.concat(m));
}
const TEXTURE_NL = [
  "少しハスキーで",
  "息が多めで",
  "透き通っていて",
  "低く落ち着いていて",
  "甘くやわらかくて",
  "芯が通っていて",
  "かすれ気味で",
  "丸くあたたかくて",
  "鼻にかかっていて",
  "よく響いて",
];



const THEMES = [
  {
    id: "midnight_radio",
    label: "🌙 深夜ラジオ",
    build: (g) =>
      `${persona(g)}の声。深夜2時のラジオブースで、たったひとりのリスナーに向けて話しかけている。${pick(TEXTURE_NL)}、マイクが近く、吐息がそのまま乗る。${pick(["ときどき小さく笑う。", "言葉の合間に長めの間がある。", "内緒話のように声をひそめる瞬間がある。"])}`,
  },
  {
    id: "streamer",
    label: "🎮 元気配信者",
    build: (g) =>
      `${persona(g, "young")}の声。ゲーム配信の冒頭で視聴者に挨拶している。${pick(TEXTURE_NL)}、テンションが高く語尾が弾む。${pick(["コメントを読んで吹き出しそうになる。", "効果音みたいなリアクションが混ざる。", "早口だが聞き取りやすい。"])}`,
  },
  {
    id: "storyteller",
    label: "📖 物語の語り手",
    build: (g) =>
      `${persona(g, "mature")}の声。暖炉のそばで子どもたちに昔話を読み聞かせている。${pick(TEXTURE_NL)}、ゆったりした間で、${pick(["登場人物ごとに声色が少し変わる。", "ページをめくる間も物語の一部のように話す。", "聞き手が眠りに落ちる直前のような穏やかさがある。"])}`,
  },
  {
    id: "whisper",
    label: "🤫 ささやき",
    build: (g) =>
      `${persona(g)}の声。耳元でささやくように話す。${pick(TEXTURE_NL)}、音量は小さいのに言葉の輪郭がはっきりしていて、${pick(["囁きの中に笑みが透ける。", "秘密を打ち明けるような親密さがある。", "息づかいが言葉と同じくらい雄弁。"])}`,
  },
  {
    id: "cool",
    label: "🧊 クールな大人",
    build: (g) =>
      `${persona(g, "adult")}の声。高層ビルの会議室で、平然と核心を突く一言を放つ。${pick(TEXTURE_NL)}、感情を表に出さないのに、聞き手を黙らせる静かな圧がある。`,
  },
  {
    id: "fantasy",
    label: "🧚 ファンタジー",
    build: (g) => {
      // 役柄語は声のアンカーにならないため「〜のように」の修飾に格下げし、
      // 先頭は必ず効く人物語彙で固定する
      const styleF = ["森の奥に住む魔女のように妖しく", "月の神殿の巫女のように静謐に", "竜を従える女王のように威厳をもって", "星を読む占い師のように思わせぶりに"];
      const styleM = ["放浪の吟遊詩人のように歌うように", "古城の執事のように恭しく", "竜騎士団の隊長のように号令するように", "老賢者のように悟ったように"];
      const style = g === "male" ? pick(styleM) : g === "female" ? pick(styleF) : pick(styleF.concat(styleM));
      return `${persona(g)}の声。${pick(TEXTURE_NL)}、${style}話す。現実離れした不思議な響きがある。`;
    },
  },
  {
    id: "retro",
    label: "📻 レトロ",
    build: (g) => {
      const styleAny = ["昭和のラジオアナウンサーのように一語一語を丁寧に置くように", "古い映画の登場人物のように時代がかった言い回しで", "蓄音機から流れてくるような格調をもって"];
      return `${persona(g, "adult")}の声。${pick(TEXTURE_NL)}、${pick(styleAny)}話す。どこか懐かしい節回しがある。`;
    },
  },
  {
    id: "drama",
    label: "🎭 感情ドラマ",
    build: (g) =>
      `${persona(g)}の声。${pick([
        "涙をこらえながら気丈に振る舞っている",
        "静かな怒りを押し殺している",
        "恋を打ち明ける直前で声が揺れている",
        "十年ぶりの再会に声が震えている",
        "大切なものを守ると決めた直後の静かな決意がある",
      ])}。${pick(TEXTURE_NL)}、感情が声の奥からにじみ出る。`,
  },
  {
    id: "mix",
    label: "🎲 おまかせ",
    build: (g) => pick(THEMES.filter((t) => t.id !== "mix")).build(g),
  },
];

// 「やってる最中の調整」ダイヤル: 息感。caption末尾に効く指示を足し、
// 多め/MAX時はテクスチャ語彙も息系に寄せる
const BREATH_KEY = "vl_swipe_breath";
const BREATH_CLAUSES = {
  normal: null,
  more: "息成分が多く、吐息まじりのやわらかい発声。",
  max: "終始ささやくような息まじりの声で、吐息が言葉を包む。",
  less: "息成分は控えめで、クリアな発声。",
};
const BREATHY_TEXTURES = ["息が多めで", "甘くやわらかくて", "かすれ気味で", "少しハスキーで"];

function applyBreath(caption) {
  const mode = swipeEls.breath?.value || "normal";
  const clause = BREATH_CLAUSES[mode];
  if (!clause) return caption;
  let out = caption;
  if (mode === "more" || mode === "max") {
    // テーマが選んだテクスチャを息系に差し替え（先頭一致したものだけ）
    for (const t of TEXTURE_NL) {
      if (!BREATHY_TEXTURES.includes(t) && out.includes(t)) {
        out = out.replace(t, pick(BREATHY_TEXTURES));
        break;
      }
    }
  }
  return `${out}${clause}`;
}

function themeById(id) {
  return THEMES.find((t) => t.id === id) || THEMES[THEMES.length - 1];
}

function randomCaption() {
  return applyBreath(themeById(swipeEls.theme.value).build(swipeEls.gender.value));
}

const SWIPE_BUFFER_TARGET = 3; // 現在のカード + 先読み3枚を常にキープ

function swipeAhead() {
  return swipe.deck.length - swipe.idx; // 現在カードを含む残り枚数
}

async function swipeGachaFetch() {
  if (swipe.gachaBusy || !swipeEls.gacha.checked) return;
  if (swipeAhead() > SWIPE_BUFFER_TARGET) return; // 充分に貯まってる
  swipe.gachaBusy = true;
  try {
    const text = els.synthText.value.trim() || "リスナーのみんな、こんにちは！これから配信始めるね。";
    const themeLabel = themeById(swipeEls.theme.value).label;
    const item = await fetchJson("/api/atlas/synthesize", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, caption: randomCaption(), num_steps: 8, engine: swipeEls.engine.value }),
    });
    item.theme_label = themeLabel;
    if (!genderBindOk(item, swipeEls.gender.value)) {
      // 指定性別の音にならなかったガチャはデッキに入れない（履歴には残る）
      return;
    }
    swipe.deck.splice(swipe.idx + 1, 0, { kind: "gacha", voice: item });
    updateSwipeCount();
    // 「生成中…」表示中（カード非表示）なら、出来上がった瞬間に表示する
    if (swipe.open && swipeEls.card.hidden) renderSwipeCard();
    refreshHistory();
  } catch {
    /* noop（下のwatchdogが再挑戦する） */
  } finally {
    swipe.gachaBusy = false;
    // バッファ（先読み3枚）が貯まるまで連続生成。弾かれ・エラー時もここで自動継続
    if (swipe.open && swipeEls.gacha.checked && swipeAhead() <= SWIPE_BUFFER_TARGET) {
      window.setTimeout(swipeGachaFetch, 150);
    }
  }
}

// watchdog: 万一どこかで止まっても、「生成中…」のまま3秒以上放置されたら再起動する
window.setInterval(() => {
  if (swipe.open && swipeEls.gacha.checked && !swipe.gachaBusy && !swipe.deck[swipe.idx] && swipeEls.entry.hidden) {
    swipeGachaFetch();
  }
}, 3000);

function updateSwipeCount() {
  const ahead = Math.max(0, swipe.deck.length - swipe.idx - 1);
  swipeEls.count.textContent = `先読み ${ahead}/${SWIPE_BUFFER_TARGET} 枚${swipe.gachaBusy ? " ⏳生成中" : ""}`;
}

function swipeCardHtml(entry) {
  const v = entry.voice;
  const f = v.features || {};
  const isGacha = entry.kind === "gacha";
  const chips = [
    v.tags?.gender ? { female: "女声", male: "男声", neutral: "中性" }[v.tags.gender] : null,
    Number.isFinite(Number(f.f0_med)) ? `F0 ${Number(f.f0_med).toFixed(0)}Hz` : null,
    `seed ${v.seed}`,
    isGacha && v.nearest_sim != null ? `新規度 ${(100 - v.nearest_sim * 100).toFixed(0)}%` : null,
  ].filter(Boolean);
  return `
    <span class="sc-badge"><i class="sc-dot" style="background:${entry.kind === "atlas" ? pointColor(v) : v.features ? pointColor(v) : "#94a3b8"}"></i>${isGacha ? (v.engine === "qwen3" ? "▲ 🐉Qwen3" : "▲ 🍙irodori") : "● アトラス"} ${v.id} ${v.theme_label ? `<span class="sc-theme">${v.theme_label}</span>` : ""}</span>
    <div class="sc-caption">${v.caption}</div>
    <div class="sc-chips">${chips.map((c) => `<span class="sc-chip">${c}</span>`).join("")}</div>
    <span class="sc-stamp like">LIKE</span>
    <span class="sc-stamp nope">SKIP</span>`;
}

function renderSwipeCard() {
  const cur = swipe.deck[swipe.idx];
  const nxt = swipe.deck[swipe.idx + 1];
  updateSwipeCount();
  swipeEls.card.style.transform = "";
  swipeEls.card.classList.remove("anim");
  if (!cur) {
    swipeEls.card.hidden = true;
    swipeEls.next.hidden = true;
    swipeEls.empty.hidden = false;
    swipeEls.empty.querySelector("p").textContent = swipeEls.gacha.checked
      ? "🎲 次の声を生成中…（5〜10秒）"
      : "デッキが空だよ。「🎲 自動補充」をONにすると無限に発掘できる";
    if (swipeEls.gacha.checked) swipeGachaFetch();
    return;
  }
  swipeEls.empty.hidden = true;
  swipeEls.card.hidden = false;
  swipeEls.card.innerHTML = swipeCardHtml(cur);
  if (nxt) {
    swipeEls.next.hidden = false;
    swipeEls.next.innerHTML = swipeCardHtml(nxt);
  } else {
    swipeEls.next.hidden = true;
  }
  swipe.audio.muted = MUTED;
  swipe.audio.src = cur.voice.audio_url;
  swipe.audio.play().catch(() => {});
  // 先読みバッファ補充（常に3枚先まで作り置き）
  if (swipeEls.gacha.checked && swipeAhead() <= SWIPE_BUFFER_TARGET) {
    swipeGachaFetch();
  }
}

function commitSwipe(dir) {
  const cur = swipe.deck[swipe.idx];
  if (!cur) return;
  recordSwiped(cur.voice.id);
  if (dir === "like") {
    const name = swipeEls.name.value.trim();
    const patch = { favorite: true };
    if (name) patch.name = name;
    saveNoteFor(cur.voice.id, patch);
    toast(`♥ 保存したよ${name ? `: ${name}` : ""}`);
  }
  swipeEls.name.value = "";
  swipeEls.card.classList.add("anim");
  swipeEls.card.style.transform = dir === "like" ? "translateX(640px) rotate(18deg)" : "translateX(-640px) rotate(-18deg)";
  swipe.idx += 1;
  window.setTimeout(renderSwipeCard, 240);
}

swipeEls.entry = document.querySelector("#swipeEntry");
swipeEls.theme = document.querySelector("#swipeTheme");
swipeEls.engine = document.querySelector("#swipeEngine");
swipeEls.engine.value = localStorage.getItem("vl_swipe_engine") || "irodori";
swipeEls.engine.addEventListener("change", () => {
  localStorage.setItem("vl_swipe_engine", swipeEls.engine.value);
  toast(swipeEls.engine.value === "qwen3" ? "🐉 Qwen3エンジンに切替——次の生成から別モデルの声になるよ" : "🍙 irodoriエンジンに切替");
  syncEntrySelections();
});
swipeEls.breath = document.querySelector("#swipeBreath");
swipeEls.elder = document.querySelector("#swipeElder");
swipeEls.elder.checked = localStorage.getItem("vl_swipe_elder") === "1";
swipeEls.elder.addEventListener("change", () => {
  localStorage.setItem("vl_swipe_elder", swipeEls.elder.checked ? "1" : "0");
  toast(swipeEls.elder.checked ? "👵 年配も混ざるようにしたよ" : "年配系を除外したよ——次の生成から効く");
});
swipeEls.breath.value = localStorage.getItem(BREATH_KEY) || "normal";
swipeEls.breath.addEventListener("change", () => {
  localStorage.setItem(BREATH_KEY, swipeEls.breath.value);
  toast(`😮‍💨 息感を「${swipeEls.breath.options[swipeEls.breath.selectedIndex].textContent.replace("😮‍💨 息感: ", "")}」に変更——次の生成から効くよ`);
});

const SWIPE_THEME_KEY = "vl_swipe_theme";

// ヘッダーと入口のテーマ選択肢を構築
for (const t of THEMES) {
  const opt = document.createElement("option");
  opt.value = t.id;
  opt.textContent = t.label;
  swipeEls.theme.append(opt);
}
swipeEls.theme.value = localStorage.getItem(SWIPE_THEME_KEY) || "mix";
{
  const box = document.querySelector("#entryThemes");
  for (const t of THEMES) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.theme = t.id;
    b.textContent = t.label;
    b.addEventListener("click", () => {
      for (const x of box.children) x.classList.toggle("is-on", x === b);
    });
    box.append(b);
  }
}

function syncEntrySelections() {
  const g = localStorage.getItem(SWIPE_GENDER_KEY) || "female";
  for (const b of document.querySelectorAll("#entryGender button")) {
    b.classList.toggle("is-on", b.dataset.gender === g);
  }
  for (const b of document.querySelectorAll("#entryEngine button")) {
    b.classList.toggle("is-on", b.dataset.engine === swipeEls.engine.value);
  }
  const t = swipeEls.theme.value;
  for (const b of document.querySelectorAll("#entryThemes button")) {
    b.classList.toggle("is-on", b.dataset.theme === t);
  }
}

function openSwipe() {
  if (!state.atlas) {
    toast("アトラス読み込み中だよ、ちょっと待ってね");
    return;
  }
  swipe.open = true;
  swipeEls.overlay.hidden = false;
  swipeEls.card.hidden = true;
  swipeEls.next.hidden = true;
  swipeEls.empty.hidden = true;
  swipeEls.entry.hidden = false;
  swipeEls.count.textContent = "";
  syncEntrySelections();
}

for (const btn of document.querySelectorAll("#entryGender button")) {
  btn.addEventListener("click", () => {
    for (const x of document.querySelectorAll("#entryGender button")) x.classList.toggle("is-on", x === btn);
  });
}

for (const btn of document.querySelectorAll("#entryEngine button")) {
  btn.addEventListener("click", () => {
    for (const x of document.querySelectorAll("#entryEngine button")) x.classList.toggle("is-on", x === btn);
  });
}

document.querySelector("#entryStart").addEventListener("click", () => {
  const g = document.querySelector("#entryGender button.is-on")?.dataset.gender || "female";
  const t = document.querySelector("#entryThemes button.is-on")?.dataset.theme || "mix";
  const e = document.querySelector("#entryEngine button.is-on")?.dataset.engine || "irodori";
  swipeEls.gender.value = g;
  swipeEls.theme.value = t;
  swipeEls.engine.value = e;
  localStorage.setItem("vl_swipe_engine", e);
  localStorage.setItem(SWIPE_GENDER_KEY, g);
  localStorage.setItem(SWIPE_THEME_KEY, t);
  swipeEls.entry.hidden = true;
  // 新世界はオンデマンド生成が主役: ガチャは常時ON
  swipeEls.gacha.checked = true;
  buildSwipeDeck();
  renderSwipeCard();
});

swipeEls.theme.addEventListener("change", () => {
  localStorage.setItem(SWIPE_THEME_KEY, swipeEls.theme.value);
  syncEntrySelections();
});

function closeSwipe() {
  swipe.open = false;
  swipeEls.overlay.hidden = true;
  swipe.audio.pause();
  refreshHistory();
  invalidateMap();
}

swipeEls.gender.addEventListener("change", () => {
  localStorage.setItem(SWIPE_GENDER_KEY, swipeEls.gender.value);
  if (swipe.open) {
    buildSwipeDeck();
    renderSwipeCard();
  }
});

swipeEls.btn.addEventListener("click", openSwipe);
swipeEls.close.addEventListener("click", closeSwipe);
swipeEls.nope.addEventListener("click", () => commitSwipe("nope"));
swipeEls.like.addEventListener("click", () => commitSwipe("like"));
swipeEls.replay.addEventListener("click", () => {
  swipe.audio.currentTime = 0;
  swipe.audio.play().catch(() => {});
});

window.addEventListener("keydown", (event) => {
  if (!swipe.open || event.target === swipeEls.name) return;
  if (event.key === "ArrowRight") commitSwipe("like");
  else if (event.key === "ArrowLeft") commitSwipe("nope");
  else if (event.key === " ") {
    event.preventDefault();
    swipe.audio.currentTime = 0;
    swipe.audio.play().catch(() => {});
  } else if (event.key === "Escape") closeSwipe();
});

// ドラッグスワイプ
const swipeDrag = { active: false, sx: 0, sy: 0, dx: 0 };
swipeEls.card.addEventListener("pointerdown", (event) => {
  swipeDrag.active = true;
  swipeDrag.sx = event.clientX;
  swipeDrag.sy = event.clientY;
  swipeDrag.dx = 0;
  swipeEls.card.setPointerCapture(event.pointerId);
  swipeEls.card.classList.remove("anim");
});
swipeEls.card.addEventListener("pointermove", (event) => {
  if (!swipeDrag.active) return;
  swipeDrag.dx = event.clientX - swipeDrag.sx;
  const dy = event.clientY - swipeDrag.sy;
  swipeEls.card.style.transform = `translate(${swipeDrag.dx}px, ${dy * 0.3}px) rotate(${swipeDrag.dx * 0.04}deg)`;
  const like = swipeEls.card.querySelector(".sc-stamp.like");
  const nope = swipeEls.card.querySelector(".sc-stamp.nope");
  if (like) like.style.opacity = Math.min(1, Math.max(0, swipeDrag.dx / 110));
  if (nope) nope.style.opacity = Math.min(1, Math.max(0, -swipeDrag.dx / 110));
});
window.addEventListener("pointerup", () => {
  if (!swipeDrag.active) return;
  swipeDrag.active = false;
  if (swipeDrag.dx > 110) commitSwipe("like");
  else if (swipeDrag.dx < -110) commitSwipe("nope");
  else {
    swipeEls.card.classList.add("anim");
    swipeEls.card.style.transform = "";
    const like = swipeEls.card.querySelector(".sc-stamp.like");
    const nope = swipeEls.card.querySelector(".sc-stamp.nope");
    if (like) like.style.opacity = 0;
    if (nope) nope.style.opacity = 0;
  }
});
