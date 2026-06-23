// キャラクター設定 — スライス画像の参照先を一元管理
// 新キャラ差し替え時はこのファイルを書き換えるだけ

const params = new URLSearchParams(window.location.search);
const defaultBasePath = 'generated_v6_gpt_hairclip/slices_gpt_hairclip_candidate_01_png';
const basePath = params.get('base') || defaultBasePath;

export default {
  // スライス画像のベースパス（public/ からの相対パス）
  basePath,
  blinkAdjustPath: params.get('blinkAdjust') || basePath.replace(/\/?[^/]+$/, '/blink_adjustments.json'),

  // 画像フォーマット（webp / png）
  ext: 'png',

  // グリッド構成: rows = 上下（0:上向き → 4:下向き）、cols = 左右（0:左向き → 4:右向き）
  rows: 5,
  cols: 5,

  // シート定義: 目開け×口[とじ/中間/開け] = A/B/C、目閉じ×口[とじ/中間/開け] = D/E/F
  sheets: {
    eyesOpen:   { close: 'A', half: 'B', open: 'C' },
    eyesClosed: { close: 'D', half: 'E', open: 'F' },
  },

  // ファイル名パターンを生成
  src(sheet, r, c) {
    return `${this.basePath}/${sheet}/r${r}c${c}.${this.ext}`;
  },
};
