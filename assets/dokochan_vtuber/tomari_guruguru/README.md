# どこちゃんぐるぐる / どこちゃんトーク

tomari-guruguru の 25 方向マウス追従・口パク・まばたき構成を、Dokochan 用素材に置き換えたブラウザアバターです。

- **どこちゃんぐるぐる**: マウス追従で 5x5 の向き差分を切り替えるシンプル版
- **どこちゃんトーク**: マイク入力または音声ファイルの音量に合わせて口パクし、自動まばたきも行うトーク版

---

## セットアップ

必要環境:

- Node.js 22 LTS 推奨
- Vite 8 の要件: Node.js 20.19+ または 22.12+
- Python 3 + Pillow + OpenCV は素材再生成時に使用

```bash
npm install
```

## ローカル起動

Windows なら `start.bat` をダブルクリックすると、ローカルサーバーを起動してブラウザで開きます。

手動で起動する場合:

```bash
npm run dev
```

手動でアクセスする場合:

```text
http://127.0.0.1:5173/talk.html
http://127.0.0.1:5173/guruguru.html
```

注意:

- マイク入力は `localhost` または HTTPS でのみ利用できます。
- Google Fonts は CDN から読み込むため、初回表示にはネット接続が必要です。

## ビルド

```bash
npm run build
npm run preview
```

preview は GitHub Pages と同じ `/dokochan-guruguru/` のベースパスで起動します。

```text
http://127.0.0.1:4173/dokochan-guruguru/talk.html
http://127.0.0.1:4173/dokochan-guruguru/guruguru.html
```

---

## ディレクトリ構成

```text
.
├── index.html              # どこちゃんトークへのリダイレクト
├── guruguru.html           # ぐるぐる版エントリ
├── talk.html               # トーク版エントリ
├── vite.config.js          # Vite 8 ビルド設定
├── package.json
├── start.bat               # Windows 用起動バッチ
├── src/
│   ├── app.jsx             # ぐるぐる版アプリ本体
│   ├── talk-app.jsx        # トーク版アプリ本体
│   ├── tweaks-panel.jsx    # 画面右下の調整パネル
│   └── character-config.js # キャラ画像の参照先を一元管理
├── public/
│   └── slices2/            # スライス済み Dokochan 画像 150 枚
├── docs/                   # 画像生成・差し替え・検証手順
├── tools/
│   ├── slice_character_sheets.py
│   └── fix_slice_alpha.py
├── sheets_raw/             # 画像生成直後の chroma-key シート
├── sheets_alpha/           # 透過化済みシート
├── sheets_patched/         # A を基準に目・口だけ合成した最終 6 シート
├── sheets/                 # slicer 入力用シート
├── uploads/                # tomari 互換ファイル名のシート
├── verification/           # 検証画像・メトリクス・E2E 証跡
├── LICENSE
├── ASSET_LICENSE.md
└── README.md
```

---

## フレーム画像の仕組み

このアプリは、キャラクターの向きと表情に応じて `public/slices2/` 内の画像を 1 枚ずつ切り替えています。

### 25方向

5列 x 5行の向き差分です。

- 列: 左向き -> 正面 -> 右向き
  - `c0`: 左向き / `c1`: 左斜め / `c2`: 正面 / `c3`: 右斜め / `c4`: 右向き
- 行: 上向き -> 水平 -> 下向き
  - `r0`: 強く上を見る / `r1`: 少し上 / `r2`: 水平 / `r3`: 少し下 / `r4`: 強く下

### 6状態

| フォルダ | 目 | 口 |
|---|---|---|
| `A` | 開け | とじ |
| `B` | 開け | 中間 |
| `C` | 開け | 開け |
| `D` | 閉じ | とじ |
| `E` | 閉じ | 中間 |
| `F` | 閉じ | 開け |

画像パス例: `slices2/A/r2c2.png`

`src/character-config.js` の `basePath` と `ext` で切り替え可能です。この Dokochan 版は透過 PNG を参照しています。

---

## Dokochan 素材の作り方

最終的に必要なシートは tomari-guruguru と同じ 6 枚です。

```text
A_目開け_口とじ.png
B_目開け_口中間.png
C_目開け_口開け.png
D_目閉じ_口とじ.png
E_目閉じ_口中間.png
F_目閉じ_口開け.png
```

Dokochan 版では、AI 画像生成の A-F 個別生成で起きる位置・サイズの揺れをそのまま使いません。

1. `sheets_raw/` に透過背景の 5x5 シートを 6 枚生成する
2. 透過 PNG をそのまま、または magenta 背景だけを除去して `sheets_alpha/` に保存する
3. `tools/dokochan_tomari_patch_variants.py` で A シートの alpha を基準にし、B/C の口、D の閉じ目、E/F の閉じ目＋口だけを合成する
4. `tools/slice_character_sheets.py` の component mode で `public/slices2/` に 150 枚を切り出す
5. `tools/fix_slice_alpha.py` で slice 後の B-F silhouette を A と完全一致させる
6. `tools/dokochan_tomari_verify_assets.py` と Playwright E2E で方向・口・瞬き・サイズ差を検証する

再スライス:

```bash
npm run slice:assets
```

検証画像とメトリクス再生成:

```bash
npm run verify:assets
```

詳しい生成プロンプトと差し替え注意点は `docs/01_画像生成用プロンプト.txt` と `docs/新キャラ差し替え手順.md` を参照してください。

---

## 劣化防止の検証基準

この成果物では、ユーザー指摘のあった劣化を以下で検証しています。

- 背景ブレ防止: 生成シートは chroma-key 背景から透過化し、アプリ側背景は固定色のみ
- 緑 spill 防止: 緑背景は使わず、透過 PNG または magenta 背景だけを許可
- 追従反転防止: Playwright E2E で左 `c0`、右 `c4`、上 `r0`、下 `r4` への切り替えを検証
- 瞬きサイズ変化防止: A-D/B-E/C-F の全75対応で alpha 差分 0px、bbox 差分 0px、重心差分 0px を検証
- 口パク防止: fake microphone 付き Playwright E2E で `B` または `C` への切り替えを検証
- 画像切り出し防止: 150 枚存在、A-F 各 25 枚、暗背景接触シートで背景残りを検証

主な証跡:

```text
verification/patched_sheets_contact.jpg
verification/slices_contact_warm.jpg
verification/slices_dark_edge_check.jpg
verification/direction_audit_A.jpg
verification/slice_metrics.txt
verification/blink_coordinate_metrics.txt
verification/playwright/
```

---

## アプリ側の確認

```bash
npm run build
npm run verify:pages
npm run test:e2e
```

確認ポイント:

- `guruguru.html` でマウス追従が 25 方向に自然に切り替わる
- 左右上下の追従が逆にならない
- `talk.html` で音声に合わせて A/B/C、D/E/F が切り替わる
- まばたき時に顔・体の外枠が跳ねない
- dark 背景でも chroma-key 残りが見えない

---

## 由来とライセンス

アプリ構造と slicer は tomari-guruguru を元に Dokochan 版へ置き換えています。プログラム部分は MIT License です。詳細は `LICENSE` を参照してください。

Dokochan の画像・生成シート・スライス済みフレーム・検証画像は MIT License の対象外です。詳細は `ASSET_LICENSE.md` を参照してください。
