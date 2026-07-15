# オントロジー駆動ナレッジグラフ構築パイプライン 仕様書（改訂版）

**対象読者**: 実装を担当するClaude Code（本ドキュメントを引き継いだ別セッション）
**前提スタック**: LangGraph / Neo4j / SHACL or GRAPH TYPE / FastAPI / Next.js

---

## 全体ワークフロー

```
🧭 GraphRAG
│
💬 チャット（AgenticSearch + Naive RAG比較）
│
Step 1: データクレンジング
│ 1.1. RAWデータ（PDF）→ テキスト抽出・チャンク分割・埋め込み
│ 1.2. LLM-Wiki → エンティティ/概念ページの生成（Markdown、[[wikilink]]相互参照）
│
Step 2: ドメイン知識の付与
│ 2.1. CQ（質問＋回答ペア）→ LLM生成＋人間レビュー・追加（専用UI）
│ 2.2. オントロジー定義 → 承認済みCQ＋LLM-WikiからLLMがクラス・関係・制約を生成
│ 2.3. オントロジー図 → 生成されたインスタンスKGをSVGフォースグラフで可視化
│
Step 3: ナレッジグラフ（GraphRAG）
│ 3.1. ナレッジグラフ構築 → Neo4jスキーマ＋データ投入＋AgenticSearch
│ 3.2. 検証UI → トレース可視化＋AgenticSearch vs Naive RAG比較
```

---

## Phase 1: データクレンジング

### 1.1. rawデータ（PDF）

**目的**: 原本PDF（例: 障害者福祉のてびき 218ページ）から機械可読なテキストを抽出する。

**実装手順**:
1. PDFから全テキストを抽出（pdfplumber等）
2. スライディングウィンドウでチャンク分割（800文字、150文字オーバーラップ）
3. チャンク＋埋め込みをJSONで保存（チャンク数: 約500、合計: 約230K文字）
4. Naive RAG用のデータ源として保持

**成果物**:
- `rag/pdf_chunks.json`（チャンクテキスト＋ページ番号）
- `rag/pdf_embeddings.json`（各チャンクの埋め込みベクトル）

### 1.2. LLM-Wiki

**目的**: 生データからエージェントが自律的に草稿知識を蓄積する層を作る。

**実装手順**:
1. 生データソース（PDF）を不変ストアとして確保（原本は編集しない）
2. LLMエージェントにMarkdownファイルを生成させる
   - エンティティ/概念ページ（例: `pages/04-allowances-pensions.md`）
   - クロスリファレンス（`[[wikilink]]`形式で概念間の関連を記録）
3. 各ページには生成元の生データへの参照（出典・ページ番号）を必ず付与する
4. LLM-Wikiは正式なオントロジーではなく、あくまで**ステージング層**

**成果物**:
- `/pages/*.md`（エンティティページ、24ファイル）

---

## Phase 2: ドメイン知識の付与

### 2.1. CQ（質問＋回答ペア）の作成

**目的**: 「このシステムは何に答えられるべきか」を**質問＋期待される回答のペア**として明文化する。

**実装手順**:
1. LLM-Wikiの情報を元に、LLMがCQ候補を**質問＋回答ペア**で自動生成（`POST /api/cq/generate`）
   - 例: 質問「身体障害者手帳2級で受けられる手当は？」／回答「心身障害者等福祉手当15,500円/月…」
   - 各CQは疑問形（「〜は？」で終わる）で生成
2. ドメイン有識者が専用管理UI（CQ管理タブ）で以下を実施:
   - 各CQの質問と回答の正しさをチェック
   - 「正しい」「修正必要」「誤り」の3択レビュー（ボタンはカード右下）
   - 不足CQをテキスト入力で追加（質問＋説明＋期待回答＋タイプ選択）
3. 確定したCQ一覧をバージョン管理（`ontology/competency_questions.yaml`）

**CQ管理UI（FastAPI + 静的HTML、`review/main.py`）**:
- ナビ: 2.1 CQ（質問＋回答）／2.2 オントロジー定義／2.3 オントロジー図
- 初期状態: CQ一覧は空（0件）。「🤖 LLMからCQを生成」ボタンでLLM生成後に表示
- CQカード: 質問（❓青字）＋期待される回答（💡緑枠）を左右に並べて表示
- タイプバッジ（色分け+ツールチップ）: 単一参照/多段探索/集約/制約確認/除外条件/併給確認
- 新規追加フォーム: 質問・説明・期待回答＋タイプセレクトボックス

**成果物**:
- `review/main.py`（FastAPI + 静的HTML、CQ/オントロジー管理UI）
- `ontology/competency_questions.yaml`（各CQ: id, question, expected_answer, type, status）

### 2.2. オントロジー定義（LLM自動生成）

**目的**: 承認済みCQの質問＋回答ペアとLLM-Wikiを入力に、LLMがクラス・関係・制約を生成する。

**実装手順**:
1. **2.1 CQ** で承認（status=approved）されたCQの質問＋回答ペアを収集
2. LLM-Wiki（pages/）の内容と合わせてLLM（Gemini）に送信
3. LLMが2段階で生成:
   - **Step 1**: オントロジー定義JSON（クラス・プロパティ・関係・制約）
   - **Step 2**: インスタンスナレッジグラフJSON（kg.json形式: nodes + edges）
4. 生成結果を画面に表示（クラス定義カード／関係定義カード／制約リスト）

**API**: `POST /api/ontology/generate`
- 入力: なし（承認済みCQとLLM-Wikiをサーバー側で収集）
- 出力: `{classes, relationships, constraints, nodes, edges}`
- 状態: `GET /api/ontology/definition` で生成結果を取得

**UI（review/main.py「2.2 オントロジー定義」タブ）**:
- 「🤖 LLMからオントロジーを生成」ボタン
- 未生成時は空状態メッセージ
- 生成後: クラス定義一覧（カード、プロパティ＋型＋必須フラグ）
- 関係定義一覧（from → 関係名 → to、説明＋プロパティ）
- 制約一覧（説明＋出典）

### 2.3. オントロジー図（SVGフォースグラフ）

**目的**: 生成されたオントロジーのインスタンスKGを視覚的に確認できるグラフを提供する。

**実装手順**:
1. `GET /api/ontology/generated/kg` からkg.jsonを取得
2. SVGフォース有向グラフで可視化（`review/main.py`内のJavaScriptで描画）
3. 機能要件:
   - ノードはラベル種別ごとに色分け
   - ノードクリックでプロパティ表示（右パネル）
   - フォースレイアウト（事前収束、静的に表示）

**API**: `GET /api/ontology/generated/kg` → `{nodes, edges}`

---

## Phase 3: ナレッジグラフ（GraphRAG）

### 3.1. ナレッジグラフ構築

**目的**: 承認済みオントロジー＋業務ルールをNeo4jに実装し、検索可能なナレッジグラフを構築する。

**実装手順**:
1. Neo4jスキーマ定義
   - ノードラベル（Service, ServiceCategory, TargetCategory, Notebook, Contact, Reference, Facility）
   - サブラベル（Allowance :: Service, MedicalAid :: Service, AssistiveDevice :: Service, TransportBenefit :: Service）
   - 関係と端点制約（HAS_CATEGORY, TARGETS, REQUIRES, ADMINISTERED_BY, DEFINED_BY, MUTUALLY_EXCLUSIVE_WITH, RELATED_TO, PROVIDED_AT）
2. 制約の実装
   - 一意制約（idベース）
   - プロパティ存在制約（NOT NULL）
   - インデックス（検索性能）
   - GRAPH TYPE（Neo4j 2026.02 Preview、書込時トランザクション検証）
3. データ投入: kg.json → kg.cypher（MERGE文） → Neo4j（Bolt経由）
4. AgenticSearch: LangGraph StateGraph（Planner → Retrieval → Critic → Answer）
   - ベクトル入口（Gemini埋め込み）＋グラフ探索の2段ハイブリッド
   - サポート: Anthropic Claude / Google Gemini
5. Naive RAG（比較用）: PDFチャンク + ベクトル類似度Top5 + LLM回答

**成果物**:
- `neo4j/docker-compose.yml`（Neo4j + APOC）
- `neo4j/create_graph_type.cypher`（スキーマDDL）
- `neo4j/load_kg.py`（データローダー＋書込時検証）
- `agent/agent.py`（Gemini agentic loop）
- `agent/langgraph_app.py`（LangGraph版）
- `agent/naive_rag.py`（PDF直接RAG比較用）

### 3.2. 検証UI（チャット画面）

**目的**: AgenticSearchのトレースとNaive RAGの比較表示を提供する。

**実装手順**:
1. AgenticSearchのトレース可視化
   - 各ステップをリアルタイムストリーミング表示（ステップ番号バッジ①②③...）
   - 参照したノード＋エッジを右パネルのグラフ上にハイライト
2. AgenticSearch vs Naive RAGの比較表示
   - 同一質問に対する両方式の回答を左右のカードに並べて比較
   - 出典リンク（Wikiページ / PDFページ）
3. ナビゲーションメニュー
   - Step1: 1.1 RAWデータ / 1.2 LLM-Wiki
   - Step2: 2.1 CQ / 2.2 オントロジー定義 / 2.3 オントロジー図
   - Step3: 3.1 ナレッジグラフ / 3.2 検証

**成果物**:
- `chat/index.html`（AgenticSearch + Naive RAG比較UI、上部ナビ付き）

---

## 三層ガバナンスとの対応関係

| ガバナンス層 | 対応フェーズ | 変更経路 |
|---|---|---|
| 語彙・構造変更（エンジニアのみ） | Phase2.2 クラス・関係定義、Phase3.1 GRAPH TYPE定義 | Git/CI/CD経由、PRレビュー |
| 参照データ（直接CRUD） | Phase3.1 Instance層（スキーマ範囲外の柔軟部分） | 直接CRUD、スキーマ検証あり |
| 業務ルール（承認フロー） | Phase2.1 CQレビュー、Phase3.2 検証UI | フォームUI＋承認フロー |

---

## 未検証・要確認事項

1. GRAPH TYPEの正式GA時期とPreview版との構文差分
2. GRAPH TYPEが値レンジ制約（sh:minInclusive相当）をどこまでサポートするか
3. マルチエージェントオントロジー生成におけるLLM呼び出しコスト
4. AgenticSearchのレイテンシ—全クエリに適用せず、単純な質問は固定パイプライン、複雑な質問のみAgenticSearchに振り分けるハイブリッド運用を検討