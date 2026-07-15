# GraphRAG パイプライン（障害者福祉ドメイン）

`ontology_graphrag_pipeline_spec.md` を、既存の **LLM-Wiki**（文京区 令和7年 障害者福祉のてびき）を生データ層として具体化したもの。
Phase1（LLM-Wiki）は完了済み。本ディレクトリは **Phase2〜6** を実装する。

## ディレクトリ

```
graphrag/
├── ontology/
│   ├── competency_questions.yaml   # CQ一覧（LLM生成＋人間レビュー後）
│   └── ontology.md                 # クラス/関係/制約/GRAPH TYPE/自然言語逆翻訳
├── graph/
│   ├── kg.json                     # 56ノード/186エッジのナレッジグラフ
│   ├── kg.cypher                   # Neo4j MERGE文（validate_kg.pyが生成）
│   ├── validate_kg.py              # 参照/端点/値制約検証＋CQ回帰＋Cypher生成
│   └── validation_report.txt       # 検証結果
├── rag/                            # 埋め込み
│   ├── build_vectors.py            # オフライン埋め込み(char n-gram TF-IDF)
│   ├── embed_gemini.py             # Gemini神経埋め込み(3072d)
│   ├── vectors.json / embeddings.json           # ノード埋め込み（生成物）
│   └── pdf_chunks.json / pdf_embeddings.json    # Naive RAG用: PDFチャンク＋埋め込み
├── agent/                          # エージェント＋バックエンド
│   ├── config.py / check_key.py    # .envローダ・キー確認
│   ├── gemini_client.py            # google-genai ラッパ（embed/chat/chat_json）
│   ├── agent.py                    # Gemini agentic loop（Planner/Critic/Answer＋ベクトル+グラフ, ストリーミング）
│   ├── naive_rag.py                # 素のRAG（PDFチャンク検索）＝比較用ベースライン
│   ├── langgraph_app.py            # LangGraph StateGraph版（Anthropic/Gemini）
│   └── server.py                   # HTTPバックエンド（/api/ask, /api/rag, /review/プロキシ, 静的配信）
├── review/
│   └── main.py                     # レビューUI(FastAPI): CQ/オントロジー生成＋人間レビュー（1950行）
├── neo4j/                          # Neo4j実ロード＋GRAPH TYPE書込時検証
│   └── docker-compose.yml / create_graph_type.cypher / load_kg.py / README.md
├── chat/
│   └── index.html                  # 質問チャット（ストリーミング/グラフ描画/上部ナビ/Gemini・NaiveRAG切替）
├── serve-all.ps1 / stop-all.ps1    # 両サーバー起動・停止（推奨）
├── serve-gemini.ps1 / serve.ps1 / serve_md.py / stop.ps1   # 個別・補助
└── .env / .env.example             # Gemini/Anthropicキー（.envはGit管理外）
```

## 起動・停止

このデモは **2つのサーバー**で動く。**両方とも `py -3.14` で起動**すること。

| サーバー | 実体 | ポート | 役割 |
|---|---|---|---|
| チャット/Geminiバックエンド | `agent/server.py` | 8790 | チャット配信・`POST /api/ask`・`/review/` プロキシ |
| レビューUI（FastAPI） | `uvicorn review.main:app` | 8789 | CQ管理・オントロジー生成・人間レビュー |

### いちばん簡単（両方まとめて）

```powershell
powershell -ExecutionPolicy Bypass -File graphrag\serve-all.ps1 -Open
powershell -ExecutionPolicy Bypass -File graphrag\stop-all.ps1
```

### 個別に起動する場合

```powershell
# レビューUI (8789) — 必ず py -3.14 で
py -3.14 -m uvicorn review.main:app --host 127.0.0.1 --port 8789
# チャット / Geminiバックエンド (8790)
py -3.14 graphrag\agent\server.py 8790 <リポジトリ直下>
```

### URL一覧

| 画面 | URL |
|---|---|
| チャット（AgenticSearch + Naive RAG比較） | `http://127.0.0.1:8790/` |
| レビューUI（CQ管理/オントロジー定義/オントロジー図） | `http://127.0.0.1:8790/review/`（直接: `http://127.0.0.1:8789/`） |
| Neo4j Browser | `http://localhost:7474` (neo4j/password123) |

## ワークフロー

### Step 1: データクレンジング（準備済み）

PDFから抽出済み。`rag/pdf_chunks.json`（706KB, 478チャンク）と`rag/pdf_embeddings.json`（21MB）が既存。

### Step 2: ドメイン知識の付与

#### 2.1 CQ（質問＋回答ペア）

1. `http://127.0.0.1:8789/` を開く（デフォルトはCQ管理タブ）
2. 「🤖 LLMからCQを生成」ボタンをクリック → LLMがLLM-Wikiから質問＋回答ペアを生成
3. 各CQをレビュー: 「正しい」「修正必要」「誤り」の3択
4. 不足CQは下部フォームから手動追加（質問・説明・期待回答・タイプ選択）
5. 承認済みCQ（status=approved）のみ次ステップへ

#### 2.2 オントロジー定義

1. 「2.2 オントロジー定義」タブを開く
2. 「🤖 LLMからオントロジーを生成」ボタンをクリック
3. LLMが2段階で生成: ① クラス・関係・制約の定義 → ② インスタンスKG（kg.json）
4. 生成結果をカード形式で表示

#### 2.3 オントロジー図

1. 「2.3 オントロジー図」タブを開く（2.2で生成後）
2. SVGフォースグラフでナレッジグラフを可視化
3. ノードクリックでプロパティ表示

### Step 3: ナレッジグラフ探索

`http://127.0.0.1:8790/` でチャットを使用:
- 「🤖 Geminiモード」: AgenticSearch（KG探索、LLM Planner/Critic/Answer、ストリーミングトレース）
- 「📄 Naive RAG比較」: PDF直接検索との比較表示
- 右パネル: 参照ノードをハイライト表示

## AgenticSearch の動作

```
①質問 → ②Planner(LLM)分解 → ③Retrieval(ベクトル入口+グラフ探索)
→ ④Critic(LLM)評価 → ⑤不十分なら再検索 → ⑥Answer(LLM)回答
```

- トレースはリアルタイムストリーミング表示（ステップ番号バッジ①②③...）
- 右パネルのKGグラフに参照ノードをハイライト
- Naive RAG比較で同一質問のPDF直接検索結果と左右比較

## 本デモの範囲と本番との差分

| 項目 | 本デモ | 仕様書の本番構成 |
|---|---|---|
| プランナー/クリティック/回答 | オフライン版=ルールベース／Geminiモード=実LLM | LangGraph化済み（`agent/langgraph_app.py`） |
| グラフDB | JSON＋インメモリ探索 | Neo4j + GRAPH TYPE（`neo4j/`） |
| ベクトル検索 | オフラインTF-IDF／Gemini埋め込み(3072d) | — |
| スキーマ検証 | `validate_kg.py` | GRAPH TYPE or SHACL+neosemantics |
| 人間レビューUI | FastAPI + 静的HTML（CQ/オントロジー管理） | 同左＋承認フローの永続化・監査 |
| LLM生成オントロジー | Gemini生成（`/api/ontology/generate`）→ 人間レビュー | マルチエージェント(Domain/Manager/Coder/QA) |

## 値の扱い（重要）

- 金額・年齢閾値・時間上限・対象等級は**原本＝令和7年5月末時点**の値。本番投入前にドメインエキスパートが一次情報で承認すること。
- `chat/index.html` は手編集済み（ストリーミング表示・グラフ描画・ナビ等）。`build_chat.py` を再実行するとこれらが巻き戻るので注意。