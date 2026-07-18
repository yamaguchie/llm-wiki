# -*- coding: utf-8 -*-
"""Phase6 (ⓑ) + Phase5 (ⓐ): Gemini-backed Agentic Search over the knowledge graph.

反復ループ:
  ①Planner(LLM)   … 質問をサブゴールに分解（構造化）
  ②Retrieval      … 各サブゴールをグラフ探索。サービス特定は Gemini埋め込み(ベクトル入口)で解決
  ③Critic(LLM)    … 集めた根拠でCQに答えられるか自己評価。不足なら追加サブゴールを提案
  ④(不足なら②へ、最大2回) → ⑤Answer(LLM) … 根拠のみから接地した回答を生成

run(query) -> {"answer": str, "trace": [ ... ]}
CLI: py -3.14 agent.py "質問"
"""
import json, os, sys, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ── アクティブ制約の読み込み ──
CONSTRAINTS_PATH = os.path.join(HERE, "active_constraints.json")
_ACTIVE_CONSTRAINTS = []
if os.path.isfile(CONSTRAINTS_PATH):
    try:
        import json
        _ACTIVE_CONSTRAINTS = json.load(open(CONSTRAINTS_PATH, encoding="utf-8")).get("constraints", [])
    except: pass
import gemini_client as gc
import config

# ---------- data ----------
KG = json.load(open(os.path.join(HERE, "..", "step3_kg", "data", "kg.json"), encoding="utf-8"))
try:
    EMB = json.load(open(os.path.join(HERE, "..", "rag", "embeddings.json"), encoding="utf-8"))
except Exception:
    EMB = {}
N = {n["id"]: n for n in KG["nodes"]}
E = KG["edges"]
def P(i): return N[i]["props"] if i in N else {}
def name(i): return P(i).get("name") or P(i).get("dept") or i
def efrom(i, t=None): return [e for e in E if e["from"]==i and (t is None or e["type"]==t)]
def einto(i, t=None): return [e for e in E if e["to"]==i and (t is None or e["type"]==t)]
def is_service(i): return i in N and "Service" in N[i]["labels"]

CATS = {P(n["id"])["name"]: n["id"] for n in KG["nodes"] if "ServiceCategory" in n["labels"]}
NBS  = {P(n["id"])["name"]: n["id"] for n in KG["nodes"] if "Notebook" in n["labels"]}
SVC_NAMES = {P(n["id"])["name"]: n["id"] for n in KG["nodes"] if is_service(n["id"])}

# ---------- vector entry (Gemini embeddings) ----------
def _cos(a, b):
    d = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
    return d/(na*nb+1e-9)
def vector_seed(query, k=5):
    qv = gc.embed_texts([query], task_type="RETRIEVAL_QUERY")[0]
    scored = [(sid, _cos(qv, nd["vec"])) for sid, nd in EMB["nodes"].items()]
    scored.sort(key=lambda x: -x[1])
    return scored[:k]

# ---------- graph queries ----------
def services_by_notebook_grade(nb, grade, cat=None):
    out = []
    for e in E:
        if e["type"]=="REQUIRES" and e["to"]==nb:
            gs = (e.get("props") or {}).get("grades", [])
            if grade is None or grade=="ALL" or grade in gs:
                if cat and not any(x["to"]==cat for x in efrom(e["from"], "HAS_CATEGORY")):
                    continue
                out.append(e["from"])
    return sorted(set(out))
def by_category(cat): return sorted({e["from"] for e in einto(cat, "HAS_CATEGORY")})
def mcc(): return sorted(n["id"] for n in KG["nodes"] if is_service(n["id"]) and P(n["id"]).get("medical_care_child"))
def mutual(a, b):
    for e in E:
        if e["type"]=="MUTUALLY_EXCLUSIVE_WITH" and ((e["from"]==a and e["to"]==b) or (e["from"]==b and e["to"]==a)):
            return e.get("props", {}).get("basis")
    return None

def resolve_service(hint):
    if not hint: return None, None
    for nm, sid in SVC_NAMES.items():
        if hint in nm or nm in hint: return sid, "name"
    seed = vector_seed(hint, 1)
    return (seed[0][0], f"vector({seed[0][1]:.2f})") if seed else (None, None)

def svc_card(sid):
    p = P(sid)
    return {
        "id": sid, "name": p["name"],
        "category": [name(e["to"]) for e in efrom(sid, "HAS_CATEGORY")],
        "targets": [P(e["to"])["code"] for e in efrom(sid, "TARGETS")],
        "requires": [f'{P(e["to"])["name"]} {"・".join((e.get("props") or {}).get("grades",[]))}{P(e["to"]).get("grade_type","")}'
                     + (f'（{e["props"]["grade_note"]}）' if (e.get("props") or {}).get("grade_note") else "")
                     for e in efrom(sid, "REQUIRES")],
        "benefit": p.get("benefit_tiers", []),
        "income_limit": p.get("income_limit"),
        "free_hours_per_month": p.get("free_hours_per_month"),
        "age_range": p.get("age_range"),
        "note": p.get("note"),
        "excludes": p.get("excludes"),
        "admins": [{"dept": P(e["to"]).get("dept"), "phone": P(e["to"]).get("phone"),
                    "for": (e.get("props") or {}).get("for"), "hours": (e.get("props") or {}).get("hours")}
                   for e in efrom(sid, "ADMINISTERED_BY")],
        "defined_by": [f'{P(e["to"])["name"]}（原本{P(e["to"]).get("booklet_page")}p）' for e in efrom(sid, "DEFINED_BY")],
        "src": p.get("src"),
    }

# ---------- retrieval per subgoal ----------
def retrieve(sg, trace, step=None):
    kind = sg.get("kind"); facts = []; used = ""; touched = []
    step = step or [0]
    nb = NBS.get(sg.get("notebook")) if sg.get("notebook") else None
    cat = CATS.get(sg.get("category")) if sg.get("category") else None
    grade = sg.get("grade")
    if kind == "eligible" and nb:
        ids = services_by_notebook_grade(nb, grade, cat); used = "graph:REQUIRES"
        facts = [svc_card(i) for i in ids]; touched = list(ids)
    elif kind == "category" and cat:
        facts = [svc_card(i) for i in by_category(cat)]; used = "graph:HAS_CATEGORY"
        touched = by_category(cat)
    elif kind == "mcc":
        ids = mcc(); facts = [svc_card(i) for i in ids]; used = "graph:medical_care_child"
        touched = list(ids)
    elif kind == "compat":
        a, _ = resolve_service(sg.get("service_hint") or "");
        b, _ = resolve_service(sg.get("service_hint2") or "")
        basis = mutual(a, b) if a and b else None
        facts = [{"compat": True, "a": name(a), "b": name(b), "exclusive": bool(basis), "basis": basis}]
        used = "graph:MUTUALLY_EXCLUSIVE_WITH"; touched = [a, b] if a and b else []
    elif kind == "abuse":
        sid = "svc_gyakutai"; facts = [svc_card(sid)]; used = "graph:近傍"
        touched = [sid]
    elif kind in ("service_detail", "constraint", "free_hours"):
        sid, how = resolve_service(sg.get("service_hint") or sg.get("_query") or "")
        if how and how.startswith("vector"):
            trace.append({"node": "VectorSeed(Gemini埋め込み)", "step": step[0], "txt": f'"{sg.get("service_hint")}" → {name(sid)} [{how}]'})
        if sid:
            svc = svc_card(sid)
            neis = [e["to"] for e in efrom(sid)] + [e["from"] for e in einto(sid)]
            facts = [svc] + [{"t": "neighbor", "id": nid} for nid in neis]
            touched = [sid] + neis
            used = f"graph:近傍展開({len(neis)}エンティティ)"
    elif kind == "contact":
        sid, how = resolve_service(sg.get("service_hint") or sg.get("_query") or "")
        if how and how.startswith("vector"):
            trace.append({"node": "VectorSeed(Gemini埋め込み)", "step": step[0], "txt": f'"{sg.get("service_hint")}" → {name(sid)} [{how}]'})
        if sid:
            svc = svc_card(sid)
            admins = [e["to"] for e in efrom(sid, "ADMINISTERED_BY")]
            if sg.get("for"):
                admins = [e["to"] for e in efrom(sid, "ADMINISTERED_BY") if (e.get("props") or {}).get("for", "").find(sg["for"]) >= 0]
            refs = [e["to"] for e in efrom(sid, "DEFINED_BY")]
            facts = [svc, {"t": "contact", "ids": admins}, {"t": "reference", "ids": refs}]
            touched = [sid] + admins + refs
            used = f"graph:ADMINISTERED_BY({len(admins)}窓口)+DEFINED_BY({len(refs)}参考)"
    else:
        used = "skip(未知のkind)"
    trace.append({"node": "Retrieval", "step": step[0], "txt": f'{kind} [{used}] 取得{len(facts)}件',
                  "touched": touched[:16]})
    return facts

# ---------- LLM prompts ----------
PLANNER_SYS = (
 "あなたはサービス検索エージェントのプランナー。ユーザーの質問を、ナレッジグラフを引くための"
 "サブゴール配列に分解する。kindは次から選ぶ: eligible(手帳と等級で受けられるサービス列挙), "
 "service_detail(特定サービスの詳細), contact(窓口), constraint(条件/制限/併給可否の確認), "
 "free_hours(月何時間無料), mcc(医療的ケア児向け一覧), category(カテゴリ一覧), abuse(虐待通報先). "
 "複合質問(例:手当と医療の両方)は複数サブゴールに分解する。"
 f"利用可能カテゴリ: {list(CATS.keys())}。手帳: {list(NBS.keys())}。"
 'JSON形式: {"subgoals":[{"kind":"...","notebook":null,"grade":null,"category":null,'
 '"service_hint":null,"service_hint2":null,"for":null}]}。'
 "notebook/categoryは上記の名称と完全一致で。等級は数字文字列。service_hintは自由記述可。"
)
CRITIC_SYS = (
 "あなたは根拠の十分性を判定するクリティック。ユーザーの質問と、取得済みの根拠(JSON)を見て、"
 "回答に十分か判定する。不足なら追加で引くべきサブゴールを1つ提案する。"
 "extra_subgoalのkindは必ず次のいずれか: eligible/service_detail/contact/constraint/free_hours/mcc/category/abuse。"
 "ユーザーの属性(年齢等)が不明なだけで、グラフから追加で引けるものが無い場合は ok:true とし extra_subgoal:null にする。"
 'JSON形式: {"ok":true/false,"missing":"不足の説明or空","extra_subgoal":null or {"kind":"...","notebook":null,"grade":null,"category":null,"service_hint":null}}'
)
ANSWER_SYS = (
    f"あなたは、{config.get_kb_label()}の案内アシスタントです。与えられた【根拠】だけを使って、日本語で簡潔に答える。"
    "重要ルール: (1)根拠に無い金額・等級・電話番号を創作しない。(2)金額や窓口(電話)、根拠(wikiページ/原本ページ)を含める。"
    "(3)金額・時間上限・等級には『※令和7年5月末時点、要確認』を一言添える。(4)根拠が不足なら『分かりません/窓口にご確認を』と述べる。"
    "(5)Markdownの見出しや箇条書きで読みやすく。"
    + (("\n\n【オントロジー定義からの制約事項（必ず守ること）】\n"
        + "\n".join(f"- {c['description']}" for c in _ACTIVE_CONSTRAINTS))
       if _ACTIVE_CONSTRAINTS else "")
)

def run(query, max_rounds=2):
    return _run_impl(query, max_rounds, stream=False)[0]

def run_stream(query, max_rounds=2):
    return _run_impl(query, max_rounds, stream=True)

def _run_impl(query, max_rounds=2, stream=False):
    trace = [{"node": "Query", "txt": query, "step": 0}]
    step = [0]
    if stream: yield {"type": "trace", "data": trace[-1]}
    # ① Planner
    try:
        plan = gc.chat_json(PLANNER_SYS, f"質問: {query}")
        subgoals = plan.get("subgoals") or []
    except Exception as ex:
        subgoals = [{"kind": "service_detail", "service_hint": query}]
        trace.append({"node": "Planner", "step": step[0], "txt": f"(フォールバック) {ex}"})
        if stream: yield {"type": "trace", "data": trace[-1]}
    if not subgoals:
        subgoals = [{"kind": "service_detail", "service_hint": query}]
    step[0] += 1
    trace.append({"node": "Planner(LLM)", "step": step[0], "txt": f"{len(subgoals)}サブゴール: " +
                  ", ".join(s.get("kind","?") for s in subgoals)})
    if stream: yield {"type": "trace", "data": trace[-1]}
    for s in subgoals: s.setdefault("_query", query)

    # ② Retrieval
    facts = []
    for sg in subgoals:
        step[0] += 1
        facts.extend(retrieve(sg, trace, step))
        if stream: yield {"type": "trace", "data": trace[-1]}

    # ③ Critic → ④ 追加検索ループ
    for r in range(max_rounds):
        try:
            crit = gc.chat_json(CRITIC_SYS, f"質問: {query}\n根拠: {json.dumps(facts, ensure_ascii=False)[:6000]}")
        except Exception:
            crit = {"ok": True}
        step[0] += 1
        trace.append({"node": "Critic(LLM)", "step": step[0], "txt": ("十分 ✓" if crit.get("ok") else f'不十分 ✗ — {crit.get("missing","")}')})
        if stream: yield {"type": "trace", "data": trace[-1]}
        if crit.get("ok") or not crit.get("extra_subgoal"):
            break
        eg = crit["extra_subgoal"]; eg.setdefault("_query", query)
        step[0] += 1
        trace.append({"node": "Rewrite→Retrieval", "step": step[0], "txt": f'追加: {eg.get("kind")}'})
        if stream: yield {"type": "trace", "data": trace[-1]}
        step[0] += 1
        facts.extend(retrieve(eg, trace, step))
        if stream: yield {"type": "trace", "data": trace[-1]}

    # ⑤ Answer
    ctx = json.dumps(facts, ensure_ascii=False)[:9000]
    try:
        answer = gc.chat(ANSWER_SYS, f"質問: {query}\n\n【根拠】\n{ctx}")
    except Exception as ex:
        answer = f"(回答生成に失敗: {ex})"
    step[0] += 1
    trace.append({"node": "Answer(LLM)", "step": step[0], "txt": f"{len(facts)}件の根拠から生成"})
    if stream: yield {"type": "answer", "data": answer, "trace": trace}
    return {"answer": answer, "trace": trace, "n_facts": len(facts)}

if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "（質問文を引数で指定してください）"
    res = run(q)
    out = ["Q: " + q, "", "TRACE:"]
    for t in res["trace"]:
        out.append(f'  [{t["node"]}] {t["txt"]}')
    out += ["", "ANSWER:", res["answer"]]
    open(os.path.join(HERE, "agent_last_output.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("n_facts=", res["n_facts"], "trace_steps=", len(res["trace"]), "-> agent_last_output.txt")
