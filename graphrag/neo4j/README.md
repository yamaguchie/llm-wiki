# Neo4j — 実ロード＋GRAPH TYPE書込時検証

## 前提
- Docker + Docker Compose
- `pip install neo4j`（Pythonドライバ）

## 起動

```bash
# 1) Neo4j 起動（初回はイメージプル）
cd graphrag/neo4j
docker compose up -d

# 2) 起動待ち（30秒程度）
docker compose logs -f

# 3) Browser UI: http://localhost:7474 (neo4j/password123)

# 4) データロード＋検証
py -3.14 load_kg.py
```

## 構成

| ファイル | 役割 |
|---|---|
| `docker-compose.yml` | Neo4j 2026.02 Enterprise + APOC |
| `create_graph_type.cypher` | GRAPH TYPE DDL（ontology.md由来） |
| `load_kg.py` | kg.cypher→Neo4j＋書込時検証 |
| `load_report.txt` | ロード結果レポート（生成物） |

## 書込時検証テスト

`load_kg.py` はデータ投入後に、意図的に型違反を送信して GRAPH TYPE が正しく拒否するか確認します。

- 必須プロパティ欠落 (`TargetCategory`に`code`がない)
- 必須プロパティNULL (`Service`に`name`がない)
- 間違ったエンドポイントラベル (`Service`→`TargetCategory`に`HAS_CATEGORY`)

## 停止

```bash
cd graphrag/neo4j
docker compose down -v    # データボリュームも削除
```