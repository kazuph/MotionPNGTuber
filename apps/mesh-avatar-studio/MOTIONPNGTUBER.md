# MotionPNGTuber 内の Mesh Avatar Studio

このディレクトリは独立したGitリポジトリやsubmoduleではありません。MotionPNGTuberと一緒に取得できます。

## 起動

Node.js 22.17以上を使います。

```sh
cd apps/mesh-avatar-studio
npm ci
npm run dev -- --port 5186
```

- 操作画面: http://127.0.0.1:5186/player?project=dokochan-bust
- OBSブラウザソース: http://127.0.0.1:5186/obs
- エディター: http://127.0.0.1:5186/

既存の5186番サーバーがある場合は同時起動せず、使用するサーバーを選んでください。

承認済み上半身モデル`dokochan-bust`と初期モデル`dokochan`の原画・rig・描画済み素材を同梱しています。ユーザーのMotionPNGTuberへの保存指示に基づく固定プロジェクトであり、一般の作業用projectsとは区別します。ローカル検証ログ・スクリーンショット・音声・一時ファイルは含めません。

カーソル追従は操作ページ内、感情切替は6種類。マイクは口パク入力だけでスピーカーへ再生しません。Irodoriは設定済みのローカルネットワークサービス（src/server/player.ts）を使用するため、そのネットワークへの接続が必要です。OBS用ページは透明映像のみで、読み上げ音声は操作ブラウザ、マイク音声は別途OBS側で取り込みます。

非表示操作タブからの実音声同期・自然終了、通常UI、実マイクの開始/停止は検証済み。native OBSでの録画確認は未完了です。髪maskの既知の品質残件も保持しています。Cubism形式のLive2Dモデルではありません。

## 出典・利用条件

基盤: https://github.com/shinshin86/mesh-avatar-studio （上流98b14c2、取り込み元7e0d25b4ac3eb982f136ba5cb2ea34c9f432d399）。改変は配信プレイヤー、Irodori、OBS同期、口spriteのみの通常瞬き維持、テスト、依存固定です。コードのMITライセンスはLICENSEを参照してください。

口素材には同梱デモのMiko素材を加工して使用しています。Miko素材はコードのMITとは別条件です。samples/miko-qipao/MIKO_ASSET_TERMS.mdを参照してください。素材単体の再配布許諾と混同しないでください。
