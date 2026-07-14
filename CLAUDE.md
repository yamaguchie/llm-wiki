# LLM-Wiki スキーマ / 運用ルール

このリポジトリは、Andrej Karpathy の [llm-wiki パターン](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
に沿って構築された「compounding knowledge base（積み上がる知識ベース）」です。
LLM エージェント（Claude Code 等）がこのフォルダを読み書き・保守することを前提とします。

## 3 層アーキテクチャ

1. **Raw sources（不変・LLM は読むだけ）** … `sources/` にメタデータ、原本 PDF はリポジトリ直下。
   - `r7syogaisyahukusinotebiki_shusei.pdf` … 文京区『令和7年 障害者福祉のてびき』（内容: 令和7年5月末現在）
   - `extracted.txt` … PDF から抽出した生テキスト（作業用中間物）。
2. **The wiki（LLM が所有する markdown 群）** … `pages/` と `entities/`。要約・エンティティ・概念・相互リンクで構成。
3. **The schema（この設定文書）** … `CLAUDE.md`。構造・記法・ワークフローを定義。

## フォルダ構成

```
llm_wiki/
├── CLAUDE.md            # 本ファイル（スキーマ）
├── README.md           # 人間向けの入口
├── index.md            # 全ページのカタログ（カテゴリ別・1行要約付き）
├── log.md              # 追記式の ingest ログ
├── sources/            # Raw source のメタデータ
│   └── r7-tebiki.md
├── pages/              # 章単位のトピックページ（01〜22 + 一覧表 + 参考資料）
└── entities/           # 横断エンティティ（手帳・窓口・対象者区分）
```

## ページ記法（convention）

- ファイル名は ASCII の kebab-case スラッグ（例 `01-consultation.md`）。タイトル（H1）は日本語。
- 各ページ先頭に YAML frontmatter:
  ```yaml
  ---
  title: 章タイトル
  source: r7-tebiki  # sources/ のキー
  source_pages: "14-30"  # 冊子ページ（PDF内ノンブル）
  category: 相談 | 手帳 | サービス | 手当年金 | 医療 | ...
  updated: 2026-07-13
  ---
  ```
- サービス項目は原則この構造で統一（該当する見出しのみ記載）:
  - **対象** / **内容** / **手当額・助成額** / **手続き** / **問い合わせ**（→ [[key-contacts]] を参照リンク）
- 相互リンクは `[[slug]]` 形式（拡張子なし）。例: `[[02-notebooks]]`, `[[key-contacts]]`。
  未作成のリンク先を書いてもよい（作るべきページのマーカーになる）。
- 金額・電話番号・住所は原本どおり正確に。改変しない。
- 対象者区分の略号: 身=身体 / 知=知的 / 精=精神 / 難=難病 / 児=障害児(18歳未満)。→ [[target-categories]]

## ワークフロー

- **Ingest**: 新しいソースを1件ずつ読み、要約を書き、`index.md` を更新、関連エンティティページを改訂、`log.md` に追記する。
- **Query**: ウィキを検索して回答。有用な発見は新ページとして還元する。
- **Lint**: 定期的に矛盾・陳腐化・孤立ページ（被リンクなし）・欠落クロスリンク・データ欠落を点検する。

## 注意（原本の免責）

- 記載内容は原本の「令和7年5月末現在」。金額・制度は改正されうる。数値を最新化する際は必ず一次情報（文京区／各所管）で確認する。
- 本ウィキは原本の要約・再構成であり、正式な申請判断は各担当窓口の案内に従う。
