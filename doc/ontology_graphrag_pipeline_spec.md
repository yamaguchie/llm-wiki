# オントロジー駆動ナレッジグラフ構築パイプライン 仕様書（最終版）

**対象読者**: 実装を担当するClaude Code
**前提スタック**: FastAPI (統合サーバー) / LangGraph / Neo4j / Gemini 3.5 Flash
**サーバー**: 単一ポート `8790` に統合（`py -m uvicorn review.main:app --port 8790`）

---

## 全体ワークフロー

```
🧭 GraphRAG
│
💬 チャット（AgenticSearch + Naive RAG比較） — http://127.0.0.1:8790/
│
Step 1: データクレンジング
│ 1.1. RAWデータ — PDFアップロード→抽出→チャンク→埋め込み（チャンク一覧表示付き）
│ 1.2. LLM-Wiki — チャンクからLLMがエンティティページを生成（標準Markdownリンク）
│
Step 2: ドメイン知識の付与
│ 2.1. QA（質問＋回答ペア） — LLM生成＋人間レビュー・3択判定・新規追加
│ 2.2. オントロジー定義 — QA＋LLM-WikiからLLMがクラス・関係・制約を生成（進捗バー付き非同期）
│ 2.3. オントロジー図 — 定義からSVGフォースグラフで可視化
│
Step 3: ナレッジグラフ（GraphRAG）
│ 3.1. ナレッジグラフ構築 — LLM-Wikiから抽出→Neo4j投入（進捗バー付き非同期）
│ 3.2. QAのAgent回帰テスト — 全QA検証＋原因解析＋失敗QAからオントロジー修正
```

---

## サーバー構成

| 項目 | 仕様 |
|---|---|
| ポート | **8790**（単一ポートに統合） |
| 起動コマンド | `py -3.14 -m uvicorn review.main:app --host 127.0.0.1 --port 8790` |
| 作業ディレクトリ | `graphrag/` |
| URL | `http://127.0.0.1:8790/` |

### URLマッピング

| URL | 画面 |
|---|---|
| `/` | チャット画面（AgenticSearch + Naive RAG比較） |
| `/review/raw` | 1.1 RAWデータ |
| `/review/llmwiki` | 1.2 LLM-Wiki |
| `/review/cq` | 2.1 QA |
| `/review/ontology-def` | 2.2 オントロジー定義 |
| `/review/ontology-graph` | 2.3 オントロジー図 |
| `/review/kg` | 3.1 ナレッジグラフ |
| `/review/validation` | 3.2 QAのAgent回帰テスト |

---

## Step 1: データクレンジング

### 1.1. RAWデータ（PDFアップロード→抽出→表示）

**目的**: 業務マニュアルPDFをアップロードし、テキスト抽出→チャンク分割→埋め込み生成を一括実行する。
抽出結果は **① ナイーブRAG**（チャット比較用）と **② LLM-Wiki**（エンティティページ生成）の両方で利用される。

**実装**:
- PDFアップロード: ドラッグ&ドロップ + ファイル選択
- テキスト抽出: pdfplumber
- チャンク分割: 800文字 / 150文字オーバーラップ
- 埋め込み: Gemini (`GEMINI_EMBED_MODEL`、.envから読み込み、デフォルト `gemini-embedding-001`)
- 保存: `rag/pdf_chunks.json` + `rag/pdf_embeddings.json`
- チャンク一覧: 抽出後、各チャンクをページ番号・プレビュー付きで表示、クリックで全文モーダル表示

**API**:
- `POST /api/raw/upload` — PDFアップロード（非同期、進捗バー付き）
- `GET /api/raw/status` — 抽出済みかどうか
- `GET /api/raw/chunks` — チャンク一覧（プレビュー付き）
- `GET /api/raw/chunk/{idx}` — チャンク全文

### 1.2. LLM-Wiki

**目的**: RAWデータのチャンクからLLMがエンティティ/概念ページを生成する。

**実装**:
- RAWチャンクを10バッチずつLLMに投入
- 標準Markdownリンク形式（`[text](page.md)`、`[[wikilink]]`不使用）
- YAML frontmatterに `page: N` で出典ページ番号を記録
- 全情報の欠落を防止（全チャンクをバッチ処理）
- 生成後は各ページへのリンク一覧表示

**API**:
- `POST /api/llmwiki/generate` — ページ生成（非同期、進捗バー付き）
- `GET /api/llmwiki/status` — 生成済みページ一覧

---

## Step 2: ドメイン知識の付与

### 2.1. QA（質問＋回答ペア）

**目的**: 質問＋期待される回答のペアを作成し、ドメイン有識者がレビューする。

**実装**:
- LLM-WikiからLLMがQAを自動生成（質問＋期待回答＋タイプ）
- 初期状態は空（0件）。「🤖 LLMからQAを生成」ボタンで生成
- QAカード: 質問（❓青字）＋期待回答（💡緑枠）を左右に並べて表示
- 3択レビュー: 「正しい」「修正必要」「誤り」
- タイプ: 📖単一参照 / 🔗多段探索 / 📋一覧取得 / ⚠️条件確認（除外条件・併給確認を統合）
- 新規追加フォーム: 質問・説明・期待回答＋タイプ選択
- 一括操作: 全QA承認 / 全QA削除

**API**:
- `POST /api/cq/generate` — LLMがQAを生成
- `POST /api/cq/approve-all` — 全QA一括承認
- `POST /api/cq/clear` — 全QA削除
- `POST /api/cq/delete` — 個別QA削除
- `POST /api/review` — QAレビュー（承認/修正/却下）

### 2.2. オントロジー定義

**目的**: 承認済みQA＋LLM-WikiからLLMがクラス・関係・制約を生成する。

**実装**:
- 非同期実行 + 進捗バー（0-100%）
- 2段階生成: ① オントロジー定義 → ② インスタンスKG
- QA充足カバレッジ表示: 反復修正の推移グラフ
- 制約事項は **クラス.プロパティ** に紐付け
- クラス定義テーブル・関係定義テーブル・制約テーブル
- Wiki→定義 / QAで反復修正 / 一括生成 の3方式
- 制約の永続化: `neo4j/generated_constraints.cypher` + `agent/active_constraints.json`

**API**:
- `POST /api/ontology/generate` — 一括生成（非同期）
- `POST /api/ontology/bootstrap` — Wikiから定義生成
- `POST /api/ontology/refine-from-cqs` — QAで反復修正
- `GET /api/ontology/definition` — 生成結果取得
- `GET /api/ontology/coverage` — QA充足カバレッジ
- `POST /api/ontology/fix-from-validation` — 検証結果から修正

### 2.3. オントロジー図

**目的**: 生成されたオントロジー定義をSVGフォースグラフで可視化する。

**実装**:
- オントロジー定義（クラス・関係）からノード＋エッジを生成
- 定義済みクラス=青色、参照のみのクラス=グレー
- ノードクリックでプロパティ表示
- フォースレイアウト（事前収束、静的表示）

---

## Step 3: ナレッジグラフ（GraphRAG）

### 3.1. ナレッジグラフ構築

**目的**: LLM-Wikiから実体を抽出し、Neo4jに投入する。

**実装**:
- 非同期実行 + 進捗バー（0-100%）
- 抽出→Cypher出力→Neo4j投入を一括実行
- Neo4jビューと抽出JSONビューの切替表示
- SVGフォースグラフでノード/エッジを可視化
- ノードクリックで全プロパティ＋入出力エッジを表示

**API**:
- `POST /api/kg/extract` — 抽出＋Neo4j投入（非同期）
- `GET /api/kg/neo4j` — Neo4jの実データ取得
- `GET /api/kg/extracted` — 最新抽出JSON取得
- `GET /api/kg/cypher` — Cypherファイルダウンロード

**チャット（AgenticSearch）**:
- AgenticSearch: Planner(LLM) → Retrieval(Vector+Graph) → Critic(LLM) → Answer(LLM)
- Naive RAG: PDFチャンク + ベクトル類似度Top5 + LLM回答
- 比較表示: AgenticSearch vs Naive RAGを左右カードに並べて表示
- 右パネル: 参照ノードをグラフ上にハイライト、ステップ番号バッジ付き
- ストリーミング: NDJSONでリアルタイムトレース表示
- Wikiリンク: 回答内の `pages/xxx.md` を自動リンク化

### 3.2. QAのAgent回帰テスト

**目的**: 承認済みQAを元に、ナイーブRAG + KGのみ + KG+Wiki補完の3方式で回答し、正誤判定する。

**実装**:
- QAごとに3方式の回答を一括生成し、LLMが正誤判定
- サマリー: 正解率＋正解+部分率のパーセント表示
- 失敗QAの原因解析: ノード欠落 / プロパティ不足 / 値誤り
- 「🔧 失敗QAからオントロジー修正」ボタン: 原因解析結果をLLMに渡してオントロジーを修正

**API**:
- `POST /api/validation/run/{cq_id}` — 1件検証
- `POST /api/validation/run-all` — 全件検証
- `GET /api/validation/results` — 検証結果一覧
- `GET /api/validation/cqs` — 承認済みQA一覧
- `POST /api/ontology/fix-from-validation` — 検証結果から修正

---

## LLMモデル設定

`.env` ファイルで設定:

```env
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.5-flash
GEMINI_EMBED_MODEL=gemini-embedding-001
```

| 用途 | モデル |
|---|---|
| チャット・Planner・Critic・Answer | `GEMINI_MODEL` |
| 埋め込み（ベクトル検索・RAW抽出） | `GEMINI_EMBED_MODEL` |

---

## ディレクトリ構成

```
graphrag/
├── review/main.py          # ★ 統合サーバー（全API + 静的配信 + チャット + レビューUI, 3076行）
├── chat/index.html         # チャット画面（AgenticSearch + Naive RAG比較）
├── agent/
│   ├── agent.py            # Gemini agentic loop（ストリーミング対応）
│   ├── naive_rag.py        # Naive RAG（PDF直接検索）
│   ├── gemini_client.py    # google-genai ラッパー
│   └── active_constraints.json  # 生成された制約（AIエージェント用）
├── neo4j/
│   ├── docker-compose.yml
│   ├── create_graph_type.cypher
│   ├── load_kg.py
│   └── generated_constraints.cypher  # 生成された制約（Neo4j用）
├── rag/
│   ├── pdf_chunks.json     # 抽出チャンク
│   └── pdf_embeddings.json # チャンク埋め込み
└── .env                    # APIキー・モデル設定
```

---

## 未検証・要確認事項

1. GRAPH TYPEの正式GA時期とPreview版との構文差分
2. GRAPH TYPEが値レンジ制約（sh:minInclusive相当）をどこまでサポートするか
3. マルチエージェントオントロジー生成におけるLLM呼び出しコスト
4. AgenticSearchのレイテンシ—全クエリに適用せず、単純な質問は固定パイプライン、複雑な質問のみAgenticSearchに振り分けるハイブリッド運用を検討