# ナレッジグラフ構築パイプライン — アーキテクチャ・ディレクトリ構成

最終更新: 2026-07-15

## アーキテクチャ

```
ブラウザ → nginx(80) → FastAPI(8790) → Neo4j(7687)
                              │
                              ├─ /api/* (REST API)
                              ├─ /review/* (管理UI)
                              └─ / (チャットUI)
```

| コンポーネント | 技術 | 役割 |
|---|---|---|
| フロントエンド | 静的HTML + Vanilla JS | チャットUI + 管理UI |
| バックエンド | FastAPI（`pipeline/server/main.py`） | 全API + 静的配信 |
| LLM | Google Gemini 3.5 Flash | QA生成 / Planner / Critic / Answer |
| ベクトル埋め込み | Gemini (`GEMINI_EMBED_MODEL`) | チャンク埋め込み + ノード埋め込み |
| グラフDB | Neo4j 2025.10 Community | ナレッジグラフ格納 |
| リバースプロキシ | nginx:alpine | 80→8790 転送 + 静的配信 |

## ディレクトリ構成

```
pipeline/
├── server/main.py           # ★ 統合FastAPIサーバー（全API + 静的配信 + チャット + レビューUI）
├── server/llm_utils.py      # LLM呼び出しヘルパー
├── server/kg_utils.py       # KG/Neo4jヘルパー
│
├── chat/index.html          # 💬 チャットUI（AgenticSearch + Naive RAG比較）
│
├── agent/                   # 🤖 AIエージェント
│   ├── agent.py             #   Gemini AgenticSearch（Planner/Critic/Answer、ストリーミング）
│   ├── naive_rag.py         #   Naive RAG（PDFチャンク直接検索）
│   ├── langgraph_app.py     #   LangGraph版（Anthropic/Gemini両対応）
│   └── gemini_client.py     #   google-genai ラッパー
│
├── step1_data/              # Step 1: データクレンジング
│   ├── raw/                 # 1.1 RAWデータ（PDFチャンク+埋め込み）
│   │   ├── chunks.json      #   PDFチャンク（800文字/150オーバーラップ）
│   │   └── raw_embeddings.json  #   Gemini埋め込み（3072次元）
│   └── wiki/                # 1.2 LLM-Wiki（生成エンティティページ 62件）
│
├── step2_domain/            # Step 2: ドメイン知識の付与
│   ├── qa/                  # 2.1 QA（質問＋回答ペア、サーバー内部管理）
│   └── ontology/            # 2.2 オントロジー定義 + 2.3 オントロジー図
│       ├── ontology.md
│       └── competency_questions.yaml
│
├── step3_kg/                # Step 3: ナレッジグラフ
│   ├── data/                # 3.1 KGデータ（56ノード/186エッジ）
│   └── neo4j/               # 3.1 Neo4j設定
│
├── rag/                     # ノード埋め込み（AgenticSearch用）
│
├── .env / .env.example      # APIキー
├── docker-compose.yml       # Docker Compose
├── Dockerfile               # アプリイメージ
├── nginx.conf               # nginx設定
└── README.md
```

## URLマッピング

| URL | 画面 |
|---|---|
| `/` | チャット |
| `/review/raw` | 1.1 RAWデータ |
| `/review/llmwiki` | 1.2 LLM-Wiki |
| `/review/cq` | 2.1 QA |
| `/review/ontology-def` | 2.2 オントロジー定義 |
| `/review/ontology-graph` | 2.3 オントロジー図 |
| `/review/kg` | 3.1 ナレッジグラフ |
| `/review/validation` | 3.2 QAのAgent回帰テスト |

## サーバー起動

```powershell
cd pipeline
py -3.14 -m uvicorn server.main:app --host 127.0.0.1 --port 8790
```

## LLM設定（`pipeline/.env`）

```env
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.5-flash
GEMINI_EMBED_MODEL=gemini-embedding-001
```

| 用途 | モデル |
|---|---|
| Chat / Planner / Critic / Answer | `GEMINI_MODEL` |
| 埋め込み（RAW抽出・ベクトル検索） | `GEMINI_EMBED_MODEL` |