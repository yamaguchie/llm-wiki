# Docker デプロイメント

## 構成図

```
ブラウザ → http://localhost:80
              │
              ▼
         nginx (80)
              │ proxy_pass http://app:8790
              ▼
         FastAPI app (8790) ──→ Neo4j (7687)
```

## ファイル

| ファイル | 役割 |
|---|---|
| `docker-compose.yml` | Neo4j + アプリ + nginx の3サービス構成 |
| `Dockerfile` | Python 3.14 + 依存パッケージ + uvicorn起動 |
| `nginx.conf` | 80番ポート受付 → `app:8790` にリバースプロキシ |
| `requirements.txt` | Python依存パッケージ |
| `.dockerignore` | イメージから除外するファイル |

## 起動・停止

```powershell
# 起動（バックグラウンド）
docker compose up -d

# ログ確認
docker compose logs -f

# 停止（ボリュームも削除）
docker compose down -v
```

## 環境変数

Docker Compose が `pipeline/.env` を自動読み込み:

```env
GEMINI_API_KEY=...           # 必須
GEMINI_MODEL=gemini-3.5-flash
GEMINI_EMBED_MODEL=gemini-embedding-001
```

Neo4j接続情報は `docker-compose.yml` 内で設定（`neo4j:7687`）。

## サービス詳細

### nginx
- イメージ: `nginx:alpine`
- ポート: `80:80`
- 設定: `nginx.conf`（読み取り専用マウント）
- タイムアウト: 600s（LLMの長時間リクエスト対応）
- アップロード制限: 100MB（PDF対応）

### アプリ
- ビルド: `Dockerfile` から
- ポート: `8790:8790`
- env_file: `pipeline/.env`
- ボリューム: `app_state:/app/pipeline/server`（状態永続化）

### Neo4j
- イメージ: `neo4j:2025.10-community`
- ポート: `7687:7687`（Bolt）
- 認証: `neo4j/password123`
- プラグイン: APOC
- ヘルスチェック: cypher-shell で定期確認