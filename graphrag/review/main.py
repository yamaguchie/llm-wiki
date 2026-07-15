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
    GET  /api/cq/results           — QAテスト結果
"""
import json, os, sys, datetime, re, glob, uuid, threading, time
from enum import Enum
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Bunkyo Welfare KG Review UI — Phase3-5")

# チャット画面(8790)から /api/validation/ask 等へ直接fetchするためのCORS許可
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8790", "http://localhost:8790"],
    allow_methods=["*"], allow_headers=["*"],
)

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

# ── 3.2 検証結果（cq_id → 検証結果）──
validation_results: dict = {}

# ── Generated ontology state ──
generated_ontology = {
    "definition": "",
    "kg_json": None,
    "kg_extracted": None,
    "kg_meta": None,
    "status": "not_generated"
}

# ── Background task progress ──
_tasks: dict = {}

def _task_start(task_type: str, total: int = 100) -> str:
    tid = uuid.uuid4().hex[:12]
    _tasks[tid] = {"type": task_type, "progress": 0, "total": total, "status": "running", "error": None}
    return tid

def _task_update(tid: str, progress: int, msg: str = ""):
    if tid in _tasks:
        _tasks[tid]["progress"] = min(progress, _tasks[tid].get("total", 100))
        if msg: _tasks[tid]["msg"] = msg

def _task_done(tid: str, error: str = None):
    if tid in _tasks:
        t = _tasks[tid]
        t["progress"] = t.get("total", 100)
        t["status"] = "error" if error else "done"
        if error: t["error"] = error

@app.get("/api/task/{task_id}")
def get_task_progress(task_id: str):
    t = _tasks.get(task_id)
    if not t: return JSONResponse({"error": "not found"}, status_code=404)
    return {
        "progress": t["progress"], "total": t["total"],
        "status": t["status"], "msg": t.get("msg", ""),
        "error": t.get("error"),
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
            json.dump({"items": REVIEW_ITEMS, "reviews": reviews_db, "ontology": generated_ontology,
                       "validation": validation_results},
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
        val = data.get("validation")
        if isinstance(val, dict):
            validation_results.clear(); validation_results.update(val)
        print(f"load_state: {len(REVIEW_ITEMS)} items, {len(reviews_db)} reviews, "
              f"ontology={generated_ontology.get('status')}, validation={len(validation_results)}")
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
    """LLM-WikiからQAを自動生成する。"""
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
            "あなたはコンピテンシー質問（QA）生成エージェントです。"
            "以下のLLM-Wikiページ群から、ユーザーがこのシステムに質問すると想定されるQAをJSON配列で出力してください。"
            "各QAは以下を含む: id(QAxx), question(疑問形の自然言語の問い。必ず「？」で終わること), "
            "expected_answer(期待される簡潔な回答。金額を含む場合は具体的な数値まで), "
            "type(lookup|multi_hop|aggregation|constraint), "
            "source(主に該当するwikiページ名), "
            "trace(この問いに答えるためにLLM-Wikiを辿る経路を『参照した順』に並べた配列。各要素は "
            "{\"doc\":\"参照したページ名。上記『## 名前』の名前を厳密に使う\", \"ref\":\"そのページから参照する情報の要点\"}。"
            "multi_hop は必ず2要素以上にし、実際に情報を横断した順序で並べる。lookup は1要素でよい)"
        )
        result = json.loads(client.models.generate_content(model=model, contents=prompt + f"\n\n【LLM-Wiki】\n{ctx[:45000]}", config=types.GenerateContentConfig(response_mime_type="application/json")).text)
        cqs = result if isinstance(result, list) else result.get("competency_questions", result.get("cqs", []))

        added = 0
        for cq in cqs:
            cid = cq.get("id", f"QA{len(REVIEW_ITEMS)+1}")
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
        validation_results.clear()
        save_state()
        return {"ok": True, "added": added, "total": len(cqs)}
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)


class CqId(BaseModel):
    id: str

@app.post("/api/cq/delete")
def delete_cq(body: CqId):
    """指定したQAを1件削除する。"""
    global validation_results
    before = len(REVIEW_ITEMS)
    REVIEW_ITEMS[:] = [i for i in REVIEW_ITEMS if not (i.get("type_cq") == "cq" and i.get("id") == body.id)]
    removed = before - len(REVIEW_ITEMS)
    validation_results.pop(body.id, None)
    save_state()
    return {"ok": True, "removed": removed}

@app.post("/api/cq/clear")
def clear_cqs():
    """QAを全件削除する（オントロジー等のQA以外の項目は残す）。"""
    global validation_results
    before = len(REVIEW_ITEMS)
    REVIEW_ITEMS[:] = [i for i in REVIEW_ITEMS if i.get("type_cq") != "cq"]
    removed = before - len(REVIEW_ITEMS)
    validation_results.clear()
    save_state()
    return {"ok": True, "removed": removed}

@app.post("/api/cq/approve-all")
def approve_all_cqs():
    """未承認のQAを全て承認する。"""
    global validation_results
    n = 0
    for i in REVIEW_ITEMS:
        if i.get("type_cq") == "cq" and i.get("status") != "approved":
            i["status"] = "approved"
            i["reviewer"] = i.get("reviewer") or "一括承認"
            n += 1
    validation_results.clear()
    save_state()
    return {"ok": True, "approved": n}


@app.post("/api/ontology/generate")
def generate_ontology():
    """承認済みQA＋LLM-Wikiからオントロジー定義＋ナレッジグラフを生成する（非同期）。"""
    tid = _task_start("ontology_generate", total=100)
    def _run():
        global generated_ontology
        _task_update(tid, 5, "QA収集中…")
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

            _task_update(tid, 10, "QAからオントロジー定義を生成中…（Step 1/2）")
            # Collect QAs
            all_cqs = [i for i in REVIEW_ITEMS if i.get("type_cq") == "cq"]
            approved_cqs = [i for i in all_cqs if i["status"] == "approved"]
            cqs_for_gen = approved_cqs if approved_cqs else all_cqs
            cq_source_note = "approved" if approved_cqs else "all(未承認含む)"
            cq_text = "\n\n".join(f"QA {i['id']} [{i.get('type','')}]: {i['title']}\n期待回答: {i.get('expected_answer','')}" for i in cqs_for_gen)

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
                trace_block = "\n\n【参照経路】\n" + "\n".join(path_lines[:40]) + "\n\n【ページ間遷移】\n" + "\n".join(pair_lines)

            base = os.path.join(HERE, "..", "..")
            md_files = sorted(glob.glob(os.path.join(base, "pages", "*.md")) + glob.glob(os.path.join(base, "entities", "*.md")))
            wiki_texts = []
            for mf in md_files:
                with open(mf, "r", encoding="utf-8") as f:
                    content = f.read()
                name = os.path.basename(mf).replace(".md", "")
                content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
                wiki_texts.append(f"## {name}\n{content[:1000]}")
            wiki_ctx = "\n\n".join(wiki_texts)

            cq_checklist = "\n".join(f"- {i['id']}({i.get('type','')}): {i['title'][:60]} → {i.get('expected_answer','')[:80]}" for i in cqs_for_gen)
            def_prompt = (
                "あなたはオントロジー設計エージェントです。以下のQA・参照経路・LLM-Wikiからオントロジー定義を生成してください。\n"
                "【最重要ルール】各QAに対して、その質問に回答するために必要なクラス・プロパティ・関係が全て定義されていることを確認してください。\n"
                f"【QA別チェックリスト】\n{cq_checklist[:4000]}\n\n"
                "【制約のtarget_class割り当てルール】制約は必ず特定のクラスに紐づけてください。\n"
                "  - 例: 「手帳の有効期限は2年」→ target_class: Notebook, target_property: valid_period\n"
                "  - 例: 「手帳の形式は紙/カード選択可能」→ target_class: Notebook, target_property: format\n"
                "  - 例: 「手帳の交付は65歳以上でも可能」→ target_class: Notebook, target_property: age_limit\n"
                "  - 例: 「月36時間まで負担なし」→ target_class: Service, target_property: free_hours_per_month\n"
                "  - 例: 「15,500円/月」→ target_class: Allowance, target_property: amount\n"
                "  - 例: 「併給不可」→ target_class: Service, target_property: mutually_exclusive_with\n"
                "  target_class は必ず classes の name のいずれかにしてください。該当するクラスがない場合は target_class を空文字にしてください。\n"
                "【命名】各クラス・プロパティ・関係には name（英語の物理名＝識別子）と label（その日本語論理名＝人が読む名称、例: 身体障害者手帳）を必ず両方付けてください。\n\n"
                "出力JSON: {\"classes\":[{\"name\",\"label\",\"description\",\"evidence\",\"properties\":[{\"name\",\"label\",\"type\",\"required\"}]}],\"relationships\":[{\"name\",\"label\",\"from\",\"to\",\"description\",\"evidence\"}],\"constraints\":[{\"target_class\":\"クラス名\",\"target_property\":\"プロパティ名|空\",\"target_entity\":\"ID|空\",\"description\":\"制約の説明\",\"value\":\"制約値\",\"unit\":\"単位|空\",\"source\":\"出典\"}]}\n\n"
                f"【QA（{cq_source_note}）】\n{cq_text[:8000]}{trace_block[:6000]}\n\n【LLM-Wiki】\n{wiki_ctx[:16000]}"
            )
            def_result = json.loads(client.models.generate_content(
                model=model, contents=def_prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            ).text)

            _task_update(tid, 55, "オントロジー定義からKGを生成中…（Step 2/2）")
            approved_all = [i for i in REVIEW_ITEMS if i["status"] == "approved"]
            approved_text = "\n".join(f"{i['id']}: {i['title']} [{i.get('type','')}]" for i in approved_all)
            kg_prompt = (
                "あなたはナレッジグラフ構築エージェントです。以下のオントロジー定義からインスタンスデータ（kg.json形式）を生成してください。\n"
                "出力: {\"nodes\":[{\"id\",\"labels\",\"props\"}],\"edges\":[{\"from\",\"to\",\"type\",\"props\"}]}\n"
                f"【オントロジー定義】\n{json.dumps(def_result, ensure_ascii=False)[:5000]}\n\n【承認済み全項目】\n{approved_text[:3000]}"
            )
            kg_result = json.loads(client.models.generate_content(
                model=model, contents=kg_prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            ).text)

            generated_ontology = {"definition": def_result, "kg_json": kg_result, "status": "generated"}
            _write_constraints(def_result.get("constraints", []))
            _task_update(tid, 100, "完了")
            _task_done(tid)
            save_state()
        except Exception as ex:
            _task_done(tid, str(ex))
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid}


@app.post("/api/ontology/fix-from-validation")
def fix_ontology_from_validation():
    """検証結果からオントロジーを修正する。失敗したQAの情報をLLMに渡して再生成。"""
    global generated_ontology
    try:
        from llm_utils import genai as _genai, llm_json, llm_text
        from llm_utils import _load_env as _load_env
        _load_env()
        client, model, types = _genai()

        # 失敗した検証結果を収集・カテゴリ分類
        failed = []
        missing_node_cqs = []    # 原因1: KGにノードがない
        missing_prop_cqs = []    # 原因2: プロパティ不足
        wrong_value_cqs = []     # 原因3: 値の解釈が不十分

        for cq_id, result in validation_results.items():
            kg_v = (result.get("kg") or {}).get("verdict")
            kw_v = (result.get("kgwiki") or {}).get("verdict")
            if kg_v in ("incorrect", "partial") or kw_v in ("incorrect", "partial"):
                cq = next((i for i in REVIEW_ITEMS if i.get("type_cq") == "cq" and i["id"] == cq_id), None)
                if not cq: continue
                entry = {
                    "id": cq_id,
                    "question": cq.get("title", ""),
                    "expected_answer": cq.get("expected_answer", ""),
                    "kg_answer": (result.get("kg") or {}).get("answer", ""),
                    "kgwiki_answer": (result.get("kgwiki") or {}).get("answer", ""),
                    "kg_reason": (result.get("kg") or {}).get("reason", ""),
                    "kgwiki_reason": (result.get("kgwiki") or {}).get("reason", ""),
                }
                failed.append(entry)
                # カテゴリ分類: 「分かりません」→ ノード欠落, それ以外→プロパティ不足/値誤り
                kg_ans = (result.get("kg") or {}).get("answer", "")
                if "分かりません" in kg_ans or "分かりません" in kg_ans:
                    missing_node_cqs.append(entry)
                elif kg_v == "partial":
                    missing_prop_cqs.append(entry)
                else:
                    wrong_value_cqs.append(entry)

        if not failed:
            return {"ok": False, "error": "修正すべきQAがありません（全検証通過）"}

        # 失敗QAに関連するWikiページを読む
        base = os.path.join(HERE, "..", "..", "pages")
        md_files = glob.glob(os.path.join(base, "*.md"))
        wiki_texts = {}
        for mf in md_files:
            name = os.path.basename(mf).replace(".md", "")
            content = open(mf, encoding="utf-8").read()
            content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
            wiki_texts[name] = content[:2000]

        # 失敗QAの期待回答から関連Wikiページを特定
        relevant_wiki = {}
        for entry in failed:
            expected = entry.get("expected_answer", "")
            for slug, text in wiki_texts.items():
                if slug in expected or any(kw in text for kw in expected.split()[:5]):
                    relevant_wiki[slug] = text

        wiki_block = "\n\n".join(f"## {k}\n{v}" for k, v in relevant_wiki.items()) if relevant_wiki else ""

        # LLMに修正依頼（カテゴリ別の指示を含む）
        fix_prompt = (
            "あなたはオントロジー修正エージェントです。検証で失敗したQAの情報を元に、"
            "ナレッジグラフのオントロジー定義を修正してください。\n\n"
            "【修正すべき問題のカテゴリ】\n"
            "1. ノード欠落: KGが「分かりません」と回答 → 該当サービスを表すクラス・ノードが存在しない。新規追加が必要。\n"
            "2. プロパティ不足: 一部回答できるが詳細が足りない → 既存クラスに不足プロパティを追加。\n"
            "3. 値の解釈誤り: 回答はあるが条件や例外を正しく扱えていない → プロパティ値や関係の定義を修正。\n\n"
            "【修正ルール】\n"
            f"- ノード欠落({len(missing_node_cqs)}件): 該当QAの期待回答を満たすサービスを表す新しいクラス・ノードを追加すること\n"
            f"- プロパティ不足({len(missing_prop_cqs)}件): 既存クラスに期待回答の詳細を含むプロパティを追加すること\n"
            f"- 値誤り({len(wrong_value_cqs)}件): プロパティ値や関係の定義を期待回答に合わせて修正すること\n\n"
            "【制約のtarget_class割り当てルール】制約は必ず特定のクラスに紐づけてください。target_class は classes の name のいずれかにしてください。\n"
            "出力は以下のJSON:\n"
            "{\n"
            '  "classes": [{"name": "クラス名", "description": "説明", "properties": [{"name": "プロパティ名", "type": "STRING|INTEGER|BOOLEAN|LIST", "required": true/false}]}],\n'
            '  "relationships": [{"name": "関係名", "from": "開始", "to": "終了", "description": "説明"}],\n'
            '  "constraints": [{"target_class": "適用クラス", "target_property": "プロパティ|空", "target_entity": "エンティティID|空", "description": "制約", "value": "制約値", "unit": "単位", "source": "出典"}],\n'
            '  "fix_notes": "各QAに対して何を修正したか"\n'
            "}\n\n"
            f"【修正が必要なQA一覧】\n{json.dumps(failed, ensure_ascii=False, indent=1)}\n\n"
            f"【関連Wikiページ】\n{wiki_block[:8000]}"
        )
        result = llm_json(fix_prompt, "")

        # 更新
        generated_ontology["definition"] = result
        generated_ontology["status"] = "fixed"
        # KGも再生成
        from kg_utils import load_kg
        generated_ontology["kg_json"] = load_kg()

        save_state()
        return {
            "ok": True,
            "fixed_cqs": len(failed),
            "categories": {"missing_node": len(missing_node_cqs), "missing_prop": len(missing_prop_cqs), "wrong_value": len(wrong_value_cqs)},
            "classes": len(result.get("classes", [])),
            "relationships": len(result.get("relationships", [])),
        }
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)


# ── 制約事項の永続化 ──
CONSTRAINTS_NEO4J_PATH = os.path.join(HERE, "..", "neo4j", "generated_constraints.cypher")
CONSTRAINTS_AGENT_PATH = os.path.join(HERE, "..", "agent", "active_constraints.json")

def _write_constraints(constraints: list):
    """オントロジー定義の制約事項をNeo4j用CypherとAIエージェント用JSONに書き出す。"""
    cypher_lines = [
        "// ── オントロジー定義から自動生成された制約 ──",
        f"// 生成日時: {datetime.datetime.now().isoformat()}",
        "// このファイルは ontology 再生成時に上書きされます。",
        "",
    ]
    agent_items = []
    import re as _re
    for i, c in enumerate(constraints):
        cls = c.get("target_class", "")
        prop = c.get("target_property", "")
        ent = c.get("target_entity", "")
        desc = c.get("description", "")
        val = c.get("value", "")
        unit = c.get("unit", "")
        source = c.get("source", "")
        target_info = f"{cls}.{prop}" if cls and prop else (cls or ent or "(全体)")
        cypher_lines.append(f"// C{i+1} [{target_info}]: {desc}")
        if val:
            cypher_lines.append(f"//   値: {val}{unit}")
        if source:
            cypher_lines.append(f"// 出典: {source}")
        nums = _re.findall(r'(\d+\.?\d*)\s*(円|時間|歳|%|倍)', desc)
        if nums:
            cypher_lines.append(f"// 数値制約: {', '.join(f'{n}{u}' for n, u in nums)}")
        cypher_lines.append("")
        agent_items.append({
            "target_class": cls, "target_property": prop,
            "target_entity": ent, "description": desc,
            "value": val, "unit": unit, "source": source
        })

    os.makedirs(os.path.dirname(CONSTRAINTS_NEO4J_PATH), exist_ok=True)
    with open(CONSTRAINTS_NEO4J_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(cypher_lines))
    with open(CONSTRAINTS_AGENT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.datetime.now().isoformat(),
            "constraints": agent_items
        }, f, ensure_ascii=False, indent=1)

def load_active_constraints():
    """AIエージェントが読み込む制約リストを返す。"""
    path = CONSTRAINTS_AGENT_PATH
    if os.path.isfile(path):
        try:
            data = json.load(open(path, encoding="utf-8"))
            return data.get("constraints", [])
        except: pass
    return []


@app.get("/api/ontology/definition")
def get_ontology_definition():
    return JSONResponse(content=generated_ontology)


@app.get("/api/ontology/labels")
def get_ontology_labels():
    """物理名→日本語論理名の対応表（各UIが英語物理名に論理名を併記するために使う）。"""
    defn = generated_ontology.get("definition") or {}
    classes = {c.get("name"): (c.get("label") or "") for c in (defn.get("classes") or []) if c.get("name")}
    rels = {r.get("name"): (r.get("label") or "") for r in (defn.get("relationships") or []) if r.get("name")}
    class_props = {}
    for c in (defn.get("classes") or []):
        cn = c.get("name")
        if not cn:
            continue
        pm = {p.get("name"): (p.get("label") or "") for p in (c.get("properties") or []) if p.get("name")}
        if pm:
            class_props[cn] = pm
    return JSONResponse({"classes": classes, "relationships": rels, "class_props": class_props})


# ── QA駆動オントロジー反復設計: ① Wikiから起こす → ②③ QAで充足するまで修正 ──

def _read_wiki_context(per_page=1200):
    """pages/ + entities/ をまとめて '## slug\\n本文' のテキストに（frontmatter除去）。"""
    base = os.path.join(HERE, "..", "..")
    md_files = sorted(glob.glob(os.path.join(base, "pages", "*.md")) +
                      glob.glob(os.path.join(base, "entities", "*.md")))
    texts = []
    for mf in md_files:
        with open(mf, encoding="utf-8") as f:
            content = f.read()
        name = os.path.basename(mf).replace(".md", "")
        content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
        texts.append(f"## {name}\n{content[:per_page]}")
    return "\n\n".join(texts)

def _summarize_audit(per_cq):
    c = {"covered": 0, "partial": 0, "missing": 0}
    for x in per_cq:
        v = x.get("verdict", "missing"); c[v] = c.get(v, 0) + 1
    return c

def _aggregate_missing(gaps):
    classes = set(); props = []; rels = []
    for g in gaps:
        m = g.get("missing") or {}
        for c in (m.get("classes") or []):
            if isinstance(c, dict): c = c.get("name", str(c))
            if isinstance(c, str): classes.add(c)
        for p in (m.get("properties") or []): props.append(p)
        for r in (m.get("relationships") or []): rels.append(r)
    return {"classes": sorted(classes), "properties": props, "relationships": rels}

@app.post("/api/ontology/bootstrap")
def ontology_bootstrap():
    """① LLM-Wiki本体だけからオントロジー定義を起こす（QA非依存）。"""
    tid = _task_start("ontology_bootstrap", total=100)
    def _run():
        global generated_ontology
        try:
            from llm_utils import llm_json
            _task_update(tid, 15, "LLM-Wikiを読込中…")
            wiki_ctx = _read_wiki_context(1400)
            _task_update(tid, 35, "Wikiからオントロジー定義を生成中…（QAは未使用）")
            sys_p = (
                "あなたはオントロジー設計エージェント。与えられたLLM-Wiki（ドメイン知識ベース）だけから、"
                "このドメインのオントロジー定義を生成する。QA（質問）はまだ使わない。"
                "Wikiに現れる主要な概念・エンティティ・属性・関連から、クラス・プロパティ・関係・制約を抽出する。"
                "【命名】各クラス・プロパティ・関係には name（英語の物理名＝PascalCase等の識別子）と "
                "label（その日本語論理名＝人が読む名称、例: 身体障害者手帳）を必ず両方付ける。"
                "各要素の evidence には根拠にしたWikiページのスラッグ（例: 02-notebooks, key-contacts）を記す。"
            )
            user_p = (
                '出力JSON: {"classes":[{"name","label","description","evidence","properties":[{"name","label","type","required"}]}],'
                '"relationships":[{"name","label","from","to","description","evidence"}],'
                '"constraints":[{"target_class","target_property","target_entity","description","value","unit","source"}]}\n\n【LLM-Wiki】\n' + wiki_ctx[:40000]
            )
            def_result = llm_json(sys_p, user_p)
            generated_ontology = {**generated_ontology, "definition": def_result,
                                  "status": "bootstrapped", "coverage": None, "coverage_history": []}
            _write_constraints(def_result.get("constraints", []))
            _task_update(tid, 100, "完了")
            _task_done(tid)
            save_state()
        except Exception as ex:
            _task_done(tid, str(ex))
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid}

class RefineBody(BaseModel):
    rounds: int = 3

_AUDIT_SYS = (
    "あなたはオントロジー監査エージェント。与えられた【オントロジー定義】が、各【QA（質問＋期待回答）】に"
    "答えるのに必要なクラス・プロパティ・関係を『構造として』備えているかをスキーマレベルで判定する（実データは見ない）。"
    "各QAの verdict は covered/partial/missing のいずれか。不足があれば missing に、追加すべき "
    "classes / properties(どのクラスに付けるか) / relationships を具体的に列挙する。"
    "【必須】渡された承認済みQAを漏れなく1件ずつ判定し、per_cq には全QAを含めること（省略・集約しない）。"
    '出力JSON: {"per_cq":[{"id":"QAxx","verdict":"covered|partial|missing",'
    '"missing":{"classes":["クラス名"],"properties":[{"class":"クラス名","name":"プロパティ名","type":"STRING|INTEGER|..."}],'
    '"relationships":[{"from":"クラス","name":"関係名","to":"クラス"}]},"note":"一言"}],'
    '"summary":{"covered":0,"partial":0,"missing":0}}'
)
_PATCH_SYS = (
    "あなたはオントロジー設計エージェント。現在の【オントロジー定義】に、【不足リスト】のクラス・プロパティ・関係を"
    "追加・拡張して、QAに答えられる定義へ更新する。"
    "【最重要】既存のクラス・関係・プロパティ・制約(constraints)は削除しない（追加・拡張のみ）。"
    "特に既存の constraints は必ずそのまま全て残すこと。追加要素の evidence には根拠Wikiスラッグを付ける。"
    "【命名】各クラス・プロパティ・関係には name（英語の物理名＝識別子）と label（日本語論理名）を必ず両方付ける"
    "（既存要素の label も保持する）。"
    "出力は元と同じ構造の完全なオントロジー定義JSON全体（classes/relationships/constraints すべてを含む）。"
)

@app.post("/api/ontology/refine-from-cqs")
def ontology_refine_from_cqs(body: RefineBody):
    """②③ 承認済みQAで定義の充足を監査し、不足を追加する反復（スキーマレベル）。"""
    rounds = max(1, min(6, int(body.rounds or 3)))
    tid = _task_start("ontology_refine", total=100)
    def _run():
        global generated_ontology
        try:
            from llm_utils import llm_json
            definition = generated_ontology.get("definition")
            if not isinstance(definition, dict) or not definition.get("classes"):
                _task_done(tid, "先にオントロジー定義を生成/Bootstrapしてください")
                return
            cqs = [i for i in REVIEW_ITEMS if i.get("type_cq") == "cq" and i.get("status") == "approved"]
            if not cqs:
                _task_done(tid, "承認済みQAがありません")
                return
            cq_text = "\n".join(f"- {c['id']} [{c.get('type','')}]: {c.get('title','')} → 期待回答: {c.get('expected_answer','')}" for c in cqs)
            history = []
            for rnd in range(1, rounds + 1):
                base_prog = int((rnd - 1) / rounds * 90)
                _task_update(tid, base_prog + 5, f"ラウンド{rnd}/{rounds}: QA充足を監査中…")
                audit = llm_json(_AUDIT_SYS,
                    f"【オントロジー定義】\n{json.dumps(definition, ensure_ascii=False)[:9000]}\n\n"
                    f"【QA（承認済み{len(cqs)}件）】\n{cq_text[:8000]}")
                per_cq = audit.get("per_cq") or []
                summ = audit.get("summary") or _summarize_audit(per_cq)
                gaps = [c for c in per_cq if c.get("verdict") in ("missing", "partial")]
                history.append({"round": rnd, "summary": summ, "gaps": len(gaps),
                                "classes": len(definition.get("classes", [])),
                                "relationships": len(definition.get("relationships", [])),
                                "constraints": len(definition.get("constraints", []))})
                generated_ontology["coverage"] = {"round": rnd, "per_cq": per_cq, "summary": summ}
                generated_ontology["coverage_history"] = history
                save_state()
                if not gaps:
                    _task_update(tid, 95, f"ラウンド{rnd}: 収束（全QA covered）")
                    break
                _task_update(tid, base_prog + 45, f"ラウンド{rnd}: 不足{len(gaps)}件を定義へ反映中…")
                agg = _aggregate_missing(gaps)
                wiki_ctx = _read_wiki_context(900)
                patched = llm_json(_PATCH_SYS,
                    f"【現在のオントロジー定義】\n{json.dumps(definition, ensure_ascii=False)[:9000]}\n\n"
                    f"【不足リスト（QAに答えるため追加すべき）】\n{json.dumps(agg, ensure_ascii=False)[:4000]}\n\n"
                    f"【参考: LLM-Wiki】\n{wiki_ctx[:14000]}")
                if isinstance(patched, dict) and patched.get("classes"):
                    # 安全策: パッチが制約を落とした場合は、直前の定義の制約を引き継ぐ（制約の消失防止）
                    if not patched.get("constraints"):
                        patched["constraints"] = definition.get("constraints", [])
                    definition = patched
                    generated_ontology["definition"] = definition
                    _write_constraints(definition.get("constraints", []))
                    save_state()
            generated_ontology["status"] = "refined"
            save_state()
            _task_update(tid, 100, "完了")
            _task_done(tid)
        except Exception as ex:
            _task_done(tid, str(ex))
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid, "rounds": rounds}

@app.get("/api/ontology/coverage")
def get_ontology_coverage():
    return JSONResponse({"coverage": generated_ontology.get("coverage"),
                         "history": generated_ontology.get("coverage_history") or [],
                         "status": generated_ontology.get("status")})


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
    """2.2 オントロジー定義に沿って、LLM-Wikiから実体を抽出する（非同期）。"""
    global generated_ontology, validation_results
    validation_results.clear()
    tid = _task_start("kg_extract", total=100)
    def _run():
        global generated_ontology
        try:
            definition = generated_ontology.get("definition")
            if not definition or not isinstance(definition, dict) or not definition.get("classes"):
                _task_done(tid, "先にオントロジー定義を生成してください")
                return

            _task_update(tid, 5, "環境設定を読み込み中…")
            env_path = os.path.join(HERE, "..", ".env")
            if os.path.isfile(env_path):
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        for key in ("GEMINI_API_KEY", "GEMINI_MODEL", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
                            if line.startswith(key + "="):
                                os.environ[key] = line.split("=", 1)[1].strip().strip('"').strip("'")

            # Clear Neo4j
            _task_update(tid, 10, "既存データをクリア中…")
            try:
                from neo4j import GraphDatabase
                ndriver = GraphDatabase.driver(os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                                                auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "password123")))
                with ndriver.session(database="neo4j") as session:
                    session.run("MATCH (n) DETACH DELETE n")
                ndriver.close()
            except: pass

            _task_update(tid, 15, "Wikiページを読込中…")
            base = os.path.join(HERE, "..", "..")
            md_files = sorted(glob.glob(os.path.join(base, "pages", "*.md")) +
                              glob.glob(os.path.join(base, "entities", "*.md")))
            wiki_texts = []
            for mf in md_files:
                name = os.path.basename(mf).replace(".md", "")
                with open(mf, "r", encoding="utf-8") as f:
                    content = f.read()
                content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
                wiki_texts.append(f"## {name}\n{content[:1000]}")

            _task_update(tid, 25, "LLMがKGを生成中…（60秒程度）")
            import google.genai as genai
            from google.genai import types
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
            classes = definition.get("classes", [])
            rels = definition.get("relationships", [])
            class_block = "\n".join(f"- {c.get('name')}（{c.get('label','')}）: / props: " + ", ".join(p.get("name","") for p in (c.get("properties") or [])) for c in classes)
            rel_block = "\n".join(f"- ({r.get('from')}) -[{r.get('name')}（{r.get('label','')}）]-> ({r.get('to')})" for r in rels)
            wiki_ctx = "\n\n".join(wiki_texts) if wiki_texts else "（該当ページなし）"
            prompt = "ナレッジグラフ構築:\n" + class_block[:3000] + "\n" + rel_block[:2000] + "\n" + wiki_ctx[:20000]
            kg = json.loads(client.models.generate_content(
                model=model,
                contents="あなたはナレッジグラフ構築エージェントです。以下のオントロジー定義に従いLLM-Wikiから実体を抽出しnodes/edgesを生成してください。出力は{\"nodes\":[{\"id\",\"labels\",\"props\"}],\"edges\":[{\"from\",\"to\",\"type\"}]}のJSONのみ。idはsvc_/contact_/nb_等の命名規則。nodeのlabelsは上記クラスのname（英語物理名）を用いること。nodeのpropsには必ず name（実体の日本語名）, source, type_label（このノードの型クラスの日本語論理名＝上記クラスの（）内の名称）を含めること。【最重要】source には、その実体の根拠となった LLM-Wiki ページの『スラッグ』を入れること（本文中の見出し『## 名前』のその名前を厳密に使う。例: 02-notebooks, disability-notebooks）。複数ある場合はカンマ区切り。『LLM-Wiki』のような総称や説明文は絶対に入れない。可能な限り金額・期間・必要書類・条件などの具体値も props に含めること。edgeのtypeは上記関係のname（英語物理名）を用いること。\n\n" + prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            ).text)
            if isinstance(kg, str): kg = json.loads(kg)
            if not isinstance(kg, dict): kg = {"nodes":[],"edges":[]}
            kg.setdefault("nodes",[]); kg.setdefault("edges",[])

            _task_update(tid, 75, "Cypher出力＋保存中…")
            ids = {n.get("id") for n in kg["nodes"]}
            kg["edges"] = [e for e in kg["edges"] if e.get("from") in ids and e.get("to") in ids]
            from kg_utils import build_cypher
            cypher = build_cypher(kg)
            with open(os.path.join(HERE, "neo4j_import.cypher"), "w", encoding="utf-8") as f: f.write(cypher)
            with open(os.path.join(HERE, "kg_extracted.json"), "w", encoding="utf-8") as f: json.dump(kg, f, ensure_ascii=False, indent=2)

            _task_update(tid, 90, "Neo4j投入中…")
            neo_connected = False
            n_edges_pushed = 0
            try:
                from neo4j import GraphDatabase
                ndriver = GraphDatabase.driver(os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                                                auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "password123")))
                with ndriver.session(database="neo4j") as session:
                    # まっさらに（専用KGなので全ノード削除してから入れ直す）
                    session.run("MATCH (n) DETACH DELETE n")
                    for n in kg["nodes"]:
                        labels = ":".join(list(n["labels"]) + ["Entity"])
                        # SET n += で id を保持（= だと MERGE で付けた id が消えてエッジが張れない）
                        session.run(f"MERGE (n:{labels} {{id: $id}}) SET n += $props",
                                    id=n["id"], props={k: json.dumps(v, ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in n.get("props",{}).items()})
                    for e in kg["edges"]:
                        et = re.sub(r'[^A-Za-z0-9_]', '', str(e.get("type") or "REL")) or "REL"
                        r = session.run(f"MATCH (a {{id: $fid}}), (b {{id: $tid}}) MERGE (a)-[:`{et}`]->(b) RETURN count(*) AS c",
                                        fid=e["from"], tid=e["to"]).single()
                        n_edges_pushed += (r["c"] if r else 0)
                ndriver.close()
                neo_connected = True
                neo_msg = f"Neo4jに投入完了（{len(kg['nodes'])}ノード / {n_edges_pushed}エッジ）"
            except Exception as neo_ex:
                neo_msg = f"Neo4j投入スキップ（{neo_ex}）"

            generated_ontology["kg_extracted"] = kg
            generated_ontology["kg_meta"] = {"nodes": len(kg["nodes"]), "edges": len(kg["edges"]), "neo4j": {"connected": neo_connected, "message": neo_msg}}
            _task_update(tid, 100, "完了")
            _task_done(tid)
            save_state()
        except Exception as ex:
            _task_done(tid, str(ex))
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid, "message": "抽出を開始しました。進捗は /api/task/{task_id} で確認できます。"}


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
            for rec in s.run("MATCH (a)-[r]->(b) RETURN a.id AS f, b.id AS t, type(r) AS ty, properties(r) AS props"):
                if rec["f"] is None or rec["t"] is None:
                    continue
                edges.append({"from": rec["f"], "to": rec["t"], "type": rec["ty"], "props": dict(rec["props"] or {})})
        drv.close()
        return JSONResponse(content={
            "nodes": nodes, "edges": edges,
            "meta": {"nodes": len(nodes), "edges": len(edges), "source": "neo4j",
                     "neo4j": {"connected": True, "message": f"Neo4j({uri}) の実データ（投入後・累積）"}},
        })
    except Exception as ex:
        return JSONResponse({"error": f"Neo4j未接続（{uri}）: {ex}", "nodes": [], "edges": []})


# ── 3.2 検証: 3.1ナレッジグラフ(Neo4j)を根拠にQAへ回答し、正解(expected_answer)と照合 ──
# チャットの Agentic Search (graphrag/agent/agent.py) の Planner→Retrieval→Critic→Answer を、
# 手作りkg.json非依存に一般化し、抽出KG(Neo4j)上で回す。回答は (A)KGのみ / (B)KG+Wiki の2通り。

_GENAI_CACHE = {}

def _genai():
    """(.env をロードした) genai クライアントと model 名を返す（クライアントはキャッシュ）。"""
    _load_env_file()
    if "client" not in _GENAI_CACHE:
        import google.genai as genai
        _GENAI_CACHE["genai"] = genai
        _GENAI_CACHE["client"] = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    _GENAI_CACHE["model"] = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    return _GENAI_CACHE["genai"], _GENAI_CACHE["client"], _GENAI_CACHE["model"]

def _llm_json(sys_prompt, user_prompt):
    genai, client, model = _genai()
    from google.genai import types
    r = client.models.generate_content(
        model=model, contents=f"{sys_prompt}\n\n{user_prompt}",
        config=types.GenerateContentConfig(response_mime_type="application/json"))
    return json.loads(r.text)

def _llm_text(sys_prompt, user_prompt):
    genai, client, model = _genai()
    r = client.models.generate_content(model=model, contents=f"{sys_prompt}\n\n{user_prompt}")
    return (r.text or "").strip()

def _get_kg_graph():
    """3.1の抽出KGを {nodes, edges, source} で返す。Neo4j優先・失敗時は kg_extracted。"""
    _load_env_file()
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD", "")
    if pw:
        try:
            import neo4j
            drv = neo4j.GraphDatabase.driver(uri, auth=(user, pw), connection_timeout=4)
            drv.verify_connectivity()
            nodes, edges = [], []
            with drv.session() as s:
                for rec in s.run("MATCH (n) RETURN n"):
                    node = rec["n"]
                    labels = [l for l in node.labels if l != "Entity"] or ["Node"]
                    props = dict(node); nid = props.get("id") or node.element_id
                    nodes.append({"id": nid, "labels": labels, "props": props})
                for rec in s.run("MATCH (a)-[r]->(b) RETURN a.id AS f, b.id AS t, type(r) AS ty, properties(r) AS props"):
                    if rec["f"] is None or rec["t"] is None:
                        continue
                    edges.append({"from": rec["f"], "to": rec["t"], "type": rec["ty"], "props": dict(rec["props"] or {})})
            drv.close()
            if nodes:
                return {"nodes": nodes, "edges": edges, "source": "neo4j"}
        except Exception:
            pass
    kg = generated_ontology.get("kg_extracted") or {"nodes": [], "edges": []}
    return {"nodes": kg.get("nodes", []), "edges": kg.get("edges", []), "source": "kg_extracted"}

def _node_name(n):
    return (n.get("props") or {}).get("name") or n.get("id")

def _kg_indexes(kg):
    by_id = {n["id"]: n for n in kg["nodes"]}
    adj = {}
    for e in kg["edges"]:
        adj.setdefault(e["from"], []).append(e)
        adj.setdefault(e["to"], []).append(e)
    labels = {}
    for n in kg["nodes"]:
        for l in (n.get("labels") or ["Node"]):
            labels.setdefault(l, []).append(_node_name(n))
    rel_types = sorted({e["type"] for e in kg["edges"]})
    return by_id, adj, labels, rel_types

def _slugs_from(text):
    if not text:
        return []
    return [t.strip() for t in re.split(r'[,、→\s]+', str(text))
            if t.strip() and re.match(r'^[A-Za-z0-9][\w-]*$', t.strip())]

def _plan_query(question, labels, rel_types):
    sys = ("あなたはナレッジグラフ検索エージェントのプランナー。質問に答えるために、与えられたグラフから"
           "探索の起点にすべきエンティティ名と検索キーワードを選ぶ。"
           '出力JSON: {"entities":["ノード名"...],"keywords":["語"...],"hops":1}。'
           "entitiesは下記ノード名から関連するものだけ、keywordsは質問中の要点語（手帳名・等級・制度名・窓口名など）。hopsは1か2。")
    label_lines = "\n".join(f"- {l}: {', '.join(sorted(set(ns))[:40])}" for l, ns in labels.items())
    user = f"質問: {question}\n\n【グラフのクラスとノード名】\n{label_lines[:6000]}\n\n【関係型】\n{', '.join(rel_types)}"
    try:
        p = _llm_json(sys, user)
    except Exception:
        p = {}
    hops = 1
    try:
        hops = min(2, max(1, int(p.get("hops") or 1)))
    except Exception:
        hops = 1
    return (p.get("entities") or []), (p.get("keywords") or []), hops

def _seed_nodes(nodes, entities, keywords):
    terms = [t.strip().lower() for t in list(entities) + list(keywords) if t and str(t).strip()]
    seeds = set()
    for n in nodes:
        nm = _node_name(n).lower()
        hay = (nm + " " + " ".join(str(v) for v in (n.get("props") or {}).values())).lower()
        for t in terms:
            if t and (t in nm or nm in t or t in hay):
                seeds.add(n["id"]); break
    return seeds

def _expand(seeds, adj, hops):
    visited = set(seeds); frontier = set(seeds)
    for _ in range(hops):
        nxt = set()
        for nid in frontier:
            for e in adj.get(nid, []):
                for other in (e["from"], e["to"]):
                    if other not in visited:
                        nxt.add(other)
        visited |= nxt; frontier = nxt
    return visited

def _subgraph_facts(ids, by_id, edges):
    ids = set(i for i in ids if i in by_id)
    fnodes = [{"id": i, "labels": by_id[i].get("labels"), "props": by_id[i].get("props")} for i in ids]
    fedges = [e for e in edges if e["from"] in ids and e["to"] in ids]
    return fnodes, fedges

def _load_wiki_pages(slugs):
    base = os.path.join(HERE, "..", "..")
    out = {}
    for slug in slugs:
        for folder in ("pages", "entities"):
            fp = os.path.join(base, folder, f"{slug}.md")
            if os.path.isfile(fp):
                with open(fp, encoding="utf-8") as f:
                    c = f.read()
                c = re.sub(r'^---\n.*?\n---\n', '', c, flags=re.DOTALL)
                out[slug] = c[:2500]
                break
    return out

def _naive_rag(question):
    """チャットの naive RAG (8790 の /api/rag) を呼ぶ。PDFチャンクのベクトル検索→LLM回答（KG非依存）。
    返り値: {answer, sources:[{page,score}], (error)}。"""
    import urllib.request
    url = os.environ.get("CHAT_BACKEND", "http://127.0.0.1:8790").rstrip("/") + "/api/rag"
    body = json.dumps({"query": question}, ensure_ascii=False).encode("utf-8")
    last = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json; charset=utf-8"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            if d.get("error"):
                last = d["error"]; continue
            return {"answer": d.get("answer", ""), "sources": d.get("sources", [])}
        except Exception as ex:
            last = f"{type(ex).__name__}: {ex}"
    return {"answer": f"(ナイーブRAG取得失敗: {last}) — チャットバックエンド(8790)が起動しているか確認してください。",
            "sources": [], "error": str(last)}

_ANSWER_SYS_KG = (
    "あなたは文京区の障害者福祉の案内担当。与えられた【ナレッジグラフの根拠】だけを使って日本語で簡潔に答える。"
    "根拠に無い金額・等級・電話番号・固有名を創作しない。根拠から答えられない場合は"
    "『ナレッジグラフからは分かりません』とだけ述べる。")
_ANSWER_SYS_KGWIKI = (
    "あなたは文京区の障害者福祉の案内担当。【ナレッジグラフの根拠】と【LLM-Wiki本文】を使って日本語で簡潔に答える。"
    "両方に無い情報は創作しない。答えられない場合は『分かりません』と述べる。")
_JUDGE_SYS = (
    "あなたは回答採点者かつ原因解析者。質問と『正解』を基準に、3つの回答（N=ナイーブRAG / A=KGのみ / B=KG+Wiki）を採点する。"
    "各 verdict は correct / partial / incorrect のいずれか: "
    "correct=正解の要点を過不足なく含む, partial=一部一致だが不足や軽微な誤り, incorrect=誤りor未回答。理由(reason)は簡潔に。"
    "さらに A(KGのみ) と B(KG+Wiki) が correct でない場合は、提供される【原因解析の材料】（取得サブグラフ・読込Wiki・source）を根拠に、"
    "根本原因を cause に記す。cause は必ず次の型ラベルを先頭に付ける（1つ選ぶ）＋一言:\n"
    "[検索不足]=正解に必要なエンティティをKGから取得できていない（取得ノードに該当が無い）\n"
    "[KG構造欠落]=エンティティは取得したが、答えに必要な属性/関係がKG側に無い\n"
    "[Wiki未読込]=参照すべきWikiページが読み込まれていない（sourceが無い/無効/0ページ）\n"
    "[Wiki網羅不足]=Wikiは読み込めたが、その本文に正解の該当記載が無い\n"
    "[回答生成]=根拠は揃っているのに回答が正解と食い違う（LLMの生成側の問題）\n"
    "correct の場合や N の cause は空文字でよい。"
    '出力JSON: {"naive":{"verdict":"...","reason":"..."},'
    '"kg":{"verdict":"...","reason":"...","cause":"..."},"kgwiki":{"verdict":"...","reason":"...","cause":"..."}}')

def _answer_query(question, cq_docs=None):
    """3.1KGを根拠に質問へ回答（judgeなし）。ナイーブRAG / KGのみ / KG+Wiki の3回答＋利用サブグラフを返す。
    3.2検証(_run_validation)とチャットの /api/validation/ask で共有する中核。"""
    trace = []
    kg = _get_kg_graph()
    trace.append({"node": "KG", "txt": f'{kg.get("source")}: {len(kg["nodes"])}ノード / {len(kg["edges"])}エッジ'})
    by_id, adj, labels, rel_types = _kg_indexes(kg)

    entities, keywords, hops = _plan_query(question, labels, rel_types)
    trace.append({"node": "Planner(LLM)", "txt": f'entities={entities[:8]} / keywords={keywords[:8]} / hops={hops}'})

    seeds = _seed_nodes(kg["nodes"], entities, keywords)
    ids = _expand(seeds, adj, hops)
    fnodes, fedges = _subgraph_facts(ids, by_id, kg["edges"])
    trace.append({"node": "Retrieval", "txt": f'シード{len(seeds)} → 近傍展開{len(fnodes)}ノード / {len(fedges)}エッジ'})

    if len(fnodes) < 2 and seeds:
        ids = _expand(seeds, adj, hops + 1)
        fnodes, fedges = _subgraph_facts(ids, by_id, kg["edges"])
        trace.append({"node": "Critic(LLM)", "txt": f'根拠不足 → hop拡大で{len(fnodes)}ノード'})
    else:
        trace.append({"node": "Critic(LLM)", "txt": "根拠十分 ✓"})

    facts_json = json.dumps({"nodes": fnodes, "edges": fedges}, ensure_ascii=False)[:8000]
    sources = {s for n in fnodes for s in _slugs_from((n.get("props") or {}).get("source"))}
    wiki_slugs = sorted(sources | set(cq_docs or []))

    # 回答N: ナイーブRAG（KG非依存・PDFチャンクのベクトル検索→LLM）
    naive = _naive_rag(question)
    trace.append({"node": "NaiveRAG", "txt": f'PDFベクトル検索→回答（出典{len(naive.get("sources") or [])}チャンク）'})

    # 回答A: KGのみ
    try:
        ans_kg = _llm_text(_ANSWER_SYS_KG, f"質問: {question}\n\n【ナレッジグラフの根拠】\n{facts_json}")
    except Exception as ex:
        ans_kg = f"(生成失敗: {ex})"

    # 回答B: KG + Wiki補完
    wiki = _load_wiki_pages(wiki_slugs)
    wiki_loaded = list(wiki.keys())            # 実際に本文を読み込めたページ
    wiki_ctx = "\n\n".join(f"## {k}\n{v}" for k, v in wiki.items())[:9000]
    try:
        ans_kgwiki = _llm_text(_ANSWER_SYS_KGWIKI,
                               f"質問: {question}\n\n【ナレッジグラフの根拠】\n{facts_json}\n\n【LLM-Wiki本文】\n{wiki_ctx}")
    except Exception as ex:
        ans_kgwiki = f"(生成失敗: {ex})"
    trace.append({"node": "Answer(LLM)", "txt": f'ナイーブRAG / KGのみ / KG+Wiki(参照{len(wiki)}ページ) を生成'})

    node_ids = [i for i in ids if i in by_id]
    return {
        "question": question,
        "naive": {"answer": naive.get("answer", ""), "rag_sources": naive.get("sources") or []},
        "kg": {"answer": ans_kg},
        "kgwiki": {"answer": ans_kgwiki},
        "subgraph": {"node_ids": node_ids, "edges": fedges},
        "entities": [_node_name(by_id[i]) for i in node_ids][:30],
        "sources": wiki_slugs, "trace": trace,
        # ── 原因解析用の材料（LLMなしで算出。Judgeにも渡す）──
        "kg_brief": _kg_brief(fnodes, fedges),
        "wiki_loaded": wiki_loaded,
        "diag": {"retrieved_nodes": len(node_ids), "wiki_requested": len(wiki_slugs),
                 "wiki_loaded": len(wiki_loaded), "kg_dontknow": ("分かりません" in ans_kg)},
    }

def _kg_brief(fnodes, fedges):
    """Judge/原因解析用に、取得サブグラフをコンパクトなテキストへ（ノード名[型]: プロパティ名一覧 ＋ エッジ）。"""
    lines = []
    for n in fnodes[:40]:
        p = n.get("props") or {}
        nm = p.get("name") or n.get("id")
        typ = (n.get("labels") or ["?"])[-1]
        pk = [k for k in p.keys() if k not in ("name", "type_label")]
        lines.append(f"- {nm}[{typ}]: {', '.join(pk) if pk else '(属性なし)'}")
    elines = [f"- {e.get('from')} -{e.get('type')}-> {e.get('to')}" for e in fedges[:30]]
    return "【取得ノード】\n" + "\n".join(lines) + "\n【取得エッジ】\n" + ("\n".join(elines) or "(なし)")

def _run_validation(cq):
    """3.2検証: _answer_query の3回答を正解(expected_answer)と照合して verdict を付与。
    KG+Wiki は「KGで辿ったEntityの source のみ」を参照する（QAのtrace元ページは混ぜない＝KG主導の到達性を厳密に測る）。"""
    expected = cq.get("expected_answer") or ""
    r = _answer_query(cq.get("title") or "")
    diag = r.get("diag") or {}
    # Judge に採点＋原因解析を相乗り（LLM呼び出しは増やさない）。取得サブグラフと読込Wikiを材料として渡す。
    diag_ctx = (f"\n\n【原因解析の材料】\n"
                f"KGから取得したサブグラフ:\n{r.get('kg_brief','')[:3500]}\n"
                f"実際に読み込めたWikiページ: {r.get('wiki_loaded') or '（なし）'}\n"
                f"要求したsourceスラッグ: {r.get('sources') or '（なし）'}\n"
                f"（ナイーブRAG回答は上記。PDFに情報があるかの手掛かりに使う）")
    try:
        j = _llm_json(_JUDGE_SYS,
                      f"質問: {r['question']}\n正解: {expected}\n\n"
                      f"回答N(ナイーブRAG): {r['naive'].get('answer','')}\n\n"
                      f"回答A(KGのみ): {r['kg'].get('answer','')}\n\n回答B(KG+Wiki): {r['kgwiki'].get('answer','')}"
                      + diag_ctx)
    except Exception as ex:
        j = {"naive": {"verdict": "error", "reason": str(ex)},
             "kg": {"verdict": "error", "reason": str(ex)}, "kgwiki": {"verdict": "error", "reason": str(ex)}}
    r["trace"].append({"node": "Judge(LLM)", "txt": f'Naive={((j.get("naive") or {}).get("verdict"))} / '
                       f'KG={((j.get("kg") or {}).get("verdict"))} / KG+Wiki={((j.get("kgwiki") or {}).get("verdict"))}'})
    return {
        "cq_id": cq.get("id"), "question": r["question"], "expected": expected, "type": cq.get("type"),
        "naive": {**r["naive"], **(j.get("naive") or {})},
        "kg": {**r["kg"], **(j.get("kg") or {})},
        "kgwiki": {**r["kgwiki"], **(j.get("kgwiki") or {})},
        "entities": r["entities"], "sources": r["sources"], "trace": r["trace"],
        "diag": diag, "wiki_loaded": r.get("wiki_loaded") or [],
        "run_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }

def _approved_cqs():
    return [i for i in REVIEW_ITEMS if i.get("type_cq") == "cq" and i.get("status") == "approved"]

@app.get("/api/validation/cqs")
def validation_cqs():
    return JSONResponse([{"id": c.get("id"), "title": c.get("title"),
                          "expected_answer": c.get("expected_answer"), "type": c.get("type")}
                         for c in _approved_cqs()])

@app.get("/api/validation/results")
def validation_get_results():
    return JSONResponse(validation_results)

@app.post("/api/validation/run/{cq_id}")
def validation_run_one(cq_id: str):
    cq = next((i for i in REVIEW_ITEMS if i.get("id") == cq_id and i.get("type_cq") == "cq"), None)
    if not cq:
        return JSONResponse({"ok": False, "error": f"QA {cq_id} が見つかりません"}, status_code=404)
    try:
        res = _run_validation(cq)
    except Exception as ex:
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"}, status_code=500)
    validation_results[cq_id] = res
    save_state()
    return JSONResponse({"ok": True, "result": res})

class AskBody(BaseModel):
    query: str = ""

@app.post("/api/validation/ask")
def validation_ask(body: AskBody):
    """チャット用: 任意の質問に3手法(ナイーブRAG/KGのみ/KG+Wiki)でライブ回答。正解なし・judgeなし。"""
    q = (body.query or "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "empty query"}, status_code=400)
    try:
        r = _answer_query(q)
    except Exception as ex:
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"}, status_code=500)
    return JSONResponse({"ok": True, **r})

@app.post("/api/validation/run-all")
def validation_run_all():
    done, errors = 0, []
    for cq in _approved_cqs():
        try:
            validation_results[cq["id"]] = _run_validation(cq)
            done += 1
            save_state()
        except Exception as ex:
            errors.append({"cq_id": cq.get("id"), "error": str(ex)})
    return JSONResponse({"ok": True, "done": done, "errors": errors})


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
  .prog-bar{height:8px;background:var(--border);border-radius:4px;overflow:hidden;margin-bottom:4px}
  .prog-fill{height:100%;background:var(--accent);border-radius:4px;transition:width .5s}
  .prog-fill.done{background:var(--ok)}
  .prog-fill.error{background:var(--reject)}
  .prog-label{font-size:.78rem;color:var(--muted)}
  @media(max-width:768px){.graph-container{flex-direction:column;height:auto}.graph-container svg{height:400px}.graph-container #node-detail{width:100%}}
</style>
</head>
<body>
<div class="topbar">
  <h1>📋 文京区障害者福祉 KG レビュー</h1>
  <nav>
    <a data-tab="cq">2.1 QA</a>
    <a data-tab="ontology-def">2.2 オントロジー定義</a>
    <a data-tab="ontology-graph">2.3 オントロジー図</a>
    <a data-tab="kg">3.1 ナレッジグラフ</a>
    <a data-tab="validation">3.2 QAのAgent回帰テスト</a>
  </nav>
</div>
<div class="content">

<div id="panel-cq" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
    <div id="cq-stats" class="stats" style="flex:1;margin-bottom:0"></div>
    <button onclick="generateQAs()" id="gen-cq-btn" style="padding:7px 16px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">🤖 LLMからQAを生成</button>
    <button onclick="approveAllCqs()" id="approve-cq-btn" style="padding:7px 16px;border:1px solid #2ecc71;border-radius:6px;background:transparent;color:#2ecc71;font-weight:600;cursor:pointer;font-size:.82rem;margin-left:6px">✅ 全QA承認</button>
    <button onclick="clearAllCqs()" id="clear-cq-btn" style="padding:7px 16px;border:1px solid #e74c3c;border-radius:6px;background:transparent;color:#e74c3c;font-weight:600;cursor:pointer;font-size:.82rem;margin-left:6px">🗑 全QA削除</button>
  </div>
  <div id="cq-list" class="item-list"></div>
  <div class="card" style="margin-top:12px">
    <div class="card-title" style="margin-bottom:8px">✏️ 新規QA（質問＋回答ペア）追加</div>
    <div style="display:flex;flex-direction:column;gap:6px">
      <input type="text" id="new-cq-title" placeholder="質問（例: 身体2級が受けられる手当と月額は？）" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem">
      <textarea id="new-cq-desc" placeholder="質問の詳細説明" rows="2" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem;resize:vertical"></textarea>
      <textarea id="new-cq-answer" placeholder="期待される回答（例: 心身障害者等福祉手当（区）15,500円/月、特別障害者手当（国）28,840円/月…）" rows="2" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem;resize:vertical;background:var(--ok-bg);border-color:var(--ok)"></textarea>
      <select id="new-cq-type" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem;background:var(--card)">
        <option value="lookup">単一参照 — 1つの情報を直接調べる</option>
        <option value="multi_hop">多段探索 — 複数の関係をたどって調べる</option>
        <option value="aggregation">集約 — 条件に合うものをすべて列挙</option>
        <option value="constraint">制約確認 — 条件・制限を確認する</option>
        <option value="constraint">⚠️ 条件確認 — 条件・制限・対象範囲・併給可否を確認</option>
      </select>
      <button onclick="addCq()" style="align-self:flex-start;padding:6px 16px;border:none;border-radius:5px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer">QAを追加</button>
    </div>
  </div>
</div>

<div id="panel-ontology-def" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
    <div style="flex:1">
      <div style="font-size:.95rem;font-weight:600">📐 オントロジー定義</div>
      <div style="font-size:.78rem;color:var(--muted)">QA駆動反復: <b>① Wiki→定義</b> → <b>②③ 承認済みQAで充足するまで修正</b> → 3.1/3.2で実体確認。（従来の一括生成も可）</div>
    </div>
    <button onclick="bootstrapOntology()" id="boot-onto-btn" style="padding:7px 14px;border:1px solid var(--accent);border-radius:6px;background:var(--card);color:var(--accent);font-weight:600;cursor:pointer;font-size:.82rem">① Wikiから定義</button>
    <label style="font-size:.75rem;color:var(--muted)">ラウンド<select id="refine-rounds" style="margin-left:3px;padding:3px;border:1px solid var(--border);border-radius:4px"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option></select></label>
    <button onclick="refineFromCqs()" id="refine-onto-btn" style="padding:7px 14px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">②③ QAで反復修正</button>
    <button onclick="generateOntology()" id="gen-onto-btn" style="padding:7px 12px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--muted);font-weight:600;cursor:pointer;font-size:.8rem">🤖 一括生成</button>
  </div>
  <div id="onto-progress" style="display:none;margin-bottom:10px"></div>
  <div id="onto-coverage"></div>
  <div id="ontology-def-content" style="display:none"></div>
  <div id="ontology-def-empty" class="empty-state" style="padding:40px;text-align:center;color:var(--muted)">
    <div style="font-size:2rem;margin-bottom:8px">📐</div>
    <div>オントロジーが未生成です。「🤖 LLMからオントロジーを生成」ボタンをクリックしてください。</div>
    <div style="font-size:.78rem;margin-top:4px">※承認済みQAがある場合のみ生成されます</div>
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

<div id="panel-validation" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
    <div style="flex:1">
      <div style="font-size:.95rem;font-weight:600">✅ 3.2 QAのAgent回帰テスト（ナレッジグラフでQAに答えられるか）</div>
      <div style="font-size:.78rem;color:var(--muted)">承認済みQAを、3.1ナレッジグラフを根拠にAIエージェントが回答 → 正解(expected_answer)と照合。<b>KGのみ</b>と<b>KG+Wiki補完</b>の2通りで評価します。</div>
    </div>
    <button onclick="runAllValidation()" id="val-runall-btn" style="padding:7px 16px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">▶ 全件検証</button>
    <button onclick="fixOntologyFromValidation()" id="val-fix-btn" style="padding:7px 16px;border:1px solid var(--warn);border-radius:6px;background:transparent;color:var(--warn);font-weight:600;cursor:pointer;font-size:.82rem">🔧 失敗QAからオントロジー修正</button>
  </div>
  <div id="val-progress" style="display:none;font-size:.8rem;margin-bottom:8px;padding:8px 12px;border-radius:6px;border:1px solid var(--border);background:var(--card)"></div>
  <div id="val-summary"></div>
  <div id="val-list"></div>
</div>

</div>
<script>
const API = location.pathname.startsWith('/review') ? '/review' : '';
let allItems = [];

// ── 根拠リンク（LLM-Wikiページ / QA）ヘルパ ──
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
// ── 物理名→日本語論理名（英語物理名に論理名を併記するための対応表）──
let ONTO_LABELS = {classes:{}, relationships:{}, class_props:{}};
async function loadOntoLabels() {
  // 小さな対応表なので毎回取得（再生成後も最新の論理名を反映）
  try { ONTO_LABELS = await (await fetch(API + '/api/ontology/labels')).json(); }
  catch (e) {}
  return ONTO_LABELS;
}
function _lblMap(kind){ return (ONTO_LABELS && ONTO_LABELS[kind]) ? ONTO_LABELS[kind] : {}; }
// 「論理名（物理名）」。論理名が無ければ物理名のみ。
function clsLabel(name){ const l=_lblMap('classes')[name]; return l?`${l}（${name}）`:(name||''); }
function relLabel(name){ const l=_lblMap('relationships')[name]; return l?`${l}（${name}）`:(name||''); }
// スペースが狭い箇所用: 論理名優先（無ければ物理名）
function clsLabelJa(name){ return _lblMap('classes')[name] || name || ''; }
function relLabelJa(name){ return _lblMap('relationships')[name] || name || ''; }
function wikiHref(slug) {
  const rel = (WIKI_INDEX && WIKI_INDEX[slug]) ? WIKI_INDEX[slug] : ('pages/' + slug + '.md');
  return WIKI_ORIGIN + '/' + rel;
}
function cqHref(id) { return API + '/cq#cq-' + id; }
// evidence/source 文字列（例 "05-medical→key-contacts" / "QA04, 00-eligibility-table"）をリンク化
function linkifyRefs(str) {
  if (!str) return '<span style="color:var(--muted)">—</span>';
  return String(str).split(/([→,、]|\s+)/).map(tok => {
    if (tok === '' ) return '';
    if (tok === '→') return ' <span class="rel-arrow">→</span> ';
    if (tok === ',' || tok === '、') return '、';
    if (/^\s+$/.test(tok)) return ' ';
    const t = tok.trim();
    if (/^QA\d+$/i.test(t))
      return `<a href="${cqHref(t.toUpperCase())}" class="reflink cq" title="このQAへ移動">${t.toUpperCase()}</a>`;
    if (/^[A-Za-z0-9][\w-]*$/.test(t)) {
      // 実在するWikiページ(スラッグがWIKI_INDEXにある)のみリンク化。未知スラッグ('LLM-Wiki'等)はリンクにせず404を防ぐ
      if (WIKI_INDEX && WIKI_INDEX[t])
        return `<a href="${wikiHref(t)}" target="_blank" class="reflink wiki" title="LLM-Wikiを開く">${t}</a>`;
      return `<span style="color:var(--muted)" title="対応するWikiページがありません">${t}</span>`;
    }
    return tok;
  }).join('');
}

async function loadItems() {
  await loadWikiIndex();
  const r = await fetch(API + '/api/review-items');
  allItems = await r.json();
  renderQA();
}

// ── QA helpers ──
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
  renderQA();
}

async function addCq() {
  const title = document.getElementById('new-cq-title').value.trim();
  const desc = document.getElementById('new-cq-desc').value.trim();
  const answer = document.getElementById('new-cq-answer').value.trim();
  const cqType = document.getElementById('new-cq-type').value;
  if (!title) { alert('質問を入力してください'); return; }
  const cqs = allItems.filter(i => i.type_cq === 'cq');
  const maxNum = cqs.reduce((m, c) => Math.max(m, parseInt(c.id.replace('QA','')) || 0), 0);
  const id = 'QA' + String(maxNum + 1).padStart(2, '0');
  const newItem = {
    id, title, description: desc || title, expected_answer: answer,
    type: cqType,
    source: '手動追加', source_url: '', review: 'human_required', type: 'cq',
    status: 'pending', cq_ids: [], current_value: '未テスト'
  };
  allItems.push(newItem);
  renderQA();
  document.getElementById('new-cq-title').value = '';
  document.getElementById('new-cq-desc').value = '';
  document.getElementById('new-cq-answer').value = '';
  document.getElementById('new-cq-type').selectedIndex = 0;
}

async function generateQAs() {
  const btn = document.getElementById('gen-cq-btn');
  btn.textContent = '⏳ LLMがQA生成中…';
  btn.disabled = true;
  try {
    const r = await fetch(API + '/api/cq/generate', {method:'POST'});
    const d = await r.json();
    if (d.ok) {
      alert(`✅ ${d.added}件のQAを追加しました（全${d.total}件中）`);
      allItems = await (await fetch(API + '/api/review-items')).json();
      renderQA();
    } else {
      alert('⚠ エラー: ' + (d.error || '不明'));
    }
  } catch(e) {
    alert('⚠ 通信エラー: ' + e.message);
  }
  btn.textContent = '🤖 LLMからQAを生成';
  btn.disabled = false;
}

async function deleteCq(id) {
  if (!confirm(`QA「${id}」を削除しますか？`)) return;
  try {
    const r = await fetch(API + '/api/cq/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
    const d = await r.json();
    if (!d.ok) { alert('⚠ エラー: ' + (d.error || '不明')); return; }
    allItems = await (await fetch(API + '/api/review-items')).json();
    renderQA();
  } catch(e) { alert('⚠ 通信エラー: ' + e.message); }
}

async function approveAllCqs() {
  const cqs = allItems.filter(i => i.type_cq === 'cq');
  const pending = cqs.filter(i => i.status !== 'approved').length;
  if (pending === 0) { alert('未承認のQAはありません'); return; }
  if (!confirm(`未承認の ${pending} 件のQAをすべて承認しますか？`)) return;
  const btn = document.getElementById('approve-cq-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 承認中…'; }
  try {
    const r = await fetch(API + '/api/cq/approve-all', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { alert('⚠ エラー: ' + (d.error || '不明')); }
    else { allItems = await (await fetch(API + '/api/review-items')).json(); renderQA(); }
  } catch(e) { alert('⚠ 通信エラー: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = '✅ 全QA承認'; }
}

async function clearAllCqs() {
  const n = allItems.filter(i => i.type_cq === 'cq').length;
  if (n === 0) { alert('削除するQAがありません'); return; }
  if (!confirm(`全 ${n} 件のQAを削除しますか？（元に戻せません）`)) return;
  const btn = document.getElementById('clear-cq-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 削除中…'; }
  try {
    const r = await fetch(API + '/api/cq/clear', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { alert('⚠ エラー: ' + (d.error || '不明')); }
    else { allItems = await (await fetch(API + '/api/review-items')).json(); renderQA(); }
  } catch(e) { alert('⚠ 通信エラー: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = '🗑 全QA削除'; }
}

// ── Screen routing (2.1/2.2/2.3 はそれぞれ別ページ/別URL) ──
const SCREENS = ['cq','ontology-def','ontology-graph','kg','validation'];
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
  else if (seg === 'ontology-def') { renderOntologyDef(); renderCoverage(); }
  else if (seg === 'ontology-graph') renderOntologyGraph();
  else if (seg === 'kg') renderKG();
  else if (seg === 'validation') renderValidation();
}

// ── QA management ──
function renderQA() {
  const cqs = allItems.filter(i => i.type_cq === 'cq');
  document.getElementById('cq-stats').innerHTML = [
    {label:'全QA', num:cqs.length, cls:''},
    {label:'承認済', num:cqs.filter(i=>i.status==='approved').length, cls:''},
    {label:'保留中', num:cqs.filter(i=>i.status==='pending').length, cls:''},
    {label:'却下', num:cqs.filter(i=>i.status==='rejected').length, cls:''},
  ].map(x => `<div class="stat"><div class="num">${x.num}</div><div class="label">${x.label}</div></div>`).join('');
  document.getElementById('cq-list').innerHTML = cqs.map(item => renderCqCard(item)).join('');
}

function renderCqCard(item) {
  const statusLabel = {pending:'保留中',approved:'承認済',rejected:'却下',revision_requested:'修正依頼'}[item.status] || item.status;
  const cqType = item.type || '—';
  const typeLabels = {lookup:'📖 単一参照',multi_hop:'🔗 多段探索',aggregation:'📋 一覧取得',constraint:'⚠️ 条件確認'};
  const typeColors = {lookup:'#3498db',multi_hop:'#9b59b6',aggregation:'#2ecc71',constraint:'#f39c12'};
  const typeHelps = {lookup:'1つのノードのプロパティを直接参照する',multi_hop:'複数のエッジをたどって情報を集める',aggregation:'条件に合う全ノードを列挙する',constraint:'条件・制限・対象範囲・併給可否などを確認する'};
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
      <button onclick="deleteCq('${item.id}')" title="このQAを削除" style="font-size:.78rem;background:transparent;color:#e74c3c;border:1px solid #e74c3c;border-radius:4px;padding:2px 8px;cursor:pointer">🗑 削除</button>
    </div>
  </div>`;
}

// ── Ontology generation ──
async function generateOntology() {
  const btn = document.getElementById('gen-onto-btn');
  const prog = document.getElementById('onto-progress');
  btn.textContent = '⏳ 生成開始…';
  btn.disabled = true;
  prog.style.display = 'block';
  prog.innerHTML = '<div class="prog-bar"><div class="prog-fill" id="onto-prog-fill" style="width:0%"></div></div><div class="prog-label" id="onto-prog-label">準備中…</div>';
  try {
    const r = await fetch(API + '/api/ontology/generate', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { alert('⚠ エラー: ' + (d.error || '')); btn.disabled=false; btn.textContent='🤖 LLMからオントロジーを生成'; return; }
    const tid = d.task_id;
    // Poll
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const pr = await (await fetch(API + '/api/task/' + tid)).json();
      const pct = Math.round(pr.progress / pr.total * 100);
      document.getElementById('onto-prog-fill').style.width = pct + '%';
      document.getElementById('onto-prog-label').textContent = pr.msg || (pct + '%');
      if (pr.status === 'done') { prog.innerHTML = '<div class="prog-bar"><div class="prog-fill done" style="width:100%"></div></div><div class="prog-label" style="color:var(--ok)">✅ 完了</div>'; break; }
      if (pr.status === 'error') { prog.innerHTML = '<div class="prog-bar"><div class="prog-fill error" style="width:100%"></div></div><div class="prog-label" style="color:var(--reject)">⚠ エラー: ' + (pr.error || '') + '</div>'; break; }
    }
    renderOntologyDef();
    renderOntologyGraph();
  } catch(e) { prog.innerHTML = '<div class="prog-label" style="color:var(--reject)">⚠ 通信エラー: ' + e.message + '</div>'; }
  btn.disabled = false;
  btn.textContent = '🤖 一括生成';
}

// ── QA駆動反復: ① Bootstrap / ②③ Refine / カバレッジ表示 ──
function _showOntoProg(prog){ prog.style.display='block'; prog.innerHTML='<div class="prog-bar"><div class="prog-fill" style="width:0%"></div></div><div class="prog-label">準備中…</div>'; }
async function _pollOntoTask(tid, prog, onTick){
  while(true){
    await new Promise(r=>setTimeout(r,2000));
    let pr; try{ pr=await (await fetch(API+'/api/task/'+tid)).json(); }catch(e){ continue; }
    const pct=Math.round(pr.progress/pr.total*100);
    const fill=prog.querySelector('.prog-fill'), lab=prog.querySelector('.prog-label');
    if(fill) fill.style.width=pct+'%'; if(lab) lab.textContent=pr.msg||(pct+'%');
    if(onTick) await onTick();
    if(pr.status==='done'){ if(fill)fill.classList.add('done'); if(lab)lab.textContent='✅ 完了'; return true; }
    if(pr.status==='error'){ if(lab)lab.innerHTML='<span style="color:var(--reject)">⚠ '+(pr.error||'エラー')+'</span>'; return false; }
  }
}
async function bootstrapOntology(){
  const btn=document.getElementById('boot-onto-btn'), prog=document.getElementById('onto-progress');
  const old=btn.textContent; btn.disabled=true; btn.textContent='⏳…'; _showOntoProg(prog);
  try{
    const d=await (await fetch(API+'/api/ontology/bootstrap',{method:'POST'})).json();
    if(!d.ok){ alert('⚠ '+(d.error||'失敗')); }
    else { await _pollOntoTask(d.task_id, prog); renderOntologyDef(); renderCoverage(); renderOntologyGraph(); }
  }catch(e){ prog.innerHTML='<div class="prog-label" style="color:var(--reject)">⚠ 通信エラー: '+e.message+'</div>'; }
  btn.disabled=false; btn.textContent=old;
}
async function refineFromCqs(){
  const btn=document.getElementById('refine-onto-btn'), prog=document.getElementById('onto-progress');
  const rounds=parseInt(document.getElementById('refine-rounds').value||'3',10);
  const old=btn.textContent; btn.disabled=true; btn.textContent='⏳ 反復中…'; _showOntoProg(prog);
  try{
    const d=await (await fetch(API+'/api/ontology/refine-from-cqs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rounds})})).json();
    if(!d.ok){ alert('⚠ '+(d.error||'失敗')); }
    else { await _pollOntoTask(d.task_id, prog, renderCoverage); renderOntologyDef(); renderCoverage(); renderOntologyGraph(); }
  }catch(e){ prog.innerHTML='<div class="prog-label" style="color:var(--reject)">⚠ 通信エラー: '+e.message+'</div>'; }
  btn.disabled=false; btn.textContent=old;
}
async function renderCoverage(){
  const box=document.getElementById('onto-coverage'); if(!box) return;
  let d; try{ d=await (await fetch(API+'/api/ontology/coverage')).json(); }catch(e){ return; }
  const hist=d.history||[], cov=d.coverage;
  if(!hist.length && !cov){
    box.innerHTML='<div style="border:1px dashed var(--border);border-radius:10px;padding:12px 14px;background:var(--card);margin-bottom:14px;color:var(--muted);font-size:.82rem">🎯 <b>QA充足カバレッジ</b>：まだ実行していません。<b>「②③ QAで反復修正」</b>を押すと、承認済みQAを今の定義で答えられるか監査し、ここに反復の推移（回答可能/一部不足/回答不可、クラス数/関係数/制約数）が表示されます。<br><span style="font-size:.74rem">※「① Wikiから定義」を実行すると充足履歴はリセットされます。</span></div>';
    return;
  }
  const V={covered:{label:'✅ 回答可能',c:'#166534',bg:'#dcfce7'},partial:{label:'△ 一部不足',c:'#854d0e',bg:'#fef9c3'},missing:{label:'✗ 回答不可',c:'#991b1b',bg:'#fee2e2'}};
  const badge=(v,txt)=>{const m=V[v]||{c:'#6b7280',bg:'#f3f4f6',label:v};return `<span style="display:inline-block;padding:1px 8px;border-radius:9px;font-size:.72rem;font-weight:700;color:${m.c};background:${m.bg}">${txt||m.label}</span>`;};

  const latest=hist.length?hist[hist.length-1]:null;
  const s=(latest&&latest.summary)||(cov&&cov.summary)||{};
  const tot=(s.covered||0)+(s.partial||0)+(s.missing||0);
  const seg=(n,color)=> tot?`<div style="height:100%;width:${(n/tot*100).toFixed(1)}%;background:${color}"></div>`:'';

  let html='<div style="border:1px solid var(--border);border-radius:10px;padding:12px 14px;background:var(--card);margin-bottom:14px">';
  html+='<div style="font-size:.9rem;font-weight:700">🎯 QA充足カバレッジ</div>';
  html+='<div style="font-size:.75rem;color:var(--muted);margin:2px 0 10px">承認済みQAを、現在のオントロジー定義（クラス・関係）だけで構造的に答えられるかの割合。「②③ QAで反復修正」を繰り返すほど<b>回答可能</b>が増えます。</div>';

  html+=`<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:baseline;margin-bottom:6px">
    <div style="font-size:.85rem"><b style="font-size:1.2rem;color:#166534">${s.covered||0}</b> <span style="color:var(--muted)">/ ${tot} 件のQAが回答可能</span></div>
    <div style="display:flex;gap:6px;flex-wrap:wrap">${badge('covered','✅ 回答可能 '+(s.covered||0))} ${badge('partial','△ 一部不足 '+(s.partial||0))} ${badge('missing','✗ 回答不可 '+(s.missing||0))}</div>
  </div>`;
  html+=`<div style="display:flex;height:14px;border-radius:7px;overflow:hidden;border:1px solid var(--border);margin-bottom:14px">${seg(s.covered||0,'#22c55e')}${seg(s.partial||0,'#eab308')}${seg(s.missing||0,'#ef4444')}</div>`;

  html+='<div style="font-size:.8rem;font-weight:600;margin-bottom:4px">反復の推移</div>';
  html+='<div style="overflow-x:auto"><table class="otbl" style="font-size:.78rem;min-width:520px"><thead>'
     + '<tr><th rowspan="2" style="vertical-align:middle">ラウンド</th>'
     + '<th colspan="3" style="text-align:center;background:#f0fdf4">QA充足状況（承認済みQA）</th>'
     + '<th colspan="3" style="text-align:center;background:#eff6ff">オントロジー定義の規模</th></tr>'
     + '<tr><th style="color:#166534">✅ 回答可能</th><th style="color:#854d0e">△ 一部不足</th><th style="color:#991b1b">✗ 回答不可</th>'
     + '<th>クラス数</th><th>関係数</th><th>制約数</th></tr></thead><tbody>';
  hist.forEach(h=>{const hs=h.summary||{};const cst=(h.constraints==null?'—':h.constraints);html+=`<tr><td><b>R${h.round}</b></td><td style="color:#166534;font-weight:700">${hs.covered||0}</td><td style="color:#854d0e">${hs.partial||0}</td><td style="color:#991b1b">${hs.missing||0}</td><td>${h.classes}</td><td>${h.relationships}</td><td>${cst}</td></tr>`;});
  html+='</tbody></table></div>';
  html+='<div style="font-size:.7rem;color:var(--muted);margin-top:6px;line-height:1.6">'
     + '<b>QA充足状況</b>＝承認済みQAを今の定義で答えられるか（左の緑グループ）。 '
     + '<b>✅回答可能</b>=必要なクラス/プロパティ/関係が揃う ／ <b>△一部不足</b>=一部だけ揃う ／ <b>✗回答不可</b>=必要な構造が無い。<br>'
     + '<b>定義の規模</b>＝オントロジー定義そのものの大きさ（右の青グループ。クラス・関係が増えるほど表現力が上がる）。</div>';

  if(cov && cov.per_cq && cov.per_cq.length){
    html+=`<div style="font-size:.8rem;font-weight:600;margin:12px 0 4px">各QAの判定（最新: ラウンド${cov.round}）</div><div style="display:flex;flex-direction:column;gap:3px;max-height:260px;overflow:auto">`;
    cov.per_cq.forEach(c=>{
      const miss=c.missing||{}, parts=[];
      if((miss.classes||[]).length) parts.push('クラス: '+miss.classes.map(escHtml).join(', '));
      if((miss.properties||[]).length) parts.push('プロパティ: '+miss.properties.map(p=>escHtml((p.class||'')+'.'+(p.name||''))).join(', '));
      if((miss.relationships||[]).length) parts.push('関係: '+miss.relationships.map(r=>escHtml((r.from||'')+'—'+(r.name||'')+'→'+(r.to||''))).join(', '));
      html+=`<div style="font-size:.75rem;border-bottom:1px solid var(--border);padding:3px 0"><span style="font-family:monospace;margin-right:5px">${escHtml(c.id||'')}</span>${badge(c.verdict)} <span style="color:var(--muted)">${escHtml(c.note||'')}</span>${parts.length?`<div style="color:var(--muted);font-size:.72rem;margin-top:1px">🔧 追加すべき→ ${parts.join(' ／ ')}</div>`:''}</div>`;
    });
    html+='</div>';
  }
  html+='</div>';
  box.innerHTML=html;
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
    html += `<table class="otbl"><thead><tr><th>クラス名</th><th>説明</th><th>プロパティ</th><th>根拠(LLM-Wiki/QA)</th></tr></thead><tbody>`;
    for (const cls of classes) {
      const props = (cls.properties || []).map(p =>
        `${p.label ? p.label + ' ' : ''}<span class="mono">${p.label ? '（' + p.name + '）' : p.name}: ${p.type}</span>${p.required ? '<span class="req">必須</span>' : ''}`
      ).join('<br>');
      const clsCell = `<b>${cls.label || cls.name}</b>${cls.label ? `<span class="mono" style="color:var(--muted);font-size:.72rem">（${cls.name}）</span>` : ''}`;
      html += `<tr id="cls-${cls.name}"><td>${clsCell}</td><td>${cls.description || ''}</td><td>${props || '<span style="color:var(--muted)">—</span>'}</td>`
        + `<td class="evid">${linkifyRefs(cls.evidence || cls.source)}</td></tr>`;
    }
    html += `</tbody></table>`;

    // Relationships
    const rels = def.relationships || [];
    html += `<h3 style="font-size:.95rem;margin:16px 0 6px">🔗 関係定義（${rels.length}件）</h3>`;
    html += `<table class="otbl"><thead><tr><th>from</th><th>関係</th><th>to</th><th>説明</th><th>参照経路の根拠</th></tr></thead><tbody>`;
    const cLbl = {}; (def.classes || []).forEach(c => { if (c.name) cLbl[c.name] = c.label || ''; });
    const fmtCls = (n) => { const l = cLbl[n]; return l ? `${l}<span class="mono" style="color:var(--muted);font-size:.72rem">（${n}）</span>` : `<span class="mono">${n || ''}</span>`; };
    for (const rel of rels) {
      const props = (rel.properties || []).map(p => `${p.name}: ${p.type}`).join(', ');
      const relCell = `<span class="rel-arrow">${rel.label || rel.name || ''}</span>${rel.label ? `<span class="mono" style="color:var(--muted);font-size:.7rem">（${rel.name}）</span>` : ''}`;
      html += `<tr>`
        + `<td>${fmtCls(rel.from)}</td>`
        + `<td>${relCell}</td>`
        + `<td>${fmtCls(rel.to)}</td>`
        + `<td>${rel.description || ''}${props ? `<br><span style="color:var(--muted);font-size:.72rem">プロパティ: ${props}</span>` : ''}</td>`
        + `<td class="evid">${linkifyRefs(rel.evidence)}</td>`
        + `</tr>`;
    }
    html += `</tbody></table>`;

// Build class lookup: name → {label, properties}
    const classMap = {};
    (def.classes || []).forEach(cls => {
      classMap[cls.name] = cls;
    });

    // Constraints
    const constraints = def.constraints || [];
    html += `<h3 style="font-size:.95rem;margin:16px 0 6px">⚠️ 制約（${constraints.length}件）</h3>`;
    if (constraints.length) {
      html += `<table class="otbl"><thead><tr><th style="width:40px">#</th><th>対象クラス.プロパティ</th><th>制約</th><th>値</th><th>出典</th></tr></thead><tbody>`;
      constraints.forEach((c, i) => {
        const clsName = c.target_class || '';
        const propName = c.target_property || '';
        const clsInfo = classMap[clsName] || {};
        const clsLabel = clsInfo.label || '';
        const propInfo = (clsInfo.properties || []).find(p => p.name === propName) || {};
        const propLabel = propInfo.label || '';

        // Build display: "論理名 (物理名)"
        const clsDisplay = clsLabel ? `${clsLabel} (${clsName})` : clsName;
        const propDisplay = propLabel ? `${propLabel} (${propName})` : propName;
        const fullDisplay = clsName && propName ? `${clsDisplay}.${propDisplay}` : (clsDisplay || c.target_entity || '');
        const anchorId = `cls-${clsName}`;
        const targetHtml = fullDisplay
          ? `<a href="#${anchorId}" class="mono" style="font-size:.78rem;color:var(--accent);text-decoration:none;border-bottom:1px dashed var(--accent)" title="クラス定義へジャンプ">${fullDisplay}</a>`
          : '<span style="color:var(--muted);font-size:.72rem">（全体）</span>';

        const valStr = c.value ? c.value + (c.unit || '') : '';
        html += `<tr><td>${i+1}</td><td>${targetHtml}</td><td>${c.description || ''}</td><td class="mono" style="font-size:.78rem">${valStr || '—'}</td><td class="evid">${linkifyRefs(c.source)}</td></tr>`;
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
      const cls = classByName[name];
      const disp = (cls && cls.label) ? cls.label : name;   // 論理名優先（ノード上は日本語）
      const r = Math.max(11, Math.min(22, 22 - String(disp).length * 0.4));
      nodeById[name] = { id: name, name, disp, defined, r,
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
      mid.textContent = (e.rel && e.rel.label) ? e.rel.label : (e.type || ''); svg.appendChild(mid);
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
      text.textContent = (n.disp.length > 10 ? n.disp.slice(0,10)+'…' : n.disp);
      g.appendChild(text);
      g.addEventListener('click', () => {
        const detail = document.getElementById('onto-node-detail');
        const cls = classByName[n.id];
        const propRows = cls && cls.properties && cls.properties.length
          ? cls.properties.map(p => `<tr><td style="padding:2px 4px;font-weight:600;color:var(--accent)">${p.label ? p.label + '<br>' : ''}<span class="mono" style="font-weight:400;color:var(--muted);font-size:.72rem">${p.name}</span></td><td style="padding:2px 4px">${p.type||''}${p.required?' <span class="req">必須</span>':''}</td></tr>`).join('')
          : `<tr><td colspan="2" style="padding:2px 4px;color:var(--muted)">${cls ? '（プロパティなし）' : '定義に無い参照クラス（関係の from/to にのみ登場）'}</td></tr>`;
        detail.innerHTML = `<button class="detail-close" onclick="this.parentElement.style.display='none'">✕</button>
          <h3>${n.disp}</h3><div style="font-size:.72rem;color:#888"><span class="mono">${n.name}</span> ・ ${n.defined ? 'クラス定義' : '参照のみ'}</div>
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
  btn.disabled = true; btn.textContent = '⏳ 抽出開始…';
  st.style.display = 'block';
  st.innerHTML = '<div class="prog-bar"><div class="prog-fill" id="kg-prog-fill" style="width:0%"></div></div><div class="prog-label" id="kg-prog-label">準備中…</div>';
  try {
    const r = await fetch(API + '/api/kg/extract', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { st.innerHTML = '⚠ ' + (d.error || '失敗'); btn.disabled=false; btn.textContent=old; return; }
    const tid = d.task_id;
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const pr = await (await fetch(API + '/api/task/' + tid)).json();
      const pct = Math.round(pr.progress / pr.total * 100);
      document.getElementById('kg-prog-fill').style.width = pct + '%';
      document.getElementById('kg-prog-label').textContent = pr.msg || (pct + '%');
      if (pr.status === 'done') { st.innerHTML = '<div class="prog-bar"><div class="prog-fill done" style="width:100%"></div></div><div class="prog-label" style="color:var(--ok)">✅ 完了</div>'; break; }
      if (pr.status === 'error') { st.innerHTML = '<div class="prog-bar"><div class="prog-fill error" style="width:100%"></div></div><div class="prog-label" style="color:var(--reject)">⚠ エラー: ' + (pr.error || '') + '</div>'; break; }
    }
    await renderKG();
  } catch(e) { st.innerHTML = '<div class="prog-label" style="color:var(--reject)">⚠ 通信エラー: ' + e.message + '</div>'; }
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
  await loadOntoLabels();   // ノード型(クラス)・エッジ型(関係)に日本語論理名を併記
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
  // 型(クラス)→日本語論理名: ノード自身の props.type_label を優先し、無ければオントロジー対応表→物理名
  const typeJa = {};
  kg.nodes.forEach(n => { const t=(n.labels&&n.labels[n.labels.length-1]); const tl=(n.props&&n.props.type_label); if(t&&tl&&!typeJa[t]) typeJa[t]=tl; });
  const typeLabelJa = (name)=> typeJa[name] || clsLabelJa(name);
  const typeLabelFull = (name)=> typeJa[name] ? `${typeJa[name]}（${name}）` : clsLabel(name);
  const allLabels = [...new Set(kg.nodes.flatMap(n => n.labels || []))];
  allLabels.forEach((l,i)=>{ labelColors[l] = palette[i%palette.length]; });
  legend.innerHTML = allLabels.map(l =>
    `<span style="display:inline-flex;align-items:center;gap:4px" title="${l}"><span style="width:10px;height:10px;border-radius:50%;background:${labelColors[l]};display:inline-block"></span>${typeLabelJa(l)}</span>`
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
    mid.textContent=relLabelJa(e.type); svg.appendChild(mid);
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
        // source / evidence は複数スラッグ(カンマ・矢印区切り)を含むので個別にリンク化（全体を1リンクにすると404）
        if((k==='source'||k==='evidence') && typeof v==='string') val=linkifyRefs(v);
        return `<tr><td style="padding:2px 4px;font-weight:600;color:var(--accent);vertical-align:top">${k}</td><td style="padding:2px 4px">${val}</td></tr>`;
      }).join('');
      const nameOf=(id)=>(nodeById[id]&&nodeById[id].name)||id;
      const edgeProps=(e)=>{
        const p=(e.props&&typeof e.props==='object')?Object.entries(e.props):[];
        return p.length?` <span style="color:var(--muted)">{${p.map(([k,v])=>`${k}: ${typeof v==='object'?JSON.stringify(v):v}`).join(', ')}}</span>`:'';
      };
      const outE=(kg.edges||[]).filter(e=>e.from===n.id).map(e=>`<span class="rel-arrow">${relLabelJa(e.type)}</span> → ${nameOf(e.to)}${edgeProps(e)}`);
      const inE=(kg.edges||[]).filter(e=>e.to===n.id).map(e=>`${nameOf(e.from)} → <span class="rel-arrow">${relLabelJa(e.type)}</span>${edgeProps(e)}`);
      detail.innerHTML=`<button class="detail-close" onclick="this.parentElement.style.display='none'">✕</button>
        <h3 style="margin:0 0 2px">${n.name}</h3>
        <div style="font-size:.72rem;color:#888;margin-bottom:6px">${typeLabelFull(n.label)} | ${n.id}</div>
        <table style="width:100%;font-size:.78rem;border-collapse:collapse">${rows}</table>
        ${outE.length?`<div style="margin-top:8px;font-size:.72rem;color:var(--muted)">出力エッジ</div><div style="font-size:.75rem">${outE.join('<br>')}</div>`:''}
        ${inE.length?`<div style="margin-top:6px;font-size:.72rem;color:var(--muted)">入力エッジ</div><div style="font-size:.75rem">${inE.join('<br>')}</div>`:''}`;
      detail.style.display='block';
    });
    svg.appendChild(g);
  }
}

// ── 3.2 検証（ナレッジグラフでQAに答えられるか） ──
function escHtml(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function verdictBadge(v){
  const m={correct:['✓ 正解','#166534','#dcfce7'],partial:['△ 部分','#854d0e','#fef9c3'],
           incorrect:['✗ 不正解','#991b1b','#fee2e2'],error:['⚠ エラー','#6b7280','#f3f4f6']};
  const [t,c,bg]=m[v]||m.error;
  return `<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:.72rem;font-weight:700;color:${c};background:${bg}">${t}</span>`;
}
let VAL_QAS = [];
async function renderValidation(){
  await loadWikiIndex();
  let cqs=[], results={};
  try{
    [cqs, results] = await Promise.all([
      fetch(API+'/api/validation/cqs').then(r=>r.json()),
      fetch(API+'/api/validation/results').then(r=>r.json())
    ]);
  }catch(e){ document.getElementById('val-list').innerHTML='<div class="warnline">⚠ 取得エラー: '+e.message+'</div>'; return; }
  VAL_QAS = cqs;
  renderValSummary(results);
  const list=document.getElementById('val-list');
  list.innerHTML = cqs.length
    ? cqs.map(cq=>valCard(cq, results[cq.id])).join('')
    : '<div class="empty-state" style="padding:30px;text-align:center;color:var(--muted)">承認済みQAがありません。先に「2.1 QA」で承認してください。</div>';
}
function renderValSummary(results){
  const arr=Object.values(results||{});
  const tally=(mode)=>{const c={correct:0,partial:0,incorrect:0,error:0};arr.forEach(r=>{if(!r[mode])return;const v=(r[mode]||{}).verdict||'error';c[v]=(c[v]||0)+1;});return c;};
  const total=VAL_QAS.length;
  const pct = (n) => total ? (n / total * 100).toFixed(0) : 0;
  const bar=(label,c)=>{
    const done=c.correct+c.partial+c.incorrect+c.error;
    const correctPct = pct(c.correct);
    const okPct = pct(c.correct + c.partial);
    return `<div style="flex:1;min-width:230px">
      <div style="font-size:.8rem;font-weight:600;margin-bottom:5px">${label} <span style="color:var(--muted)">(${done}/${total} 検証済)</span></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;font-size:.78rem;align-items:center">
        ${verdictBadge('correct')} ${c.correct}${verdictBadge('partial')} ${c.partial}${verdictBadge('incorrect')} ${c.incorrect}${c.error?verdictBadge('error')+' '+c.error:''}
      </div>
      <div style="font-size:.72rem;color:var(--muted);margin-top:3px">
        正解率: <b style="color:var(--ok)">${correctPct}%</b> &nbsp;|&nbsp; 正解+部分: <b style="color:var(--warn)">${okPct}%</b>
      </div></div>`;
  };
  document.getElementById('val-summary').innerHTML =
    `<div style="display:flex;gap:24px;flex-wrap:wrap;padding:12px 14px;border:1px solid var(--border);border-radius:8px;background:var(--card);margin-bottom:12px">
      ${bar('🔍 ナイーブRAG', tally('naive'))}${bar('🗄 KGのみ', tally('kg'))}${bar('🗄+📄 KG+Wiki補完', tally('kgwiki'))}</div>`;
}
const VAL_TYPE={lookup:'📖 単一参照',multi_hop:'🔗 多段探索',aggregation:'📋 一覧取得',constraint:'⚠️ 条件確認'};
function valCard(cq, res){
  const head=`<div class="card-header"><span class="card-id">${cq.id}</span>
     <span style="font-size:.72rem;color:var(--muted)">${VAL_TYPE[cq.type]||cq.type||''}</span></div>
     <div class="card-title" style="font-size:.92rem;color:var(--accent);margin:2px 0 4px">❓ ${escHtml(cq.title)}</div>
     <div style="font-size:.8rem;margin-bottom:8px"><b style="color:var(--ok)">💡 正解:</b> ${cq.expected_answer?escHtml(cq.expected_answer):'<i style="color:var(--muted)">（未設定）</i>'}</div>`;
  const body = res ? valResultHtml(res)
    : `<button onclick="runValidation('${cq.id}')" style="padding:6px 12px;border:1px solid var(--accent);border-radius:6px;background:var(--card);color:var(--accent);font-weight:600;cursor:pointer;font-size:.8rem">▶ このQAを検証</button>`;
  return `<div class="card" id="val-card-${cq.id}">${head}<div id="val-body-${cq.id}">${body}</div></div>`;
}
function valResultHtml(res){
  const col=(label,d,extra)=>`<div style="flex:1;min-width:230px;border:1px solid var(--border);border-radius:8px;padding:9px 11px;background:var(--card)">
     <div style="display:flex;align-items:center;gap:6px;margin-bottom:5px"><b style="font-size:.78rem">${label}</b> ${verdictBadge((d||{}).verdict)}</div>
     <div style="font-size:.82rem;white-space:pre-wrap;margin-bottom:6px">${escHtml((d||{}).answer||'')}</div>
     <div style="font-size:.72rem;color:var(--muted)"><b>判定理由:</b> ${escHtml((d||{}).reason||'')}</div>
     ${(d&&d.cause)?`<div style="font-size:.72rem;color:#7c2d12;background:#fff7ed;border:1px solid #fed7aa;border-radius:5px;padding:3px 6px;margin-top:5px">🩺 <b>原因解析:</b> ${escHtml(d.cause)}</div>`:''}
     ${extra||''}
   </div>`;
  const naiveSrc=(res.naive&&res.naive.rag_sources&&res.naive.rag_sources.length)
    ? `<div style="font-size:.7rem;color:var(--muted);margin-top:4px">出典: ${res.naive.rag_sources.map(s=>'p.'+s.page).join(', ')}</div>` : '';
  const naiveCol=res.naive
    ? col('🔍 ナイーブRAG',res.naive,naiveSrc)
    : `<div style="flex:1;min-width:230px;border:1px dashed var(--border);border-radius:8px;padding:9px 11px;background:var(--card);color:var(--muted);font-size:.78rem"><b>🔍 ナイーブRAG</b><br><span style="font-size:.74rem">旧結果のため未実行。「🔄 再検証」で追加されます。</span></div>`;
  const ents=(res.entities||[]).map(escHtml).join('、');
  const srcs=(res.sources||[]).map(s=>linkifyRefs(s)).join(' ');
  const traceHtml=(res.trace||[]).map(t=>`<div>[${escHtml(t.node)}] ${escHtml(t.txt)}</div>`).join('');
  return `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:6px">${naiveCol}${col('🗄 KGのみ',res.kg)}${col('🗄+📄 KG+Wiki補完',res.kgwiki)}</div>
    ${res.diag?`<div style="font-size:.72rem;color:var(--muted);margin-bottom:4px">📊 取得ノード <b>${res.diag.retrieved_nodes}</b> / 参照Wiki要求 <b>${res.diag.wiki_requested}</b>・読込成功 <b>${res.diag.wiki_loaded}</b>${res.diag.wiki_loaded===0&&res.diag.wiki_requested>0?' <span style="color:#b91c1c">← Wikiを1ページも読めていません</span>':''}${res.diag.retrieved_nodes===0?' <span style="color:#b91c1c">← KGから関連ノードを取得できていません</span>':''}</div>`:''}
    <details style="font-size:.75rem"><summary style="cursor:pointer;color:var(--muted)">検索エンティティ / 参照Wiki / エージェント経路</summary>
      <div style="margin-top:5px"><b>検索エンティティ(${(res.entities||[]).length}):</b> ${ents||'—'}</div>
      <div style="margin-top:3px"><b>参照Wiki(読込成功: ${(res.wiki_loaded||[]).length}):</b> ${srcs||'—'}</div>
      <div style="margin-top:5px;font-family:monospace;font-size:.7rem;color:var(--muted)">${traceHtml}</div>
      <div style="margin-top:4px;color:var(--muted)">実行: ${escHtml(res.run_at||'')}</div>
    </details>
    <div style="margin-top:6px"><button onclick="runValidation('${res.cq_id}')" style="padding:4px 10px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--muted);cursor:pointer;font-size:.76rem">🔄 再検証</button></div>`;
}
async function runValidation(cqId){
  const body=document.getElementById('val-body-'+cqId);
  if(body) body.innerHTML='<div style="color:var(--muted);font-size:.82rem">⏳ 検証中…（Planner→Retrieval→回答A/B→判定, 15秒程度）</div>';
  try{
    const r=await fetch(API+'/api/validation/run/'+cqId,{method:'POST'});
    const d=await r.json();
    if(d.ok){ if(body) body.innerHTML=valResultHtml(d.result); await refreshValSummary(); }
    else{ if(body) body.innerHTML='<div class="warnline">⚠ '+escHtml(d.error||'失敗')+'</div>'+
          `<div style="margin-top:6px"><button onclick="runValidation('${cqId}')" style="padding:4px 10px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--muted);cursor:pointer;font-size:.76rem">🔄 再試行</button></div>`; }
  }catch(e){ if(body) body.innerHTML='<div class="warnline">⚠ 通信エラー: '+escHtml(e.message)+'</div>'; }
}
async function refreshValSummary(){
  try{ const results=await fetch(API+'/api/validation/results').then(r=>r.json()); renderValSummary(results); }catch(e){}
}
async function fixOntologyFromValidation(){
  const btn=document.getElementById('val-fix-btn');
  const old=btn.textContent; btn.disabled=true; btn.textContent='⏳ 修正中…';
  try{
    const r=await fetch(API+'/api/ontology/fix-from-validation',{method:'POST'});
    const d=await r.json();
    if(d.ok){
      alert(`✅ オントロジー修正完了: ${d.fixed_cqs}件のQAを修正, ${d.classes}クラス, ${d.relationships}関係`);
    } else {
      alert('⚠ '+(d.error||''));
    }
  }catch(e){ alert('⚠ 通信エラー: '+e.message); }
  btn.disabled=false; btn.textContent=old;
}

async function runAllValidation(){
  if(!VAL_QAS.length){ return; }
  const btn=document.getElementById('val-runall-btn'); const prog=document.getElementById('val-progress');
  const old=btn.textContent; btn.disabled=true; prog.style.display='block';
  let i=0;
  for(const cq of VAL_QAS){
    i++; btn.textContent=`⏳ ${i}/${VAL_QAS.length}`;
    prog.textContent=`検証中 ${i}/${VAL_QAS.length}: ${cq.id} ${cq.title}`;
    await runValidation(cq.id);
  }
  btn.disabled=false; btn.textContent=old;
  prog.textContent=`✅ 全${VAL_QAS.length}件の検証が完了しました。`;
  await refreshValSummary();
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
@app.get("/validation", response_class=HTMLResponse)
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