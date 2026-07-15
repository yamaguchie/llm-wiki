# ナレッジグラフ構築パイプライン（障害者福祉ドメイン）

`ontology_graphrag_pipeline_spec.md` に基づき、文京区令和7年 障害者福祉のてびき（PDF）を入力に、
ナレッジグラフの構築 + AgenticSearch + Naive RAG 比較を実現するパイプライン。

## ディレクトリ構成

```
pipeline/
├── server/                             # ★ 統合FastAPIサーバー（ポート8790）
│   ├── main.py                         #   全API + 静的配信 + チャット + レビューUI (3260行)
│   ├── llm_utils.py                    #   LLM呼び出しヘルパー
│   └── kg_utils.py                     #   KG/Neo4jヘルパー
│
├── chat/                               # 💬 チャットUI
│   └── index.html                      #   AgenticSearch + Naive RAG比較（上部ナビ付き）
│
├── agent/                              # 🤖 AIエージェント
│   ├── agent.py                        #   Gemini AgenticSearch（Planner/Critic/Answer）
│   ├── naive_rag.py                    #   Naive RAG（PDFチャンク直接検索）
│   ├── langgraph_app.py                #   LangGraph版（Anthropic/Gemini）
│   ├── gemini_client.py                #   google-genai ラッパー
│   ├── config.py / check_key.py        #   .envローダー + キー確認
│   └── active_constraints.json         #   AIエージェント用制約（オントロジー生成時に自動更新）
│
├── step1_data/                         # Step 1: データクレンジング
│   ├── raw/                            #   1.1 RAWデータ
│   │   ├── chunks.json                 #     PDFチャンク（800文字/150オーバーラップ）
│   │   └── raw_embeddings.json         #     Gemini埋め込み（3072次元）
│   └── wiki/                           #   1.2 LLM-Wiki
│       └── *.md                        #     生成されたエンティティページ（62ファイル）
│
├── step2_domain/                       # Step 2: ドメイン知識の付与
│   ├── ontology/                       #   2.2+2.3 オントロジー定義 + 図
│   │   ├── ontology.md                 #     クラス/関係/制約/逆翻訳
│   │   └── competency_questions.yaml   #     QA一覧
│   └── qa/                             #   2.1 QA（質問＋回答ペア）
│       └── （サーバー内部で管理）
│
├── step3_kg/                           # Step 3: ナレッジグラフ
│   ├── data/                           #   3.1 KGデータ
│   │   ├── kg.json                     #     ナレッジグラフ（56ノード / 186エッジ）
│   │   ├── kg.cypher                   #     Neo4j MERGE文
│   │   └── validate_kg.py              #     整合性検証 + CQ回帰テスト
│   └── neo4j/                          #   3.1 Neo4j設定
│       ├── docker-compose.yml          #     Neo4j単体起動用
│       ├── create_graph_type.cypher    #     スキーマDDL
│       ├── load_kg.py                  #     データローダー + 書込時検証
│       ├── generated_constraints.cypher #    生成された制約
│       └── README.md
│
├── rag/                                # ノード埋め込み（AgenticSearch用）
│   └── （`embed_gemini.py` で生成）
│
├── .env                                # APIキー（GEMINI_API_KEY等）
├── .env.example                        # .envテンプレート
├── docker-compose.yml                  # ★ 本番用: Neo4j + アプリ + nginx
├── Dockerfile                          # アプリ用Dockerイメージ
├── nginx.conf                          # nginxリバースプロキシ設定
├── serve-all.cmd / serve-all.ps1       # 起動スクリプト
└── README.md                           # 本ファイル
```

## 起動・停止

**単一サーバー（ポート8790）** で全画面 + 全APIを提供:

```powershell
cd pipeline
py -3.14 -m uvicorn server.main:app --host 127.0.0.1 --port 8790
```

| 画面 | URL |
|---|---|
| チャット（AgenticSearch + Naive RAG） | `http://127.0.0.1:8790/` |
| 1.1 RAWデータ | `http://127.0.0.1:8790/review/raw` |
| 1.2 LLM-Wiki | `http://127.0.0.1:8790/review/llmwiki` |
| 2.1 QA | `http://127.0.0.1:8790/review/cq` |
| 2.2 オントロジー定義 | `http://127.0.0.1:8790/review/ontology-def` |
| 2.3 オントロジー図 | `http://127.0.0.1:8790/review/ontology-graph` |
| 3.1 ナレッジグラフ | `http://127.0.0.1:8790/review/kg` |
| 3.2 QAのAgent回帰テスト | `http://127.0.0.1:8790/review/validation` |

## ワークフロー

| 順序 | Step | 操作 |
|---|---|---|
| 1 | 1.1 RAWデータ | PDFをアップロード → 抽出・チャンク・埋め込み（1.2+2.1自動クリア） |
| 2 | 1.2 LLM-Wiki | 「🤖 LLM-Wikiを生成」（2.1自動クリア） |
| 3 | 2.1 QA | 「🤖 LLMからQAを生成」→ 質問＋回答ペアをレビュー・承認 |
| 4 | 2.2 オントロジー定義 | 「🤖 一括生成」→ クラス・関係・制約を生成 |
| 5 | 2.3 オントロジー図 | 定義をSVGフォースグラフで確認 |
| 6 | 3.1 ナレッジグラフ | 「🤖 LLM-Wikiから抽出→Neo4j」→ KG投入 |
| 7 | 3.2 QAのAgent回帰テスト | 「▶ 全件検証」→ 正誤判定 + 失敗QAから修正 |

💡 チャット画面の「▶ 全行程実行」ボタンで Step 1.2〜3.2 を一括実行可能。

## 本番デプロイ（Docker）

```powershell
# Nginx(80) + アプリ(8790) + Neo4j(7687) を一括起動
docker compose up -d

# 停止
docker compose down
```

## 値の扱い（重要）

- 金額・年齢閾値・時間上限・対象等級は**原本＝令和7年5月末時点**の値。本番投入前にドメインエキスパートが一次情報で承認すること。