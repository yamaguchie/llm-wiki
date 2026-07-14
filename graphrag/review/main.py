# -*- coding: utf-8 -*-
"""
FastAPI review UI for Bunkyo Welfare KG (human review of constraints data).

Usage:
    pip install fastapi uvicorn
    uvicorn main:app --reload --port 8789

Endpoints:
    GET  /             — static HTML review UI
    GET  /api/kg       — full kg.json
    GET  /api/constraints — constraint list from ontology.md (hardcoded)
    GET  /api/ontology — ontology summary (classes, relationships)
    POST /api/review   — save review notes (in-memory)
    GET  /api/reviews  — list saved reviews
"""
import json, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Bunkyo Welfare KG Review UI")

# ── Load KG ──
KG_PATH = os.path.join(HERE, "..", "graph", "kg.json")
kg_data = json.load(open(KG_PATH, encoding="utf-8"))


# ── Constraints (from ontology.md C01-C10) ──
CONSTRAINTS = [
    {"id":"C01","description":"心身障害者等福祉手当 身体1・2級/愛1-3度=15,500円","src":"04 (44p)","review":"human_required","status":"pending"},
    {"id":"C02","description":"心身障害者等福祉手当 身体3級/愛4度=13,500円","src":"04 (44p)","review":"human_required","status":"pending"},
    {"id":"C03","description":"精神障害者福祉手当=精神1級・10,000円","src":"04 (45p)","review":"human_required","status":"pending"},
    {"id":"C04","description":"心身障害者等福祉手当 除外条件(65歳以上・施設入所等)","src":"04 (44p)","review":"human_required","status":"pending"},
    {"id":"C05","description":"マル障 対象手帳(身体/愛/精神)","src":"05 (56p)","review":"human_required","status":"pending"},
    {"id":"C06","description":"移動支援/同行援護=月36時間まで負担なし","src":"03/07 (43/82p)","review":"human_required","status":"pending"},
    {"id":"C07","description":"補装具 対象疾病→難病一覧(199p)","src":"06/99 (199p)","review":"none","status":"approved"},
    {"id":"C08","description":"所得制限あり手当は全て所得制限限度額表を参照","src":"99 (204p)","review":"none","status":"approved"},
    {"id":"C09","description":"精神福祉手当×心身福祉手当=併給不可","src":"04 (45p)","review":"none","status":"approved"},
    {"id":"C10","description":"在宅レスパイト 年288時間上限","src":"07 (85p)","review":"human_required","status":"pending"},
]

# ── In-memory reviews ──
reviews_db: list[dict] = []


class ReviewNote(BaseModel):
    constraint_id: str
    reviewer: str = ""
    comment: str = ""
    approved: bool = False


# ── API Routes ──

@app.get("/api/kg")
def get_kg():
    return JSONResponse(content=kg_data)


@app.get("/api/constraints")
def get_constraints():
    return JSONResponse(content=CONSTRAINTS)


@app.get("/api/ontology")
def get_ontology():
    labels = sorted(set(l for n in kg_data["nodes"] for l in n["labels"]))
    rels = sorted(set(e["type"] for e in kg_data["edges"]))
    return {
        "node_labels": labels,
        "relationship_types": rels,
        "n_nodes": len(kg_data["nodes"]),
        "n_edges": len(kg_data["edges"]),
        "node_count_by_label": {l: sum(1 for n in kg_data["nodes"] if l in n["labels"]) for l in labels},
    }


@app.post("/api/review")
def post_review(note: ReviewNote):
    entry = {
        "constraint_id": note.constraint_id,
        "reviewer": note.reviewer,
        "comment": note.comment,
        "approved": note.approved,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    reviews_db.append(entry)
    # Update constraint status
    for c in CONSTRAINTS:
        if c["id"] == note.constraint_id:
            c["status"] = "approved" if note.approved else "rejected"
            c["reviewer"] = note.reviewer
            c["comment"] = note.comment
            break
    return {"ok": True, "entry": entry}


@app.get("/api/reviews")
def get_reviews():
    return JSONResponse(content=reviews_db)


# ── Static HTML UI ──

HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bunkyo Welfare KG — Review UI</title>
<style>
  :root { --bg: #f5f5f5; --card: #fff; --border: #ddd; --text: #333; --accent: #1a73e8; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, 'Hiragino Sans', 'Noto Sans JP', sans-serif; background: var(--bg); color: var(--text); padding: 16px; }
  h1 { font-size: 1.4rem; margin-bottom: 12px; }
  h2 { font-size: 1.1rem; margin: 16px 0 8px; }
  .tabs { display: flex; gap: 4px; margin-bottom: 16px; }
  .tabs button { padding: 8px 16px; border: 1px solid var(--border); background: var(--card); cursor: pointer; border-radius: 6px 6px 0 0; font-size: .9rem; }
  .tabs button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .panel { display: none; }
  .panel.active { display: block; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 12px; }
  .constraint { display: flex; align-items: flex-start; gap: 12px; padding: 12px; border-bottom: 1px solid var(--border); }
  .constraint:last-child { border: none; }
  .constraint .id { font-weight: bold; min-width: 48px; color: var(--accent); }
  .constraint .desc { flex: 1; }
  .constraint .src { color: #888; font-size: .8rem; }
  .constraint .status { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: .75rem; font-weight: bold; }
  .status-pending { background: #fff3cd; color: #856404; }
  .status-approved { background: #d4edda; color: #155724; }
  .status-rejected { background: #f8d7da; color: #721c24; }
  .review-form { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; }
  .review-form input, .review-form textarea { padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px; font-size: .85rem; }
  .review-form textarea { flex: 1; min-width: 200px; }
  .review-form button { padding: 6px 14px; border: none; border-radius: 4px; background: var(--accent); color: #fff; cursor: pointer; }
  .review-form button.reject { background: #dc3545; }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--border); }
  th { background: #f0f0f0; font-weight: 600; }
  .stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 10px; text-align: center; }
  .stat .num { font-size: 1.5rem; font-weight: bold; color: var(--accent); }
  .stat .label { font-size: .75rem; color: #888; }
  @media (max-width: 600px) { .constraint { flex-direction: column; } }
</style>
</head>
<body>
<h1>文京区障害者福祉 KG レビューUI</h1>
<div class="tabs">
  <button class="active" data-tab="constraints">制約レビュー</button>
  <button data-tab="ontology">オントロジー</button>
  <button data-tab="kg">KG一覧</button>
</div>

<div id="panel-constraints" class="panel active">
  <p style="margin-bottom:12px;color:#888;">金額・年齢閾値・時間上限は原本で確認後「承認」してください。</p>
  <div id="constraints-list"></div>
</div>

<div id="panel-ontology" class="panel">
  <div id="ontology-stats" class="stats"></div>
  <h2>ノードラベル</h2>
  <div id="node-labels"></div>
  <h2>関係タイプ</h2>
  <div id="rel-types"></div>
</div>

<div id="panel-kg" class="panel">
  <h2>全ノード (<span id="node-count"></span>)</h2>
  <table id="kg-table">
    <thead><tr><th>ID</th><th>ラベル</th><th>名称</th><th>カテゴリ</th></tr></thead>
    <tbody></tbody>
  </table>
</div>

<script>
async function loadJSON(url) { const r = await fetch(url); return r.json(); }

// ── Constraints tab ──
async function renderConstraints() {
  const constraints = await loadJSON('/api/constraints');
  const el = document.getElementById('constraints-list');
  el.innerHTML = constraints.map(c => {
    const statusClass = `status-${c.status}`;
    return `<div class="constraint">
      <div class="id">${c.id}</div>
      <div class="desc">
        <div>${c.description}</div>
        <div class="src">出典: ${c.src} | 要審査: ${c.review}</div>
        <div><span class="status ${statusClass}">${c.status}</span></div>
        <div class="review-form">
          <input type="text" placeholder="レビュアー名" id="reviewer-${c.id}" style="width:120px">
          <textarea placeholder="コメント" id="comment-${c.id}" rows="1"></textarea>
          <button onclick="submitReview('${c.id}',true)">✓ 承認</button>
          <button class="reject" onclick="submitReview('${c.id}',false)">✗ 却下</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

async function submitReview(cid, approved) {
  const reviewer = document.getElementById(`reviewer-${cid}`).value || 'anonymous';
  const comment = document.getElementById(`comment-${cid}`).value || '';
  await fetch('/api/review', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ constraint_id: cid, reviewer, comment, approved }),
  });
  renderConstraints();
}

// ── Ontology tab ──
async function renderOntology() {
  const o = await loadJSON('/api/ontology');
  document.getElementById('ontology-stats').innerHTML = [
    {label:'ノード数', num:o.n_nodes},
    {label:'エッジ数', num:o.n_edges},
    {label:'ラベル種別', num:o.node_labels.length},
    {label:'関係種別', num:o.relationship_types.length},
  ].map(s => `<div class="stat"><div class="num">${s.num}</div><div class="label">${s.label}</div></div>`).join('');
  document.getElementById('node-labels').innerHTML =
    Object.entries(o.node_count_by_label).map(([l,n]) => `<span class="status status-approved" style="margin:2px;display:inline-block">${l}: ${n}</span>`).join(' ');
  document.getElementById('rel-types').innerHTML =
    o.relationship_types.map(r => `<span class="status status-pending" style="margin:2px;display:inline-block">${r}</span>`).join(' ');
}

// ── KG tab ──
async function renderKG() {
  const kg = await loadJSON('/api/kg');
  document.getElementById('node-count').textContent = kg.nodes.length;
  const tbody = document.querySelector('#kg-table tbody');
  tbody.innerHTML = kg.nodes.map(n => {
    const labels = n.labels.join(', ');
    const name = n.props.name || n.props.dept || n.id;
    const cat = n.props.code || (n.labels.includes('Service') ? (n.props.income_limit ? '所得制限有' : '') : '');
    return `<tr><td>${n.id}</td><td>${labels}</td><td>${name}</td><td>${cat}</td></tr>`;
  }).join('');
}

// ── Tab switching ──
document.querySelectorAll('.tabs button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add('active');
    if (btn.dataset.tab === 'ontology') renderOntology();
    if (btn.dataset.tab === 'kg') renderKG();
  });
});

renderConstraints();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8789, reload=True)