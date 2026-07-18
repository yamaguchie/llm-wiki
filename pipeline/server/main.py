# -*- coding: utf-8 -*-
"""
オントロジー駆動ナレッジグラフ構築パイプライン — レビューUI（Phase3-5 相当）

Usage:
    pip install fastapi uvicorn
    uvicorn main:app --reload --port 8790

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
import json, os, sys, datetime, re, glob, uuid, threading, time, shutil, asyncio, hashlib, unicodedata
from enum import Enum
from typing import Optional
from pathlib import Path
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "agent"))

from fastapi import FastAPI, Query, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Bunkyo Welfare KG — 統合サーバー")
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── チャットAPI（統合） ──
# 旧 /api/ask（agent.py の固定 kg.json ベース検索）は、チャット画面が動的KG(kg_extracted/Neo4j)
# ベースの /api/validation/ask に一本化されたことで不要になったため削除した（agent.py 自体は
# 独立実行可能なCLIとして残置）。KGの実体は今後すべて 3.1 の抽出結果（kg_extracted/Neo4j）に一本化する。
import naive_rag

@app.post("/api/rag")
async def rag_ask(request: Request):
    data = await request.json()
    q = (data.get("query") or "").strip()
    if not q: return JSONResponse({"error": "empty query"}, status_code=400)
    try:
        return JSONResponse(naive_rag.ask(q))
    except Exception as ex:
        return JSONResponse({"error": f"{type(ex).__name__}: {ex}"}, status_code=500)

# ── レビューUI（各画面） ──
@app.get("/review/{path:path}")
async def review_ui(path: str):
    return HTMLResponse(HTML)


# ── 静的ファイル配信 ──
TEXT_EXT = {".md", ".txt", ".yaml", ".yml", ".cypher"}
BOM = b"\xef\xbb\xbf"


def _serve_file(path):
    full = os.path.join(ROOT, path.lstrip("/"))
    if not os.path.isfile(full):
        return None
    ext = os.path.splitext(full)[1].lower()
    if ext == ".md":
        with open(full, "r", encoding="utf-8") as f:
            raw = f.read()
        return HTMLResponse(_md_to_html(raw, full))
    if ext in (".yaml", ".yml") and "competency_questions" in path:
        with open(full, "r", encoding="utf-8") as f:
            raw = f.read()
        return HTMLResponse(_yaml_to_html(raw, full))
    if ext in TEXT_EXT or ext == ".json":
        with open(full, "rb") as f:
            body = f.read()
        if ext != ".json" and not body.startswith(BOM):
            body = BOM + body
        ctype = "application/json" if ext == ".json" else "text/plain"
        return Response(content=body, media_type=f"{ctype}; charset=utf-8", headers={"Cache-Control": "no-store"})
    return FileResponse(full, headers={"Cache-Control": "no-store"})

# ── LLM-Wiki / 静的ファイル配信（統合サーバが .md を HTML 表示・PDF等も配信）──
@app.get("/pipeline/step1_data/wiki/{fname}")
def serve_wiki_file(fname: str):
    r = _serve_file(f"pipeline/step1_data/wiki/{fname}")
    return r if r is not None else JSONResponse({"error": "not found"}, status_code=404)
    r = _serve_file(f"entities/{fname}")
    return r if r is not None else JSONResponse({"error": "not found"}, status_code=404)

@app.get("/pipeline/step1_data/raw/pages/{fname}")
def serve_raw_page_file(fname: str):
    r = _serve_file(f"pipeline/step1_data/raw/pages/{fname}")
    return r if r is not None else JSONResponse({"error": "not found"}, status_code=404)

@app.get("/pipeline/step1_data/raw/chapters/{fname}")
def serve_raw_chapter_file(fname: str):
    r = _serve_file(f"pipeline/step1_data/raw/chapters/{fname}")
    return r if r is not None else JSONResponse({"error": "not found"}, status_code=404)

@app.get("/pipeline/step1_data/raw/{fname}")
def serve_raw_root_file(fname: str):
    r = _serve_file(f"pipeline/step1_data/raw/{fname}")
    return r if r is not None else JSONResponse({"error": "not found"}, status_code=404)

@app.get("/sources/{fname}")
def serve_source_file(fname: str):
    r = _serve_file(f"sources/{fname}")
    return r if r is not None else JSONResponse({"error": "not found"}, status_code=404)

@app.get("/{fname}.pdf")
def serve_pdf_file(fname: str):
    r = _serve_file(f"{fname}.pdf")
    return r if r is not None else JSONResponse({"error": "not found"}, status_code=404)

def _md_to_html(raw, filepath):
    text = re.sub(r'\[\[([\w-]+)\]\]', r'[\1](/pipeline/step1_data/wiki/\1.md)', raw)
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = text.split("\n"); html_parts = []; in_table = False; in_list = [False]
    for line in lines:
        s = line.strip()
        m = re.match(r'^(#{1,6})\s+(.+)$', s)
        if m: html_parts.append(f"<h{len(m.group(1))}>{_imd(m.group(2))}</h{len(m.group(1))}>"); continue
        if re.match(r'^---+$', s): html_parts.append("<hr>"); continue
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.split("|")[1:-1]]
            if not in_table: html_parts.append("<table>"); in_table = True
            if re.match(r'^[\s|:\-]+$', s): continue
            t = "th" if in_table else "td"
            html_parts.append(f"<tr><{'><'.join([t]*len(cells))}>" + f"</{t}><{t}>".join(_imd(c) for c in cells) + f"</{t}></tr>")
            continue
        if in_table: html_parts.append("</table>"); in_table = False
        m = re.match(r'^(\s*)[-*]\s+(.+)$', line)
        if m:
            content = _imd(m.group(2)); d = len(m.group(1)) // 2
            while len(in_list) <= d: in_list.append(False)
            if not in_list[d]: html_parts.append("<ul>" * (1 if d == 0 or not in_list[d-1] else 0) + "<li>" + content); in_list[d] = True
            else: html_parts.append("<li>" + content)
            continue
        for d in range(len(in_list)):
            if in_list[d]: html_parts.append("</li></ul>"); in_list[d] = False
        if not s: html_parts.append("<div class='pbreak'></div>"); continue
        html_parts.append(f"<p>{_imd(s)}</p>")
    if in_table: html_parts.append("</table>")
    body = "\n".join(html_parts)
    title = (re.search(r'<h1>(.*?)</h1>', body) or [None, os.path.basename(filepath)]).group(1)
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>*{{box-sizing:border-box}}body{{font-family:system-ui,Hiragino Sans,sans-serif;max-width:900px;margin:0 auto;padding:16px;background:#f6f7f9;color:#1b1f24;line-height:1.7}}
h1{{font-size:1.3rem;border-bottom:2px solid #2b6cb0;padding-bottom:6px}}h2{{font-size:1.1rem;margin-top:1.2em}}h3{{font-size:1rem}}h4{{font-size:.95rem;color:#555}}
a{{color:#2b6cb0;text-decoration:none}}a:hover{{text-decoration:underline}}table{{border-collapse:collapse;width:100%;margin:8px 0;font-size:.85rem}}
th,td{{border:1px solid #ddd;padding:5px 8px;text-align:left;vertical-align:top}}th{{background:#eef2f7;font-weight:600}}
tr:nth-child(even){{background:#fafbfc}}.pbreak{{height:8px}}hr{{border:0;border-top:1px solid #ddd;margin:12px 0}}code{{background:#eee;padding:1px 4px;border-radius:3px;font-size:.85rem}}
</style></head><body><div style="font-size:.75rem;color:#888;margin-bottom:8px">📄 <a href="/">チャット</a> / <a href="/{os.path.relpath(filepath, ROOT)}">原文.md</a></div>{body}</body></html>"""

def _yaml_to_html(raw, filepath):
    text = raw.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    text = re.sub(r'(^|\n)(\w[\w-]*)(:)', r'\1<span style="color:#2563eb;font-weight:600">\2</span>\3', text)
    text = re.sub(r'(#.*)', r'<span style="color:#9ca3af;font-style:italic">\1</span>', text)
    text = re.sub(r'("(?:[^"\\]|\\.)*")', r'<span style="color:#16a34a">\1</span>', text)
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>QA一覧</title><style>
*{{box-sizing:border-box}}body{{font-family:system-ui,Hiragino Sans,sans-serif;max-width:960px;margin:0 auto;padding:16px;background:#f6f7f9;color:#1b1f24;line-height:1.7}}
pre{{background:#f8f9fb;border:1px solid #e2e6ea;border-radius:8px;padding:14px;overflow-x:auto;font-size:.82rem;line-height:1.5}}a{{color:#2563eb;text-decoration:none}}
</style></head><body><a href="/">チャット</a> / <a href="/pipeline/ontology/competency_questions.yaml">原文.yaml</a><pre>{text}</pre></body></html>"""

def _imd(text):
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" style="max-width:100%">', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    return text

# ── Load KG ──
KG_PATH = os.path.join(HERE, "..", "step3_kg", "data", "kg.json")
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
# step1_data はホストとバインドマウントされているため、コンテナ再作成後も状態が残る
STATE_PATH = os.path.join(HERE, "..", "step1_data", "review_state.json")

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


@app.get("/api/raw/status")
def raw_status():
    rag_dir = os.path.join(HERE, "..", "step1_data", "raw")
    chunks_path = os.path.join(rag_dir, "chunks.json")
    meta_path = os.path.join(rag_dir, "ocr_meta.json")
    page_files = sorted(glob.glob(os.path.join(rag_dir, "pages", "*.txt")))
    chapter_files = sorted(glob.glob(os.path.join(rag_dir, "chapters", "*.txt")))
    if not (page_files or os.path.isfile(chunks_path)):
        return {"exists": False}
    n_chunks = total_chars = 0
    if os.path.isfile(chunks_path):
        data = json.load(open(chunks_path, encoding="utf-8"))
        n_chunks = data.get("n_chunks", len(data.get("chunks", [])))
        total_chars = data.get("total_chars", 0)
    chapters = []
    if os.path.isfile(meta_path):
        try:
            chapters = json.load(open(meta_path, encoding="utf-8")).get("chapters", [])
        except Exception:
            chapters = []
    if not chapters:
        chapters = [{"file": os.path.basename(c), "title": os.path.basename(c)} for c in chapter_files]
    return {
        "exists": True,
        "n_pages": len(page_files),
        "n_chapters": len(chapter_files),
        "chunks": n_chunks,
        "chars": total_chars,
        "pages": [os.path.basename(p) for p in page_files],
        "chapters": chapters,
    }


def _do_generate_llmwiki(tid):
    """③ raw_text.txt → Karpathy方式LLM-Wiki → 相互リンク付きmdページ（1.2画面／一括実行から共有）"""
    try:
            from llm_utils import llm_text, llm_json
            rag_dir = os.path.join(HERE, "..", "step1_data", "raw")
            wiki_dir = os.path.join(HERE, "..", "step1_data", "wiki")
            # 入力: 1.1 OCRの章ファイル群 chapters/*.txt（無ければ raw_text.txt にフォールバック）
            _task_update(tid, 5, "章テキスト読み込み中…")
            chap_files = sorted(glob.glob(os.path.join(rag_dir, "chapters", "*.txt")))
            if chap_files:
                chapter_texts = [(os.path.basename(cf), open(cf, encoding="utf-8").read()) for cf in chap_files]
            else:
                raw_path = os.path.join(rag_dir, "raw_text.txt")
                if not os.path.isfile(raw_path):
                    _task_done(tid, "章テキストがありません。先に1.1 RAWデータでOCRを実行してください。")
                    return
                chapter_texts = [("raw_text.txt", open(raw_path, encoding="utf-8").read())]
            raw_text = "\n\n".join(t for _, t in chapter_texts)
            os.makedirs(wiki_dir, exist_ok=True)
            for f in glob.glob(os.path.join(wiki_dir, "*.md")):
                os.remove(f)

            # Karpathy方式: LLMに全テキストを渡し、エンティティ・概念・相互リンクを一括生成させる
            _task_update(tid, 15, "LLMがエンティティ一覧を生成中…（章ごと）")
            # Step 1: 章ごとにエンティティ/概念を抽出して統合。
            # 【重複対策】同じ実体が複数の章（目次・一覧表・索引・詳細章など）にまたがって
            # 言及されることがあり、章ごとに独立してLLMを呼ぶと、同じ実体に別々の物理名が
            # 付いて重複ページが生成される（旧: name完全一致でしか重複排除していなかった）。
            # 原本のページ番号(page)は実体単位でほぼ一意なため、page一致＋ラベル類似を
            # 重複判定に使い、章をまたいでも1エンティティ1ページに正規化する。
            entities, _seen_names = [], set()
            _by_page = {}  # page番号 -> entities のインデックス（重複判定用）
            for ci, (cname, ctext) in enumerate(chapter_texts, 1):
                _task_update(tid, 15 + int(ci / max(1, len(chapter_texts)) * 15), f"エンティティ抽出 {ci}/{len(chapter_texts)}章")
                try:
                    el = llm_json(
                        "あなたは知識ベース構築エージェントです。以下の文書（1章分）から、重要な概念・制度・サービス・窓口・手続きを列挙してください。"
                        "出力はJSON配列: [{\"name\":\"物理名(英数字+ハイフン)\",\"label\":\"日本語名\",\"page\":\"ページ番号\",\"summary\":\"一言説明\"}]",
                        f"【文書】\n{ctext[:15000]}"
                    )
                except Exception:
                    el = []
                items = el if isinstance(el, list) else el.get("entities", el.get("concepts", []))
                for e in (items or []):
                    nm = (e.get("name") or "").strip()
                    if not nm or nm in _seen_names:
                        continue
                    label = _norm(e.get("label") or "")
                    page = str(e.get("page") or "").strip()
                    dup = None
                    if page:
                        for existing in _by_page.get(page, []):
                            el_label = _norm(existing.get("label") or "")
                            if label and el_label and (label == el_label or label in el_label or el_label in label):
                                dup = existing; break
                    if dup is not None:
                        # 重複: 新しい物理名は作らず、由来した章だけ既存エンティティに追記する
                        dup.setdefault("_chapters", set()).add(cname)
                        continue
                    _seen_names.add(nm)
                    e["_chapters"] = {cname}
                    entities.append(e)
                    if page:
                        _by_page.setdefault(page, []).append(e)

            _task_update(tid, 30, f"LLMが {len(entities)} 件のWikiページを生成中…（Karpathy方式）")
            # Step 2: 全エンティティの情報を一度にLLMに渡し、相互リンク付きの全ページを生成
            entity_text = "\n".join(
                f"- {e.get('name','?')}: {e.get('label','?')} (p.{e.get('page','?')}) — {e.get('summary','?')}"
                for e in entities
            )[:20000]
            # 章名 -> 本文 の対応表（バッチごとに関連する章の全文だけを渡すために使う）
            chapter_text_by_name = dict(chapter_texts)

            # バッチ処理: エンティティを分割して各バッチでページ生成（全件処理・上限キャップなし）
            batch_size = 15
            n_total = len(entities)
            total_batches = max(1, (n_total - 1) // batch_size + 1)
            generated_pages = []

            for batch_idx in range(0, n_total, batch_size):
                batch_entities = entities[batch_idx:batch_idx + batch_size]
                batch_names = [e.get('name', '') for e in batch_entities]
                prog = 35 + int(batch_idx / max(1, n_total) * 55)
                _task_update(tid, prog, f"バッチ{batch_idx//batch_size+1}/{total_batches} ページ生成中…")

                sys_p = (
                    "あなたはWikipediaスタイルの知識ベース構築エージェントです。"
                    "以下のエンティティ一覧について、各エンティティ1ファイルのMarkdownページを生成してください。\n\n"
                    "【出力ルール】\n"
                    "- 各ファイルの本文は必ず1行目を `FILE: 物理名.md`（英数字+ハイフンのファイル名）から開始すること\n"
                    "- 2行目以降に `---\npage: N\n---\n` 形式のYAML frontmatter（出典ページ番号）を置くこと\n"
                    "- 全エンティティ間の相互リンクを張ること。リンク先は `[label](物理名.md)` の標準Markdownリンク形式\n"
                    "- 日本語の見出し・説明・箇条書きを含める\n"
                    "- 【最重要】内容は必ず下記【元文書】に実際に書かれている記述だけを転記・要約すること。"
                    "【元文書】に記載が無い窓口名・金額・条件等は絶対に創作しない。"
                    "書かれていない場合はその項目を省略する（推測で埋めない）\n"
                    "- 出力は各ファイルを `---FILE---` で区切ってください"
                )
                # 【重要】以前はバッチによらず文書全体の先頭12000字(=全体のごく一部)を固定で渡していたため、
                # 該当箇所がその範囲に無いバッチはLLMが実質ゼロ知識でページ内容を創作していた
                # （これが重複ページ間で窓口名が食い違う等の実害の原因だった）。
                # ここではバッチの各エンティティが実際に抽出された章の本文だけを渡すことで、
                # 常に根拠のある原文をもとに生成させる。
                batch_chapters = []
                seen_ch = set()
                for e in batch_entities:
                    for ch in sorted(e.get("_chapters") or []):
                        if ch not in seen_ch:
                            seen_ch.add(ch); batch_chapters.append(ch)
                if batch_chapters:
                    source_text = "\n\n".join(f"### {ch}\n{chapter_text_by_name.get(ch, '')}" for ch in batch_chapters)
                else:
                    source_text = raw_text  # 章情報が無い旧データ形式へのフォールバック
                user_p = (
                    f"【全エンティティ一覧（相互リンク用）】\n{entity_text}\n\n"
                    f"【このバッチで生成するエンティティ】\n{', '.join(batch_names)}\n\n"
                    f"【元文書（このバッチのエンティティが実際に記載されている章のみ）】\n{source_text[:40000]}"
                )
                result = llm_text(sys_p, user_p)

                sections = result.split("---FILE---")
                for sec in sections:
                    sec = sec.strip()
                    if not sec:
                        continue
                    # 1行目の `FILE: xxx.md` マーカーからファイル名を確定（本文中の相互リンクと誤認しないように）
                    first_line, _, rest = sec.partition("\n")
                    m = re.match(r'FILE:\s*([\w-]+\.md)', first_line.strip())
                    if not m:
                        continue
                    filename = m.group(1)
                    body = rest.strip()
                    # 相互リンクを標準形式に変換（念のため）
                    body = re.sub(r'\[\[([\w-]+)\]\]', r'[\1](\1.md)', body)
                    filepath = os.path.join(wiki_dir, filename)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(body)
                    generated_pages.append(filename)

            _task_update(tid, 95, "インデックスページ生成中…")
            # インデックスページ
            index_lines = [f"- [{e.get('label','?')}]({e.get('name','')}.md) — p.{e.get('page','?')}" for e in entities]
            with open(os.path.join(wiki_dir, "index.md"), "w", encoding="utf-8") as f:
                f.write("# ドメイン知識ベース\n\n" + "\n".join(index_lines))

            # 2.1 QA クリア
            global REVIEW_ITEMS, validation_results
            REVIEW_ITEMS[:] = [i for i in REVIEW_ITEMS if i.get("type_cq") != "cq"]
            validation_results.clear()
            save_state()

            _task_update(tid, 100, f"完了: {len(generated_pages)}ページ生成")
            _task_done(tid)
    except Exception as ex:
        _task_done(tid, str(ex))

@app.post("/api/llmwiki/generate")
def generate_llmwiki():
    tid = _task_start("llmwiki_gen", total=100)
    threading.Thread(target=_do_generate_llmwiki, args=(tid,), daemon=True).start()
    return {"ok": True, "task_id": tid}


@app.get("/api/llmwiki/status")
def llmwiki_status():
    pages_dir = os.path.join(HERE, "..", "step1_data", "wiki")
    md_files = glob.glob(os.path.join(pages_dir, "*.md"))
    pages = []
    for mf in sorted(md_files):
        name = os.path.basename(mf)
        content = open(mf, encoding="utf-8").read()
        # ページ番号を抽出
        page_nums = re.findall(r'page:\s*(\d+)', content)
        size = len(content)
        pages.append({"name": name, "size": size, "pages": page_nums})
    return {"exists": len(pages) > 0, "count": len(pages), "pages": pages}


@app.get("/api/llmwiki/graph")
def llmwiki_graph():
    """1.2 LLM-Wikiのページ相互リンクをグラフ（ノード=ページ、エッジ=相互リンク）として返す。"""
    wiki_dir = os.path.join(HERE, "..", "step1_data", "wiki")
    md_files = sorted(glob.glob(os.path.join(wiki_dir, "*.md")))
    ids = {os.path.basename(f) for f in md_files}
    ids.discard("index.md")
    nodes = []
    edges = []
    seen_edge = set()
    for mf in md_files:
        fname = os.path.basename(mf)
        if fname == "index.md":
            continue
        content = open(mf, encoding="utf-8").read()
        body = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
        m = re.search(r'^#\s+(.+)$', body, re.MULTILINE)
        title = m.group(1).strip() if m else fname[:-3]
        nodes.append({"id": fname, "title": title})
        for lm in re.finditer(r'\[[^\]]*\]\(([\w-]+\.md)\)', body):
            target = lm.group(1)
            if target == fname or target not in ids:
                continue
            key = (fname, target)
            if key in seen_edge:
                continue
            seen_edge.add(key)
            edges.append({"source": fname, "target": target})
    return {"nodes": nodes, "edges": edges}


@app.get("/api/raw/chunks")
def raw_chunks():
    rag_dir = os.path.join(HERE, "..", "step1_data", "raw")
    chunks_path = os.path.join(rag_dir, "chunks.json")
    if not os.path.isfile(chunks_path):
        return JSONResponse({"error": "not found"}, status_code=404)
    data = json.load(open(chunks_path, encoding="utf-8"))
    chunks = data.get("chunks", [])
    # Return summary only (no full text for performance)
    return [{"index": i, "page": c.get("page", "?"), "chars": len(c.get("text", "")), "preview": c.get("text", "")[:120] + "..."} for i, c in enumerate(chunks)]


@app.get("/api/raw/chunk/{idx}")
def raw_chunk(idx: int):
    rag_dir = os.path.join(HERE, "..", "step1_data", "raw")
    chunks_path = os.path.join(rag_dir, "chunks.json")
    if not os.path.isfile(chunks_path):
        return JSONResponse({"error": "not found"}, status_code=404)
    data = json.load(open(chunks_path, encoding="utf-8"))
    chunks = data.get("chunks", [])
    if idx < 0 or idx >= len(chunks):
        return JSONResponse({"error": "invalid index"}, status_code=400)
    return chunks[idx]


@app.post("/api/raw/upload")
async def upload_raw_pdf(file: UploadFile = File(...)):
    """① PDF→Gemini→テキストファイル ② チャンク分割→埋め込み→Naive RAG用データ"""
    tid = _task_start("raw_upload", total=100)
    raw_data = await file.read()
    def _run():
        try:
            import fitz  # PyMuPDF
            import google.genai as _genai
            from google.genai import types
            from llm_utils import llm_json
            rag_dir = os.path.join(HERE, "..", "step1_data", "raw")
            pages_dir = os.path.join(rag_dir, "pages")
            chapters_dir = os.path.join(rag_dir, "chapters")
            os.makedirs(pages_dir, exist_ok=True)
            os.makedirs(chapters_dir, exist_ok=True)

            _task_update(tid, 2, "PDF保存中…")
            pdf_path = os.path.join(rag_dir, "uploaded_raw.pdf")
            with open(pdf_path, "wb") as f:
                f.write(raw_data)

            # env / client
            env_path = os.path.join(HERE, "..", ".env")
            if os.path.isfile(env_path):
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        for k in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_EMBED_MODEL"):
                            if line.startswith(k + "="):
                                os.environ.setdefault(k, line.split("=", 1)[1].strip())
            # timeout未設定だと応答待ちのまま無限にハングしうるため必ず設定する（ミリ秒単位）
            _client = _genai.Client(api_key=os.environ.get("GEMINI_API_KEY"),
                                    http_options=types.HttpOptions(timeout=120000))
            model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

            # 旧出力（ページ/章txt）をクリア
            for d in (pages_dir, chapters_dir):
                for old in glob.glob(os.path.join(d, "*.txt")):
                    os.remove(old)

            OCR_PROMPT = (
                "この画像はアップロードされた日本語PDF資料の1ページです。ページ内の本文・見出し・箇条書き・"
                "表・金額・電話番号・住所を、レイアウトを尊重しつつ忠実にテキスト化してください。"
                "表は行/列が分かる形で。創作・要約・翻訳はせず、書かれている内容のみを出力。"
                "前置き（「はい」等）や説明は不要、テキストのみを返してください。"
            )

            # ── ① ページごとに Gemini VLM でOCR → pages/page_NNN.txt ──
            doc = fitz.open(pdf_path)
            n_pages = doc.page_count
            pages = []
            for i in range(n_pages):
                pg = doc.load_page(i)
                png = pg.get_pixmap(dpi=200).tobytes("png")
                try:
                    text = _client.models.generate_content(
                        model=model,
                        contents=[types.Part.from_bytes(data=png, mime_type="image/png"), OCR_PROMPT],
                    ).text or ""
                except Exception as oe:
                    text = f"(OCR失敗 p.{i+1}: {oe})"
                text = text.strip()
                with open(os.path.join(pages_dir, f"page_{i+1:03d}.txt"), "w", encoding="utf-8") as f:
                    f.write(text)
                pages.append({"page": i + 1, "text": text})
                _task_update(tid, 5 + int((i + 1) / max(1, n_pages) * 55), f"① VLM-OCR中… {i+1}/{n_pages}ページ")
            doc.close()

            # ── ② LLMで章セグメント → 章ごとにマージ → chapters/NN-title.txt ──
            _task_update(tid, 62, "② 章の区切りを判定中…")
            heads = "\n".join(f"p{p['page']}: {p['text'][:150].replace(chr(10), ' ')}" for p in pages)
            try:
                seg = llm_json(
                    "あなたは文書構造化エージェント。アップロードされた日本語PDF資料の各ページ冒頭テキストから、"
                    "章（大きな節）の区切りを判定する。全ページを漏れなく連続した章に割り当てること"
                    "（start_page/end_pageは1から総ページまで連続・重複なし）。"
                    '出力JSON: {"chapters":[{"chapter_no":1,"title":"章タイトル","start_page":1,"end_page":10}]}',
                    f"総ページ数: {n_pages}\n各ページ冒頭:\n{heads[:60000]}"
                )
            except Exception:
                seg = {}
            chapters = seg.get("chapters") if isinstance(seg, dict) else (seg if isinstance(seg, list) else [])
            if not chapters:
                chapters = [{"chapter_no": 1, "title": "全体", "start_page": 1, "end_page": n_pages}]

            _task_update(tid, 68, "② 章ごとにマージ・保存中…")
            def _safe(s):
                s = re.sub(r'[\s/\\:*?"<>|]+', '-', str(s or "").strip())
                return re.sub(r'-+', '-', s).strip('-')[:30] or "chapter"
            page_text = {p["page"]: p["text"] for p in pages}
            chapter_meta, raw_parts = [], []
            for ci, ch in enumerate(chapters, 1):
                no = int(ch.get("chapter_no", ci) or ci)
                title = ch.get("title", f"第{no}章")
                sp = int(ch.get("start_page", 1) or 1)
                ep = int(ch.get("end_page", sp) or sp)
                body = "\n\n".join(page_text.get(pg, "") for pg in range(sp, ep + 1) if page_text.get(pg))
                fname = f"{no:02d}-{_safe(title)}.txt"
                with open(os.path.join(chapters_dir, fname), "w", encoding="utf-8") as f:
                    f.write(f"# {title}（p.{sp}-{ep}）\n\n{body}")
                chapter_meta.append({"chapter_no": no, "title": title, "start_page": sp, "end_page": ep, "file": fname})
                raw_parts.append(f"# {title}（p.{sp}-{ep}）\n\n{body}")

            with open(os.path.join(rag_dir, "raw_text.txt"), "w", encoding="utf-8") as f:
                f.write("\n\n".join(raw_parts))
            json.dump({"n_pages": n_pages, "chapters": chapter_meta},
                      open(os.path.join(rag_dir, "ocr_meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

            # ── ③ ナイーブRAG用: チャンク分割 → 埋め込み ──
            _task_update(tid, 72, "③ チャンク分割中…")
            CHUNK_SIZE, CHUNK_OVERLAP = 800, 150
            chunks = []
            for p in pages:
                t = p["text"]; i = 0
                while i < len(t):
                    c = t[i:i + CHUNK_SIZE].strip()
                    if c:
                        chunks.append({"page": p["page"], "text": c})
                    i += CHUNK_SIZE - CHUNK_OVERLAP
            _task_update(tid, 78, f"③ 埋め込み生成中…（{len(chunks)}チャンク）")
            embed_model = os.environ.get("GEMINI_EMBED_MODEL", "text-embedding-004")
            all_vecs = []
            for i in range(0, len(chunks), 50):
                bt = [c["text"] for c in chunks[i:i + 50]]
                resp = _client.models.embed_content(
                    model=embed_model, contents=bt,
                    config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"))
                all_vecs.extend(list(e.values) if hasattr(e, 'values') else e for e in resp.embeddings)
            json.dump({"total_chars": sum(len(p["text"]) for p in pages), "n_chunks": len(chunks), "chunks": chunks},
                      open(os.path.join(rag_dir, "chunks.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            json.dump({"chunks": chunks, "embeddings": all_vecs},
                      open(os.path.join(rag_dir, "raw_embeddings.json"), "w", encoding="utf-8"), ensure_ascii=False)

            # ── 後続（Wiki/QA）クリア ──
            _task_update(tid, 92, "後続データをクリア中…")
            wiki_dir = os.path.join(HERE, "..", "step1_data", "wiki")
            for f in glob.glob(os.path.join(wiki_dir, "*.md")):
                os.remove(f)
            global REVIEW_ITEMS, validation_results
            REVIEW_ITEMS[:] = [i for i in REVIEW_ITEMS if i.get("type_cq") != "cq"]
            validation_results.clear()
            save_state()

            _task_update(tid, 100, f"完了: {n_pages}ページOCR / {len(chapter_meta)}章 / {len(chunks)}チャンク（1.2 Wiki + 2.1 QA もクリア）")
            _task_done(tid)
        except Exception as ex:
            _task_done(tid, str(ex))
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid}


def _do_generate_cqs(tid):
    """LLM-WikiからQAを自動生成する（1.2画面／一括実行から共有）。"""
    global validation_results
    try:
            _task_update(tid, 5, "環境設定を読み込み中…")
            env_path = os.path.join(HERE, "..", ".env")
            if os.path.isfile(env_path):
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GEMINI_API_KEY="):
                            os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
            import google.genai as genai
            from google.genai import types
            # timeout未設定だと応答待ちのまま無限にハングしうるため必ず設定する（ミリ秒単位）
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"),
                                  http_options=types.HttpOptions(timeout=120000))
            model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

            _task_update(tid, 10, "Wikiページを読込中…")
            ctx = _read_wiki_context(500000)

            _task_update(tid, 25, "LLMがQAを生成中…（30秒程度）")
            MAX_QAS, MIN_MULTIHOP = 30, 15
            prompt = (
                "あなたはコンピテンシー質問（QA）生成エージェントです。"
                "以下のLLM-Wikiページ群から、ユーザーがこのシステムに質問すると想定されるQAをJSON配列で出力してください。"
                f"【件数の制約（最重要）】QAは合計{MAX_QAS}件以内にすること。そのうち{MIN_MULTIHOP}件以上は "
                "type=multi_hop（複数のWikiページを横断しないと答えられない多段探索の問い）にすること。"
                "残りは lookup/aggregation/constraint を適宜組み合わせる。"
                "各QAは以下を含む: id(QAxx), question(疑問形の自然言語の問い。必ず「？」で終わること), "
                "expected_answer(期待される簡潔な回答。金額を含む場合は具体的な数値まで), "
                "type(lookup|multi_hop|aggregation|constraint), "
                "source(主に該当するwikiページ名), "
                "trace(この問いに答えるためにLLM-Wikiを辿る経路を『参照した順』に並べた配列。各要素は "
                "{\"doc\":\"参照したページ名。上記『## 名前』の名前を厳密に使う\", \"ref\":\"そのページから参照する情報の要点\"}。"
                "multi_hop は必ず2要素以上にし、実際に情報を横断した順序で並べる。lookup は1要素でよい)"
            )
            result = json.loads(client.models.generate_content(model=model, contents=prompt + f"\n\n【LLM-Wiki】\n{ctx}", config=types.GenerateContentConfig(response_mime_type="application/json")).text)
            cqs = result if isinstance(result, list) else result.get("competency_questions", result.get("cqs", []))

            # 安全策: LLMが件数制約を超えた場合、multi_hopを優先的に残しつつ合計をMAX_QASに切り詰める
            if len(cqs) > MAX_QAS:
                multi = [c for c in cqs if c.get("type") == "multi_hop"]
                other = [c for c in cqs if c.get("type") != "multi_hop"]
                keep_multi = multi[:MAX_QAS]
                keep_other = other[:max(0, MAX_QAS - len(keep_multi))]
                cqs = keep_multi + keep_other

            _task_update(tid, 85, "結果を処理中…")
            added = 0
            for cq in cqs:
                cid = cq.get("id", f"QA{len(REVIEW_ITEMS)+1}")
                if any(i["id"] == cid for i in REVIEW_ITEMS):
                    continue
                REVIEW_ITEMS.append({
                    # [:60] で切ると質問が文の途中で途切れ、検証・表示の双方が壊れる（表示側でCSS省略する）
                    "id": cid, "title": cq.get("question", ""),
                    "description": cq.get("question", ""),
                    "expected_answer": cq.get("expected_answer", cq.get("answer_shape", "")),
                    "type": cq.get("type", "manual"),
                    "trace": cq.get("trace"),
                    "source": cq.get("source", "LLM自動生成"), "source_url": "",
                    "review": "human_required", "type_cq": "cq",
                    "status": "pending", "cq_ids": [],
                    "current_value": "未テスト"
                })
                added += 1
            validation_results.clear()
            save_state()
            n_multi = sum(1 for c in cqs if c.get("type") == "multi_hop")
            _task_update(tid, 100, f"完了: {added}件追加（全{len(cqs)}件中・多段探索{n_multi}件）")
            _task_done(tid)
    except Exception as ex:
        _task_done(tid, str(ex))

@app.post("/api/cq/generate")
def generate_cqs():
    """LLM-WikiからQAを自動生成する（非同期、進捗バー付き）。"""
    tid = _task_start("qa_generate", total=100)
    threading.Thread(target=_do_generate_cqs, args=(tid,), daemon=True).start()
    return {"ok": True, "task_id": tid}


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

@app.post("/api/pipeline/run-all")
def run_pipeline():
    """1.2→2.1→2.2(①Bootstrap→②③反復修正)→3.1→3.2 を、各画面と同じ実処理関数を順番に呼び出して一括実行する（非同期）。"""
    tid = _task_start("pipeline", total=100)
    def _run():
        try:
            weights = {"llmwiki": 15, "qa_gen": 10, "qa_approve": 5,
                       "ontology_bootstrap": 12, "ontology_refine": 18, "kg": 25, "validation": 15}
            progress = 0

            def _run_step(step_key, label, fn, *args):
                nonlocal progress
                _task_update(tid, progress, label)
                sub_tid = _task_start(f"pipeline_{step_key}", total=100)
                fn(sub_tid, *args)
                t = _tasks.get(sub_tid) or {}
                if t.get("status") == "error":
                    raise RuntimeError(f"{label} → {t.get('error')}")
                progress += weights[step_key]

            # Step 1/6: 1.2 LLM-Wiki生成
            _run_step("llmwiki", "Step 1/6: LLM-Wiki 生成中…", _do_generate_llmwiki)

            # Step 2/6: 2.1 QA生成
            _run_step("qa_gen", "Step 2/6: QA 生成中…", _do_generate_cqs)

            # Step 3/6: QA一括承認
            _task_update(tid, progress, "Step 3/6: QA 一括承認中…")
            approve_all_cqs()
            progress += weights["qa_approve"]

            # Step 4/6: 2.2 オントロジー定義（①Wikiから起こす → ②③承認済みQAで反復修正）
            _run_step("ontology_bootstrap", "Step 4/6: オントロジー定義を生成中…（①Wikiから起こす）", _do_ontology_bootstrap)
            _run_step("ontology_refine", "Step 4/6: オントロジー定義を修正中…（②③QAで反復）", _do_ontology_refine, 3)

            # Step 5/6: 3.1 KG抽出 + Neo4j投入
            _run_step("kg", "Step 5/6: KG 抽出 + Neo4j 投入中…", _do_extract_kg)

            # Step 6/6: 3.2 QA検証
            _task_update(tid, progress, "Step 6/6: QA 検証中…")
            validation_run_all()
            progress += weights["validation"]

            _task_update(tid, 100, "✅ 全行程完了！")
            _task_done(tid)
        except Exception as ex:
            _task_done(tid, str(ex))
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid}


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
            # timeout未設定だと応答待ちのまま無限にハングしうるため必ず設定する（ミリ秒単位）
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"),
                                  http_options=types.HttpOptions(timeout=120000))
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

            wiki_ctx = _read_wiki_context(500000)

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
                "【命名】各クラス・プロパティ・関係には name（英語の物理名＝識別子）と label（その日本語論理名＝人が読む名称、例: name=item_category, label=品目区分）を必ず両方付けてください。\n\n"
                "出力JSON: {\"classes\":[{\"name\",\"label\",\"description\",\"evidence\",\"properties\":[{\"name\",\"label\",\"type\",\"required\"}]}],\"relationships\":[{\"name\",\"label\",\"from\",\"to\",\"description\",\"evidence\"}],\"constraints\":[{\"target_class\":\"クラス名\",\"target_property\":\"プロパティ名|空\",\"target_entity\":\"ID|空\",\"description\":\"制約の説明\",\"value\":\"制約値\",\"unit\":\"単位|空\",\"source\":\"出典\"}]}\n\n"
                f"【QA（{cq_source_note}）】\n{cq_text[:8000]}{trace_block[:6000]}\n\n【LLM-Wiki】\n{wiki_ctx}"
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
                f"【オントロジー定義】\n{json.dumps(def_result, ensure_ascii=False)[:18000]}\n\n【承認済み全項目】\n{approved_text[:3000]}"
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


_ONTOLOGY_FIXABLE_TAGS = {"検索不足", "KG構造欠落"}   # オントロジー構造で対処できる原因

def _cause_tag(cause):
    """cause 文字列の先頭 [タグ] を取り出す（例 '[検索不足] …' → '検索不足'）。"""
    m = re.match(r'\s*\[([^\]]+)\]', str(cause or ""))
    return m.group(1).strip() if m else ""

_FIX_SYS = (
    "あなたはオントロジー修正エージェント。QA回帰テストで失敗したQAと、その『原因解析(cause)』を根拠に、"
    "現在のオントロジー定義を『追加・拡張』して、失敗QAに構造として答えられるようにする。\n"
    "【原因タグ別の対応】\n"
    "・[検索不足]=回答に必要なエンティティ/クラスがKGに無い → cause が名指しするクラスを新規追加する。\n"
    "・[KG構造欠落]=エンティティはあるが属性/関係が無い → 該当クラスに不足プロパティ/関係を追加する。\n"
    "（[Wiki未読込]/[Wiki網羅不足]/[回答生成] はオントロジー構造の問題ではないので対象外。）\n"
    "【最重要】既存のクラス・関係・プロパティ・制約・label は削除しない（追加・拡張のみ）。"
    "各クラス・プロパティ・関係には name(英語物理名) と label(日本語論理名) を必ず付ける（既存labelも保持）。"
    "追加要素の evidence には根拠Wikiスラッグを付ける。\n"
    "出力は完全なオントロジー定義JSON全体: "
    '{"classes":[{"name","label","description","evidence","properties":[{"name","label","type","required"}]}],'
    '"relationships":[{"name","label","from","to","description","evidence"}],'
    '"constraints":[{"target_class","target_property","target_entity","description","value","unit","source"}],'
    '"fix_notes":"各QAに何を追加したかの要約"}'
)

@app.post("/api/ontology/fix-from-validation")
def fix_ontology_from_validation():
    """QA回帰テストの失敗＋原因解析(cause)を根拠に、オントロジー定義を追加的に修正する。
    cause の型タグで仕分けし、オントロジーで対処できる原因([検索不足]/[KG構造欠落])のみ対象にする。"""
    global generated_ontology
    try:
        from llm_utils import llm_json, _load_env
        _load_env()
        definition = generated_ontology.get("definition")
        if not isinstance(definition, dict) or not definition.get("classes"):
            return {"ok": False, "error": "先にオントロジー定義を生成してください"}

        actionable, non_actionable = [], []
        cause_counts = {}
        wiki_slugs = set()
        for cq_id, result in validation_results.items():
            kg = result.get("kg") or {}; kw = result.get("kgwiki") or {}
            if kg.get("verdict") not in ("incorrect", "partial") and kw.get("verdict") not in ("incorrect", "partial"):
                continue
            cq = next((i for i in REVIEW_ITEMS if i.get("type_cq") == "cq" and i["id"] == cq_id), None)
            if not cq: continue
            causes = [c for c in (kg.get("cause"), kw.get("cause")) if c]
            tags = {t for t in (_cause_tag(c) for c in causes) if t}
            for t in tags: cause_counts[t] = cause_counts.get(t, 0) + 1
            entry = {"id": cq_id, "question": cq.get("title", ""),
                     "expected_answer": cq.get("expected_answer", ""),
                     "原因(KGのみ)": kg.get("cause", ""), "原因(KG+Wiki)": kw.get("cause", ""),
                     "kg_answer": kg.get("answer", ""), "tags": sorted(tags)}
            if tags & _ONTOLOGY_FIXABLE_TAGS:
                actionable.append(entry)
                for s in (result.get("sources") or []): wiki_slugs.add(s)
            else:
                non_actionable.append({"id": cq_id, "tags": sorted(tags)})

        if not actionable:
            return {"ok": False,
                    "error": "オントロジー修正で対処できる失敗がありません（原因が Wiki未読込/網羅不足/回答生成 のみ）。まず抽出/Wiki側の対応が必要です。",
                    "cause_counts": cause_counts, "non_actionable": len(non_actionable)}

        # 対象QAが参照したWikiページを根拠として渡す（不足属性・値の判断材料）
        wiki = _load_wiki_pages(sorted(wiki_slugs))
        wiki_block = "\n\n".join(f"## {k}\n{v}" for k, v in wiki.items())[:8000]

        user = (f"【現在のオントロジー定義】\n{json.dumps(definition, ensure_ascii=False)[:9000]}\n\n"
                f"【修正対象の失敗QA（原因解析つき）】\n{json.dumps(actionable, ensure_ascii=False, indent=1)[:6000]}\n\n"
                f"【関連Wiki本文】\n{wiki_block}")
        result = llm_json(_FIX_SYS, user)

        if not (isinstance(result, dict) and result.get("classes")):
            return {"ok": False, "error": "修正結果が不正でした（classesが空）"}
        # 制約が落ちたら直前の定義から引き継ぐ（消失防止）
        if not result.get("constraints"):
            result["constraints"] = definition.get("constraints", [])
        prev_classes = len(definition.get("classes", []))
        prev_rels = len(definition.get("relationships", []))
        generated_ontology["definition"] = result
        generated_ontology["status"] = "fixed"
        _write_constraints(result.get("constraints", []))
        save_state()
        return {
            "ok": True,
            "fixed_cqs": len(actionable),
            "non_actionable": len(non_actionable),
            "cause_counts": cause_counts,
            "classes": len(result.get("classes", [])), "classes_added": len(result.get("classes", [])) - prev_classes,
            "relationships": len(result.get("relationships", [])), "relationships_added": len(result.get("relationships", [])) - prev_rels,
            "fix_notes": result.get("fix_notes", ""),
        }
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)


# ── 制約事項の永続化 ──
CONSTRAINTS_NEO4J_PATH = os.path.join(HERE, "..", "step3_kg", "neo4j", "generated_constraints.cypher")
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


@app.get("/api/meta")
def get_meta():
    """このプロジェクトの表示名（.env の KB_TITLE/KB_LABEL）。チャット画面のタイトル・見出しなど、
    ドメイン名をHTMLに直書きせず動的に反映するために使う（別プロジェクトへforkする際は.envの変更だけで済む）。"""
    _load_env_file()
    return JSONResponse({
        "kb_title": os.environ.get("KB_TITLE", KB_TITLE),
        "kb_label": os.environ.get("KB_LABEL", KB_LABEL),
    })


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

def _read_wiki_context(total_budget=45000):
    """全Wikiページを '## slug\\n本文' でまとめたテキストに（frontmatter除去）。
    全ページの合計文字数がtotal_budget以下なら、一切truncateせず全ページ全文を渡す
    （Wiki全体は現状数百KB程度でLLMのコンテキストに十分収まるため）。
    予算を超える場合のみ、ページ数に応じて1ページあたりの文字数を動的に配分し、
    アルファベット順の先頭ページだけに予算を使い切って後続の章が丸ごと欠落する問題を避ける。"""
    md_files = sorted(glob.glob(os.path.join(HERE, "..", "step1_data", "wiki", "*.md")))
    md_files = [f for f in md_files if os.path.basename(f) != "index.md"]
    if not md_files:
        return ""
    pages = []
    for mf in md_files:
        with open(mf, encoding="utf-8") as f:
            content = f.read()
        name = os.path.basename(mf).replace(".md", "")
        content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
        pages.append((name, content))
    if sum(len(c) for _, c in pages) <= total_budget:
        return "\n\n".join(f"## {name}\n{content}" for name, content in pages)
    per_page = max(150, total_budget // len(pages))
    return "\n\n".join(f"## {name}\n{content[:per_page]}" for name, content in pages)

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

def _do_ontology_bootstrap(tid):
    """① LLM-Wiki本体だけからオントロジー定義を起こす（QA非依存。2.2画面／一括実行から共有）。"""
    global generated_ontology
    try:
            from llm_utils import llm_json
            _task_update(tid, 15, "LLM-Wikiを読込中…")
            wiki_ctx = _read_wiki_context(500000)
            _task_update(tid, 35, "Wikiからオントロジー定義を生成中…（QAは未使用）")
            sys_p = (
                "あなたはオントロジー設計エージェント。与えられたLLM-Wiki（ドメイン知識ベース）だけから、"
                "このドメインのオントロジー定義を生成する。QA（質問）はまだ使わない。"
                "Wikiに現れる主要な概念・エンティティ・属性・関連から、クラス・プロパティ・関係・制約を抽出する。"
                "【命名】各クラス・プロパティ・関係には name（英語の物理名＝PascalCase等の識別子）と "
                "label（その日本語論理名＝人が読む名称、例: name=item_category, label=品目区分）を必ず両方付ける。"
                "各要素の evidence には根拠にしたWikiページのスラッグ（例: 02-notebooks, key-contacts）を記す。"
            )
            user_p = (
                '出力JSON: {"classes":[{"name","label","description","evidence","properties":[{"name","label","type","required"}]}],'
                '"relationships":[{"name","label","from","to","description","evidence"}],'
                '"constraints":[{"target_class","target_property","target_entity","description","value","unit","source"}]}\n\n【LLM-Wiki】\n' + wiki_ctx
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

@app.post("/api/ontology/bootstrap")
def ontology_bootstrap():
    """① LLM-Wiki本体だけからオントロジー定義を起こす（QA非依存）。"""
    tid = _task_start("ontology_bootstrap", total=100)
    threading.Thread(target=_do_ontology_bootstrap, args=(tid,), daemon=True).start()
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

def _do_ontology_refine(tid, rounds=3):
    """②③ 承認済みQAで定義の充足を監査し、不足を追加する反復（2.2画面／一括実行から共有）。"""
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
                wiki_ctx = _read_wiki_context(500000)
                patched = llm_json(_PATCH_SYS,
                    f"【現在のオントロジー定義】\n{json.dumps(definition, ensure_ascii=False)[:9000]}\n\n"
                    f"【不足リスト（QAに答えるため追加すべき）】\n{json.dumps(agg, ensure_ascii=False)[:4000]}\n\n"
                    f"【参考: LLM-Wiki】\n{wiki_ctx}")
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

@app.post("/api/ontology/refine-from-cqs")
def ontology_refine_from_cqs(body: RefineBody):
    """②③ 承認済みQAで定義の充足を監査し、不足を追加する反復（スキーマレベル）。"""
    rounds = max(1, min(6, int(body.rounds or 3)))
    tid = _task_start("ontology_refine", total=100)
    threading.Thread(target=_do_ontology_refine, args=(tid, rounds), daemon=True).start()
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
    """Wikiスラッグ → サーバ相対パス。フロントが根拠リンクを生成するために使う。"""
    wiki_dir = os.path.join(HERE, "..", "step1_data", "wiki")
    idx = {}
    for mf in sorted(glob.glob(os.path.join(wiki_dir, "*.md"))):
        slug = os.path.basename(mf).replace(".md", "")
        idx[slug] = f"pipeline/step1_data/wiki/{slug}.md"
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
        f"// {KB_TITLE} ナレッジグラフ（オントロジー定義に沿ってLLM-Wikiから抽出）",
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
    uri = os.environ.get("NEO4J_URI") or "bolt://neo4j:7687"
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


# ── KG抽出のID安定化（ドメイン非依存）──
# 抽出はページをバッチ（20件ずつ）に分けてLLMを繰り返し呼ぶため、同じ実体が複数の
# バッチにまたがって言及されると、バッチごとに独立したLLM呼び出しが互いを知らないまま
# 別々のID（例: svc_futon_drying_disinfection と svc_futon_drying）を付けてしまい、
# 同一実体が重複ノードとして分裂することがあった。ここでは「クラス＋正規化した名前」から
# 決定的にIDを算出し直すことで、どのバッチ・どの実行でも同じ実体には同じIDが付くようにする。
# ラベル名（クラス名）と名前の正規化だけを使い、ドメインの語彙には一切依存しない。
def _normalize_entity_key(name):
    s = unicodedata.normalize("NFKC", str(name or "")).strip()
    return re.sub(r'\s+', '', s)

def _stable_node_id(labels, name):
    label = (labels or ["Entity"])[0] if labels else "Entity"
    label_slug = re.sub(r'[^A-Za-z0-9]', '', str(label)) or "Entity"
    key = _normalize_entity_key(name)
    if not key:
        return None
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    return f"{label_slug.lower()}_{h}"

def _merge_node_props(old_props, new_props):
    """複数バッチ/複数回の抽出で同じ実体に行き当たった際のプロパティ統合。
    source はスラッグの和集合（重複除去・出現順維持）、他のキーは値が空でない方・より情報量の
    多い方を残す簡易ヒューリスティック。"""
    merged = dict(old_props or {})
    for k, v in (new_props or {}).items():
        if v in (None, ""):
            continue
        if k == "source":
            old_srcs = [s.strip() for s in str(merged.get("source", "")).split(",") if s.strip()]
            new_srcs = [s.strip() for s in str(v).split(",") if s.strip()]
            merged["source"] = ", ".join(dict.fromkeys(old_srcs + new_srcs))
        elif not merged.get(k) or len(str(v)) > len(str(merged[k])):
            merged[k] = v
    return merged

def _extract_kg_batch(client, model, sys_instr, class_block, rel_block, batch_pages, extra_note=""):
    """1バッチ(ページ集合)をLLMで抽出し、(nodes_dict, edges_list, error) を返す。
    nodes_dict は 決定的ID -> {id,labels,props} のマージ済み辞書、edges_list は [{from,to,type,props}]。
    _generate_json_robust を使うため、以前のように応答が壊れたJSONだった際に無言で0件になることがない
    （壊れていた場合は error にメッセージが入り、呼び出し側でログ・可視化できる）。"""
    batch_ctx = "\n\n".join(f"## {name}\n{content}" for name, content in batch_pages)
    prompt = "ナレッジグラフ構築:\n" + class_block[:3000] + "\n" + rel_block[:2000] + extra_note + "\n" + batch_ctx
    nodes_by_id, edges_all, error = {}, [], None
    try:
        kg_part = _generate_json_robust(client, model, sys_instr + "\n\n" + prompt)
        if not isinstance(kg_part, dict):
            kg_part = {}
    except Exception as ex:
        kg_part = {}
        error = f"{type(ex).__name__}: {ex}"
    # LLMが割り当てたIDはバッチ間で揺れる（同じ実体でも別IDになりうる）ため、
    # クラス＋正規化した名前から決定的なIDへ振り直してから統合する。
    id_remap = {}
    for n in (kg_part.get("nodes") or []):
        old_id = n.get("id")
        props = n.get("props") or {}
        new_id = _stable_node_id(n.get("labels"), props.get("name")) or old_id
        if not new_id:
            continue
        if old_id:
            id_remap[old_id] = new_id
        if new_id in nodes_by_id:
            nodes_by_id[new_id]["props"] = _merge_node_props(nodes_by_id[new_id].get("props"), props)
        else:
            nodes_by_id[new_id] = {"id": new_id, "labels": n.get("labels"), "props": props}
    for e in (kg_part.get("edges") or []):
        ef = id_remap.get(e.get("from"), e.get("from"))
        et_ = id_remap.get(e.get("to"), e.get("to"))
        if not (ef and et_ and e.get("type")):
            continue
        edges_all.append({"from": ef, "to": et_, "type": e.get("type"), "props": e.get("props") or {}})
    return nodes_by_id, edges_all, error

def _compute_missing_wiki(kg, md_files):
    """このKGのどのノードの source にも登場しない Wiki ページのスラッグ一覧
    （＝抽出漏れの可能性がある未カバーページ）。"""
    covered = set()
    for n in (kg or {}).get("nodes", []):
        for s in _slugs_from((n.get("props") or {}).get("source")):
            covered.add(s)
    all_slugs = [os.path.basename(f)[:-3] for f in md_files]
    return [s for s in all_slugs if s not in covered]

def _push_kg_to_neo4j(kg):
    """kg(nodes/edges)をNeo4jへまっさらに投入する（全削除してから入れ直す）。
    戻り値: (connected: bool, message: str)"""
    try:
        from neo4j import GraphDatabase
        ndriver = GraphDatabase.driver(os.environ.get("NEO4J_URI") or "bolt://neo4j:7687",
                                        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "password123")))
        n_edges_pushed = 0
        with ndriver.session(database="neo4j") as session:
            session.run("MATCH (n) DETACH DELETE n")
            for n in kg["nodes"]:
                labels = ":".join(list(n["labels"]) + ["Entity"])
                session.run(f"MERGE (n:{labels} {{id: $id}}) SET n += $props",
                            id=n["id"], props={k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in n.get("props", {}).items()})
            for e in kg["edges"]:
                et = re.sub(r'[^A-Za-z0-9_]', '', str(e.get("type") or "REL")) or "REL"
                r = session.run(f"MATCH (a {{id: $fid}}), (b {{id: $tid}}) MERGE (a)-[:`{et}`]->(b) RETURN count(*) AS c",
                                fid=e["from"], tid=e["to"]).single()
                n_edges_pushed += (r["c"] if r else 0)
        ndriver.close()
        return True, f"Neo4jに投入完了（{len(kg['nodes'])}ノード / {n_edges_pushed}エッジ）"
    except Exception as neo_ex:
        return False, f"Neo4j投入スキップ（{neo_ex}）"

def _do_extract_kg(tid):
    """3.1 オントロジー定義に沿って、LLM-Wikiから実体を抽出する（3.1画面／一括実行から共有）。"""
    global generated_ontology, validation_results
    validation_results.clear()
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
                        # setdefault: docker-compose の environment: が渡す NEO4J_URI（コンテナ内ネットワーク用の
                        # bolt://neo4j:7687）を、.envのローカル開発用の値（bolt://localhost:7687等）で
                        # 上書きしないようにする。これが原因でNeo4j投入が毎回失敗していた。
                        for key in ("GEMINI_API_KEY", "GEMINI_MODEL", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
                            if line.startswith(key + "="):
                                os.environ.setdefault(key, line.split("=", 1)[1].strip().strip('"').strip("'"))

            # Clear Neo4j
            _task_update(tid, 10, "既存データをクリア中…")
            try:
                from neo4j import GraphDatabase
                ndriver = GraphDatabase.driver(os.environ.get("NEO4J_URI") or "bolt://neo4j:7687",
                                                auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "password123")))
                with ndriver.session(database="neo4j") as session:
                    session.run("MATCH (n) DETACH DELETE n")
                ndriver.close()
            except: pass

            _task_update(tid, 15, "Wikiページを読込中…")
            wiki_dir = os.path.join(HERE, "..", "step1_data", "wiki")
            md_files = sorted(glob.glob(os.path.join(wiki_dir, "*.md")))
            md_files = [f for f in md_files if os.path.basename(f) != "index.md"]
            pages = []
            for mf in md_files:
                with open(mf, encoding="utf-8") as f:
                    content = f.read()
                name = os.path.basename(mf).replace(".md", "")
                content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
                pages.append((name, content))

            import google.genai as genai
            from google.genai import types
            # timeout未設定だと応答待ちのまま無限にハングしうるため必ず設定する（ミリ秒単位）
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"),
                                  http_options=types.HttpOptions(timeout=120000))
            model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
            classes = definition.get("classes", [])
            rels = definition.get("relationships", [])
            class_block = "\n".join(f"- {c.get('name')}（{c.get('label','')}）: / props: " + ", ".join(p.get("name","") for p in (c.get("properties") or [])) for c in classes)
            rel_block = "\n".join(f"- ({r.get('from')}) -[{r.get('name')}（{r.get('label','')}）]-> ({r.get('to')})" for r in rels)
            sys_instr = ("あなたはナレッジグラフ構築エージェントです。以下のオントロジー定義に従いLLM-Wikiから実体を抽出しnodes/edgesを生成してください。"
                "出力は{\"nodes\":[{\"id\",\"labels\",\"props\"}],\"edges\":[{\"from\",\"to\",\"type\"}]}のJSONのみ。idはsvc_/contact_/nb_等の命名規則。"
                "nodeのlabelsは上記クラスのname（英語物理名）を用いること。nodeのpropsには必ず name（実体の日本語名）, source, "
                "type_label（このノードの型クラスの日本語論理名＝上記クラスの（）内の名称）を含めること。"
                "【最重要】source には、その実体の根拠となった LLM-Wiki ページの『スラッグ』を入れること"
                "（本文中の見出し『## 名前』のその名前を厳密に使う。例: 02-notebooks, disability-notebooks）。複数ある場合はカンマ区切り。"
                "『LLM-Wiki』のような総称や説明文は絶対に入れない。可能な限り金額・期間・必要書類・条件などの具体値も props に含めること。"
                "edgeのtypeは上記関係のname（英語物理名）を用いること。")

            # 全ページを一括で1回のLLM呼び出しに渡すと出力がLLMのトークン上限に当たり
            # 少数のノードしか返らないため、ページをバッチに分けて抽出→ノードIDで統合する。
            _task_update(tid, 25, f"LLMがKGを生成中…（{len(pages)}ページをバッチ処理）")
            batch_size = 20
            n_batches = max(1, (len(pages) - 1) // batch_size + 1)
            nodes_by_id, edges_all, seen_edge = {}, [], set()
            batch_errors = []
            for bi in range(0, len(pages), batch_size):
                batch = pages[bi:bi + batch_size]
                prog = 25 + int(bi / max(1, len(pages)) * 60)
                _task_update(tid, prog, f"バッチ{bi // batch_size + 1}/{n_batches} 実体抽出中…")
                bnodes, bedges, err = _extract_kg_batch(client, model, sys_instr, class_block, rel_block, batch)
                if err:
                    # 以前はここで例外が無言で握りつぶされ、そのバッチの全ページ(最大20件)が
                    # 気づかれないまま0件のまま残っていた。ログに残し、後段の未カバー検出でも拾えるようにする。
                    batch_errors.append({"batch": bi // batch_size + 1, "pages": [p[0] for p in batch], "error": err})
                for nid, n in bnodes.items():
                    if nid in nodes_by_id:
                        nodes_by_id[nid]["props"] = _merge_node_props(nodes_by_id[nid].get("props"), n.get("props"))
                    else:
                        nodes_by_id[nid] = n
                for e in bedges:
                    key = (e["from"], e["to"], e["type"])
                    if key in seen_edge:
                        continue
                    seen_edge.add(key)
                    edges_all.append(e)
            kg = {"nodes": list(nodes_by_id.values()), "edges": edges_all}

            _task_update(tid, 75, "Cypher出力＋保存中…")
            ids = {n.get("id") for n in kg["nodes"]}
            kg["edges"] = [e for e in kg["edges"] if e.get("from") in ids and e.get("to") in ids]
            from kg_utils import build_cypher
            cypher = build_cypher(kg)
            with open(os.path.join(HERE, "neo4j_import.cypher"), "w", encoding="utf-8") as f: f.write(cypher)
            with open(os.path.join(HERE, "kg_extracted.json"), "w", encoding="utf-8") as f: json.dump(kg, f, ensure_ascii=False, indent=2)

            _task_update(tid, 90, "Neo4j投入中…")
            neo_connected, neo_msg = _push_kg_to_neo4j(kg)

            uncovered = _compute_missing_wiki(kg, md_files)
            generated_ontology["kg_extracted"] = kg
            generated_ontology["kg_meta"] = {
                "nodes": len(kg["nodes"]), "edges": len(kg["edges"]),
                "neo4j": {"connected": neo_connected, "message": neo_msg},
                "batch_errors": batch_errors,
                "uncovered_pages": uncovered,
            }
            msg = "完了" if not batch_errors else f"完了（{len(batch_errors)}バッチで抽出失敗。未カバー{len(uncovered)}ページ）"
            _task_update(tid, 100, msg)
            _task_done(tid)
            save_state()
    except Exception as ex:
        _task_done(tid, str(ex))

@app.post("/api/kg/extract")
def extract_kg():
    """2.2 オントロジー定義に沿って、LLM-Wikiから実体を抽出する（非同期）。"""
    tid = _task_start("kg_extract", total=100)
    threading.Thread(target=_do_extract_kg, args=(tid,), daemon=True).start()
    return {"ok": True, "task_id": tid, "message": "抽出を開始しました。進捗は /api/task/{task_id} で確認できます。"}


def _do_extract_missing(tid):
    """①の安全網: 前回の抽出で1件もノードが得られなかったWikiページだけを対象に、
    より小さいバッチ・明示的な指示で再抽出し、既存KGにマージする
    （決定的ID＋プロパティマージにより、既存ノードとの重複は自動的に統合される）。"""
    global generated_ontology
    try:
        kg = generated_ontology.get("kg_extracted") or {"nodes": [], "edges": []}
        definition = generated_ontology.get("definition")
        if not definition or not isinstance(definition, dict) or not definition.get("classes"):
            _task_done(tid, "先にオントロジー定義を生成してください")
            return

        _task_update(tid, 5, "未カバーページを検出中…")
        wiki_dir = os.path.join(HERE, "..", "step1_data", "wiki")
        md_files = sorted(glob.glob(os.path.join(wiki_dir, "*.md")))
        md_files = [f for f in md_files if os.path.basename(f) != "index.md"]
        missing_slugs = set(_compute_missing_wiki(kg, md_files))
        if not missing_slugs:
            generated_ontology.setdefault("kg_meta", {})["uncovered_pages"] = []
            _task_update(tid, 100, "未カバーページはありません")
            _task_done(tid)
            save_state()
            return

        pages = []
        for mf in md_files:
            name = os.path.basename(mf)[:-3]
            if name not in missing_slugs:
                continue
            with open(mf, encoding="utf-8") as f:
                content = f.read()
            content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
            pages.append((name, content))

        _load_env_file()
        import google.genai as genai
        from google.genai import types
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"),
                              http_options=types.HttpOptions(timeout=120000))
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        classes = definition.get("classes", [])
        rels = definition.get("relationships", [])
        class_block = "\n".join(f"- {c.get('name')}（{c.get('label','')}）: / props: " + ", ".join(p.get("name", "") for p in (c.get("properties") or [])) for c in classes)
        rel_block = "\n".join(f"- ({r.get('from')}) -[{r.get('name')}（{r.get('label','')}）]-> ({r.get('to')})" for r in rels)
        sys_instr = ("あなたはナレッジグラフ構築エージェントです。以下のオントロジー定義に従いLLM-Wikiから実体を抽出しnodes/edgesを生成してください。"
            "出力は{\"nodes\":[{\"id\",\"labels\",\"props\"}],\"edges\":[{\"from\",\"to\",\"type\"}]}のJSONのみ。idはsvc_/contact_/nb_等の命名規則。"
            "nodeのlabelsは上記クラスのname（英語物理名）を用いること。nodeのpropsには必ず name（実体の日本語名）, source, "
            "type_label（このノードの型クラスの日本語論理名＝上記クラスの（）内の名称）を含めること。"
            "【最重要】source には、その実体の根拠となった LLM-Wiki ページの『スラッグ』を入れること"
            "（本文中の見出し『## 名前』のその名前を厳密に使う）。複数ある場合はカンマ区切り。"
            "『LLM-Wiki』のような総称や説明文は絶対に入れない。可能な限り金額・期間・必要書類・条件などの具体値も props に含めること。"
            "edgeのtypeは上記関係のname（英語物理名）を用いること。")
        # 通常抽出(20ページ/バッチ)より大幅に小さくし、1ページあたりの見落としリスクを下げる。
        # さらに「必ず最低1件」と明示することで、地味・短いページが素通りされにくくする。
        extra_note = ("\n【今回は前回の抽出で見落とされたページの再抽出です。以下の各ページから必ず最低1件は"
                      "エンティティを抽出してください。適切なクラスが無ければ、ページの主題そのもの"
                      "（制度名・窓口名・センター名など）を最低限のエンティティとしてください。】\n")

        nodes_by_id = {n["id"]: n for n in kg.get("nodes", [])}
        edges_all = list(kg.get("edges", []))
        seen_edge = {(e.get("from"), e.get("to"), e.get("type")) for e in edges_all}

        batch_size = 4
        n_batches = max(1, (len(pages) - 1) // batch_size + 1)
        batch_errors = []
        for bi in range(0, len(pages), batch_size):
            batch = pages[bi:bi + batch_size]
            prog = 10 + int(bi / max(1, len(pages)) * 70)
            _task_update(tid, prog, f"未カバー再抽出 バッチ{bi // batch_size + 1}/{n_batches}…")
            bnodes, bedges, err = _extract_kg_batch(client, model, sys_instr, class_block, rel_block, batch, extra_note)
            if err:
                batch_errors.append({"batch": bi // batch_size + 1, "pages": [p[0] for p in batch], "error": err})
            for nid, n in bnodes.items():
                if nid in nodes_by_id:
                    nodes_by_id[nid]["props"] = _merge_node_props(nodes_by_id[nid].get("props"), n.get("props"))
                else:
                    nodes_by_id[nid] = n
            for e in bedges:
                key = (e["from"], e["to"], e["type"])
                if key in seen_edge:
                    continue
                seen_edge.add(key)
                edges_all.append(e)

        kg2 = {"nodes": list(nodes_by_id.values()), "edges": edges_all}
        ids = {n.get("id") for n in kg2["nodes"]}
        kg2["edges"] = [e for e in kg2["edges"] if e.get("from") in ids and e.get("to") in ids]

        _task_update(tid, 85, "Cypher出力＋保存中…")
        from kg_utils import build_cypher
        cypher = build_cypher(kg2)
        with open(os.path.join(HERE, "neo4j_import.cypher"), "w", encoding="utf-8") as f: f.write(cypher)
        with open(os.path.join(HERE, "kg_extracted.json"), "w", encoding="utf-8") as f: json.dump(kg2, f, ensure_ascii=False, indent=2)

        _task_update(tid, 92, "Neo4j投入中…")
        neo_connected, neo_msg = _push_kg_to_neo4j(kg2)

        remaining_missing = _compute_missing_wiki(kg2, md_files)
        generated_ontology["kg_extracted"] = kg2
        generated_ontology["kg_meta"] = {
            "nodes": len(kg2["nodes"]), "edges": len(kg2["edges"]),
            "neo4j": {"connected": neo_connected, "message": neo_msg},
            "batch_errors": batch_errors,
            "uncovered_pages": remaining_missing,
            "last_action": f"未カバー{len(pages)}件を再抽出（残り未カバー{len(remaining_missing)}件）",
        }
        _task_update(tid, 100, f"完了（{len(pages)}件を再抽出、残り未カバー{len(remaining_missing)}件）")
        _task_done(tid)
        save_state()
    except Exception as ex:
        _task_done(tid, str(ex))

@app.post("/api/kg/extract-missing")
def extract_missing_kg():
    """①の安全網: 前回の抽出で1件もノードが得られなかったWikiページだけを、
    小さいバッチ・明示的な指示で再抽出し、既存KGにマージする。"""
    tid = _task_start("kg_extract_missing", total=100)
    threading.Thread(target=_do_extract_missing, args=(tid,), daemon=True).start()
    return {"ok": True, "task_id": tid, "message": "未カバーページの再抽出を開始しました。"}


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
    """pipeline/.env の GEMINI/NEO4J 設定を os.environ に反映する（既に設定済みのキーは上書きしない）。
    Docker環境ではdocker-compose.ymlのenvironment:がNEO4J_URI等をコンテナ内ネットワーク用の
    正しい値（bolt://neo4j:7687等）で明示的に上書きしている。ここで無条件に.envの値（ローカル開発用の
    bolt://localhost:7687等）で再上書きすると、コンテナ内からNeo4jに接続できなくなる不具合があった。
    setdefault方式にすることで、Docker実行時はdocker-compose側の値を優先しつつ、
    .env単体で動かすローカル実行時はこれまで通り.envの値が使われる。"""
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
                    os.environ.setdefault(key, line.split("=", 1)[1].strip().strip('"').strip("'"))


@app.get("/api/kg/neo4j")
def get_kg_from_neo4j():
    """投入後の Neo4j 実グラフ（複数回抽出をMERGEで累積したもの）をクエリして可視化用に返す。
    /api/kg/extracted が『最新1回分の抽出JSON』を返すのに対し、こちらは『DBの実データ』を返す。"""
    _load_env_file()
    uri = os.environ.get("NEO4J_URI") or "bolt://neo4j:7687"
    user = os.environ.get("NEO4J_USER", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD", "")
    if not pw:
        return JSONResponse({"error": "NEO4J_PASSWORD未設定です。pipeline/.env に設定し、Neo4jを起動してください。",
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
# チャットの Agentic Search (pipeline/agent/agent.py) の Planner→Retrieval→Critic→Answer を、
# 手作りkg.json非依存に一般化し、抽出KG(Neo4j)上で回す。回答は (A)KGのみ / (B)KG+Wiki の2通り。

_GENAI_CACHE = {}

def _genai():
    """(.env をロードした) genai クライアントと model 名を返す（クライアントはキャッシュ）。"""
    _load_env_file()
    if "client" not in _GENAI_CACHE:
        import google.genai as genai
        from google.genai import types
        _GENAI_CACHE["genai"] = genai
        # timeout未設定だと応答待ちのまま無限にハングしうるため必ず設定する（ミリ秒単位）。
        # 実際に3-1のKG抽出バッチ処理がこれで無応答のまま停止した事例があった。
        _GENAI_CACHE["client"] = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"),
                                              http_options=types.HttpOptions(timeout=120000))
    _GENAI_CACHE["model"] = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    return _GENAI_CACHE["genai"], _GENAI_CACHE["client"], _GENAI_CACHE["model"]

def _extract_json_text(text):
    """コードフェンス(```json ... ```)を剥がす。それ以外は素通し。"""
    text = (text or "").strip()
    m = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
    return m.group(1).strip() if m else text

def _parse_json_loose(text):
    """response_mime_type=application/json を指定してもなお、LLMが文中の改行やMarkdown引用を
    そのままJSON文字列に埋め込んで壊れたJSONを返すことがある（例: 'Expecting , delimiter'）。
    ここで段階的に緩く解釈する。"""
    text = _extract_json_text(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        # strict=False: 文字列内の生の制御文字(未エスケープの改行等)を許容する
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        return json.loads(m.group(0), strict=False)
    raise json.JSONDecodeError("no JSON object found", text, 0)

def _generate_json_robust(client, model, contents):
    """generate_content(response_mime_type=json) を呼び、緩いJSONパースで解釈する。
    それでも壊れていれば、1回だけ『JSON修復』をLLMに依頼して再パースする。
    Judge/Planner/KG抽出バッチなど、JSONを要求するあらゆる呼び出しで共用する
    （以前はKG抽出だけこの堅牢化が無く、失敗したバッチが無言で0件になっていた）。"""
    from google.genai import types
    r = client.models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(response_mime_type="application/json"))
    try:
        return _parse_json_loose(r.text)
    except json.JSONDecodeError:
        # 1回だけ「壊れたJSONの修復」をLLMに依頼して再パース（意味は変えず整形のみ求める）
        fix = client.models.generate_content(
            model=model,
            contents=("次のテキストは壊れたJSONです。中身の意味・値は変えずに、"
                       "有効なJSON（1つのオブジェクトまたは配列）だけを出力してください。"
                       "文字列内の改行は \\n にエスケープしてください。\n\n" + (r.text or "")),
            config=types.GenerateContentConfig(response_mime_type="application/json"))
        return _parse_json_loose(fix.text)

def _llm_json(sys_prompt, user_prompt):
    genai, client, model = _genai()
    return _generate_json_robust(client, model, f"{sys_prompt}\n\n{user_prompt}")

def _llm_text(sys_prompt, user_prompt):
    genai, client, model = _genai()
    r = client.models.generate_content(model=model, contents=f"{sys_prompt}\n\n{user_prompt}")
    return (r.text or "").strip()

def _get_kg_graph():
    """3.1の抽出KGを {nodes, edges, source} で返す。Neo4j優先・失敗時は kg_extracted。"""
    _load_env_file()
    uri = os.environ.get("NEO4J_URI") or "bolt://neo4j:7687"
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
    # クラスあたりの候補ノード名を先頭N件に絞ると、KGが充実した際に長尾のエンティティが
    # 検索候補から漏れ続けるため、上限を十分に大きく取る（名前は短くコストへの影響は小さい）。
    label_lines = "\n".join(f"- {l}: {', '.join(sorted(set(ns))[:500])}" for l, ns in labels.items())
    user = f"質問: {question}\n\n【グラフのクラスとノード名】\n{label_lines[:60000]}\n\n【関係型】\n{', '.join(rel_types)}"
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

# ── 検索の絞り込み設定（文字数で切るのではなく「関連度で選ぶ」）──
SEED_LIMIT = 40      # シードにする上位ノード数
NODE_CAP = 30        # 回答プロンプトに載せるノードの上限件数
WIKI_PAGE_LIMIT = 8  # 回答プロンプトに載せるWikiページの上限件数
# 文字数予算は「安全網」。実際の絞り込みは上の件数(NODE_CAP / WIKI_PAGE_LIMIT)で行う。
# 投入内容が関連度上位のみになったため、以前(8000/9000)より広く取っても無関係な文脈は増えない。
FACTS_BUDGET = 12000  # ノード+エッジJSONの文字数予算（要素単位で詰め、途中で切らない）
WIKI_BUDGET = 20000   # Wiki本文の文字数予算（ページ単位で詰め、途中で切らない）

def _norm(s):
    return str(s or "").strip().lower()

# 「汎用すぎて検索の役に立たない語（1語で大半のノードがヒットしてしまう）」を、
# 固定の語彙リストではなく、そのKG自身の分布から統計的に判定する。
# 例えば障害福祉KGでは「障害者」が大半のノードに出現し検索の役に立たないが、これは
# コードに書かれたドメイン知識ではなく、実行時にKGのノード群を数えて導かれる値なので、
# 別ドメインのKG（例: 図書館・観光・社内規程）でもコード変更なしに同じ仕組みが機能する。
STOPWORD_DF_THRESHOLD = 0.12  # ノードの12%以上にヒットする語は識別力が無いとみなす

def _is_too_common(term, nodes, threshold=STOPWORD_DF_THRESHOLD):
    if not nodes or not term:
        return False
    hit = 0
    for n in nodes:
        hay = _norm(_node_name(n)) + " " + _norm(" ".join(str(v) for v in (n.get("props") or {}).values()))
        if term in hay:
            hit += 1
            if hit / len(nodes) > threshold:
                return True
    return False

def _query_terms(entities, keywords, nodes=None):
    """検索語を正規化。短すぎる語と、KG内での出現頻度が高すぎる語（=識別力が無い語）を落とす。"""
    terms = []
    for t in list(entities) + list(keywords):
        t = _norm(t)
        if not t:
            continue
        if len(t) < 2:          # 1文字は「区」「都」等でノイズしか生まない
            continue
        if nodes is not None and _is_too_common(t, nodes):
            continue
        terms.append(t)
    return list(dict.fromkeys(terms))   # 重複除去（順序維持）

def _score_node(n, terms):
    """質問語とノードの関連度。名前一致を最重視し、属性値の一致は補助的に加点する。
    以前の『名前が検索語の部分文字列(nm in t)』『全プロパティへの部分一致』は
    ほぼ全ノードにヒットしてしまうため廃止/限定した。"""
    nm = _norm(_node_name(n))
    props = n.get("props") or {}
    body = _norm(" ".join(str(v) for k, v in props.items() if k not in ("source", "id")))
    score = 0.0
    for t in terms:
        if t == nm:
            score += 6.0
        elif t in nm:
            # 語がノード名に占める割合が高いほど確度が高い（短語の巻き込みを抑制）
            score += 3.0 * min(1.0, len(t) / max(1, len(nm)))
        elif len(nm) >= 3 and nm in t:
            # 検索語が長いフレーズで、ノード名がその中に含まれる場合
            # （例: 語「愛の手帳の判定」⊃ ノード名「愛の手帳」）。
            # 旧実装はこれを無条件に許して「区」「都」等の短名で全ヒットしていたため、
            # ノード名3文字以上に限定する。
            score += 2.5
        if len(t) >= 3 and t in body:   # 属性一致は3文字以上の語に限定
            score += 1.0
    return score

def _seed_nodes(nodes, entities, keywords, question=None, limit=SEED_LIMIT):
    """関連度スコア上位のノードだけをシードにする（部分一致の総なめを避ける）。
    戻り値: (seedのidセット, {id: score})"""
    terms = _query_terms(entities, keywords, nodes=nodes)
    scored = []
    for n in nodes:
        s = _score_node(n, terms)
        if s > 0:
            scored.append((s, n["id"]))
    if not scored and terms:
        # 保険1: プランナーが長いフレーズしか返さなかった等で1件も当たらない場合、
        # 語を2文字以上のトークンに分解して緩く再照合する（0件回答を避ける）。
        subs = {w for t in terms for w in re.split(r'[\s、。・（）()「」]+', t) if len(w) >= 2}
        subs = [w for w in subs if not _is_too_common(w, nodes)]
        for n in nodes:
            s = _score_node(n, subs)
            if s > 0:
                scored.append((s, n["id"]))
    if not scored and question:
        # 保険2（最終手段）: Plannerが entities/keywords を1つも返さなかった場合
        # （質問が複雑・長文だと起きる）、語彙一致に一切頼らず、質問文とノード名の
        # 埋め込み類似度で直接シードを選ぶ。Wikiページ選定と同じ仕組み。
        scored = _seed_nodes_by_embedding(question, nodes, limit)
    scored.sort(key=lambda x: (-x[0], x[1]))
    seeds = {nid for _, nid in scored[:limit]}
    return seeds, {nid: s for s, nid in scored}

def _select_nodes(fnodes, terms, seeds, score_by_id, cap=NODE_CAP):
    """近傍展開後のノードを関連度で並べ、上位capだけ残す。
    シード（質問に直接マッチしたノード）を最優先し、次に展開先をスコア順。"""
    ranked = []
    for n in fnodes:
        s = score_by_id.get(n["id"], 0.0) or _score_node(n, terms)
        if n["id"] in seeds:
            s += 10.0          # 質問に直接マッチしたものを必ず上位に
        ranked.append((s, n))
    ranked.sort(key=lambda x: (-x[0], str(x[1].get("id"))))
    return [n for _, n in ranked[:cap]]

def _pack_facts(fnodes, fedges, budget=FACTS_BUDGET):
    """ノード/エッジを要素単位で文字数予算に詰める。JSONを途中で切らない（壊れたJSONを渡さない）。
    以前は json.dumps(...)[:8000] と文字列を直接切っていたため、回答LLMには
    途中で切れた不正なJSONが渡っていた。"""
    kept, total = [], 24            # {"nodes": [], "edges": []} の枠ぶん
    node_budget = budget * 0.8      # エッジぶんを残す
    for n in fnodes:
        s = json.dumps(n, ensure_ascii=False)
        if kept and total + len(s) + 1 > node_budget:
            break
        kept.append(n); total += len(s) + 1
    kept_ids = {n["id"] for n in kept}
    kedges = []
    for e in fedges:
        if e["from"] not in kept_ids or e["to"] not in kept_ids:
            continue
        s = json.dumps(e, ensure_ascii=False)
        if total + len(s) + 1 > budget:
            break
        kedges.append(e); total += len(s) + 1

    # ── 構造的な曖昧さ検出（ドメイン非依存）──
    # 投入ノードの中に「同じクラス」に属するものが複数あれば、それは回答LLMが混同しうる
    # 候補群として明示する。プロパティの意味（値が何を表すか）は一切解釈せず、
    # グラフのラベル一致だけで機械的に判定するため、対象ドメインを問わず動く。
    groups = {}
    for n in kept:
        lbl = tuple(n.get("labels") or [])
        if lbl:
            groups.setdefault(lbl, []).append(n["id"])
    same_class_groups = [{"labels": list(lbl), "ids": ids} for lbl, ids in groups.items() if len(ids) >= 2]

    payload = {"nodes": kept, "edges": kedges}
    if same_class_groups:
        payload["same_class_groups"] = same_class_groups
    return json.dumps(payload, ensure_ascii=False), kept, kedges

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

def _wiki_dir():
    return os.path.join(HERE, "..", "step1_data", "wiki")

def _read_wiki(slug):
    fp = os.path.join(_wiki_dir(), f"{slug}.md")
    if not os.path.isfile(fp):
        return None
    with open(fp, encoding="utf-8") as f:
        c = f.read()
    return re.sub(r'^---\n.*?\n---\n', '', c, flags=re.DOTALL)

def _load_wiki_pages(slugs):
    """（互換用）指定スラッグの本文を読む。存在するものだけを返す。"""
    out = {}
    for slug in slugs:
        c = _read_wiki(slug)
        if c is not None:
            out[slug] = c[:2500]
    return out

# ── Wikiページの埋め込みキャッシュ（意味的類似度によるランキング用）──
# 語の部分一致だけでは言い換え・同義語・英語スラッグとの表層不一致に弱く、
# 正解ページが上位N件の枠から漏れることがあった。埋め込みは内容の意味を見るため、
# 表現の違いに強い。ページ単位でハッシュ管理し、変更されたページだけ再計算する
# （永続化: pipeline/step1_data/wiki_embeddings.json）。ドメイン知識は一切使わない。
WIKI_EMB_PATH = os.path.join(HERE, "..", "step1_data", "wiki_embeddings.json")
_wiki_emb_cache = None

def _content_hash(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def _wiki_emb_load():
    global _wiki_emb_cache
    if _wiki_emb_cache is None:
        if os.path.isfile(WIKI_EMB_PATH):
            try:
                with open(WIKI_EMB_PATH, encoding="utf-8") as f:
                    _wiki_emb_cache = json.load(f)
            except Exception:
                _wiki_emb_cache = {"model": "", "pages": {}}
        else:
            _wiki_emb_cache = {"model": "", "pages": {}}
    return _wiki_emb_cache

def _wiki_emb_save():
    if _wiki_emb_cache is not None:
        with open(WIKI_EMB_PATH, "w", encoding="utf-8") as f:
            json.dump(_wiki_emb_cache, f, ensure_ascii=False)

def _embed_documents(texts):
    genai, client, _ = _genai()
    from google.genai import types
    embed_model = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    out = []
    for i in range(0, len(texts), 50):
        batch = texts[i:i + 50]
        r = client.models.embed_content(
            model=embed_model, contents=batch,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"))
        out.extend(list(e.values) for e in r.embeddings)
    return out

def _embed_query(text):
    genai, client, _ = _genai()
    from google.genai import types
    embed_model = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    r = client.models.embed_content(
        model=embed_model, contents=[text],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"))
    return list(r.embeddings[0].values)

def _cosine(a, b):
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    na = sum(x * x for x in a[:n]) ** 0.5
    nb = sum(x * x for x in b[:n]) ** 0.5
    return dot / (na * nb) if na and nb else 0.0

def _ensure_wiki_embeddings(slug_text_pairs):
    """候補スラッグの埋め込みをキャッシュに用意する（無ければ生成、本文が変わっていれば再生成）。
    質問のたびに関係する候補だけを対象にするので軽く、繰り返し使ううちに自然とキャッシュが育つ。"""
    cache = _wiki_emb_load()
    embed_model = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    if cache.get("model") != embed_model:
        cache["pages"] = {}
        cache["model"] = embed_model
    pages = cache["pages"]
    todo_slugs, todo_texts = [], []
    for slug, text in slug_text_pairs:
        h = _content_hash(text)
        entry = pages.get(slug)
        if entry and entry.get("hash") == h:
            continue
        todo_slugs.append(slug); todo_texts.append(text)
    if todo_texts:
        try:
            vecs = _embed_documents(todo_texts)
            for slug, text, vec in zip(todo_slugs, todo_texts, vecs):
                pages[slug] = {"hash": _content_hash(text), "vec": vec}
            _wiki_emb_save()
        except Exception:
            pass  # 埋め込み失敗時は語彙スコアのみにフォールバック（下の呼び出し側で自然に処理される）
    return pages

# ── KGノードの埋め込みキャッシュ（Plannerが検索語を1つも返せなかった場合の最終手段）──
# 質問が複雑・長文だと、Planner LLMが entities/keywords を両方とも空で返すことがある。
# その場合、語彙ベースの一致は何をやっても0件のままなので（検索語が無いため）、
# Wikiページ選定と同じ仕組み（質問文とノード名の埋め込み類似度）でノードを直接探す。
KG_NODE_EMB_PATH = os.path.join(HERE, "..", "step1_data", "kg_node_embeddings.json")
_kg_node_emb_cache = None

def _kg_node_emb_load():
    global _kg_node_emb_cache
    if _kg_node_emb_cache is None:
        if os.path.isfile(KG_NODE_EMB_PATH):
            try:
                with open(KG_NODE_EMB_PATH, encoding="utf-8") as f:
                    _kg_node_emb_cache = json.load(f)
            except Exception:
                _kg_node_emb_cache = {"model": "", "nodes": {}}
        else:
            _kg_node_emb_cache = {"model": "", "nodes": {}}
    return _kg_node_emb_cache

def _kg_node_emb_save():
    if _kg_node_emb_cache is not None:
        with open(KG_NODE_EMB_PATH, "w", encoding="utf-8") as f:
            json.dump(_kg_node_emb_cache, f, ensure_ascii=False)

def _ensure_kg_node_embeddings(id_name_pairs):
    """ノードの埋め込みをキャッシュに用意する（無ければ生成、名前が変わっていれば再生成）。
    KG全体を毎回埋め込むと重いが、内容ハッシュで差分だけ計算するため、初回以降は軽い。"""
    cache = _kg_node_emb_load()
    embed_model = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    if cache.get("model") != embed_model:
        cache["nodes"] = {}
        cache["model"] = embed_model
    store = cache["nodes"]
    todo_ids, todo_texts = [], []
    for nid, name in id_name_pairs:
        h = _content_hash(name)
        entry = store.get(nid)
        if entry and entry.get("hash") == h:
            continue
        todo_ids.append(nid); todo_texts.append(name)
    if todo_texts:
        try:
            vecs = _embed_documents(todo_texts)
            for nid, text, vec in zip(todo_ids, todo_texts, vecs):
                store[nid] = {"hash": _content_hash(text), "vec": vec}
            _kg_node_emb_save()
        except Exception:
            pass  # 埋め込み失敗時は何も返せない（呼び出し側で空扱いになる）
    return store

def _seed_nodes_by_embedding(question, nodes, limit):
    """語彙一致が全滅した場合の最終手段。質問文とノード名の意味的類似度だけでシードを選ぶ。
    戻り値: [(score, id), ...]（_seed_nodes の scored と同じ形）"""
    pairs = [(n["id"], _node_name(n)) for n in nodes]
    store = _ensure_kg_node_embeddings(pairs)
    try:
        qvec = _embed_query(question)
    except Exception:
        return []
    scored = []
    for nid, _name in pairs:
        entry = store.get(nid)
        if not entry:
            continue
        sim = _cosine(qvec, entry["vec"])
        if sim > 0.3:   # 無関係なノードまで拾わない最低限のしきい値
            scored.append((sim * 6.0, nid))   # 語彙一致スコア(最大6.0)と同程度のスケールに揃える
    return scored

def _select_wiki(slugs, terms, slug_score, question, limit=WIKI_PAGE_LIMIT, budget=WIKI_BUDGET):
    """関連度上位のWikiページだけを本文としてプロンプトに載せる。
    以前は sorted()＝アルファベット順に全ページを連結してから [:9000] で切っていたため、
    『名前が若いページ』だけが残り、正解ページが落ちていた。ここでは
    (1) 質問文とページ本文の埋め込み類似度（意味ベース・主信号）
    (2) KG上の関連ノードのスコア／スラッグ・見出しの語一致（補助信号）
    で並べ替え、上位limit件をページ単位で予算に詰める（ページを途中で切らない）。
    戻り値: (delivered_dict, loaded_count)"""
    cands = []
    for slug in slugs:
        c = _read_wiki(slug)
        if c is None:
            continue
        cands.append((slug, c))
    loaded = len(cands)
    if not cands:
        return {}, 0

    EMB_CHARS = 4000
    pages_emb = _ensure_wiki_embeddings([(slug, c[:EMB_CHARS]) for slug, c in cands])
    try:
        qvec = _embed_query(question)
    except Exception:
        qvec = None

    scored = []
    for slug, c in cands:
        s = 0.0
        entry = pages_emb.get(slug)
        if qvec is not None and entry:
            s += 10.0 * _cosine(qvec, entry["vec"])   # 主信号: 意味的類似度
        s += 0.5 * float(slug_score.get(slug, 0.0))   # 補助信号: KG関連ノードからの継承スコア
        sl = _norm(slug)
        head = _norm(c[:600])          # 見出し付近の一致は本文全体より信頼できる
        for t in terms:
            if t in sl:
                s += 0.3
            if t in head:
                s += 0.2
        scored.append((s, slug, c))
    # スコア降順（同点はスラッグ名で安定化）。※アルファベット順の偏りをここで解消
    scored.sort(key=lambda x: (-x[0], x[1]))

    delivered, total = {}, 0
    for s, slug, c in scored[:limit]:
        text = c[:2500]
        chunk = len(text) + len(slug) + 6
        if delivered and total + chunk > budget:
            break
        delivered[slug] = text
        total += chunk
    return delivered, loaded

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

# ドメイン名（文京区障害者福祉 等）をプロンプト文言やHTMLに直書きしない。将来別ユースケースでも
# .env の書き換えだけでコード変更なしに使い回せるよう、ペルソナ/画面タイトルは環境変数で外出しする
# （未設定時は汎用ラベル）。KB_TITLE はチャット画面のタイトル・見出しに、KB_LABEL はLLMプロンプトの
# ペルソナ文言に使う。
KB_LABEL = os.environ.get("KB_LABEL", "この知識ベース")
KB_TITLE = os.environ.get("KB_TITLE", "ナレッジベース Agentic Search")

# ── 同一クラス複数候補の混同を防ぐ指示（ドメイン非依存）──
# 「エンティティ」「クラス」「プロパティ」というオントロジー一般の語彙のみで書かれており、
# ドメイン固有の名詞（等級・障害種別 等）を含まない。_pack_facts が根拠JSONに
# same_class_groups を機械的に付与し、この指示はそのフィールドの読み方だけを説明する。
_DISAMBIGUATION_RULE = (
    "\n\n【重要】根拠のJSONに same_class_groups というフィールドがある場合、それは同じクラスに属する"
    "複数の候補エンティティ群です。質問文が指定する条件（プロパティの値）に一致するエンティティのみを"
    "採用し、条件が一致しない他の候補の情報を回答に混在させないでください。『念のため』として"
    "複数候補を併記せず、最も条件に一致する1件だけを答えてください。条件に一致するものを"
    "特定できない場合は、その旨を明示してください。"
)

_ANSWER_SYS_KG = (
    f"あなたは、{KB_LABEL}の案内アシスタントです。与えられた【ナレッジグラフの根拠】だけを使って"
    "日本語で簡潔に答えてください。根拠に無い情報を創作しないでください。根拠から答えられない場合は"
    "『ナレッジグラフからは分かりません』とだけ述べてください。" + _DISAMBIGUATION_RULE)
_ANSWER_SYS_KGWIKI = (
    f"あなたは、{KB_LABEL}の案内アシスタントです。【ナレッジグラフの根拠】と【本文】を使って"
    "日本語で簡潔に答えてください。両方に無い情報は創作しないでください。答えられない場合は"
    "『分かりません』と述べてください。" + _DISAMBIGUATION_RULE)

# ── 4手法目: LLM-Wiki検索 + Agentic Search（KGを一切使わず、Wikiページ間の
# [表示名](slug.md) リンクだけをたどって根拠を集める）──
# LLM-Wikiはページ間の相互リンクを持つため、リンク構造だけでAgentic Searchが成立するか
# （＝KGを介さなくても「たどる」検索ができるか）を、ナイーブRAG/KG手法と並べて比較する。
_WIKI_LINK_RE = re.compile(r'\[([^\]]+)\]\(([A-Za-z0-9_-]+)\.md\)')

def _extract_wiki_links(content):
    """本文中の [表示名](slug.md) 形式のリンクを (表示名, slug) のリストで返す。"""
    return [(m.group(1), m.group(2)) for m in _WIKI_LINK_RE.finditer(content or "")]

_WIKI_AGENTIC_PLANNER_SYS = (
    "あなたはWikiページのリンクをたどって情報を探すエージェントです。質問と、現在読んでいるWikiページ本文、"
    "および現在のページからたどれるリンク候補（表示名の一覧）が与えられます。"
    "質問に答えるための情報が現在のページ群だけで十分なら done:true とし、follow は空配列にしてください。"
    "不十分で、リンク候補の中に関連しそうなものがあれば、その表示名を関連度が高い順に最大3件 follow に入れてください"
    "（無関係な候補しか無ければ follow は空配列にし、done:true としてください）。"
    '出力JSON: {"done": true/false, "follow": ["リンク表示名", ...], "reason": "簡潔な理由"}'
)

_WIKI_AGENTIC_ANSWER_SYS = (
    f"あなたは、{KB_LABEL}の案内アシスタントです。以下のLLM-Wikiページ本文だけを使って、"
    "日本語で簡潔に答えてください。\n"
    "【重要】ページ本文中の記述は、多少の言い換え・要約や、複数箇所・複数ページの記述を"
    "組み合わせて答えを構成することも含め、根拠として積極的に使ってください。"
    "文中に答えが（表現を変えてでも）書かれているのに『分かりません』と答えるのは避けてください。"
    "本文に一切登場しない固有名詞・金額・数値・条件だけを創作しないでください。"
    "どのページにも関連する記載が本当に無い場合のみ『分かりません』と述べてください。"
)

def _wiki_agentic_search(question, max_hops=3, max_pages=8, seed_top_k=2):
    """LLM-Wikiのmdリンク構造だけをたどるAgentic Search（KG不使用）。
    ① 埋め込みで起点ページを選ぶ → ② 現在のページ群からたどれるリンク候補をLLMに提示し、
    関連しそうなものを選んでもらう → ③ 十分になるかmax_hopsに達するまで②を繰り返す →
    ④ 集めたページ本文だけから回答する。
    戻り値: {"answer":..., "trace":[...], "pages_visited":[...]}"""
    trace = []
    wiki_dir = os.path.join(HERE, "..", "step1_data", "wiki")
    all_slugs = sorted(os.path.basename(f)[:-3] for f in glob.glob(os.path.join(wiki_dir, "*.md")) if os.path.basename(f) != "index.md")

    # ① 埋め込みで起点ページを選ぶ（Wikiページ選定と同じ仕組みを流用）
    all_pairs = [(s, (_read_wiki(s) or "")[:4000]) for s in all_slugs]
    store = _ensure_wiki_embeddings(all_pairs)
    try:
        qvec = _embed_query(question)
        scored = sorted(((_cosine(qvec, store[s]["vec"]), s) for s, _ in all_pairs if s in store), key=lambda x: -x[0])
    except Exception as ex:
        scored = []
        trace.append({"node": "WikiSeed(埋め込み)", "txt": f"起点検索に失敗: {ex}"})
    if not scored:
        return {"answer": "分かりません（起点ページの検索に失敗しました）", "trace": trace or [{"node": "WikiSeed", "txt": "候補なし"}], "pages_visited": []}
    # 起点は上位1件だけでなく複数件（既定2件）にする。単一の起点選定ミスで
    # 正解ページへのリンクが全く辿れなくなる（[リンク未到達]）ケースを減らすため。
    seed_slugs = [s for _, s in scored[:seed_top_k]]
    trace.append({"node": "WikiSeed(埋め込み)", "txt": "起点ページ: " + ", ".join(f"{s}（類似度{sc:.2f}）" for sc, s in scored[:seed_top_k])})

    visited = {}
    def _load(slug):
        c = _read_wiki(slug)
        if c is not None:
            visited[slug] = c[:3000]
    for s in seed_slugs:
        _load(s)

    for hop in range(max_hops):
        if len(visited) >= max_pages:
            trace.append({"node": "Critic(LLM)", "txt": f"上限{max_pages}ページに到達 → 終了"})
            break
        # 現在読んでいるページ群からリンク候補を集める（訪問済み・自己参照は除く）
        candidates = {}
        for slug, content in visited.items():
            for text, target in _extract_wiki_links(content):
                if target not in visited and target != slug and target in all_slugs:
                    candidates.setdefault(text, target)
        if not candidates:
            trace.append({"node": "Critic(LLM)", "txt": "たどれるリンクなし → 終了"})
            break
        ctx = "\n\n".join(f"## {s}\n{c}" for s, c in visited.items())[:9000]
        cand_list = "\n".join(f"- {t}" for t in list(candidates.keys())[:40])
        try:
            plan = _llm_json(_WIKI_AGENTIC_PLANNER_SYS,
                              f"質問: {question}\n\n【現在読んでいるページ】\n{ctx}\n\n【たどれるリンク候補】\n{cand_list}")
        except Exception as ex:
            plan = {"done": True, "follow": [], "reason": f"判定失敗: {ex}"}
        if plan.get("done") or not plan.get("follow"):
            trace.append({"node": "Critic(LLM)", "txt": ("根拠十分 ✓" if plan.get("done") else "関連リンクなし → 終了") + (f"（{plan.get('reason')}）" if plan.get("reason") else "")})
            break
        followed = []
        for t in (plan.get("follow") or [])[:3]:
            target = candidates.get(t)
            if not target:
                for k, v in candidates.items():   # 表示名の完全一致が無ければ部分一致でフォールバック
                    if t in k or k in t:
                        target = v; break
            if target and target not in visited:
                _load(target)
                followed.append(f"{t}→{target}")
        trace.append({"node": "Rewrite→Retrieval", "txt": f"リンクをたどる: {', '.join(followed) if followed else '(該当リンクなし)'}"})
        if not followed:
            break

    trace.append({"node": "Retrieval", "txt": f"{len(visited)}ページを読了: {', '.join(visited.keys())}"})
    ctx = "\n\n".join(f"## {s}\n{c}" for s, c in visited.items())[:12000]
    try:
        answer = _llm_text(_WIKI_AGENTIC_ANSWER_SYS, f"質問: {question}\n\n【LLM-Wikiページ本文】\n{ctx}")
    except Exception as ex:
        answer = f"(生成失敗: {ex})"
    trace.append({"node": "Answer(LLM)", "txt": f"{len(visited)}ページの本文から生成"})
    return {"answer": answer, "trace": trace, "pages_visited": list(visited.keys())}

_JUDGE_SYS = (
    "あなたは回答採点者かつ原因解析者。質問と『正解』を基準に、4つの回答"
    "（N=ナイーブRAG / W=LLM-Wiki検索(Agentic) / A=KGのみ / B=KG+Wiki）を採点する。"
    "各 verdict は correct / partial / incorrect のいずれか: "
    "correct=正解の要点を過不足なく含む, partial=一部一致だが不足や軽微な誤り, incorrect=誤りor未回答。理由(reason)は簡潔に。"
    "さらに W・A・B が correct でない場合は、提供される【原因解析の材料】（Wiki探索経路・取得サブグラフ・読込Wiki・source）を"
    "根拠に、根本原因を cause に記す。cause は必ず次の型ラベルを先頭に付ける（1つ選ぶ）＋一言:\n"
    "[検索不足]=正解に必要なエンティティがプロンプトに投入されていない（投入ノードに該当が無い。A・B専用）\n"
    "[KG構造欠落]=エンティティは投入されたが、答えに必要な属性/関係がKG側に無い（A・B専用）\n"
    "[Wiki未読込]=参照すべきWikiページがプロンプトに投入されていない（sourceが無い/本文が無い/0ページ。A・B専用）\n"
    "[Wiki網羅不足]=関連するWikiページは投入・到達できたが、その本文に正解の該当記載が無い（W・B共通）\n"
    "[起点誤り]=Wiki検索(W)の起点ページ自体が質問と無関係だった（W専用）\n"
    "[リンク未到達]=起点ページは合っていたが、正解が載っているページまでリンクをたどり着けなかった（W専用）\n"
    "[回答生成]=必要な根拠が投入・到達できているのに回答が正解と食い違う（LLMの生成側の問題）。"
    "投入・到達できていない根拠を理由にこのラベルを選んではいけない。"
    "『同一クラスの複数候補』の中から条件に合わない候補を混ぜてしまっている場合もこのラベルとする\n"
    "correct の場合や N の cause は空文字でよい。"
    '出力JSON: {"naive":{"verdict":"...","reason":"..."},'
    '"wikiagent":{"verdict":"...","reason":"...","cause":"..."},'
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

    terms = _query_terms(entities, keywords, nodes=kg["nodes"])
    seeds, score_by_id = _seed_nodes(kg["nodes"], entities, keywords, question=question)
    ids = _expand(seeds, adj, hops)
    fnodes, fedges = _subgraph_facts(ids, by_id, kg["edges"])
    retrieved_nodes = len(fnodes)
    trace.append({"node": "Retrieval", "txt": f'シード{len(seeds)} → 近傍展開{len(fnodes)}ノード / {len(fedges)}エッジ'})

    if len(fnodes) < 2 and seeds:
        ids = _expand(seeds, adj, hops + 1)
        fnodes, fedges = _subgraph_facts(ids, by_id, kg["edges"])
        retrieved_nodes = len(fnodes)
        trace.append({"node": "Critic(LLM)", "txt": f'根拠不足 → hop拡大で{len(fnodes)}ノード'})
    else:
        trace.append({"node": "Critic(LLM)", "txt": "根拠十分 ✓"})

    # 関連度で上位のみに絞る（文字数での打ち切りに任せない）
    fnodes = _select_nodes(fnodes, terms, seeds, score_by_id)
    kept_ids = {n["id"] for n in fnodes}
    fedges = [e for e in fedges if e["from"] in kept_ids and e["to"] in kept_ids]
    facts_json, fnodes, fedges = _pack_facts(fnodes, fedges)
    trace.append({"node": "Rank", "txt": f'関連度上位{len(fnodes)}ノード / {len(fedges)}エッジをプロンプトに投入（取得{retrieved_nodes}件から選抜）'})

    # Wikiの候補は「プロンプトに載せたノードのsource」。ノードのスコアをスラッグへ引き継ぐ
    slug_score, sources = {}, set()
    for n in fnodes:
        ns = score_by_id.get(n["id"], 0.0)
        for s in _slugs_from((n.get("props") or {}).get("source")):
            sources.add(s)
            slug_score[s] = max(slug_score.get(s, 0.0), ns)
    wiki_slugs = sorted(sources | set(cq_docs or []))

    # 回答N: ナイーブRAG（KG非依存・PDFチャンクのベクトル検索→LLM）
    naive = _naive_rag(question)
    trace.append({"node": "NaiveRAG", "txt": f'PDFベクトル検索→回答（出典{len(naive.get("sources") or [])}チャンク）'})

    # 回答W: LLM-Wiki検索 + Agentic Search（KG不使用。Wikiページ間のmdリンクだけをたどる）
    wa = _wiki_agentic_search(question)
    trace.append({"node": "WikiAgentic", "txt": f'{len(wa.get("pages_visited") or [])}ページを探索: {", ".join(wa.get("pages_visited") or [])}'})

    # 回答A: KGのみ
    try:
        ans_kg = _llm_text(_ANSWER_SYS_KG, f"質問: {question}\n\n【ナレッジグラフの根拠】\n{facts_json}")
    except Exception as ex:
        ans_kg = f"(生成失敗: {ex})"

    # 回答B: KG + Wiki補完（関連度上位ページのみ。アルファベット順の打ち切りをしない）
    wiki, wiki_loaded_n = _select_wiki(wiki_slugs, terms, slug_score, question)
    wiki_delivered = list(wiki.keys())          # 実際にプロンプトへ載ったページ
    wiki_ctx = "\n\n".join(f"## {k}\n{v}" for k, v in wiki.items())
    try:
        ans_kgwiki = _llm_text(_ANSWER_SYS_KGWIKI,
                               f"質問: {question}\n\n【ナレッジグラフの根拠】\n{facts_json}\n\n【LLM-Wiki本文】\n{wiki_ctx}")
    except Exception as ex:
        ans_kgwiki = f"(生成失敗: {ex})"
    trace.append({"node": "Answer(LLM)", "txt": f'ナイーブRAG / KGのみ / KG+Wiki(投入{len(wiki)}ページ / 読込可{wiki_loaded_n}件) を生成'})

    node_ids = [n["id"] for n in fnodes]
    return {
        "question": question,
        "naive": {"answer": naive.get("answer", ""), "rag_sources": naive.get("sources") or []},
        "wikiagent": {"answer": wa.get("answer", ""), "pages_visited": wa.get("pages_visited") or [], "trace": wa.get("trace") or []},
        "kg": {"answer": ans_kg},
        "kgwiki": {"answer": ans_kgwiki},
        "subgraph": {"node_ids": node_ids, "edges": fedges},
        "entities": [_node_name(by_id[i]) for i in node_ids if i in by_id][:30],
        "sources": wiki_slugs, "trace": trace,
        # ── 原因解析用の材料（LLMなしで算出。Judgeにも渡す）──
        # kg_brief / wiki_delivered は「実際にプロンプトへ載った内容」。Judgeの誤判定を防ぐため
        # 読込数ではなく投入数を材料にする。
        "kg_brief": _kg_brief(fnodes, fedges),
        "wiki_loaded": wiki_delivered,
        "wiki_delivered": wiki_delivered,
        "diag": {"retrieved_nodes": retrieved_nodes, "wiki_requested": len(wiki_slugs),
                 "wiki_loaded": wiki_loaded_n,
                 "nodes_delivered": len(fnodes), "wiki_delivered": len(wiki_delivered),
                 "wikiagent_pages": len(wa.get("pages_visited") or []),
                 "kg_dontknow": ("分かりません" in ans_kg)},
    }

def _kg_brief(fnodes, fedges):
    """Judge/原因解析用に、実際にプロンプトへ投入したサブグラフをコンパクトなテキスト化。
    以前は fnodes[:40] と固定スライスしていたため、Judgeは投入内容の一部しか見ずに
    [検索不足] と誤判定していた。ここでは投入ノード全件（_select_nodes で上限済）を、
    属性名だけでなく短い値付きで渡す（[KG構造欠落]との切り分けに必要）。"""
    lines = []
    for n in fnodes:
        p = n.get("props") or {}
        nm = p.get("name") or n.get("id")
        typ = (n.get("labels") or ["?"])[-1]
        kv = [f"{k}={str(v)[:40]}" for k, v in p.items() if k not in ("name", "type_label")]
        lines.append(f"- {nm}[{typ}]: {', '.join(kv) if kv else '(属性なし)'}")
    elines = [f"- {e.get('from')} -{e.get('type')}-> {e.get('to')}" for e in fedges]

    # 同一クラスの複数候補（_pack_facts の same_class_groups と同じ判定基準）。
    # 回答LLMが混同していないか、Judgeが原因を切り分けるための材料。
    groups = {}
    for n in fnodes:
        lbl = tuple(n.get("labels") or [])
        if lbl:
            p = n.get("props") or {}
            groups.setdefault(lbl, []).append(p.get("name") or n.get("id"))
    glines = [f"- {list(lbl)}: {', '.join(names)}" for lbl, names in groups.items() if len(names) >= 2]

    return ("【プロンプトに投入したノード】\n" + ("\n".join(lines) or "(なし)")
            + "\n【プロンプトに投入したエッジ】\n" + ("\n".join(elines) or "(なし)")
            + "\n【同一クラスの複数候補（回答が取り違えていないか要確認）】\n" + ("\n".join(glines) or "(なし)"))

def _run_validation(cq):
    """3.2検証: _answer_query の3回答を正解(expected_answer)と照合して verdict を付与。
    KG+Wiki は「KGで辿ったEntityの source のみ」を参照する（QAのtrace元ページは混ぜない＝KG主導の到達性を厳密に測る）。"""
    expected = cq.get("expected_answer") or ""
    # title は生成時に question[:60] で切られており、24/30件が文の途中で途切れている。
    # 採点は「完全な質問」に対する expected_answer で行うため、質問文には全文(description)を使う。
    question = (cq.get("description") or "").strip() or (cq.get("title") or "")
    r = _answer_query(question)
    diag = r.get("diag") or {}
    # Judge に採点＋原因解析を相乗り（LLM呼び出しは増やさない）。取得サブグラフと読込Wikiを材料として渡す。
    _d = r.get("diag") or {}
    wa = r.get("wikiagent") or {}
    wa_trace = wa.get("trace") or []
    wa_path = " → ".join(t.get("txt", "") for t in wa_trace if t.get("node") in ("WikiSeed(埋め込み)", "Rewrite→Retrieval"))
    diag_ctx = (f"\n\n【原因解析の材料】※以下は回答LLMが実際に受け取った内容そのもの\n"
                f"プロンプトに投入したサブグラフ:\n{r.get('kg_brief','')[:3500]}\n"
                f"プロンプトに投入したWikiページ({_d.get('wiki_delivered')}件): {r.get('wiki_delivered') or '（なし）'}\n"
                f"（参考）KG検索でヒットした総ノード数: {_d.get('retrieved_nodes')} / "
                f"source候補スラッグ: {_d.get('wiki_requested')}件 / うち本文が存在: {_d.get('wiki_loaded')}件\n"
                f"Wiki検索(W)が探索したページ({len(wa.get('pages_visited') or [])}件): {wa.get('pages_visited') or '（なし）'}\n"
                f"Wiki検索(W)の探索経路: {wa_path or '（記録なし）'}\n"
                f"注意: 上記『投入した』『探索した』もの以外は回答LLMには渡っていない。"
                f"候補にあっても投入・到達されていないページ/ノードを『読み込めているのに反映できていない』と判定しないこと。\n"
                f"（ナイーブRAG回答は上記。PDFに情報があるかの手掛かりに使う）")
    try:
        j = _llm_json(_JUDGE_SYS,
                      f"質問: {r['question']}\n正解: {expected}\n\n"
                      f"回答N(ナイーブRAG): {r['naive'].get('answer','')}\n\n"
                      f"回答W(LLM-Wiki検索): {wa.get('answer','')}\n\n"
                      f"回答A(KGのみ): {r['kg'].get('answer','')}\n\n回答B(KG+Wiki): {r['kgwiki'].get('answer','')}"
                      + diag_ctx)
    except Exception as ex:
        j = {"naive": {"verdict": "error", "reason": str(ex)}, "wikiagent": {"verdict": "error", "reason": str(ex)},
             "kg": {"verdict": "error", "reason": str(ex)}, "kgwiki": {"verdict": "error", "reason": str(ex)}}
    r["trace"].append({"node": "Judge(LLM)", "txt": f'Naive={((j.get("naive") or {}).get("verdict"))} / '
                       f'Wiki検索={((j.get("wikiagent") or {}).get("verdict"))} / '
                       f'KG={((j.get("kg") or {}).get("verdict"))} / KG+Wiki={((j.get("kgwiki") or {}).get("verdict"))}'})
    return {
        "cq_id": cq.get("id"), "question": r["question"], "expected": expected, "type": cq.get("type"),
        "naive": {**r["naive"], **(j.get("naive") or {})},
        "wikiagent": {**wa, **(j.get("wikiagent") or {})},
        "kg": {**r["kg"], **(j.get("kg") or {})},
        "kgwiki": {**r["kgwiki"], **(j.get("kgwiki") or {})},
        "entities": r["entities"], "sources": r["sources"], "trace": r["trace"],
        "diag": diag, "wiki_loaded": r.get("wiki_loaded") or [],
        "wiki_delivered": r.get("wiki_delivered") or [],
        "run_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }

def _approved_cqs():
    return [i for i in REVIEW_ITEMS if i.get("type_cq") == "cq" and i.get("status") == "approved"]

@app.get("/api/validation/cqs")
def validation_cqs():
    # title は question[:60] で切られている既存データがあるため、全文(description)を優先して表示する
    return JSONResponse([{"id": c.get("id"),
                          "title": (c.get("description") or "").strip() or c.get("title"),
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
    """チャット用: 任意の質問に4手法(ナイーブRAG/LLM-Wiki検索(Agentic)/KGのみ/KG+Wiki)でライブ回答。正解なし・judgeなし。"""
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
<title id="page-title">KG レビューUI</title>
<style>
  :root{--bg:#f5f7fa;--card:#fff;--border:#e2e6ea;--text:#1b1f24;--muted:#6b7280;--accent:#2563eb;--accent-light:#eff6ff;--warn:#d97706;--warn-bg:#fffbeb;--ok:#16a34a;--ok-bg:#f0fdf4;--reject:#dc2626;--reject-bg:#fef2f2;--purple:#7c3aed;--purple-bg:#f5f3ff}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,'Hiragino Sans','Noto Sans JP',sans-serif;background:var(--bg);color:var(--text);padding:0;line-height:1.6}
  .topbar{background:var(--card);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
  .topbar h1{font-size:1.1rem;font-weight:700}
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
  <button onclick="if(history.length>1){history.back()}else{location.assign('/')}" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--accent);font-weight:600;cursor:pointer;font-size:.85rem">← 戻る</button>
  <h1 id="page-h1">📋 KG レビューUI</h1>
</div>
<div class="content">

<div id="panel-raw" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
    <div style="flex:1">
      <div style="font-size:.95rem;font-weight:600">📄 1.1 RAWデータ</div>
      <div style="font-size:.78rem;color:var(--muted)">PDFをアップロードして、テキスト抽出→チャンク分割→埋め込みを一括実行。抽出結果は以下のタスクに利用されます。</div>
    </div>
  </div>
  <div style="display:flex;gap:12px;flex-wrap:wrap">
    <div style="flex:1;min-width:300px;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px">
      <div style="font-size:.85rem;font-weight:600;margin-bottom:8px">📤 PDFアップロード</div>
      <div style="border:2px dashed var(--border);border-radius:8px;padding:20px;text-align:center;background:var(--bg);cursor:pointer" id="drop-zone" ondragover="event.preventDefault()" ondrop="event.preventDefault();handleDrop(event)">
        <div style="font-size:2rem;margin-bottom:6px">📄</div>
        <div style="font-size:.82rem;color:var(--muted)">PDFファイルをここにドロップ、または</div>
        <input type="file" id="pdf-upload" accept=".pdf" style="display:none" onchange="handleFileSelect(event)">
        <button onclick="document.getElementById('pdf-upload').click()" style="margin-top:8px;padding:6px 16px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">ファイルを選択</button>
      </div>
      <div id="raw-progress" style="display:none;margin-top:8px"></div>
      <div id="raw-stats" style="display:none;margin-top:8px;font-size:.82rem"></div>
      <div id="raw-chunks" style="display:none;margin-top:10px">
        <div style="font-size:.82rem;font-weight:600;margin-bottom:4px">📋 抽出チャンク一覧</div>
        <div style="border:1px solid var(--border);border-radius:6px;padding:6px;max-height:300px;overflow:auto;background:var(--bg)" id="raw-chunk-list"></div>
        <div id="raw-chunk-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:1000;align-items:center;justify-content:center">
          <div style="background:var(--card);border-radius:10px;padding:16px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2)">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <div style="font-size:.85rem;font-weight:600" id="raw-chunk-title"></div>
              <button onclick="document.getElementById('raw-chunk-modal').style.display='none'" style="border:none;background:none;font-size:1.2rem;cursor:pointer;color:var(--muted)">✕</button>
            </div>
            <div style="font-size:.82rem;white-space:pre-wrap;line-height:1.6" id="raw-chunk-content"></div>
          </div>
        </div>
      </div>
    </div>
    <div style="flex:1;min-width:200px;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px">
      <div style="font-size:.85rem;font-weight:600;margin-bottom:8px">🔗 後続タスク</div>
      <div style="display:flex;flex-direction:column;gap:6px;font-size:.8rem">
        <div style="padding:8px 10px;background:var(--ok-bg);border:1px solid var(--ok);border-radius:6px">
          <div style="font-weight:600;color:var(--ok)">① ナイーブRAG</div>
          <div style="color:var(--muted);font-size:.75rem">PDFチャンク+埋め込み→ベクトル類似度Top5→LLM回答</div>
        </div>
        <div style="padding:8px 10px;background:var(--accent-light);border:1px solid var(--accent);border-radius:6px">
          <div style="font-weight:600;color:var(--accent)">② LLM-Wiki</div>
          <div style="color:var(--muted);font-size:.75rem">チャンクからLLMがエンティティページを生成</div>
        </div>
      </div>
    </div>
  </div>
</div>

<div id="panel-llmwiki" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
    <div style="flex:1">
      <div style="font-size:.95rem;font-weight:600">📝 1.2 LLM-Wiki</div>
      <div style="font-size:.78rem;color:var(--muted)">RAWデータ（章テキスト）からLLMがエンティティ/概念ページを生成。左のグラフはページ間の相互リンクを表示、ノードをクリックすると右にページ内容を表示します。</div>
    </div>
    <button onclick="generateLlmWiki()" id="wiki-gen-btn" style="padding:7px 16px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">🤖 LLM-Wikiを生成</button>
  </div>
  <div id="wiki-progress" style="display:none;margin-bottom:10px"></div>
  <div id="wiki-stats" style="display:none;margin-bottom:10px"></div>
  <div id="wiki-empty" class="empty-state" style="padding:40px;text-align:center;color:var(--muted)">
    <div style="font-size:2rem;margin-bottom:8px">📝</div>
    <div>まだ生成されていません。「🤖 LLM-Wikiを生成」をクリックしてください。</div>
  </div>
  <div id="wiki-container" style="display:none;gap:10px;height:640px">
    <svg id="wiki-graph-svg" style="flex:1;background:#fafafa;border:1px solid var(--border);border-radius:8px;overflow:hidden"></svg>
    <div style="width:420px;flex-shrink:0;display:flex;flex-direction:column;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--card)">
      <div id="wiki-frame-title" style="padding:8px 12px;font-size:.8rem;font-weight:600;color:var(--muted);border-bottom:1px solid var(--border)">← ノードをクリックしてページを表示</div>
      <iframe id="wiki-page-frame" style="flex:1;border:none;background:#fff"></iframe>
    </div>
  </div>
</div>

<div id="panel-cq" class="panel">
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
    <div id="cq-stats" class="stats" style="flex:1;margin-bottom:0"></div>
    <button onclick="generateQAs()" id="gen-cq-btn" style="padding:7px 16px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">🤖 LLMからQAを生成</button>
    <button onclick="approveAllCqs()" id="approve-cq-btn" style="padding:7px 16px;border:1px solid #2ecc71;border-radius:6px;background:transparent;color:#2ecc71;font-weight:600;cursor:pointer;font-size:.82rem;margin-left:6px">✅ 全QA承認</button>
    <button onclick="clearAllCqs()" id="clear-cq-btn" style="padding:7px 16px;border:1px solid #e74c3c;border-radius:6px;background:transparent;color:#e74c3c;font-weight:600;cursor:pointer;font-size:.82rem;margin-left:6px">🗑 全QA削除</button>
    <div id="qa-progress" style="display:none;margin-top:8px"></div>
  </div>
  <div id="cq-list" class="item-list"></div>
  <div class="card" style="margin-top:12px">
    <div class="card-title" style="margin-bottom:8px">✏️ 新規QA（質問＋回答ペア）追加</div>
    <div style="display:flex;flex-direction:column;gap:6px">
      <input type="text" id="new-cq-title" placeholder="質問（例: ○○の対象条件と金額は？）" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem">
      <textarea id="new-cq-desc" placeholder="質問の詳細説明" rows="2" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem;resize:vertical"></textarea>
      <textarea id="new-cq-answer" placeholder="期待される回答（例: ○○制度は月額15,500円、△△制度は月額28,840円…）" rows="2" style="padding:6px 10px;border:1px solid var(--border);border-radius:5px;font-size:.85rem;resize:vertical;background:var(--ok-bg);border-color:var(--ok)"></textarea>
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
    <button onclick="extractMissingKG()" id="kg-extract-missing-btn" style="display:none;padding:7px 14px;border:1px solid var(--warn);border-radius:6px;background:var(--warn-bg);color:var(--warn);font-weight:600;cursor:pointer;font-size:.82rem">🔍 未カバー分のみ再抽出</button>
    <button onclick="extractKG()" id="kg-extract-btn" style="padding:7px 16px;border:none;border-radius:6px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:.82rem">🤖 LLM-Wikiから抽出 → Neo4j</button>
  </div>
  <div id="kg-coverage" style="display:none;font-size:.76rem;margin-bottom:8px;padding:6px 10px;border-radius:6px;border:1px solid var(--warn);background:var(--warn-bg);color:var(--warn)"></div>
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
const API = '';   // 統合サーバ(review.main on 8790)により同一オリジン。/api/* を直接呼ぶ（/review 接頭辞はcatch-allのHTMLに吸われるため付けない）
let allItems = [];

// ── 根拠リンク（LLM-Wikiページ / QA）ヘルパ ──
// 統合サーバーにより同一オリジン(=8790)。
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
  const rel = (WIKI_INDEX && WIKI_INDEX[slug]) ? WIKI_INDEX[slug] : ('pipeline/step1_data/wiki/' + slug + '.md');
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
  const prog = document.getElementById('qa-progress');
  btn.disabled = true;
  btn.textContent = '⏳ 生成開始…';
  prog.style.display = 'block';
  prog.innerHTML = '<div class="prog-bar"><div class="prog-fill" id="qa-prog-fill" style="width:0%"></div></div><div class="prog-label" id="qa-prog-label">準備中…</div>';
  try {
    const r = await fetch(API + '/api/cq/generate', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { prog.innerHTML = '<div class="prog-label" style="color:var(--reject)">⚠ エラー: ' + (d.error || '') + '</div>'; btn.disabled=false; btn.textContent='🤖 LLMからQAを生成'; return; }
    const tid = d.task_id;
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const pr = await (await fetch(API + '/api/task/' + tid)).json();
      const pct = Math.round(pr.progress / pr.total * 100);
      document.getElementById('qa-prog-fill').style.width = pct + '%';
      document.getElementById('qa-prog-label').textContent = pr.msg || (pct + '%');
      if (pr.status === 'done') {
        prog.innerHTML = '<div class="prog-bar"><div class="prog-fill done" style="width:100%"></div></div><div class="prog-label" style="color:var(--ok)">✅ 完了</div>';
        break;
      }
      if (pr.status === 'error') {
        prog.innerHTML = '<div class="prog-bar"><div class="prog-fill error" style="width:100%"></div></div><div class="prog-label" style="color:var(--reject)">⚠ エラー: ' + (pr.error || '') + '</div>';
        btn.disabled = false; btn.textContent = '🤖 LLMからQAを生成'; return;
      }
    }
    allItems = await (await fetch(API + '/api/review-items')).json();
    renderQA();
  } catch(e) {
    prog.innerHTML = '<div class="prog-label" style="color:var(--reject)">⚠ 通信エラー: ' + e.message + '</div>';
  }
  btn.disabled = false;
  btn.textContent = '🤖 LLMからQAを生成';
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
const SCREENS = ['raw', 'llmwiki', 'cq','ontology-def','ontology-graph','kg','validation'];
function currentScreen() {
  const seg = location.pathname.replace(/\/+$/,'').split('/').pop();
  return SCREENS.includes(seg) ? seg : 'cq';
}
// ── 1.2 LLM-Wiki: RAWデータから概念ページを生成 ──
function renderLlmWiki() {
  const stats = document.getElementById('wiki-stats');
  const empty = document.getElementById('wiki-empty');
  const container = document.getElementById('wiki-container');
  fetch(API + '/api/llmwiki/status').then(r => r.json()).then(d => {
    if (d.exists) {
      stats.style.display = 'block';
      stats.innerHTML = `<div style="color:var(--ok);font-weight:600">✅ 生成済み: ${d.count}ページ</div>`;
      empty.style.display = 'none';
      container.style.display = 'flex';
      renderLlmWikiGraph();
    } else {
      stats.style.display = 'block';
      stats.innerHTML = '<div style="color:var(--muted)">まだ生成されていません。「🤖 LLM-Wikiを生成」をクリックしてください。</div>';
      empty.style.display = 'block';
      container.style.display = 'none';
    }
  }).catch(() => {});
}

async function renderLlmWikiGraph() {
  const svg = document.getElementById('wiki-graph-svg');
  try {
    const g = await (await fetch(API + '/api/llmwiki/graph')).json();
    const rawNodes = g.nodes || [], rawEdges = g.edges || [];
    if (!rawNodes.length) { while (svg.firstChild) svg.removeChild(svg.firstChild); return; }

    const W = svg.clientWidth || 760, H = svg.clientHeight || 640;
    const nodeById = {};
    rawNodes.forEach(n => {
      const r = Math.max(6, Math.min(14, 14 - String(n.title).length * 0.15));
      nodeById[n.id] = { id: n.id, title: n.title, r,
        x: W/2+(Math.random()-0.5)*W*0.9, y: H/2+(Math.random()-0.5)*H*0.9, vx: 0, vy: 0 };
    });
    const nodes = Object.values(nodeById);
    const edges = rawEdges.map(e => ({ a: nodeById[e.source], b: nodeById[e.target] })).filter(e => e.a && e.b);

    // Force simulation（ノード数に応じて反復回数を調整）
    const ITERS = nodes.length > 200 ? 70 : nodes.length > 80 ? 120 : 200;
    for (let i = 0; i < ITERS; i++) {
      for (const n of nodes) {
        n.vx += (W/2-n.x)*0.002; n.vy += (H/2-n.y)*0.002;
      }
      for (const e of edges) {
        const dx=e.b.x-e.a.x, dy=e.b.y-e.a.y, d=Math.sqrt(dx*dx+dy*dy)||1;
        const f = (d-(e.a.r+e.b.r+40))*0.0035;
        e.a.vx += (dx/d)*f; e.a.vy += (dy/d)*f;
        e.b.vx -= (dx/d)*f; e.b.vy -= (dy/d)*f;
      }
      // 反発力: ノード数が多い場合は間引いてO(n^2)コストを抑える
      const step = nodes.length > 200 ? 3 : 1;
      for (let ni = 0; ni < nodes.length; ni++) {
        const n = nodes[ni];
        for (let oi = ni + step; oi < nodes.length; oi += step) {
          const o = nodes[oi];
          let dx=n.x-o.x, dy=n.y-o.y, d=Math.sqrt(dx*dx+dy*dy)||1, f=900/(d*d);
          n.vx += (dx/d)*f; n.vy += (dy/d)*f; o.vx -= (dx/d)*f; o.vy -= (dy/d)*f;
        }
      }
      for (const n of nodes) { n.vx*=0.86; n.vy*=0.86; n.x+=n.vx; n.y+=n.vy; n.x=Math.max(n.r,Math.min(W-n.r,n.x)); n.y=Math.max(n.r,Math.min(H-n.r,n.y)); }
    }

    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const NS = 'http://www.w3.org/2000/svg';

    for (const e of edges) {
      const line = document.createElementNS(NS, 'line');
      line.setAttribute('x1', e.a.x); line.setAttribute('y1', e.a.y);
      line.setAttribute('x2', e.b.x); line.setAttribute('y2', e.b.y);
      line.setAttribute('stroke', '#ccc'); line.setAttribute('stroke-width', '0.7');
      svg.appendChild(line);
    }
    for (const n of nodes) {
      const g2 = document.createElementNS(NS, 'g');
      g2.setAttribute('transform', `translate(${n.x},${n.y})`);
      g2.style.cursor = 'pointer';
      const circle = document.createElementNS(NS, 'circle');
      circle.setAttribute('r', n.r); circle.setAttribute('fill', '#3498db90');
      circle.setAttribute('stroke', '#3498db'); circle.setAttribute('stroke-width', '1.2');
      g2.appendChild(circle);
      const title = document.createElementNS(NS, 'title');
      title.textContent = n.title; g2.appendChild(title);
      const text = document.createElementNS(NS, 'text');
      text.setAttribute('text-anchor', 'middle'); text.setAttribute('y', n.r + 8);
      text.setAttribute('font-size', '6'); text.setAttribute('fill', '#333');
      text.textContent = n.title.length > 9 ? n.title.slice(0,9)+'…' : n.title;
      g2.appendChild(text);
      g2.addEventListener('click', () => showWikiPage(n.id, n.title));
      svg.appendChild(g2);
    }
  } catch(e) {
    svg.outerHTML = `<div class="warnline">⚠ グラフ読み込みエラー: ${e.message}</div>`;
  }
}

function showWikiPage(fname, title) {
  const frame = document.getElementById('wiki-page-frame');
  const titleEl = document.getElementById('wiki-frame-title');
  titleEl.textContent = title || fname;
  frame.src = WIKI_ORIGIN + '/pipeline/step1_data/wiki/' + encodeURIComponent(fname);
}

async function generateLlmWiki() {
  const btn = document.getElementById('wiki-gen-btn');
  const prog = document.getElementById('wiki-progress');
  btn.disabled = true;
  btn.textContent = '⏳ 生成中…';
  prog.style.display = 'block';
  prog.innerHTML = '<div class="prog-bar"><div class="prog-fill" id="wiki-prog-fill" style="width:0%"></div></div><div class="prog-label" id="wiki-prog-label">RAWデータ読み込み中…</div>';
  try {
    const r = await fetch(API + '/api/llmwiki/generate', {method: 'POST'});
    const d = await r.json();
    if (!d.ok) { prog.innerHTML = '<div class="prog-label" style="color:var(--reject)">⚠ エラー</div>'; btn.disabled=false; btn.textContent='🤖 LLM-Wikiを生成'; return; }
    const tid = d.task_id;
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const pr = await (await fetch(API + '/api/task/' + tid)).json();
      const pct = Math.round(pr.progress / pr.total * 100);
      document.getElementById('wiki-prog-fill').style.width = pct + '%';
      document.getElementById('wiki-prog-label').textContent = pr.msg || (pct + '%');
      if (pr.status === 'done') {
        prog.innerHTML = '<div class="prog-bar"><div class="prog-fill done" style="width:100%"></div></div><div class="prog-label" style="color:var(--ok)">✅ 完了</div>';
        break;
      }
      if (pr.status === 'error') {
        prog.innerHTML = `<div class="prog-label" style="color:var(--reject)">⚠ エラー: ${pr.error || ''}</div>`;
        btn.disabled=false; btn.textContent='🤖 LLM-Wikiを生成'; return;
      }
    }
    renderLlmWiki();
  } catch(e) {
    prog.innerHTML = `<div class="prog-label" style="color:var(--reject)">⚠ 通信エラー: ${e.message}</div>`;
  }
  btn.disabled = false;
  btn.textContent = '🤖 LLM-Wikiを生成';
}

// ── 1.1 RAWデータ: PDFアップロード→抽出→チャンク→埋め込み ──
function renderRaw() {
  const stats = document.getElementById('raw-stats');
  const chunkSection = document.getElementById('raw-chunks');
  // Check if chunks already exist
  fetch(API + '/api/raw/status').then(r => r.json()).then(d => {
    if (d.exists) {
      stats.style.display = 'block';
      let chapHtml = '';
      if (d.chapters && d.chapters.length) {
        chapHtml = '<div style="margin-top:8px;font-weight:600;font-size:.8rem">章 (' + d.n_chapters + ')</div>' +
          d.chapters.map(c => {
            const fn = c.file || '';
            const label = (c.chapter_no ? String(c.chapter_no).padStart(2,'0') + '. ' : '') + (c.title || fn) +
              (c.start_page ? ` <span style="color:var(--muted)">(p.${c.start_page}–${c.end_page})</span>` : '');
            return `<div style="padding:2px 6px;border-bottom:1px solid var(--border);font-size:.78rem">` +
              (fn ? `<a href="${WIKI_ORIGIN}/pipeline/step1_data/raw/chapters/${encodeURIComponent(fn)}" target="_blank" style="color:var(--accent)">${label}</a>` : label) +
              `</div>`;
          }).join('');
      }
      let pageHtml = '';
      if (d.pages && d.pages.length) {
        pageHtml = '<div style="margin-top:8px;font-weight:600;font-size:.8rem">ページ (' + d.n_pages + ')</div>' +
          '<div style="max-height:140px;overflow:auto;display:flex;flex-wrap:wrap;gap:3px;margin-top:4px">' +
          d.pages.map(p => `<a href="${WIKI_ORIGIN}/pipeline/step1_data/raw/pages/${encodeURIComponent(p)}" target="_blank" style="font-size:.72rem;padding:1px 5px;border:1px solid var(--border);border-radius:3px;color:var(--accent);text-decoration:none">${p.replace('page_','').replace('.txt','')}</a>`).join('') +
          '</div>';
      }
      const isOcr = (d.n_pages || 0) > 0;
      const badge = isOcr
        ? '<div style="color:var(--ok);font-weight:600">✅ 1.1 完了 — OCR抽出済み</div>'
        : '<div style="color:var(--reject);font-weight:600">⚠ 未OCR（旧データのみ）— PDFをアップロードしてください</div>';
      const artifactsHtml = isOcr
        ? '<div style="margin-top:8px;font-weight:600;font-size:.8rem">成果物</div>' +
          '<div style="font-size:.78rem;line-height:1.7">' +
          `<a href="${WIKI_ORIGIN}/pipeline/step1_data/raw/raw_text.txt" target="_blank" style="color:var(--accent)">raw_text.txt（全章連結・全文）</a><br>` +
          `<a href="${WIKI_ORIGIN}/pipeline/step1_data/raw/ocr_meta.json" target="_blank" style="color:var(--accent)">ocr_meta.json（ページ→章 対応）</a><br>` +
          `<a href="${WIKI_ORIGIN}/pipeline/step1_data/raw/chunks.json" target="_blank" style="color:var(--accent)">chunks.json（ナイーブRAG用チャンク）</a><br>` +
          `<a href="${WIKI_ORIGIN}/pipeline/step1_data/raw/raw_embeddings.json" target="_blank" style="color:var(--accent)">raw_embeddings.json（ナイーブRAG用埋め込み）</a>` +
          '</div>'
        : '';
      stats.innerHTML = badge +
        `<div style="color:var(--muted);font-size:.78rem">${d.n_pages||0}ページ / ${d.n_chapters||0}章 / ${d.chunks}チャンク / ${d.chars}文字</div>`
        + artifactsHtml + chapHtml + pageHtml;
      // Show chunk list
      fetch(API + '/api/raw/chunks').then(r => r.json()).then(cs => {
        if (!Array.isArray(cs)) return;
        chunkSection.style.display = 'block';
        const list = document.getElementById('raw-chunk-list');
        list.innerHTML = cs.map(c => `<div style="padding:3px 6px;cursor:pointer;border-bottom:1px solid var(--border);font-size:.78rem;display:flex;justify-content:space-between;transition:background .15s" 
          onmouseenter="this.style.background='var(--accent-light)'" onmouseleave="this.style.background=''"
          onclick="showChunkDetail(${c.index})">
          <span>#${c.index} <span style="color:var(--muted)">p.${c.page}</span> ${c.preview}</span>
          <span style="color:var(--muted);white-space:nowrap;margin-left:8px">${c.chars}字</span>
        </div>`).join('') || '<div style="color:var(--muted);font-size:.78rem;padding:6px">チャンクがありません</div>';
      });
    } else {
      stats.style.display = 'block';
      stats.innerHTML = '<div style="color:var(--muted)">まだ抽出されていません。PDFをアップロードしてください。</div>';
chunkSection.style.display = 'none';
    }
  }).catch(() => {});
}

async function showChunkDetail(idx) {
  const modal = document.getElementById('raw-chunk-modal');
  const titleEl = document.getElementById('raw-chunk-title');
  const contentEl = document.getElementById('raw-chunk-content');
  modal.style.display = 'flex';
  titleEl.textContent = '⏳ 読み込み中…';
  contentEl.textContent = '';
  try {
    const r = await fetch(API + '/api/raw/chunk/' + idx);
    const c = await r.json();
    titleEl.textContent = `チャンク #${idx} — ページ ${c.page}（${c.text.length}文字）`;
    contentEl.textContent = c.text;
  } catch(e) {
    contentEl.textContent = '読み込みエラー: ' + e.message;
  }
}

async function handleFileSelect(e) {
  const file = e.target.files[0];
  if (!file) return;
  await uploadPdf(file);
}

async function handleDrop(e) {
  const file = e.dataTransfer.files[0];
  if (!file || !file.name.endsWith('.pdf')) { alert('PDFファイルをドロップしてください'); return; }
  await uploadPdf(file);
}

async function uploadPdf(file) {
  const prog = document.getElementById('raw-progress');
  const stats = document.getElementById('raw-stats');
  prog.style.display = 'block';
  prog.innerHTML = '<div class="prog-bar"><div class="prog-fill" id="raw-prog-fill" style="width:0%"></div></div><div class="prog-label" id="raw-prog-label">アップロード中…</div>';
  stats.style.display = 'none';
  try {
    const form = new FormData();
    form.append('file', file);
    const r = await fetch(API + '/api/raw/upload', {method:'POST', body:form});
    const d = await r.json();
    if (!d.ok) { prog.innerHTML = '<div class="prog-label" style="color:var(--reject)">⚠ エラー</div>'; return; }
    const tid = d.task_id;
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const pr = await (await fetch(API + '/api/task/' + tid)).json();
      const pct = Math.round(pr.progress / pr.total * 100);
      document.getElementById('raw-prog-fill').style.width = pct + '%';
      document.getElementById('raw-prog-label').textContent = pr.msg || (pct + '%');
      if (pr.status === 'done') {
        prog.innerHTML = '<div class="prog-bar"><div class="prog-fill done" style="width:100%"></div></div><div class="prog-label" style="color:var(--ok)">✅ 完了</div>';
        break;
      }
      if (pr.status === 'error') {
        prog.innerHTML = `<div class="prog-label" style="color:var(--reject)">⚠ エラー: ${pr.error || ''}</div>`;
        return;
      }
    }
    renderRaw();
  } catch(e) {
    prog.innerHTML = `<div class="prog-label" style="color:var(--reject)">⚠ 通信エラー: ${e.message}</div>`;
  }
}

function initScreen() {
  const seg = currentScreen();
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const panel = document.getElementById('panel-' + seg);
  if (panel) panel.classList.add('active');
  if (seg === 'raw') renderRaw();
  else if (seg === 'llmwiki') renderLlmWiki();
  else if (seg === 'cq') loadItems();
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

async function extractMissingKG() {
  const btn = document.getElementById('kg-extract-missing-btn');
  const st = document.getElementById('kg-status');
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '⏳ 未カバー分を再抽出中…';
  st.style.display = 'block';
  st.innerHTML = '<div class="prog-bar"><div class="prog-fill" id="kg-prog-fill" style="width:0%"></div></div><div class="prog-label" id="kg-prog-label">準備中…</div>';
  try {
    const r = await fetch(API + '/api/kg/extract-missing', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { st.innerHTML = '⚠ ' + (d.error || '失敗'); btn.disabled=false; btn.textContent=old; return; }
    const tid = d.task_id;
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const pr = await (await fetch(API + '/api/task/' + tid)).json();
      const pct = Math.round(pr.progress / pr.total * 100);
      document.getElementById('kg-prog-fill').style.width = pct + '%';
      document.getElementById('kg-prog-label').textContent = pr.msg || (pct + '%');
      if (pr.status === 'done') { st.innerHTML = '<div class="prog-bar"><div class="prog-fill done" style="width:100%"></div></div><div class="prog-label" style="color:var(--ok)">✅ ' + (pr.msg||'完了') + '</div>'; break; }
      if (pr.status === 'error') { st.innerHTML = '<div class="prog-bar"><div class="prog-fill error" style="width:100%"></div></div><div class="prog-label" style="color:var(--reject)">⚠ エラー: ' + (pr.error || '') + '</div>'; break; }
    }
    await renderKG('extracted');
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

  // 未カバーページ表示（表示中がNeo4jビューでも、直近の抽出メタから判定する）
  try {
    const extMeta = (await fetchKG('/api/kg/extracted')).meta || {};
    const uncovered = extMeta.uncovered_pages || [];
    const berrs = extMeta.batch_errors || [];
    const covDiv = document.getElementById('kg-coverage');
    const missBtn = document.getElementById('kg-extract-missing-btn');
    if (uncovered.length || berrs.length) {
      covDiv.style.display = 'block';
      covDiv.innerHTML = uncovered.length
        ? `⚠ 未カバーページ: <b>${uncovered.length}件</b>（KGに1件もノードが無いWikiページ） `
          + `<span style="color:var(--muted);font-size:.7rem">${uncovered.slice(0,6).map(escHtml).join(', ')}${uncovered.length>6?' …':''}</span>`
        : '';
      if (berrs.length) covDiv.innerHTML += (uncovered.length?'<br>':'') + `⚠ 抽出失敗バッチ: <b>${berrs.length}件</b>（自動でJSON修復リトライ済みでも失敗）`;
      missBtn.style.display = uncovered.length ? 'inline-block' : 'none';
      missBtn.textContent = `🔍 未カバー分のみ再抽出（${uncovered.length}件）`;
    } else {
      covDiv.style.display = 'none';
      missBtn.style.display = 'none';
    }
  } catch(e) {}

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
// LLM回答はMarkdown（**強調**・箇条書き・見出し等）で返ってくるため、生テキストのまま表示すると
// 記号がそのまま見えてしまう。簡易Markdown→HTML変換で読みやすく表示する。
function mdToHtml(md){
  const lines=(md||'').split('\n'); let html='',inList=false;
  const inline = s => escHtml(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  for(const raw of lines){
    const line=raw.replace(/\s+$/,'');
    if(/^\s*[-*]\s+/.test(line)){ if(!inList){html+='<ul style="margin:2px 0 2px 18px;padding:0">';inList=true;} html+='<li>'+inline(line.replace(/^\s*[-*]\s+/,''))+'</li>'; continue; }
    if(inList){html+='</ul>';inList=false;}
    if(/^#{1,6}\s+/.test(line)){ html+='<div style="font-weight:700;margin-top:4px">'+inline(line.replace(/^#{1,6}\s+/,''))+'</div>'; continue; }
    if(/^---+$/.test(line)){ html+='<hr style="border:0;border-top:1px solid var(--border);margin:6px 0">'; continue; }
    if(line.trim()===''){ html+='<div style="height:4px"></div>'; continue; }
    html+='<div>'+inline(line)+'</div>';
  }
  if(inList)html+='</ul>';
  return html || '<span style="color:var(--muted)">（回答なし）</span>';
}
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
      ${bar('🔍 ナイーブRAG', tally('naive'))}${bar('📖 LLM-Wiki検索(Agentic)', tally('wikiagent'))}${bar('🗄 KGのみ', tally('kg'))}${bar('🗄+📄 KG+Wiki補完', tally('kgwiki'))}</div>`;
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
     <div style="font-size:.82rem;margin-bottom:6px">${mdToHtml((d||{}).answer||'')}</div>
     <div style="font-size:.72rem;color:var(--muted)"><b>判定理由:</b> ${escHtml((d||{}).reason||'')}</div>
     ${(d&&d.cause)?`<div style="font-size:.72rem;color:#7c2d12;background:#fff7ed;border:1px solid #fed7aa;border-radius:5px;padding:3px 6px;margin-top:5px">🩺 <b>原因解析:</b> ${escHtml(d.cause)}</div>`:''}
     ${extra||''}
   </div>`;
  const naiveSrc=(res.naive&&res.naive.rag_sources&&res.naive.rag_sources.length)
    ? `<div style="font-size:.7rem;color:var(--muted);margin-top:4px">出典: ${res.naive.rag_sources.map(s=>'p.'+s.page).join(', ')}</div>` : '';
  const naiveCol=res.naive
    ? col('🔍 ナイーブRAG',res.naive,naiveSrc)
    : `<div style="flex:1;min-width:230px;border:1px dashed var(--border);border-radius:8px;padding:9px 11px;background:var(--card);color:var(--muted);font-size:.78rem"><b>🔍 ナイーブRAG</b><br><span style="font-size:.74rem">旧結果のため未実行。「🔄 再検証」で追加されます。</span></div>`;
  const wa = res.wikiagent;
  const waPages = wa && wa.pages_visited && wa.pages_visited.length
    ? `<div style="font-size:.7rem;color:var(--muted);margin-top:4px">探索: ${wa.pages_visited.map(s=>linkifyRefs(s)).join(' → ')}</div>` : '';
  const wikiagentCol=wa
    ? col('📖 LLM-Wiki検索(Agentic)',wa,waPages)
    : `<div style="flex:1;min-width:230px;border:1px dashed var(--border);border-radius:8px;padding:9px 11px;background:var(--card);color:var(--muted);font-size:.78rem"><b>📖 LLM-Wiki検索(Agentic)</b><br><span style="font-size:.74rem">旧結果のため未実行。「🔄 再検証」で追加されます。</span></div>`;
  const ents=(res.entities||[]).map(escHtml).join('、');
  const srcs=(res.sources||[]).map(s=>linkifyRefs(s)).join(' ');
  const traceHtml=(res.trace||[]).map(t=>`<div>[${escHtml(t.node)}] ${escHtml(t.txt)}</div>`).join('');
  return `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:6px">${naiveCol}${wikiagentCol}${col('🗄 KGのみ',res.kg)}${col('🗄+📄 KG+Wiki補完',res.kgwiki)}</div>
    ${res.diag?`<div style="font-size:.72rem;color:var(--muted);margin-bottom:4px">📊 KG検索ヒット <b>${res.diag.retrieved_nodes}</b> → <b style="color:var(--accent)">LLM投入ノード ${res.diag.nodes_delivered??'—'}</b> ／ source候補 <b>${res.diag.wiki_requested}</b>・本文あり <b>${res.diag.wiki_loaded}</b> → <b style="color:var(--accent)">LLM投入Wiki ${res.diag.wiki_delivered??'—'}ページ</b> ／ Wiki検索が探索 <b style="color:var(--accent)">${res.diag.wikiagent_pages??'—'}ページ</b>${res.diag.wiki_delivered===0&&res.diag.wiki_requested>0?' <span style="color:#b91c1c">← Wikiを1ページも投入できていません</span>':''}${res.diag.retrieved_nodes===0?' <span style="color:#b91c1c">← KGから関連ノードを取得できていません</span>':''}</div>`:''}
    <details style="font-size:.75rem"><summary style="cursor:pointer;color:var(--muted)">検索エンティティ / 参照Wiki / エージェント経路</summary>
      <div style="margin-top:5px"><b>検索エンティティ(${(res.entities||[]).length}):</b> ${ents||'—'}</div>
      <div style="margin-top:3px"><b>LLMに投入したWiki(${(res.wiki_delivered||[]).length}):</b> ${(res.wiki_delivered||[]).map(s=>linkifyRefs(s)).join(' ')||'—'}</div>
      <div style="margin-top:3px;color:var(--muted)"><b>source候補(${(res.sources||[]).length}):</b> ${srcs||'—'}</div>
      <div style="margin-top:3px"><b>Wiki検索(W)が探索したページ(${(wa&&wa.pages_visited||[]).length}):</b> ${(wa&&wa.pages_visited||[]).map(s=>linkifyRefs(s)).join(' ')||'—'}</div>
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
  const prog=document.getElementById('val-progress');
  const old=btn.textContent; btn.disabled=true; btn.textContent='⏳ 原因から修正中…';
  prog.style.display='block';
  prog.innerHTML = '<div class="prog-bar"><div class="prog-fill" id="fix-prog-fill" style="width:0%"></div></div><div class="prog-label" id="fix-prog-label">原因解析中…</div>';
  try{
    const r=await fetch(API+'/api/ontology/fix-from-validation',{method:'POST'});
    const d=await r.json();
    document.getElementById('fix-prog-fill').style.width='100%';
    document.getElementById('fix-prog-label').textContent='完了';
    const causeStr = d.cause_counts ? Object.entries(d.cause_counts).map(([k,v])=>`[${k}]${v}`).join(' / ') : '';
    if(d.ok){
      prog.innerHTML = '<div class="prog-bar"><div class="prog-fill done" style="width:100%"></div></div><div class="prog-label" style="color:var(--ok)">✅ 修正完了</div>';
      alert('✅ 原因解析に基づくオントロジー修正 完了\n'
        + `修正対象(構造で対処可能)の失敗QA: ${d.fixed_cqs}件`
        + (d.non_actionable?`（対象外 ${d.non_actionable}件=Wiki未読込/網羅不足/回答生成 は抽出・Wiki側の課題）`:'')+'\n'
        + `原因内訳: ${causeStr||'—'}\n`
        + `クラス ${d.classes}（+${d.classes_added}） / 関係 ${d.relationships}（+${d.relationships_added}）\n`
        + (d.fix_notes?('\n修正内容:\n'+d.fix_notes):''));
    } else {
      prog.innerHTML = '<div class="prog-bar"><div class="prog-fill error" style="width:100%"></div></div><div class="prog-label" style="color:var(--reject)">⚠ エラー</div>';
      alert('⚠ '+(d.error||'')+(causeStr?`\n原因内訳: ${causeStr}`:''));
    }
  }catch(e){
    prog.innerHTML = '<div class="prog-label" style="color:var(--reject)">⚠ 通信エラー: '+e.message+'</div>';
    alert('⚠ 通信エラー: '+e.message);
  }
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
// 画面タイトル/見出しは .env の KB_TITLE から取得（別プロジェクトへforkしても.envの変更だけで済むように）
fetch(API + '/api/meta').then(r=>r.json()).then(m=>{
  if(m && m.kb_title){
    document.getElementById('page-title').textContent = m.kb_title + ' — レビューUI';
    document.getElementById('page-h1').textContent = '📋 ' + m.kb_title;
  }
}).catch(()=>{});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    chat_path = os.path.join(ROOT, "pipeline", "chat", "index.html")
    if os.path.isfile(chat_path):
        return HTMLResponse(open(chat_path, encoding="utf-8").read())
    return HTML

@app.get("/raw", response_class=HTMLResponse)
@app.get("/llmwiki", response_class=HTMLResponse)
@app.get("/cq", response_class=HTMLResponse)
@app.get("/ontology-def", response_class=HTMLResponse)
@app.get("/ontology-graph", response_class=HTMLResponse)
@app.get("/kg", response_class=HTMLResponse)
@app.get("/validation", response_class=HTMLResponse)
def legacy_review():
    return HTML


# ── 静的ファイル キャッチオール ──
@app.get("/{path:path}")
async def serve_static(path: str):
    resp = _serve_file(path)
    if resp:
        return resp
    return HTMLResponse(content='<!doctype html><meta charset="utf-8"><h1>404 Not Found</h1><p>' + path + '</p>', status_code=404)


if __name__ == "__main__":
    import uvicorn
    # reload is OPT-IN (default off). The uvicorn reloader spawns a worker via
    # multiprocessing whose command line is opaque; a stale/wrong-interpreter worker
    # then holds the port and is hard to kill (incident 2026-07-15). Enable only when
    # you deliberately want auto-reload:  set REVIEW_RELOAD=1
    _reload = os.environ.get("REVIEW_RELOAD", "").strip().lower() in ("1", "true", "yes", "on")
    uvicorn.run("main:app", host="127.0.0.1", port=8790, reload=_reload)