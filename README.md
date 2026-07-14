# 障害者福祉のてびき LLM-Wiki（文京区 令和7年）

文京区『令和7年 障害者福祉のてびき』を、Andrej Karpathy の
[llm-wiki パターン](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
に沿って構造化した知識ベースです。LLM エージェント（Claude Code 等）が読み書き・保守することを前提としています。

## 使い方

- **人が読む/探す** → [`index.md`](index.md)（全ページのカタログ）から目的の章へ。概要は [`pages/00-overview.md`](pages/00-overview.md)。
- **LLM に質問する** → このフォルダを Claude Code 等に読ませ、`index.md` と関連ページを参照させて回答させます。良い回答は新しいページとして還元できます。
- **窓口の連絡先を引く** → [`entities/key-contacts.md`](entities/key-contacts.md)。

## 構成

```
CLAUDE.md            スキーマ・運用ルール（記法/ワークフロー）
index.md             全ページのカタログ
log.md               ingest ログ
sources/r7-tebiki.md 一次ソースのメタデータ
pages/               章ページ（00概要, 00一覧表, 01〜22, 99参考資料）
entities/            横断エンティティ（手帳・窓口・対象者区分）
```

- 相互リンクは `[[slug]]` 形式（Obsidian 互換）。
- 原本 PDF（`r7syogaisyahukusinotebiki_shusei.pdf`）と抽出テキスト（`extracted.txt`）はソース層で、Wiki からは参照のみ行い改変しません。

## 注意

- 内容は原本の **令和7年5月末現在**。金額・制度は改正されうるため、正式な申請判断は各担当窓口の最新案内に従ってください。
- 一部の縦組み表（障害程度別対象事業一覧表・身体障害者等級表）は抽出上の制約から要約にとどめ、詳細は原本参照としています（該当ページに注記）。

## メンテナンス（Karpathy パターン）

- **Ingest**: 新しい版の PDF や通知が出たら、1件ずつ読み、該当ページを更新し `log.md` に追記。
- **Lint**: 定期的に矛盾・陳腐化・孤立ページ・欠落リンクを点検（`CLAUDE.md` 参照）。
