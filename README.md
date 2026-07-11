# atcoder-roadmap

[Coji](https://atcoder.jp/users/Coji) に特化した AtCoder 攻略ロードマップと進捗管理。

## 目的

AWS Jam の **AI 無しコンテスト**に慣れるための、地力（速度・典型処理・時間内の実戦力）強化。
レート 800〜900 で停滞している状態を客観データで分析し、個別最適なロードマップを引く。

## 構成

- `research/` — 成績・精進データの客観分析（AtCoder Problems API 等）
- `knowledge/` — upsolve の振り返り（理解していなかった概念と、AC に繋がった気づき）
- `logs/` — 週次サイクル・コンテスト結果の記録（例: `logs/2026-W28.md`）。爆死レビューや弱点実測の材料
- `PLAN.md` — 分析にもとづく攻略ロードマップ（マイルストーン）
- Issues — マイルストーンを分解した進捗管理

## 進め方

CLAUDE.md（`~/git/CLAUDE.md`）の Issue 先行サイクルに従う:
`Issue 起票 → 実装/精進 → レビュー → PR → マージ`
