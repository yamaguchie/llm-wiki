# GraphRAG パイプライン（障害者福祉ドメイン）

`ontology_graphrag_pipeline_spec.md` を、既存の **LLM-Wiki**（文京区 令和7年 障害者福祉のてびき）を生データ層として具体化したもの。
Phase1（LLM-Wiki）は完了済みなので、本ディレクトリは **Phase2〜6** を実装する。

## 仕様書Phaseとの対応

| Phase | 仕様書 | 本リポジトリの成果物 | 状態 |
|---|---|---|---|
| 1 | 生データ→LLM-Wiki | `../pages/`, `../entities/`, `../CLAUDE.md`（ステージング層） | ✅ 既存 |
| 2 | コンピテンシー質問(CQ) | [`ontology/competency_questions.yaml`](ontology/competency_questions.yaml)（CQ01–CQ12） | ✅ |
| 3 | オントロジー自動生成＋逆翻訳＋人間レビュー | [`ontology/ontology.md`](ontology/ontology.md)（クラス/関係/制約＋自然言語逆翻訳＋`human_required`フラグ） | ✅ |
| 4 | プロパティグラフ化(Neo4j+GRAPH TYPE) | `ontology.md`のGRAPH TYPEブロック ＋ [`graph/kg.json`](graph/kg.json) ＋ 生成物 [`graph/kg.cypher`](graph/kg.cypher) | ✅ |
| 3-2/5 | 整合性チェック／CQ回帰テスト | [`graph/validate_kg.py`](graph/validate_kg.py) → [`graph/validation_report.txt`](graph/validation_report.txt) | ✅ |
| 5 | Graph RAG（ベクトル＋多ホップ） | [`rag/build_vectors.py`](rag/build_vectors.py) → `rag/vectors.json`（char n-gram TF-IDF埋め込み）。chatに**ベクトル入口→グラフ探索の2段ハイブリッド**を実装 | ✅（オフライン埋め込み版） |
| 6 | Agentic Search（反復検索ループ） | [`chat/index.html`](chat/index.html)（質問チャット）＋生成器 [`chat/build_chat.py`](chat/build_chat.py) | ✅ |

## ディレクトリ

```
graphrag/
├── ontology/
│   ├── competency_questions.yaml   # Phase2: CQ（ID・型・グラフパターン・出典・review）
│   └── ontology.md                 # Phase3/4: クラス/関係/制約/GRAPH TYPE/逆翻訳
├── graph/
│   ├── kg.json                     # Phase4: インスタンス（出典付き・単一の真実源）
│   ├── kg.cypher                   # Phase4: Neo4j MERGE（validate_kg.pyが生成）
│   ├── validate_kg.py              # Phase3-2/5: 検証＋CQ回帰＋Cypher生成
│   └── validation_report.txt       # 検証結果（生成物）
├── neo4j/                          # ★ Neo4j実ロード＋GRAPH TYPE書込時検証
│   ├── docker-compose.yml
│   ├── create_graph_type.cypher
│   ├── load_kg.py
│   └── README.md
├── agent/                          # Phase6: agentサーバ＋★LangGraph版
│   ├── agent.py                    #   Gemini agentic loop (custom)
│   ├── server.py                   #   HTTP backend
│   ├── langgraph_app.py            #   ★ LangGraph StateGraph版 (Anthropic/Gemini)
│   └── ...
├── review/                         # ★ レビューUI (FastAPI+静的HTML)
│   └── main.py
└── chat/
    ├── build_chat.py               # kg.jsonを埋め込みindex.htmlを生成
    └── index.html                  # Phase6: Agentic Search 質問チャット（生成物）
```

## 実行方法

```bash
# 1) KG検証＋CQ回帰テスト＋Neo4j Cypher生成
cd graph && py -3.14 validate_kg.py
#   => nodes=56 edges=186 constraints=10/10 CQ=12/12 allpass=True

# 2) 質問チャットを再生成（kg.jsonを変更したとき）
cd ../chat && py -3.14 build_chat.py

# 3) チャットを開く（file://はブラウザ制約があるためHTTP経由推奨）
py -3.14 -m http.server 8777
#   => http://127.0.0.1:8777/index.html
```

Neo4jに載せる場合は `graph/kg.cypher` を `cypher-shell` 等で流し込む（GRAPH TYPE定義は `ontology/ontology.md` 参照）。

## Agentic Search チャットの中身（Phase6）

`chat/index.html` は KG を埋め込み、以下の反復ループを **実際に実行**して回答する（トレースを可視化）:

```
①クエリ受信 → ②プランナー(分解要否) → ③検索(ツール動的選択:グラフ探索/プロパティ/フォールバック)
→ ④エビデンスギャップ評価 → ⑤十分? → Yes:回答生成 / No:クエリ書き換えて③へ（最大4反復）
```

**動作確認済みの挙動:**
- **分解**: 「精神1級で使える手当と医療の助成」→ 手当・医療の2サブゴールに分解して各々グラフ探索。
- **多段参照**: 「難病の補装具の窓口＋対象疾病の場所」→ `ADMINISTERED_BY{for:難病}` で **03-5803-1847** を特定し、`DEFINED_BY` で難病一覧(199p)へ。※以前の素のRAGが外した問い。
- **再検索ループ**: 「身体障害者手帳で受けられる手当」（等級なし）→ クリティックが「等級未指定」を検出 → 全等級で再検索。
- **集約**: 「医療的ケア児が使えるサービス」→ `medical_care_child=true` で4件を一括。
- 全回答に**出典チップ**（LLM-Wikiページ＋原本ページ）を付与。

## Geminiモード（実LLM＋実埋め込み） ★実装済み

`.env` に Gemini APIキーを入れると、ルールベース版に加えて **本物のLLMノード＋神経埋め込み**で動く：

- **ⓐ 実埋め込み**: `rag/embed_gemini.py` が各ノードを `gemini-embedding-001`(3072次元)で埋め込み → `rag/embeddings.json`。クエリも実埋め込みしてコサイン類似で起点ノードを特定（文字n-gramでは無理な同義語も意味で橋渡し）。
- **ⓑ LLMノード**: `agent/agent.py` が **Planner / Critic / Answer を Gemini** で実行（LangGraph相当の反復ループ）。retrieval はベクトル入口＋グラフ探索。
- **backend**: `agent/server.py` が静的配信＋ `POST /api/ask` を提供。チャットの「🤖 Geminiモード」チェックでこのbackendを使う。

### セットアップ
```bash
# 1) 依存
pip install google-genai
# 2) graphrag/.env に自分のキーを貼る（Git管理外）
#    GEMINI_API_KEY=...           GEMINI_MODEL=gemini-2.5-flash   GEMINI_EMBED_MODEL=gemini-embedding-001
py -3.14 graphrag\agent\check_key.py        # キー/モデル/SDK を確認（キーは表示しない・通信なし）
# 3) ノードを実埋め込み（初回のみ）
py -3.14 graphrag\rag\embed_gemini.py       # -> rag/embeddings.json
# 4) backend起動（=8790）
powershell -ExecutionPolicy Bypass -File graphrag\serve-gemini.ps1 -Open
#    または serve-gemini.cmd をダブルクリック
```
ブラウザで `http://127.0.0.1:8790/graphrag/chat/index.html` を開き、**「🤖 Geminiモード」をオン**にして質問。回答は根拠(金額・窓口・原本ページ)のみから生成し、金額には「※要確認」を付す。トレースに `Planner(LLM) / VectorSeed(Gemini埋め込み) / Retrieval / Critic(LLM) / Answer(LLM)` が出る。

> 注意: Geminiモードは質問文・KGテキストをGoogleに送信し、キーのクォータを消費します。CLI単体テストは `py -3.14 graphrag\agent\agent.py "質問"`（結果は `agent_last_output.txt`）。

## 本デモの範囲と本番との差分（正直な注記）

| 項目 | 本デモ | 仕様書の本番構成 |
|---|---|---|
| プランナー/クリティック/回答 | オフライン版=ルールベース／**Geminiモード=実LLM(gemini-2.5-flash)** | **LangGraph化済み** (`agent/langgraph_app.py` — LangGraph StateGraph + Anthropic/Gemini) |
| グラフDB | JSON＋JS/Pythonのインメモリ探索 | **Neo4j + GRAPH TYPE済み** (`neo4j/` — docker-compose + DDL + 書込時検証) |
| ベクトル検索 | ✅ オフライン=char n-gram TF-IDF／**Geminiモード=`gemini-embedding-001`(3072d) 神経埋め込み**（同義語も橋渡し） | — |
| スキーマ検証 | `validate_kg.py`（参照/端点/値制約/CQ） | GRAPH TYPE or SHACL+neosemantics |
| 人間レビューUI | — | **FastAPI + 静的HTML** (`review/main.py` — 承認/却下フロー) |
| LLM生成のオントロジー | 人手キュレート（LLM生成→人間レビュー相当） | マルチエージェント(Domain/Manager/Coder/QA) |

→ 本デモは「**検証済みKGに対する Agentic Search が、素のRAGでは外した多段参照・分解・集約・再検索を、経路つきで解ける**」ことを、APIなしで再現可能に示すもの。LLMノード化・Neo4j化は下記の差分を埋める成果物。

## 残★実装済み

### 1. Neo4j 実ロード＋GRAPH TYPE書込時検証

| ファイル | 役割 |
|---|---|
| [`neo4j/docker-compose.yml`](neo4j/docker-compose.yml) | Neo4j 2026.02 Enterprise + APOC |
| [`neo4j/create_graph_type.cypher`](neo4j/create_graph_type.cypher) | GRAPH TYPE DDL（ontology.md由来） |
| [`neo4j/load_kg.py`](neo4j/load_kg.py) | kg.cypher→Neo4j＋書込時検証 |
| [`neo4j/README.md`](neo4j/README.md) | セットアップ手順 |

```bash
cd neo4j
docker compose up -d
py -3.14 load_kg.py
```

### 2. LangGraph＋実LLMノード（プランナー/クリティック/回答）

[`agent/langgraph_app.py`](agent/langgraph_app.py) — LangGraphのStateGraphを使用。

- ノード構成: `Planner(LLM) → Retrieval → Critic(LLM) → (不足ならRetrievalへ、最大2回) → Answer(LLM)`
- サポートプロバイダ: **Anthropic Claude**（デフォルト） / **Gemini**（`--provider gemini`）
- ツール: ベクトル検索（Gemini埋め込み）＋グラフ探索（kg.json）

```bash
pip install langgraph langchain-anthropic langchain-google-genai
# Anthropic
py -3.14 agent/langgraph_app.py "身体障害者手帳2級で受けられる手当は？"
# Gemini
py -3.14 agent/langgraph_app.py --provider gemini "..."
```

### 3. レビューUI（FastAPI＋静的HTML）

[`review/main.py`](review/main.py) — FastAPI backend + 静的HTML（3タブ: 制約レビュー/オントロジー/KG一覧）。

```bash
pip install fastapi uvicorn
uvicorn review.main:app --reload --port 8789
# → http://127.0.0.1:8789
```

制約タブで金額・年齢閾値・時間上限を承認/却下でき、結果はPOST /api/reviewで永続化（現在はメモリ）。

## 値の扱い（重要）
- 金額・年齢閾値・時間上限・対象等級は**原本＝令和7年5月末時点**の値で、`ontology.md`で `human_required` を付与。
  本番投入前にドメインエキスパートが一次情報で承認すること（仕様書§0・Phase3-5）。
- KG更新は `kg.json` を編集 → `validate_kg.py` で検証 → `build_chat.py` で再生成、の順。
