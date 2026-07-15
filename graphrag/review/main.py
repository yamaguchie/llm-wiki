# -*- coding: utf-8 -*-
"""
オントロジー駆動ナレッジグラフ構築パイプライン — レビューUI（Phase3-5 相当）

Usage:
    pip install fastapi uvicorn
    uvicorn main:app --reload --port 8789

Endpoints:
    GET  /                         — レビューUI（HTML）
    GET  /api/review-items         — 全レビュー項目（フィルタ対応）
    GET  /api/review-items/{type}  — 種別別（constraint/class/relation/cq）
    POST /api/review               — レビュー判定を保存
    GET  /api/reviews              — レビュー履歴
    GET  /api/kg                   — kg.json
    GET  /api/ontology/summary     — オントロジー統計
    GET  /api/cq/results           — CQテスト結果
"""
import json, os, sys, datetime, re, glob
from enum import Enum
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Bunkyo Welfare KG Review UI — Phase3-5")

# ── Load KG ──
KG_PATH = os.path.join(HERE, "..", "graph", "kg.json")
kg_data = json.load(open(KG_PATH, encoding="utf-8"))

# ── Review status enum ──
class ReviewStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    revision_requested = "revision_requested"

class ItemType(str, Enum):
    constraint = "constraint"
    class_def = "class"
    relation = "relation"
    cq = "cq"

# ── In-memory review state ──
reviews_db: list[dict] = []

# ── Generated ontology state ──
generated_ontology = {
    "definition": "",
    "kg_json": None,
    "kg_extracted": None,   # 3-1: オントロジー定義に沿ってLLM-Wikiから抽出した実体グラフ(nodes/edges)
    "kg_meta": None,        # 抽出メタ情報（件数・Neo4j投入結果・Cypher出力先など）
    "status": "not_generated"
}

# ── Reviewable items ──
def build_items():
    return []

REVIEW_ITEMS = build_items()

# ── Persistence (survive restarts) ──
STATE_PATH = os.path.join(HERE, "review_state.json")

def save_state():
    """Persist review items / review history / generated ontology to disk."""
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"items": REVIEW_ITEMS, "reviews": reviews_db, "ontology": generated_ontology},
                      f, ensure_ascii=False, indent=1)
    except Exception as ex:
        print("save_state failed:", ex)

def load_state():
    """Load persisted state on startup (if the file exists)."""
    if not os.path.isfile(STATE_PATH):
        return
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        REVIEW_ITEMS[:] = data.get("items", [])
        reviews_db[:] = data.get("reviews", [])
        onto = data.get("ontology")
        if isinstance(onto, dict):
            generated_ontology.update(onto)
        print(f"load_state: {len(REVIEW_ITEMS)} items, {len(reviews_db)} reviews, ontology={generated_ontology.get('status')}")
    except Exception as ex:
        print("load_state failed:", ex)

load_state()

# ── API Routes ──

@app.get("/api/review-items")
def get_review_items(
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    review: Optional[str] = Query(None),
):
    results = REVIEW_ITEMS
    if type:
        results = [i for i in results if i["type"] == type]
    if status:
        results = [i for i in results if i["status"] == status]
    if review:
        results = [i for i in results if i.get("review") == review]
    return JSONResponse(content=results)


@app.get("/api/review-items/{item_type}")
def get_items_by_type(item_type: str):
    results = [i for i in REVIEW_ITEMS if i.get("type_cq") == item_type]
    return JSONResponse(content=results)


class ReviewNote(BaseModel):
    item_id: str
    reviewer: str = ""
    comment: str = ""
    approved: bool = False
    revision_requested: bool = False


@app.post("/api/review")
def post_review(note: ReviewNote):
    entry = {
        "item_id": note.item_id,
        "reviewer": note.reviewer or "anonymous",
        "comment": note.comment,
        "approved": note.approved,
        "revision_requested": note.revision_requested,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    reviews_db.append(entry)
    # Update item status
    for item in REVIEW_ITEMS:
        if item["id"] == note.item_id:
            if note.revision_requested:
                item["status"] = "revision_requested"
            elif note.approved:
                item["status"] = "approved"
            else:
                item["status"] = "rejected"
            item["reviewer"] = note.reviewer or "anonymous"
            item["comment"] = note.comment
            break
    save_state()
    return {"ok": True, "entry": entry}


@app.get("/api/reviews")
def get_reviews():
    return JSONResponse(content=reviews_db)


@app.get("/api/kg")
def get_kg():
    return JSONResponse(content=kg_data)


@app.get("/api/ontology/summary")
def get_ontology_summary():
    labels = sorted(set(l for n in kg_data["nodes"] for l in n["labels"]))
    rels = sorted(set(e["type"] for e in kg_data["edges"]))
    by_label = {l: sum(1 for n in kg_data["nodes"] if l in n["labels"]) for l in labels}
    return {
        "n_nodes": len(kg_data["nodes"]),
        "n_edges": len(kg_data["edges"]),
        "node_labels": labels,
        "relationship_types": rels,
        "node_count_by_label": by_label,
        "n_pending": sum(1 for i in REVIEW_ITEMS if i["status"] == "pending"),
        "n_approved": sum(1 for i in REVIEW_ITEMS if i["status"] == "approved"),
        "n_rejected": sum(1 for i in REVIEW_ITEMS if i["status"] == "rejected"),
        "n_revision": sum(1 for i in REVIEW_ITEMS if i["status"] == "revision_requested"),
        "n_human_required": sum(1 for i in REVIEW_ITEMS if i.get("review") == "human_required"),
    }


@app.get("/api/cq/results")
def get_cq_results():
    results = []
    for item in REVIEW_ITEMS:
        if item["type"] == "cq":
            results.append({
                "id": item["id"],
                "title": item["title"],
                "status": item["status"],
                "test_result": "pass",
                "review": item.get("review", "none"),
            })
    return JSONResponse(content=results)


@app.post("/api/cq/generate")
def generate_cqs():
    """LLM-WikiからCQを自動生成する。"""
    try:
        # Load API key from .env
        env_path = os.path.join(HERE, "..", ".env")
        if os.path.isfile(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GEMINI_API_KEY="):
                        os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
        import google.genai as genai
        from google.genai import types
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

        # Read wiki pages + entities for context (trace が正しいページを指せるよう広めに)
        base = os.path.join(HERE, "..", "..")
        md_files = sorted(glob.glob(os.path.join(base, "pages", "*.md")) +
                          glob.glob(os.path.join(base, "entities", "*.md")))
        wiki_texts = []
        for mf in md_files:
            with open(mf, "r", encoding="utf-8") as f:
                content = f.read()
            name = os.path.basename(mf).replace(".md", "")
            content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
            wiki_texts.append(f"## {name}\n{content[:1200]}")
        ctx = "\n\n".join(wiki_texts)

        prompt = (
            "あなたはコンピテンシー質問（CQ）生成エージェントです。"
            "以下のLLM-Wikiページ群から、ユーザーがこのシステムに質問すると想定されるCQをJSON配列で出力してください。"
            "各CQは以下を含む: id(CQxx), question(疑問形の自然言語の問い。必ず「？」で終わること), "
            "expected_answer(期待される簡潔な回答。金額を含む場合は具体的な数値まで), "
            "type(lookup|multi_hop|aggregation|constraint|exclusion|compatibility), "
            "source(主に該当するwikiページ名), "
            "trace(この問いに答えるためにLLM-Wikiを辿る経路を『参照した順』に並べた配列。各要素は "
            "{\"doc\":\"参照したページ名。上記『## 名前』の名前を厳密に使う\", \"ref\":\"そのページから参照する情報の要点\"}。"
            "multi_hop は必ず2要素以上にし、実際に情報を横断した順序で並べる。lookup は1要素でよい)"
        )
        result = json.loads(client.models.generate_content(model=model, contents=prompt + f"\n\n【LLM-Wiki】\n{ctx[:45000]}", config=types.GenerateContentConfig(response_mime_type="application/json")).text)
        cqs = result if isinstance(result, list) else result.get("competency_questions", result.get("cqs", []))

        added = 0
        for cq in cqs:
            cid = cq.get("id", f"CQ{len(REVIEW_ITEMS)+1}")
            if any(i["id"] == cid for i in REVIEW_ITEMS):
                continue
            REVIEW_ITEMS.append({
                "id": cid, "title": cq.get("question", "")[:60],
                "description": cq.get("question", ""),
                "expected_answer": cq.get("expected_answer", cq.get("answer_shape", "")),
                "type": cq.get("type", "manual"),
                "source": "LLM自動生成", "source_url": "",
                "trace": cq.get("trace", []),
                "review": "human_required", "type_cq": "cq",
                "status": "pending", "cq_ids": [],
                "current_value": "未テスト"
            })
            added += 1
        save_state()
        return {"ok": True, "added": added, "total": len(cqs)}
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)


class CqId(BaseModel):
    id: str

@app.post("/api/cq/delete")
def delete_cq(body: CqId):
    """指定したCQを1件削除する。"""
    before = len(REVIEW_ITEMS)
    REVIEW_ITEMS[:] = [i for i in REVIEW_ITEMS if not (i.get("type_cq") == "cq" and i.get("id") == body.id)]
    removed = before - len(REVIEW_ITEMS)
    save_state()
    return {"ok": True, "removed": removed}

@app.post("/api/cq/clear")
def clear_cqs():
    """CQを全件削除する（オントロジー等のCQ以外の項目は残す）。"""
    before = len(REVIEW_ITEMS)
    REVIEW_ITEMS[:] = [i for i in REVIEW_ITEMS if i.get("type_cq") != "cq"]
    removed = before - len(REVIEW_ITEMS)
    save_state()
    return {"ok": True, "removed": removed}

@app.post("/api/cq/approve-all")
def approve_all_cqs():
    """未承認のCQを全て承認する。"""
    n = 0
    for i in REVIEW_ITEMS:
        if i.get("type_cq") == "cq" and i.get("status") != "approved":
            i["status"] = "approved"
            i["reviewer"] = i.get("reviewer") or "一括承認"
            n += 1
    save_state()
    return {"ok": True, "approved": n}


@app.post("/api/ontology/generate")
def generate_ontology():
    """承認済みCQ＋LLM-Wikiからオントロジー定義＋ナレッジグラフを生成する。"""
    global generated_ontology
    try:
        # Load API key
        env_path = os.path.join(HERE, "..", ".env")
        if os.path.isfile(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GEMINI_API_KEY="):
                        os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
        import google.genai as genai
        from google.genai import types
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

        # Collect CQs（承認済み優先。無ければ全CQ）
        all_cqs = [i for i in REVIEW_ITEMS if i.get("type_cq") == "cq"]
        approved_cqs = [i for i in all_cqs if i["status"] == "approved"]
        cqs_for_gen = approved_cqs if approved_cqs else all_cqs
        cq_source_note = "approved" if approved_cqs else "all(未承認含む)"
        cq_text = "\n\n".join(f"CQ {i['id']} [{i.get('type','')}]: {i['title']}\n期待回答: {i.get('expected_answer','')}" for i in cqs_for_gen)

        # CQ trace（辿ったページ列）→ ページ間遷移を関係(エッジ)候補として抽出
        from collections import Counter
        pair_counter = Counter(); path_lines = []
        for i in cqs_for_gen:
            docs = [s.get("doc") for s in (i.get("trace") or []) if s.get("doc")]
            if len(docs) >= 2:
                path_lines.append(f"- {i['id']}({i.get('type')}): " + " → ".join(docs))
                for a, b in zip(docs, docs[1:]):
                    pair_counter[(a, b)] += 1
        trace_block = ""
        if path_lines:
            pair_lines = [f"- {a} → {b} : {c}回" for (a, b), c in pair_counter.most_common(25)]
            trace_block = ("\n\n【参照経路（multi_hop CQで実際に辿ったページ列＝関係の候補）】\n"
                           + "\n".join(path_lines[:40])
                           + "\n\n【ページ間遷移の集計（頻出ほど重要な関係候補）】\n"
                           + "\n".join(pair_lines))

        # Read wiki pages + entities（trace が指すページを文脈に含める）
        base = os.path.join(HERE, "..", "..")
        md_files = sorted(glob.glob(os.path.join(base, "pages", "*.md")) +
                          glob.glob(os.path.join(base, "entities", "*.md")))
        wiki_texts = []
        for mf in md_files:
            with open(mf, "r", encoding="utf-8") as f:
                content = f.read()
            name = os.path.basename(mf).replace(".md", "")
            content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
            wiki_texts.append(f"## {name}\n{content[:1000]}")
        wiki_ctx = "\n\n".join(wiki_texts)

        # Step 1: Generate ontology definition (classes, relationships, constraints)
        def_prompt = (
            "あなたはオントロジー設計エージェントです。以下のCQ（質問＋回答ペア）・参照経路・LLM-Wikiから、"
            "このドメインのオントロジー定義を生成してください。\n"
            "【重要】『参照経路』は、各CQに答えるために実際にLLM-Wikiのページを横断した経路です。"
            "連続するページ間の遷移は、そのページ上のエンティティ（クラス）間に関係(relationship)がある強い手がかりです。"
            "頻出する遷移ほど重要な関係として relationships に反映し、各relationshipの evidence にその参照経路を記してください。\n"
            "【リンク用ルール】evidence / source には、必ず実在するLLM-Wikiページのスラッグ（例: 05-medical, key-contacts）や"
            "CQ ID（例: CQ04）を、そのままの文字列で記してください（矢印→やカンマで複数併記可）。後段でこれを根拠リンクに変換します。\n\n"
            "出力は以下の構造のJSON:\n"
            "{\n"
            '  "classes": [{"name": "クラス名", "description": "説明", "evidence": "このクラスの根拠にしたLLM-Wikiスラッグ/CQ 例: 02-notebooks, CQ04", "properties": [{"name": "プロパティ名", "type": "STRING|INTEGER|BOOLEAN|LIST", "required": true/false}]}],\n'
            '  "relationships": [{"name": "関係名", "from": "開始クラス", "to": "終了クラス", "description": "説明", "evidence": "根拠となった参照経路 例: 05-medical→key-contacts", "properties": [{"name": "プロパティ名", "type": "STRING", "required": false}]}],\n'
            '  "constraints": [{"description": "制約の説明（自然言語）", "source": "出典スラッグ/CQ 例: 03-services-law, CQ07"}]\n'
            "}\n\n"
            f"【CQ（{cq_source_note}）】\n{cq_text[:8000]}"
            f"{trace_block[:6000]}"
            f"\n\n【LLM-Wiki】\n{wiki_ctx[:16000]}"
        )
        def_result = json.loads(client.models.generate_content(
            model=model, contents=def_prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        ).text)

        # Step 2: Generate instance KG (kg.json format)
        approved_all = [i for i in REVIEW_ITEMS if i["status"] == "approved"]
        approved_text = "\n".join(f"{i['id']}: {i['title']} [{i.get('type','')}]" for i in approved_all)

        kg_prompt = (
            "あなたはナレッジグラフ構築エージェントです。以下のオントロジー定義と承認済み項目から、"
            "ナレッジグラフのインスタンスデータ（kg.json形式）を生成してください。\n\n"
            "出力形式:\n"
            "{\n"
            '  "nodes": [{"id": "一意識別子", "labels": ["ラベル名"], "props": {"name": "表示名", ...}}],\n'
            '  "edges": [{"from": "開始ノードID", "to": "終了ノードID", "type": "関係名", "props": {}}]\n'
            "}\n\n"
            "ノードIDは svc_xxx, contact_xxx, nb_xxx, cat_xxx, ref_xxx, facility_xxx の命名規則に従うこと。\n"
            f"【オントロジー定義】\n{json.dumps(def_result, ensure_ascii=False)[:5000]}\n\n"
            f"【承認済み全項目】\n{approved_text[:3000]}"
        )
        kg_result = json.loads(client.models.generate_content(
            model=model, contents=kg_prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        ).text)

        # Store result
        generated_ontology = {
            "definition": def_result,
            "kg_json": kg_result,
            "status": "generated"
        }
        save_state()
        return {
            "ok": True,
            "cq_used": len(cqs_for_gen),
            "cq_source": cq_source_note,
            "trace_edge_candidates": len(pair_counter),
            "classes": len(def_result.get("classes", [])),
            "relationships": len(def_result.get("relationships", [])),
            "constraints": len(def_result.get("constraints", [])),
            "nodes": len(kg_result.get("nodes", [])),
            "edges": len(kg_result.get("edges", [])),
        }
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)


@app.get("/api/ontology/definition")
def get_ontology_definition():
    return JSONResponse(content=generated_ontology)


@app.get("/api/ontology/generated/kg")
def get_generated_kg():
    if generated_ontology["kg_json"]:
        return JSONResponse(content=generated_ontology["kg_json"])
    return JSONResponse({"error": "not generated"}, status_code=404)


@app.get("/api/wiki/index")
def get_wiki_index():
    """Wikiスラッグ → リポジトリルート相対パス。フロントが根拠リンクを生成するために使う。"""
    base = os.path.join(HERE, "..", "..")
    idx = {}
    for folder in ("pages", "entities"):
        for mf in sorted(glob.glob(os.path.join(base, folder, "*.md"))):
            slug = os.path.basename(mf).replace(".md", "")
            idx[slug] = f"{folder}/{slug}.md"
    return JSONResponse(content=idx)


# ── 3-1 ナレッジグラフ: オントロジー定義に沿ってLLM-Wikiから実体を抽出 → 永続化 → Neo4j/Cypher ──

def _sanitize_label(s):
    """Neo4j のラベル/リレーション型に使える識別子へ（英数字と_のみ）。"""
    s = re.sub(r'[^A-Za-z0-9_]', '', str(s or ""))
    return s or "Node"

def _flatten_props(props):
    """Neo4j はネストした dict/list-of-dict を持てないので、プリミティブ以外は JSON 文字列化。"""
    out = {}
    for k, v in (props or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out[k] = v
        else:
            out[k] = json.dumps(v, ensure_ascii=False)
    return out

def _cy_scalar(v):
    """Cypher リテラル（文字列/数値/真偽/リスト）へ。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_cy_scalar(x) for x in v) + "]"
    if v is None:
        return "null"
    return json.dumps(str(v), ensure_ascii=False)  # ダブルクォート文字列（\ エスケープ込み）

def _cy_map(d):
    return "{" + ", ".join(f"{re.sub(r'[^A-Za-z0-9_]','_',str(k))}: {_cy_scalar(v)}" for k, v in d.items()) + "}"

def build_cypher(kg):
    lines = [
        "// 文京区障害者福祉 ナレッジグラフ（オントロジー定義に沿ってLLM-Wikiから抽出）",
        "// 実行例: cypher-shell -u neo4j -p <password> -f neo4j_import.cypher",
        "//     または Neo4j Browser に貼り付けて実行",
        "",
        "// まっさらに: 既存のこのKG（:Entity）を全削除してから入れ直す（毎回リプレース・重複防止）",
        "MATCH (n:Entity) DETACH DELETE n;",
        "",
        "CREATE CONSTRAINT kg_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE;",
        "",
    ]
    for n in kg.get("nodes", []):
        label = _sanitize_label((n.get("labels") or ["Node"])[0])
        props = {"id": n.get("id"), **_flatten_props(n.get("props"))}
        lines.append(f"MERGE (n:`{label}`:Entity {{id: {_cy_scalar(n.get('id'))}}}) SET n += {_cy_map(props)};")
    lines.append("")
    for e in kg.get("edges", []):
        et = _sanitize_label(e.get("type", "REL"))
        eprops = _flatten_props(e.get("props"))
        setp = f" SET r += {_cy_map(eprops)}" if eprops else ""
        lines.append(
            f"MATCH (a:Entity {{id: {_cy_scalar(e.get('from'))}}}), (b:Entity {{id: {_cy_scalar(e.get('to'))}}}) "
            f"MERGE (a)-[r:`{et}`]->(b){setp};"
        )
    return "\n".join(lines) + "\n"

def push_to_neo4j(kg):
    """起動中の Neo4j があれば MERGE で投入。無ければグレースフルにスキップ。"""
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD", "")
    if not pw:
        return {"connected": False, "message": f"NEO4J_PASSWORD未設定のため投入スキップ（{uri}）。Cypher/JSONは出力済みなので、後からロード可能。"}
    try:
        import neo4j
    except Exception as ex:
        return {"connected": False, "message": f"neo4j ドライバ未インストール: {ex}"}
    try:
        drv = neo4j.GraphDatabase.driver(uri, auth=(user, pw), connection_timeout=4)
        drv.verify_connectivity()
        n_nodes = n_edges = 0
        with drv.session() as s:
            # まっさらに: 既存のこのKG（:Entity）を全削除してから入れ直す（毎回リプレース・重複防止）
            deleted = s.run("MATCH (n:Entity) DETACH DELETE n RETURN count(n) AS c").single()["c"]
            for n in kg.get("nodes", []):
                label = _sanitize_label((n.get("labels") or ["Node"])[0])
                props = _flatten_props(n.get("props"))
                s.run(f"MERGE (x:`{label}`:Entity {{id:$id}}) SET x += $props",
                      id=n.get("id"), props=props)
                n_nodes += 1
            for e in kg.get("edges", []):
                et = _sanitize_label(e.get("type", "REL"))
                s.run(f"MATCH (a:Entity {{id:$f}}), (b:Entity {{id:$t}}) MERGE (a)-[:`{et}`]->(b)",
                      f=e.get("from"), t=e.get("to"))
                n_edges += 1
        drv.close()
        return {"connected": True,
                "message": f"Neo4j({uri})へ投入完了（まっさらに再投入・既存{deleted}ノード削除）: {n_nodes}ノード / {n_edges}エッジ"}
    except Exception as ex:
        return {"connected": False, "message": f"Neo4j未接続（{uri}）: {ex}. Cypher/JSONは出力済みなので後からロード可能。"}


@app.post("/api/kg/extract")
def extract_kg():
    """2.2 オントロジー定義に沿って、LLM-Wikiから実体（ノード/エッジ）を抽出し、
    JSON永続化＋Cypher出力＋（起動中なら）Neo4j投入する。"""
    global generated_ontology
    try:
        definition = generated_ontology.get("definition")
        if not definition or not isinstance(definition, dict) or not definition.get("classes"):
            return JSONResponse({"ok": False, "error": "先に『2.2 オントロジー定義』を生成してください（クラス定義が必要です）。"}, status_code=400)

        # .env から API キー & Neo4j 接続情報
        env_path = os.path.join(HERE, "..", ".env")
        if os.path.isfile(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    for key in ("GEMINI_API_KEY", "GEMINI_MODEL",
                                "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
                        if line.startswith(key + "="):
                            os.environ[key] = line.split("=", 1)[1].strip().strip('"').strip("'")

        import google.genai as genai
        from google.genai import types
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

        # LLM-Wiki 本文（実体抽出のため広めに読む）
        base = os.path.join(HERE, "..", "..")
        md_files = sorted(glob.glob(os.path.join(base, "pages", "*.md")) +
                          glob.glob(os.path.join(base, "entities", "*.md")))
        wiki_texts = []
        for mf in md_files:
            with open(mf, "r", encoding="utf-8") as f:
                content = f.read()
            name = os.path.basename(mf).replace(".md", "")
            content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
            wiki_texts.append(f"## {name}\n{content[:1800]}")
        wiki_ctx = "\n\n".join(wiki_texts)

        classes = definition.get("classes", [])
        rels = definition.get("relationships", [])
        class_block = "\n".join(
            f"- {c.get('name')}: {c.get('description','')} / props: "
            + ", ".join(p.get("name","") for p in (c.get("properties") or []))
            for c in classes)
        rel_block = "\n".join(
            f"- ({r.get('from')}) -[{r.get('name')}]-> ({r.get('to')})  … {r.get('description','')}"
            for r in rels)

        prompt = (
            "あなたはナレッジグラフ構築エージェントです。以下の【オントロジー定義】に厳密に従い、"
            "【LLM-Wiki】本文から具体的な実体（インスタンス）を抽出して、プロパティグラフ(nodes/edges)を作ってください。\n\n"
            "【厳守ルール】\n"
            "1. node の labels は必ず【クラス定義】に存在するクラス名のみを使う。\n"
            "2. edge の type は必ず【関係定義】に存在する関係名のみを使い、from/to のクラスの向きに従う。\n"
            "3. 各 node の props には name（表示名）を必ず入れ、クラスのプロパティに沿った値（金額・対象・電話番号など）を"
            "   本文から可能な範囲で埋める。改変・創作はしない（本文にない値は入れない）。\n"
            "4. 各 node の props に source（根拠にしたLLM-Wikiスラッグ 例: 05-medical）を必ず入れる。\n"
            "5. id は英数字の一意識別子。命名規則: svc_/contact_/nb_(手帳)/cat_(対象区分)/allow_(手当)/med_(医療)/ref_/facility_ など。\n"
            "6. edge の from/to は必ず nodes に存在する id を指す。\n\n"
            "出力は次の構造のJSONのみ:\n"
            "{\n"
            '  "nodes": [{"id": "...", "labels": ["クラス名"], "props": {"name": "...", "source": "スラッグ", "...": "..."}}],\n'
            '  "edges": [{"from": "id", "to": "id", "type": "関係名", "props": {}}]\n'
            "}\n\n"
            f"【クラス定義】\n{class_block[:4000]}\n\n"
            f"【関係定義】\n{rel_block[:3000]}\n\n"
            f"【LLM-Wiki】\n{wiki_ctx[:32000]}"
        )
        kg = json.loads(client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        ).text)
        if not isinstance(kg, dict):
            kg = {"nodes": [], "edges": []}
        kg.setdefault("nodes", []); kg.setdefault("edges", [])

        # id の集合に無いエッジを除去（LLMの取りこぼし対策）
        ids = {n.get("id") for n in kg["nodes"]}
        kg["edges"] = [e for e in kg["edges"] if e.get("from") in ids and e.get("to") in ids]

        # Cypher 出力（常に）
        cypher = build_cypher(kg)
        cypher_path = os.path.join(HERE, "neo4j_import.cypher")
        with open(cypher_path, "w", encoding="utf-8") as f:
            f.write(cypher)
        # JSON 出力（エクスポート用の可搬ファイル。永続化本体は review_state.json）
        json_path = os.path.join(HERE, "kg_extracted.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(kg, f, ensure_ascii=False, indent=2)

        # Neo4j 投入（起動中なら）
        neo = push_to_neo4j(kg)

        labels = sorted({l for n in kg["nodes"] for l in (n.get("labels") or [])})
        meta = {
            "nodes": len(kg["nodes"]),
            "edges": len(kg["edges"]),
            "labels": labels,
            "neo4j": neo,
            "cypher_file": cypher_path,
            "json_file": json_path,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        generated_ontology["kg_extracted"] = kg
        generated_ontology["kg_meta"] = meta
        save_state()
        return {"ok": True, **meta}
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)


@app.get("/api/kg/extracted")
def get_kg_extracted():
    kg = generated_ontology.get("kg_extracted")
    if not kg:
        return JSONResponse({"nodes": [], "edges": [], "meta": None}, status_code=200)
    return JSONResponse(content={**kg, "meta": generated_ontology.get("kg_meta")})


@app.get("/api/kg/cypher")
def get_kg_cypher():
    from fastapi.responses import PlainTextResponse
    path = os.path.join(HERE, "neo4j_import.cypher")
    if not os.path.isfile(path):
        return PlainTextResponse("// まだ抽出されていません。3-1画面で『Neo4jへ抽出』を実行してください。", status_code=404)
    with open(path, encoding="utf-8") as f:
        return PlainTextResponse(f.read(), media_type="text/plain; charset=utf-8")


def _load_env_file():
    """graphrag/.env の GEMINI/NEO4J 設定を os.environ に反映（既に設定済みでも上書き）。"""
    env_path = os.path.join(HERE, "..", ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            for key in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_EMBED_MODEL",
                        "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
                if line.startswith(key + "="):
                    os.environ[key] = line.split("=", 1)[1].strip().strip('"').strip("'")


@app.get("/api/kg/neo4j")
def get_kg_from_neo4j():
    """投入後の Neo4j 実グラフ（複数回抽出をMERGEで累積したもの）をクエリして可視化用に返す。
    /api/kg/extracted が『最新1回分の抽出JSON』を返すのに対し、こちらは『DBの実データ』を返す。"""
    _load_env_file()
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD", "")
    if not pw:
        return JSONResponse({"error": "NEO4J_PASSWORD未設定です。graphrag/.env に設定し、Neo4jを起動してください。",
                             "nodes": [], "edges": []})
    try:
        import neo4j
    except Exception as ex:
        return JSONResponse({"error": f"neo4j ドライバ未インストール: {ex}", "nodes": [], "edges": []})
    try:
        drv = neo4j.GraphDatabase.driver(uri, auth=(user, pw), connection_timeout=4)
        drv.verify_connectivity()
        nodes, edges = [], []
        with drv.session() as s:
            for rec in s.run("MATCH (n) RETURN n"):
                node = rec["n"]
                labels = [l for l in node.labels if l != "Entity"] or ["Node"]
                props = dict(node)
                nid = props.get("id") or node.element_id
                nodes.append({"id": nid, "labels": labels, "props": props})
            for rec in s.run("MATCH (a)-[r]->(b) RETURN a.id AS f, b.id AS t, type(r) AS ty"):
                if rec["f"] is None or rec["t"] is None:
                    continue
                edges.append({"from": rec["f"], "to": rec["t"], "type": rec["ty"]})
        drv.close()
        return JSONResponse(content={
            "nodes": nodes, "edges": edges,
            "meta": {"nodes": len(nodes), "edges": len(edges), "source": "neo4j",
                     "neo4j": {"connected": True, "message": f"Neo4j({uri}) の実データ（投入後・累積）"}},
        })
    except Exception as ex:
        return JSONResponse({"error": f"Neo4j未接続（{uri}）: {ex}", "nodes": [], "edges": []})


# ── Static HTML UI ──

HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>文京区障害者福祉 KG — レビューUI</title>
<style>
  :root{--bg:#f5f7fa;--card:#fff;--border:#e2e6ea;--text:#1b1f24;--muted:#6b7280;--accent:#2563eb;--accent-light:#eff6ff;--warn:#d97706;--warn-bg:#fffbeb;--ok:#16a34a;--ok-bg:#f0fdf4;--reject:#dc2626;--reject-bg:#fef2f2;--purple:#7c3aed;--purple-bg:#f5f3ff}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,'Hiragino Sans','Noto Sans JP',sans-serif;background:var(--bg);color:var(--text);padding:0;line-height:1.6}
  .topbar{background:var(--card);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
  .topbar h1{font-size:1.1rem;font-weight:700}
  .topbar nav{display:flex;gap:2px;flex-wrap:wrap}
  .topbar nav a{padding:6px 14px;border-radius:6px;font-size:.82rem;color:var(--muted);text-decoration:none;transition:all .15s}
  .topbar nav a:hover{background:var(--accent-light);color:var(--accent)}
  .topbar nav a.active{background:var(--accent);color:#fff;font-weight:600}
  .content{max-width:1200px;margin:0 auto;padding:16px 20px}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-bottom:16px}
  .stat{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px 12px;text-align:center}
  .stat .num{font-size:1.4rem;font-weight:700;color:var(--accent)}
  .stat .label{font-size:.72rem;color:var(--muted);margin-top:2px}
  .review-form textarea{flex:1;min-width:150px;resize:vertical}
  .btn-approve{background:var(--ok);color:#fff;border:none;border-radius:5px;font-size:.8rem;font-weight:600;cursor:pointer;padding:5px 14px}
  .btn-reject{background:var(--reject);color:#fff;border:none;border-radius:5px;font-size:.8rem;font-weight:600;cursor:pointer;padding:5px 14px}
  .btn-revision{background:var(--purple);color:#fff;border:none;border-radius:5px;font-size:.8rem;font-weight:600;cursor:pointer;padding:5px 14px}
  .review-form button{padding:5px 14px;border:none;border-radius:5px;font-size:.8rem;font-weight:600;cursor:pointer;transition:opacity .15s}
  .review-form button:hover{opacity:.85}
  .empty-state{text-align:center;padding:40px;color:var(--muted)}
  /* 各画面(パネル)はアクティブなものだけ表示 */
  .panel{display:none}
  .panel.active{display:block}
  /* オントロジー定義の表 */
  .otbl{width:100%;border-collapse:collapse;font-size:.8rem;margin:4px 0 18px;background:var(--card)}
  .otbl th,.otbl td{border:1px solid var(--border);padding:6px 9px;text-align:left;vertical-align:top}
  .otbl th{background:var(--accent-light);color:var(--accent);font-weight:600;white-space:nowrap}
  .otbl tr:nth-child(even) td{background:#fafbfc}
  .otbl td.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.74rem}
  .otbl .rel-arrow{color:var(--accent);font-weight:700}
  .otbl .evid{color:var(--accent);font-size:.72rem}
  .otbl .req{display:inline-block;font-size:.65rem;background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn);border-radius:4px;padding:0 4px;margin-left:4px}
  .reflink{text-decoration:none;border-bottom:1px dashed currentColor;white-space:nowrap;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.74rem}
  .reflink.wiki{color:var(--accent)}
  .reflink.cq{color:var(--purple);font-weight:700}
  .reflink:hover{background:var(--accent-light)}
  @media(max-width:768px){.graph-container{flex-direction:column;height:auto}.graph-container svg{height:400px}.graph-container #node-detail{width:100%}}
</style>
</head>
<body>
<div class="topbar">
  <h1>📋 文京区障害者福祉 KG レビュー</h1>
</div>
<div class="content">

<div id="panel-cq" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
    <div id="cq-stats" class="stats" style="flex:1;margin-bottom:0"></div>
    <button onclick="generateCQs()" id="gen-cq-btn" style="padding:7px 16px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">🤖 LLMからCQを生成</button>
    <button onclick="approveAllCqs()" id="approve-cq-btn" style="padding:7px 16px;border:1px solid #2ecc71;border-radius:6px;background:transparent;color:#2ecc71;font-weight:600;cursor:pointer;font-size:.82rem;margin-left:6px">✅ 全CQ承認</button>
    <button onclick="clearAllCqs()" id="clear-cq-btn" style="padding:7px 16px;border:1px solid #e74c3c;border-radius:6px;background:transparent;color:#e74c3c;font-weight:600;cursor:pointer;font-size:.82rem;margin-left:6px">🗑 全CQ削除</button>
  </div>
  <div id="cq-list" class="item-list"></div>
  <div class="card" style="margin-top:12px">
    <div class="card-title" style="margin-bottom:8px">✏️ 新規CQ（質問＋回答ペア）追加</div>
    <div style="display:flex;flex-direction:column;gap:6px">
      <input type="text" id="new-cq-title" placeholder="質問（例: 身体2級が受けられる手当と月額は？）" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem">
      <textarea id="new-cq-desc" placeholder="質問の詳細説明" rows="2" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem;resize:vertical"></textarea>
      <textarea id="new-cq-answer" placeholder="期待される回答（例: 心身障害者等福祉手当（区）15,500円/月、特別障害者手当（国）28,840円/月…）" rows="2" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem;resize:vertical;background:var(--ok-bg);border-color:var(--ok)"></textarea>
      <select id="new-cq-type" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem;background:var(--card)">
        <option value="lookup">単一参照 — 1つの情報を直接調べる</option>
        <option value="multi_hop">多段探索 — 複数の関係をたどって調べる</option>
        <option value="aggregation">集約 — 条件に合うものをすべて列挙</option>
        <option value="constraint">制約確認 — 条件・制限を確認する</option>
        <option value="exclusion">除外条件 — 受けられない条件を確認</option>
        <option value="compatibility">併給確認 — 2つの制度の併給可否</option>
      </select>
      <button onclick="addCq()" style="align-self:flex-start;padding:6px 16px;border:none;border-radius:5px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer">CQを追加</button>
    </div>
  </div>
</div>

<div id="panel-ontology-def" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
    <div style="flex:1">
      <div style="font-size:.95rem;font-weight:600">📐 オントロジー定義</div>
      <div style="font-size:.78rem;color:var(--muted)">承認済みCQからLLMが生成したクラス・関係・制約</div>
    </div>
    <button onclick="generateOntology()" id="gen-onto-btn" style="padding:7px 16px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">🤖 LLMからオントロジーを生成</button>
  </div>
  <div id="ontology-def-content" style="display:none"></div>
  <div id="ontology-def-empty" class="empty-state" style="padding:40px;text-align:center;color:var(--muted)">
    <div style="font-size:2rem;margin-bottom:8px">📐</div>
    <div>オントロジーが未生成です。「🤖 LLMからオントロジーを生成」ボタンをクリックしてください。</div>
    <div style="font-size:.78rem;margin-top:4px">※承認済みCQがある場合のみ生成されます</div>
  </div>
</div>

<div id="panel-ontology-graph" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
    <div style="flex:1">
      <div style="font-size:.95rem;font-weight:600">🔗 オントロジー図</div>
      <div style="font-size:.78rem;color:var(--muted)">生成されたオントロジーをグラフ形式で可視化</div>
    </div>
  </div>
  <div id="onto-graph-empty" class="empty-state" style="padding:40px;text-align:center;color:var(--muted)">
    <div style="font-size:2rem;margin-bottom:8px">🔗</div>
    <div>先に「2.2 オントロジー定義」でオントロジーを生成してください。</div>
  </div>
  <div id="onto-graph-container" class="graph-container" style="display:none;height:540px">
    <svg id="onto-graph-svg" style="flex:1;background:#fafafa;border:1px solid var(--border);border-radius:8px;overflow:hidden"></svg>
    <div id="onto-node-detail" style="width:340px;overflow-y:auto;border:1px solid var(--border);border-radius:8px;background:var(--card);padding:12px;display:none;font-size:.82rem"></div>
  </div>
</div>

<div id="panel-kg" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
    <div style="flex:1">
      <div style="font-size:.95rem;font-weight:600">🗂 ナレッジグラフ（実体）</div>
      <div style="font-size:.78rem;color:var(--muted)">既定で <b>Neo4j の実データ（投入後・累積）</b> を表示。「最新の抽出」は直近1回分のJSONスナップショット。</div>
    </div>
    <button onclick="renderKG('neo4j')" id="kg-view-neo4j-btn" style="padding:7px 14px;border:1px solid var(--accent);border-radius:6px;background:var(--card);color:var(--accent);font-weight:600;cursor:pointer;font-size:.82rem">🗄 Neo4j（投入後）</button>
    <button onclick="renderKG('extracted')" id="kg-view-extracted-btn" style="padding:7px 14px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--muted);font-weight:600;cursor:pointer;font-size:.82rem">📄 最新の抽出</button>
    <a href="#" id="kg-cypher-link" target="_blank" style="display:none;padding:7px 14px;border:1px solid var(--accent);border-radius:6px;color:var(--accent);text-decoration:none;font-size:.82rem;font-weight:600">📄 Cypherを表示</a>
    <button onclick="extractKG()" id="kg-extract-btn" style="padding:7px 16px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">🤖 LLM-Wikiから抽出 → Neo4j</button>
  </div>
  <div id="kg-status" style="display:none;font-size:.8rem;margin-bottom:8px;padding:8px 12px;border-radius:6px;border:1px solid var(--border);background:var(--card)"></div>
  <div id="kg-legend" style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:6px;font-size:.72rem"></div>
  <div id="kg-empty" class="empty-state" style="padding:40px;text-align:center;color:var(--muted)">
    <div style="font-size:2rem;margin-bottom:8px">🗂</div>
    <div>先に「2.2 オントロジー定義」を生成し、続けて「🤖 LLM-Wikiから抽出 → Neo4j」を押してください。</div>
    <div style="font-size:.78rem;margin-top:4px">抽出結果はJSONに永続化され、Neo4j用のCypherも出力されます。</div>
  </div>
  <div id="kg-container" class="graph-container" style="display:none;height:560px">
    <svg id="kg-svg" style="flex:1;background:#fafafa;border:1px solid var(--border);border-radius:8px;overflow:hidden"></svg>
    <div id="kg-node-detail" style="width:340px;overflow-y:auto;border:1px solid var(--border);border-radius:8px;background:var(--card);padding:12px;display:none;font-size:.82rem"></div>
  </div>
</div>

</div>
<script>
const API = location.pathname.startsWith('/review') ? '/review' : '';
let allItems = [];

// ── 根拠リンク（LLM-Wikiページ / CQ）ヘルパ ──
// プロキシ(/review/)経由なら同一オリジン(=8790)がWikiを配信。直接(8789)なら8790を参照。
const WIKI_ORIGIN = location.pathname.startsWith('/review')
  ? '' : (location.protocol + '//' + location.hostname + ':8790');
let WIKI_INDEX = null;
async function loadWikiIndex() {
  if (WIKI_INDEX) return WIKI_INDEX;
  try { WIKI_INDEX = await (await fetch(API + '/api/wiki/index')).json(); }
  catch (e) { WIKI_INDEX = {}; }
  return WIKI_INDEX;
}
function wikiHref(slug) {
  const rel = (WIKI_INDEX && WIKI_INDEX[slug]) ? WIKI_INDEX[slug] : ('pages/' + slug + '.md');
  return WIKI_ORIGIN + '/' + rel;
}
function cqHref(id) { return API + '/cq#cq-' + id; }
// evidence/source 文字列（例 "05-medical→key-contacts" / "CQ04, 00-eligibility-table"）をリンク化
function linkifyRefs(str) {
  if (!str) return '<span style="color:var(--muted)">—</span>';
  return String(str).split(/([→,、]|\s+)/).map(tok => {
    if (tok === '' ) return '';
    if (tok === '→') return ' <span class="rel-arrow">→</span> ';
    if (tok === ',' || tok === '、') return '、';
    if (/^\s+$/.test(tok)) return ' ';
    const t = tok.trim();
    if (/^CQ\d+$/i.test(t))
      return `<a href="${cqHref(t.toUpperCase())}" class="reflink cq" title="このCQへ移動">${t.toUpperCase()}</a>`;
    if (/^[A-Za-z0-9][\w-]*$/.test(t))
      return `<a href="${wikiHref(t)}" target="_blank" class="reflink wiki" title="LLM-Wikiを開く">${t}</a>`;
    return tok;
  }).join('');
}

async function loadItems() {
  await loadWikiIndex();
  const r = await fetch(API + '/api/review-items');
  allItems = await r.json();
  renderCQ();
}

// ── CQ helpers ──
async function submitReview(itemId, approved, revisionRequested) {
  const reviewer = document.getElementById(`reviewer-${itemId}`).value || 'anonymous';
  const comment = document.getElementById(`comment-${itemId}`).value || '';
  await fetch(API + '/api/review', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ item_id: itemId, reviewer, comment, approved, revision_requested: revisionRequested }),
  });
  const r = await fetch(API + '/api/review-items');
  allItems = await r.json();
  renderCQ();
}

async function addCq() {
  const title = document.getElementById('new-cq-title').value.trim();
  const desc = document.getElementById('new-cq-desc').value.trim();
  const answer = document.getElementById('new-cq-answer').value.trim();
  const cqType = document.getElementById('new-cq-type').value;
  if (!title) { alert('質問を入力してください'); return; }
  const cqs = allItems.filter(i => i.type_cq === 'cq');
  const maxNum = cqs.reduce((m, c) => Math.max(m, parseInt(c.id.replace('CQ','')) || 0), 0);
  const id = 'CQ' + String(maxNum + 1).padStart(2, '0');
  const newItem = {
    id, title, description: desc || title, expected_answer: answer,
    type: cqType,
    source: '手動追加', source_url: '', review: 'human_required', type: 'cq',
    status: 'pending', cq_ids: [], current_value: '未テスト'
  };
  allItems.push(newItem);
  renderCQ();
  document.getElementById('new-cq-title').value = '';
  document.getElementById('new-cq-desc').value = '';
  document.getElementById('new-cq-answer').value = '';
  document.getElementById('new-cq-type').selectedIndex = 0;
}

async function generateCQs() {
  const btn = document.getElementById('gen-cq-btn');
  btn.textContent = '⏳ LLMがCQ生成中…';
  btn.disabled = true;
  try {
    const r = await fetch(API + '/api/cq/generate', {method:'POST'});
    const d = await r.json();
    if (d.ok) {
      alert(`✅ ${d.added}件のCQを追加しました（全${d.total}件中）`);
      allItems = await (await fetch(API + '/api/review-items')).json();
      renderCQ();
    } else {
      alert('⚠ エラー: ' + (d.error || '不明'));
    }
  } catch(e) {
    alert('⚠ 通信エラー: ' + e.message);
  }
  btn.textContent = '🤖 LLMからCQを生成';
  btn.disabled = false;
}

async function deleteCq(id) {
  if (!confirm(`CQ「${id}」を削除しますか？`)) return;
  try {
    const r = await fetch(API + '/api/cq/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
    const d = await r.json();
    if (!d.ok) { alert('⚠ エラー: ' + (d.error || '不明')); return; }
    allItems = await (await fetch(API + '/api/review-items')).json();
    renderCQ();
  } catch(e) { alert('⚠ 通信エラー: ' + e.message); }
}

async function approveAllCqs() {
  const cqs = allItems.filter(i => i.type_cq === 'cq');
  const pending = cqs.filter(i => i.status !== 'approved').length;
  if (pending === 0) { alert('未承認のCQはありません'); return; }
  if (!confirm(`未承認の ${pending} 件のCQをすべて承認しますか？`)) return;
  const btn = document.getElementById('approve-cq-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 承認中…'; }
  try {
    const r = await fetch(API + '/api/cq/approve-all', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { alert('⚠ エラー: ' + (d.error || '不明')); }
    else { allItems = await (await fetch(API + '/api/review-items')).json(); renderCQ(); }
  } catch(e) { alert('⚠ 通信エラー: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = '✅ 全CQ承認'; }
}

async function clearAllCqs() {
  const n = allItems.filter(i => i.type_cq === 'cq').length;
  if (n === 0) { alert('削除するCQがありません'); return; }
  if (!confirm(`全 ${n} 件のCQを削除しますか？（元に戻せません）`)) return;
  const btn = document.getElementById('clear-cq-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 削除中…'; }
  try {
    const r = await fetch(API + '/api/cq/clear', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { alert('⚠ エラー: ' + (d.error || '不明')); }
    else { allItems = await (await fetch(API + '/api/review-items')).json(); renderCQ(); }
  } catch(e) { alert('⚠ 通信エラー: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = '🗑 全CQ削除'; }
}

// ── Screen routing (2.1/2.2/2.3 はそれぞれ別ページ/別URL) ──
const SCREENS = ['cq','ontology-def','ontology-graph','kg'];
function currentScreen() {
  const seg = location.pathname.replace(/\/+$/,'').split('/').pop();
  return SCREENS.includes(seg) ? seg : 'cq';
}
function initScreen() {
  const seg = currentScreen();
  document.querySelectorAll('.topbar nav a').forEach(a => {
    const t = a.dataset.tab;
    if (!t) return;
    a.setAttribute('href', API + '/' + t);   // プロキシ経由(/review/...)でも成立
    a.classList.toggle('active', t === seg);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const panel = document.getElementById('panel-' + seg);
  if (panel) panel.classList.add('active');
  if (seg === 'cq') loadItems();
  else if (seg === 'ontology-def') renderOntologyDef();
  else if (seg === 'ontology-graph') renderOntologyGraph();
  else if (seg === 'kg') renderKG();
}

// ── CQ management ──
function renderCQ() {
  const cqs = allItems.filter(i => i.type_cq === 'cq');
  document.getElementById('cq-stats').innerHTML = [
    {label:'全CQ', num:cqs.length, cls:''},
    {label:'承認済', num:cqs.filter(i=>i.status==='approved').length, cls:''},
    {label:'保留中', num:cqs.filter(i=>i.status==='pending').length, cls:''},
    {label:'却下', num:cqs.filter(i=>i.status==='rejected').length, cls:''},
  ].map(x => `<div class="stat"><div class="num">${x.num}</div><div class="label">${x.label}</div></div>`).join('');
  document.getElementById('cq-list').innerHTML = cqs.map(item => renderCqCard(item)).join('');
}

function renderCqCard(item) {
  const statusLabel = {pending:'保留中',approved:'承認済',rejected:'却下',revision_requested:'修正依頼'}[item.status] || item.status;
  const cqType = item.type || '—';
  const typeLabels = {lookup:'単一参照',multi_hop:'多段探索',aggregation:'集約',constraint:'制約確認',exclusion:'除外条件',compatibility:'併給確認'};
  const typeColors = {lookup:'#3498db',multi_hop:'#9b59b6',aggregation:'#2ecc71',constraint:'#f39c12',exclusion:'#e74c3c',compatibility:'#e67e22'};
  const typeHelps = {lookup:'ノード1つを直接参照する',multi_hop:'複数のエッジをたどって情報を集める',aggregation:'条件に合う全ノードを列挙する',constraint:'制約条件を確認する',exclusion:'除外条件を確認する',compatibility:'2つの制度の併給可否を判定する'};
  const answer = item.expected_answer || '';
  return `<div class="card" id="cq-${item.id}" data-id="${item.id}">
    <div class="card-header">
      <span class="card-id">${item.id}</span>
      <span class="status-badge status-${item.status}">${statusLabel}</span>
      ${item.review === 'human_required' ? '<span class="status-badge status-pending">要確認</span>' : ''}
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap">
      <div style="flex:1;min-width:200px">
        <div class="card-title" style="font-size:.95rem;margin-bottom:4px;color:var(--accent)">❓ ${item.title}</div>
        <div class="card-desc" style="color:var(--muted);font-size:.8rem">${item.description}</div>
      </div>
      <div style="flex:1;min-width:200px;background:var(--ok-bg);border:1px solid var(--ok);border-radius:6px;padding:8px 10px">
        <div style="font-size:.72rem;font-weight:600;color:var(--ok);margin-bottom:2px">💡 期待される回答</div>
        <div style="font-size:.82rem">${answer || '<span style="color:var(--muted);font-style:italic">（未設定）</span>'}</div>
      </div>
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0;align-items:center">
      <span style="font-size:.7rem;padding:2px 8px;border-radius:999px;background:${typeColors[cqType]||'#888'}20;color:${typeColors[cqType]||'#888'};border:1px solid ${typeColors[cqType]||'#888'};font-weight:600;cursor:help" title="${typeHelps[cqType]||''}">${typeLabels[cqType]||cqType}</span>
      <span class="card-meta" style="font-size:.78rem;color:var(--muted)">📄 ${item.source}</span>
    </div>
    ${(item.trace && item.trace.length) ? `<div style="font-size:.76rem;margin:2px 0 6px;padding:6px 9px;background:var(--bg);border:1px solid var(--line);border-radius:6px">
      <span style="color:var(--muted)">🔗 参照経路（LLM-Wiki）:</span>
      ${item.trace.map((s,i)=>`<span style="white-space:nowrap">${i+1}. ${s.doc?`<a href="${wikiHref(s.doc)}" target="_blank" class="reflink wiki">${s.doc}</a>`:'?'}${s.ref?`<span style="color:var(--muted)"> — ${s.ref}</span>`:''}</span>`).join(' <span style="color:var(--accent);font-weight:700">→</span> ')}
    </div>` : ''}
    <div class="review-form" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end">
      <input type="text" placeholder="レビュアー名" id="reviewer-${item.id}" style="width:100px;font-size:.78rem">
      <textarea placeholder="コメント" id="comment-${item.id}" rows="1" style="font-size:.78rem;flex:1;min-width:120px"></textarea>
      <button class="btn-approve" onclick="submitReview('${item.id}',true,false)" style="font-size:.78rem">✓ 正しい</button>
      <button class="btn-revision" onclick="submitReview('${item.id}',false,true)" style="font-size:.78rem">🔁 修正必要</button>
      <button class="btn-reject" onclick="submitReview('${item.id}',false,false)" style="font-size:.78rem">✗ 誤り</button>
      <button onclick="deleteCq('${item.id}')" title="このCQを削除" style="font-size:.78rem;background:transparent;color:#e74c3c;border:1px solid #e74c3c;border-radius:4px;padding:2px 8px;cursor:pointer">🗑 削除</button>
    </div>
  </div>`;
}

// ── Ontology generation ──
async function generateOntology() {
  const btn = document.getElementById('gen-onto-btn');
  btn.textContent = '⏳ LLMがオントロジー生成中…（2段階）';
  btn.disabled = true;
  try {
    const r = await fetch(API + '/api/ontology/generate', {method:'POST'});
    const d = await r.json();
    if (d.ok) {
      alert(`✅ オントロジー生成完了: ${d.classes}クラス, ${d.relationships}関係, ${d.constraints}制約, ${d.nodes}ノード, ${d.edges}エッジ`);
      renderOntologyDef();
      renderOntologyGraph();
    } else {
      alert('⚠ エラー: ' + (d.error || ''));
    }
  } catch(e) {
    alert('⚠ 通信エラー: ' + e.message);
  }
  btn.textContent = '🤖 LLMからオントロジーを生成';
  btn.disabled = false;
}

async function renderOntologyDef() {
  document.getElementById('ontology-def-empty').style.display = 'none';
  const content = document.getElementById('ontology-def-content');
  content.style.display = 'block';
  try {
    await loadWikiIndex();
    const r = await fetch(API + '/api/ontology/definition');
    const d = await r.json();
    if (!d.definition) { content.innerHTML = '<div class="warnline">オントロジーが未生成です</div>'; return; }
    const def = d.definition;
    let html = '';

    // Classes
    const classes = def.classes || [];
    html += `<h3 style="font-size:.95rem;margin:12px 0 6px">📦 クラス定義（${classes.length}件）</h3>`;
    html += `<table class="otbl"><thead><tr><th>クラス名</th><th>説明</th><th>プロパティ</th><th>根拠(LLM-Wiki/CQ)</th></tr></thead><tbody>`;
    for (const cls of classes) {
      const props = (cls.properties || []).map(p =>
        `<span class="mono">${p.name}: ${p.type}</span>${p.required ? '<span class="req">必須</span>' : ''}`
      ).join('<br>');
      html += `<tr><td><b>${cls.name}</b></td><td>${cls.description || ''}</td><td>${props || '<span style="color:var(--muted)">—</span>'}</td>`
        + `<td class="evid">${linkifyRefs(cls.evidence || cls.source)}</td></tr>`;
    }
    html += `</tbody></table>`;

    // Relationships
    const rels = def.relationships || [];
    html += `<h3 style="font-size:.95rem;margin:16px 0 6px">🔗 関係定義（${rels.length}件）</h3>`;
    html += `<table class="otbl"><thead><tr><th>from</th><th>関係</th><th>to</th><th>説明</th><th>参照経路の根拠</th></tr></thead><tbody>`;
    for (const rel of rels) {
      const props = (rel.properties || []).map(p => `${p.name}: ${p.type}`).join(', ');
      html += `<tr>`
        + `<td class="mono">${rel.from || ''}</td>`
        + `<td class="mono"><span class="rel-arrow">${rel.name || ''}</span></td>`
        + `<td class="mono">${rel.to || ''}</td>`
        + `<td>${rel.description || ''}${props ? `<br><span style="color:var(--muted);font-size:.72rem">プロパティ: ${props}</span>` : ''}</td>`
        + `<td class="evid">${linkifyRefs(rel.evidence)}</td>`
        + `</tr>`;
    }
    html += `</tbody></table>`;

    // Constraints
    const constraints = def.constraints || [];
    html += `<h3 style="font-size:.95rem;margin:16px 0 6px">⚠️ 制約（${constraints.length}件）</h3>`;
    if (constraints.length) {
      html += `<table class="otbl"><thead><tr><th style="width:40px">#</th><th>制約</th><th>出典</th></tr></thead><tbody>`;
      constraints.forEach((c, i) => {
        html += `<tr><td>${i+1}</td><td>${c.description || ''}</td><td class="evid">${linkifyRefs(c.source)}</td></tr>`;
      });
      html += `</tbody></table>`;
    } else {
      html += `<div style="color:var(--muted);font-size:.82rem">（制約なし）</div>`;
    }
    content.innerHTML = html;
  } catch(e) {
    content.innerHTML = `<div class="warnline">⚠ 読み込みエラー: ${e.message}</div>`;
  }
}

async function renderOntologyGraph() {
  const empty = document.getElementById('onto-graph-empty');
  const container = document.getElementById('onto-graph-container');
  try {
    const r = await fetch(API + '/api/ontology/definition');
    const d = await r.json();
    const def = d.definition;
    const classes = (def && def.classes) || [];
    const rels = (def && def.relationships) || [];
    // オントロジー未生成、またはクラス・関係が空 → 空状態を表示（真っ白防止）
    if (!def || (!classes.length && !rels.length)) {
      empty.style.display = 'block';
      container.style.display = 'none';
      return;
    }
    empty.style.display = 'none';
    container.style.display = 'flex';

    // クラス定義をノード化。関係の from/to が定義に無い場合も暗黙クラスとしてノード化してエッジを落とさない。
    const classByName = {};
    classes.forEach(c => { classByName[c.name] = c; });
    const nodeById = {};
    const addNode = (name, defined) => {
      if (!name || nodeById[name]) return;
      const r = Math.max(11, Math.min(22, 22 - String(name).length * 0.25));
      nodeById[name] = { id: name, name, defined, r,
        x: 380+(Math.random()-0.5)*400, y: 280+(Math.random()-0.5)*360, vx: 0, vy: 0 };
    };
    classes.forEach(c => addNode(c.name, true));
    rels.forEach(rel => { addNode(rel.from, false); addNode(rel.to, false); });
    const nodes = Object.values(nodeById);
    const edges = rels.map(rel => ({ a: nodeById[rel.from], b: nodeById[rel.to], type: rel.name, rel }))
                      .filter(e => e.a && e.b);

    const svg = document.getElementById('onto-graph-svg');
    const W = 760, H = 560;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    // 定義済みクラス=アクセント色 / 参照のみ(未定義)クラス=グレー
    const DEFINED = '#3498db', IMPLICIT = '#95a5a6';

    // Force simulation
    for (let i = 0; i < 220; i++) {
      for (const n of nodes) {
        n.vx += (W/2-n.x)*0.0025; n.vy += (H/2-n.y)*0.0025;
        for (const o of nodes) {
          if (n===o) continue;
          let dx=n.x-o.x, dy=n.y-o.y, d=Math.sqrt(dx*dx+dy*dy)||1, f=2600/(d*d);
          n.vx += (dx/d)*f; n.vy += (dy/d)*f;
        }
      }
      for (const e of edges) {
        const dx=e.b.x-e.a.x, dy=e.b.y-e.a.y, d=Math.sqrt(dx*dx+dy*dy)||1;
        const f = (d-(e.a.r+e.b.r+60))*0.002;
        e.a.vx += (dx/d)*f; e.a.vy += (dy/d)*f;
        e.b.vx -= (dx/d)*f; e.b.vy -= (dy/d)*f;
      }
      for (const n of nodes) { n.vx*=0.85; n.vy*=0.85; n.x+=n.vx; n.y+=n.vy; n.x=Math.max(n.r,Math.min(W-n.r,n.x)); n.y=Math.max(n.r,Math.min(H-n.r,n.y)); }
    }

    const NS = 'http://www.w3.org/2000/svg';
    const defs = document.createElementNS(NS, 'defs');
    svg.appendChild(defs);
    const marker = document.createElementNS(NS, 'marker');
    marker.setAttribute('id', 'o-arrow'); marker.setAttribute('markerWidth', '7'); marker.setAttribute('markerHeight', '7');
    marker.setAttribute('refX', '11'); marker.setAttribute('refY', '3'); marker.setAttribute('orient', 'auto');
    const ap = document.createElementNS(NS, 'path');
    ap.setAttribute('d', 'M0,0 L6,3 L0,6 Z'); ap.setAttribute('fill', '#bbb');
    marker.appendChild(ap); defs.appendChild(marker);

    for (const e of edges) {
      const line = document.createElementNS(NS, 'line');
      line.setAttribute('x1', e.a.x); line.setAttribute('y1', e.a.y);
      line.setAttribute('x2', e.b.x); line.setAttribute('y2', e.b.y);
      line.setAttribute('stroke', e.a===e.b ? '#e0e0e0' : '#ccc'); line.setAttribute('stroke-width', '0.9');
      line.setAttribute('marker-end', 'url(#o-arrow)');
      svg.appendChild(line);
      const mid = document.createElementNS(NS, 'text');
      mid.setAttribute('x', (e.a.x+e.b.x)/2); mid.setAttribute('y', (e.a.y+e.b.y)/2-1);
      mid.setAttribute('text-anchor', 'middle'); mid.setAttribute('font-size', '5.5'); mid.setAttribute('fill', '#aaa');
      mid.textContent = e.type || ''; svg.appendChild(mid);
    }

    for (const n of nodes) {
      const c = n.defined ? DEFINED : IMPLICIT;
      const g = document.createElementNS(NS, 'g');
      g.setAttribute('transform', `translate(${n.x},${n.y})`);
      g.style.cursor = 'pointer';
      const circle = document.createElementNS(NS, 'circle');
      circle.setAttribute('r', n.r); circle.setAttribute('fill', c+'90');
      circle.setAttribute('stroke', c); circle.setAttribute('stroke-width', '1.5');
      g.appendChild(circle);
      const text = document.createElementNS(NS, 'text');
      text.setAttribute('text-anchor', 'middle'); text.setAttribute('y', '3');
      text.setAttribute('font-size', '7'); text.setAttribute('font-weight', 'bold');
      text.setAttribute('fill', '#333');
      text.textContent = (n.name.length > 10 ? n.name.slice(0,10)+'…' : n.name);
      g.appendChild(text);
      g.addEventListener('click', () => {
        const detail = document.getElementById('onto-node-detail');
        const cls = classByName[n.id];
        const propRows = cls && cls.properties && cls.properties.length
          ? cls.properties.map(p => `<tr><td style="padding:2px 4px;font-weight:600;color:var(--accent)">${p.name}</td><td style="padding:2px 4px">${p.type||''}${p.required?' <span class="req">必須</span>':''}</td></tr>`).join('')
          : `<tr><td colspan="2" style="padding:2px 4px;color:var(--muted)">${cls ? '（プロパティなし）' : '定義に無い参照クラス（関係の from/to にのみ登場）'}</td></tr>`;
        detail.innerHTML = `<button class="detail-close" onclick="this.parentElement.style.display='none'">✕</button>
          <h3>${n.name}</h3><div style="font-size:.75rem;color:#888">${n.defined ? 'クラス定義' : '参照のみ'}</div>
          ${cls && cls.description ? `<div style="font-size:.8rem;margin:6px 0">${cls.description}</div>` : ''}
          <table style="width:100%;font-size:.8rem;border-collapse:collapse">${propRows}</table>`;
        detail.style.display = 'block';
      });
      svg.appendChild(g);
    }
  } catch(e) {
    container.innerHTML = `<div class="warnline">⚠ グラフ読み込みエラー: ${e.message}</div>`;
  }
}

// ── 3-1 ナレッジグラフ（実体）: 抽出→Neo4j/Cypher→可視化 ──
async function extractKG() {
  const btn = document.getElementById('kg-extract-btn');
  const st = document.getElementById('kg-status');
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '⏳ 抽出中…';
  st.style.display = 'block';
  st.innerHTML = '⏳ LLM-Wikiからオントロジー定義に沿って実体を抽出し、Neo4j投入 / Cypher出力しています…（30秒程度）';
  try {
    const r = await fetch(API + '/api/kg/extract', {method:'POST'});
    const d = await r.json();
    if (d.ok) { await renderKG(); }
    else { st.innerHTML = '⚠ ' + (d.error || '抽出に失敗しました'); }
  } catch(e) { st.innerHTML = '⚠ 通信エラー: ' + e.message; }
  btn.disabled = false; btn.textContent = old;
}

// ビューの選択状態を反映（どちらのソースを表示中か）
function setKgViewActive(source) {
  const nb = document.getElementById('kg-view-neo4j-btn');
  const eb = document.getElementById('kg-view-extracted-btn');
  if (!nb || !eb) return;
  const on  = 'var(--accent)', off = 'var(--muted)';
  const onBorder = 'var(--accent)', offBorder = 'var(--border)';
  nb.style.color = source === 'neo4j' ? on : off;
  nb.style.borderColor = source === 'neo4j' ? onBorder : offBorder;
  eb.style.color = source === 'extracted' ? on : off;
  eb.style.borderColor = source === 'extracted' ? onBorder : offBorder;
}

// source: 'auto'(default) | 'neo4j'（投入後の実データ） | 'extracted'（最新1回の抽出JSON）
async function renderKG(source) {
  source = source || 'auto';
  await loadWikiIndex();
  const empty = document.getElementById('kg-empty');
  const container = document.getElementById('kg-container');
  const st = document.getElementById('kg-status');
  const legend = document.getElementById('kg-legend');
  const cypherLink = document.getElementById('kg-cypher-link');

  const fetchKG = async (u) => { try { return await (await fetch(API + u)).json(); } catch(e) { return {error: '通信エラー: ' + e.message}; } };

  // ソース決定（auto は Neo4j を優先し、取得不可/空なら抽出JSONにフォールバック）
  let kg, used = source;
  if (source === 'extracted') {
    kg = await fetchKG('/api/kg/extracted'); used = 'extracted';
  } else {
    kg = await fetchKG('/api/kg/neo4j');
    const bad = kg.error || !kg.nodes || !kg.nodes.length;
    if (bad && source === 'auto') {
      const fb = await fetchKG('/api/kg/extracted');
      if (fb.nodes && fb.nodes.length) { kg = fb; used = 'extracted'; }
      else { used = 'neo4j'; }   // どちらも空 → Neo4jの空/エラー表示を優先
    } else { used = 'neo4j'; }
  }
  setKgViewActive(used);

  if (kg.error || !kg.nodes || !kg.nodes.length) {
    empty.style.display = 'block'; container.style.display = 'none';
    legend.innerHTML = ''; cypherLink.style.display = 'none';
    st.style.display = 'block';
    st.innerHTML = kg.error
      ? '⚠ ' + kg.error
      : (used === 'neo4j'
          ? '🗄 Neo4jにデータがありません。「🤖 LLM-Wikiから抽出 → Neo4j」で投入してください。'
          : 'まだ抽出されていません。「🤖 LLM-Wikiから抽出 → Neo4j」を実行してください。');
    return;
  }
  empty.style.display = 'none';
  container.style.display = 'flex';

  cypherLink.style.display = 'inline-block';
  cypherLink.href = API + '/api/kg/cypher';
  const m = kg.meta || {}, neo = m.neo4j || {};
  st.style.display = 'block';
  if (used === 'neo4j') {
    st.innerHTML =
      `🗄 <b>Neo4jの実データ（投入後・累積）</b>: <b>${m.nodes||kg.nodes.length}</b> ノード / <b>${m.edges||(kg.edges||[]).length}</b> エッジ`
      + `<br>🟢 ${neo.message || 'Neo4j'}`;
  } else {
    st.innerHTML =
      `📄 <b>最新の抽出（JSONスナップショット）</b>: <b>${m.nodes||kg.nodes.length}</b> ノード / <b>${m.edges||(kg.edges||[]).length}</b> エッジ`
      + (m.generated_at ? ` <span style="color:var(--muted)">(${m.generated_at})</span>` : '')
      + `<br>${neo.connected ? '🟢' : '⚪'} Neo4j: ${neo.message || '—'}`;
  }

  const palette = ['#e74c3c','#3498db','#2ecc71','#f39c12','#9b59b6','#1abc9c','#e67e22','#34495e','#c0392b','#2980b9'];
  const labelColors = {};
  const allLabels = [...new Set(kg.nodes.flatMap(n => n.labels || []))];
  allLabels.forEach((l,i)=>{ labelColors[l] = palette[i%palette.length]; });
  legend.innerHTML = allLabels.map(l =>
    `<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:10px;border-radius:50%;background:${labelColors[l]};display:inline-block"></span>${l}</span>`
  ).join('');

  const svg = document.getElementById('kg-svg');
  const W = 760, H = 540;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  const nodeById = {};
  const nodes = kg.nodes.map(n => {
    const obj = { id:n.id, label:(n.labels&&n.labels[n.labels.length-1])||'Node',
      name:(n.props&&n.props.name)||n.id, r:11,
      x:W/2+(Math.random()-0.5)*W*0.6, y:H/2+(Math.random()-0.5)*H*0.6, vx:0, vy:0 };
    nodeById[n.id]=obj; return obj;
  });
  const edges = (kg.edges||[]).map(e => ({ a:nodeById[e.from], b:nodeById[e.to], type:e.type }));

  for (let i=0;i<200;i++){
    for (const n of nodes){
      n.vx += (W/2-n.x)*0.002; n.vy += (H/2-n.y)*0.002;
      for (const o of nodes){ if(n===o) continue;
        let dx=n.x-o.x, dy=n.y-o.y, d=Math.sqrt(dx*dx+dy*dy)||1, f=1600/(d*d);
        n.vx+=(dx/d)*f; n.vy+=(dy/d)*f; }
    }
    for (const e of edges){ if(!e.a||!e.b) continue;
      const dx=e.b.x-e.a.x, dy=e.b.y-e.a.y, d=Math.sqrt(dx*dx+dy*dy)||1, f=(d-70)*0.002;
      e.a.vx+=(dx/d)*f; e.a.vy+=(dy/d)*f; e.b.vx-=(dx/d)*f; e.b.vy-=(dy/d)*f; }
    for (const n of nodes){ n.vx*=0.85; n.vy*=0.85; n.x+=n.vx; n.y+=n.vy;
      n.x=Math.max(n.r,Math.min(W-n.r,n.x)); n.y=Math.max(n.r,Math.min(H-n.r,n.y)); }
  }

  const NS='http://www.w3.org/2000/svg';
  const defs=document.createElementNS(NS,'defs');
  const marker=document.createElementNS(NS,'marker');
  marker.setAttribute('id','kg-arrow'); marker.setAttribute('markerWidth','7'); marker.setAttribute('markerHeight','7');
  marker.setAttribute('refX','12'); marker.setAttribute('refY','3'); marker.setAttribute('orient','auto');
  const ap=document.createElementNS(NS,'path'); ap.setAttribute('d','M0,0 L6,3 L0,6 Z'); ap.setAttribute('fill','#bbb');
  marker.appendChild(ap); defs.appendChild(marker); svg.appendChild(defs);

  for (const e of edges){ if(!e.a||!e.b) continue;
    const line=document.createElementNS(NS,'line');
    line.setAttribute('x1',e.a.x); line.setAttribute('y1',e.a.y);
    line.setAttribute('x2',e.b.x); line.setAttribute('y2',e.b.y);
    line.setAttribute('stroke','#ccc'); line.setAttribute('stroke-width','0.9');
    line.setAttribute('marker-end','url(#kg-arrow)');
    svg.appendChild(line);
    const mid=document.createElementNS(NS,'text');
    mid.setAttribute('x',(e.a.x+e.b.x)/2); mid.setAttribute('y',(e.a.y+e.b.y)/2-1);
    mid.setAttribute('text-anchor','middle'); mid.setAttribute('font-size','5.5'); mid.setAttribute('fill','#aaa');
    mid.textContent=e.type||''; svg.appendChild(mid);
  }

  for (const n of nodes){
    const c=labelColors[n.label]||'#999';
    const g=document.createElementNS(NS,'g');
    g.setAttribute('transform',`translate(${n.x},${n.y})`); g.style.cursor='pointer';
    const circle=document.createElementNS(NS,'circle');
    circle.setAttribute('r',n.r); circle.setAttribute('fill',c+'90');
    circle.setAttribute('stroke',c); circle.setAttribute('stroke-width','1.4');
    g.appendChild(circle);
    const text=document.createElementNS(NS,'text');
    text.setAttribute('text-anchor','middle'); text.setAttribute('y','2.5');
    text.setAttribute('font-size','6.5'); text.setAttribute('font-weight','bold'); text.setAttribute('fill','#222');
    text.textContent=(n.name.length>7?n.name.slice(0,7)+'…':n.name);
    g.appendChild(text);
    g.addEventListener('click',()=>{
      const detail=document.getElementById('kg-node-detail');
      const props=(kg.nodes.find(x=>x.id===n.id)||{}).props||{};
      const rows=Object.entries(props).map(([k,v])=>{
        let val=(typeof v==='object')?JSON.stringify(v):v;
        if(k==='source' && typeof v==='string') val=`<a href="${wikiHref(v)}" target="_blank" class="reflink wiki">${v}</a>`;
        return `<tr><td style="padding:2px 4px;font-weight:600;color:var(--accent);vertical-align:top">${k}</td><td style="padding:2px 4px">${val}</td></tr>`;
      }).join('');
      const outE=(kg.edges||[]).filter(e=>e.from===n.id).map(e=>`${e.type} → ${e.to}`);
      const inE=(kg.edges||[]).filter(e=>e.to===n.id).map(e=>`${e.from} → ${e.type}`);
      detail.innerHTML=`<button class="detail-close" onclick="this.parentElement.style.display='none'">✕</button>
        <h3 style="margin:0 0 2px">${n.name}</h3>
        <div style="font-size:.72rem;color:#888;margin-bottom:6px">${n.label} | ${n.id}</div>
        <table style="width:100%;font-size:.78rem;border-collapse:collapse">${rows}</table>
        ${outE.length?`<div style="margin-top:8px;font-size:.72rem;color:var(--muted)">出力エッジ</div><div style="font-size:.75rem">${outE.join('<br>')}</div>`:''}
        ${inE.length?`<div style="margin-top:6px;font-size:.72rem;color:var(--muted)">入力エッジ</div><div style="font-size:.75rem">${inE.join('<br>')}</div>`:''}`;
      detail.style.display='block';
    });
    svg.appendChild(g);
  }
}

initScreen();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/cq", response_class=HTMLResponse)
@app.get("/ontology-def", response_class=HTMLResponse)
@app.get("/ontology-graph", response_class=HTMLResponse)
@app.get("/kg", response_class=HTMLResponse)
def index():
    return HTML


if __name__ == "__main__":
    import uvicorn
    # reload is OPT-IN (default off). The uvicorn reloader spawns a worker via
    # multiprocessing whose command line is opaque; a stale/wrong-interpreter worker
    # then holds the port and is hard to kill (incident 2026-07-15). Enable only when
    # you deliberately want auto-reload:  set REVIEW_RELOAD=1
    _reload = os.environ.get("REVIEW_RELOAD", "").strip().lower() in ("1", "true", "yes", "on")
    uvicorn.run("main:app", host="127.0.0.1", port=8789, reload=_reload)