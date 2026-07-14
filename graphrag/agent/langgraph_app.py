# -*- coding: utf-8 -*-
"""
LangGraph agentic search — Planner/Critic/Answer nodes + vector/graph tools.

Supports both Anthropic Claude and Google Gemini.
Configurable via environment (see config at bottom).

Usage:
    pip install langgraph langchain-anthropic langchain-google-genai
    py -3.14 langgraph_app.py "身体障害者手帳2級で受けられる手当は？"

Architecture (matches spec Phase6):
    ① Planner(LLM) → ② Retrieval(graph/vector) → ③ Critic(LLM)
    → 不足なら②へ戻る(最大2回) → ④ Answer(LLM)

State:
    query: str            — ユーザーの質問
    subgoals: list[dict]  — Planner出力のサブゴール配列
    facts: list[dict]     — 蓄積された根拠
    trace: list[dict]     — 実行トレース
    round: int            — 反復回数カウンタ
    answer: str           — 最終回答
"""

import json, math, os, sys, re
from typing import Annotated, TypedDict
from operator import add

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ── LLM client abstraction ──

class _LLMClient:
    def __init__(self, provider: str):
        self.provider = provider
        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            api_key = self._get_key("ANTHROPIC_API_KEY")
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
            self.client = ChatAnthropic(model=model, api_key=api_key, temperature=0.0)
        elif provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI
            api_key = self._get_key("GEMINI_API_KEY")
            model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
            self.client = ChatGoogleGenerativeAI(model=model, api_key=api_key, temperature=0.0)
        else:
            raise ValueError(f"Unknown provider: {provider}. Use 'anthropic' or 'gemini'.")

    def _get_key(self, name):
        from config import _parse_env_file, _ENV
        env = _parse_env_file(os.path.join(HERE, "..", ".env"))
        v = env.get(name, os.environ.get(name, "")).strip()
        if not v:
            raise RuntimeError(f"{name} is empty. Set it in graphrag/.env or environment.")
        return v

    def chat(self, system: str, user: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage
        r = self.client.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        return r.content.strip()

    def chat_json(self, system: str, user: str) -> dict:
        txt = self.chat(system + "\n\nReturn ONLY valid JSON, no markdown fences.", user)
        m = re.search(r"\{.*\}|\[.*\]", txt, re.S)
        raw = m.group(0) if m else txt
        return json.loads(raw)


# ── Data ──

KG = json.load(open(os.path.join(HERE, "..", "graph", "kg.json"), encoding="utf-8"))
EMB = json.load(open(os.path.join(HERE, "..", "rag", "embeddings.json"), encoding="utf-8"))
N = {n["id"]: n for n in KG["nodes"]}
E = KG["edges"]


def P(i): return N[i]["props"] if i in N else {}
def name(i): return P(i).get("name") or P(i).get("dept") or i
def efrom(i, t=None): return [e for e in E if e["from"] == i and (t is None or e["type"] == t)]
def einto(i, t=None): return [e for e in E if e["to"] == i and (t is None or e["type"] == t)]
def is_service(i): return i in N and "Service" in N[i]["labels"]

CATS = {P(n["id"])["name"]: n["id"] for n in KG["nodes"] if "ServiceCategory" in n["labels"]}
NBS = {P(n["id"])["name"]: n["id"] for n in KG["nodes"] if "Notebook" in n["labels"]}
SVC_NAMES = {P(n["id"])["name"]: n["id"] for n in KG["nodes"] if is_service(n["id"])}


# ── Vector tool (Gemini embeddings) ──

def _cos(a, b):
    d = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return d / (na * nb + 1e-9)


def vector_seed(query: str, k: int = 5, client=None) -> list:
    if client and client.provider == "gemini":
        from gemini_client import embed_texts as ge
        qv = ge([query], task_type="RETRIEVAL_QUERY")[0]
    else:
        qv = _fake_embed(query)
    scored = [(sid, _cos(qv, nd["vec"])) for sid, nd in EMB["nodes"].items()]
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def _fake_embed(text: str) -> list:
    import hashlib
    h = hashlib.md5(text.encode()).digest()
    scale = sum(b for b in h) / 255.0
    return [((b / 255.0) * 2 - 1) * scale for b in h[:128]]


# ── Graph query tools ──

def services_by_notebook_grade(nb, grade, cat=None):
    out = []
    for e in E:
        if e["type"] == "REQUIRES" and e["to"] == nb:
            gs = (e.get("props") or {}).get("grades", [])
            if grade is None or grade == "ALL" or grade in gs:
                if cat and not any(x["to"] == cat for x in efrom(e["from"], "HAS_CATEGORY")):
                    continue
                out.append(e["from"])
    return sorted(set(out))


def by_category(cat):
    return sorted({e["from"] for e in einto(cat, "HAS_CATEGORY")})


def mcc():
    return sorted(n["id"] for n in KG["nodes"] if is_service(n["id"]) and P(n["id"]).get("medical_care_child"))


def mutual(a, b):
    for e in E:
        if e["type"] == "MUTUALLY_EXCLUSIVE_WITH" and (
            (e["from"] == a and e["to"] == b) or (e["from"] == b and e["to"] == a)
        ):
            return e.get("props", {}).get("basis")
    return None


def resolve_service(hint, client=None):
    if not hint:
        return None, None
    for nm, sid in SVC_NAMES.items():
        if hint in nm or nm in hint:
            return sid, "name"
    seed = vector_seed(hint, 1, client)
    return (seed[0][0], f"vector({seed[0][1]:.2f})") if seed else (None, None)


def svc_card(sid):
    p = P(sid)
    return {
        "id": sid,
        "name": p["name"],
        "category": [name(e["to"]) for e in efrom(sid, "HAS_CATEGORY")],
        "targets": [P(e["to"])["code"] for e in efrom(sid, "TARGETS")],
        "requires": [
            f'{P(e["to"])["name"]} {"・".join((e.get("props") or {}).get("grades",[]))}{P(e["to"]).get("grade_type","")}'
            + (f'（{e["props"]["grade_note"]}）' if (e.get("props") or {}).get("grade_note") else "")
            for e in efrom(sid, "REQUIRES")
        ],
        "benefit": p.get("benefit_tiers", []),
        "income_limit": p.get("income_limit"),
        "free_hours_per_month": p.get("free_hours_per_month"),
        "age_range": p.get("age_range"),
        "note": p.get("note"),
        "excludes": p.get("excludes"),
        "admins": [
            {"dept": P(e["to"]).get("dept"), "phone": P(e["to"]).get("phone"),
             "for": (e.get("props") or {}).get("for"), "hours": (e.get("props") or {}).get("hours")}
            for e in efrom(sid, "ADMINISTERED_BY")
        ],
        "defined_by": [
            f'{P(e["to"])["name"]}（原本{P(e["to"]).get("booklet_page")}p）'
            for e in efrom(sid, "DEFINED_BY")
        ],
        "src": p.get("src"),
    }


# ── Prompts ──

PLANNER_SYS = (
    "あなたは障害者福祉サービス検索エージェントのプランナー。ユーザーの質問を、ナレッジグラフを引くための"
    "サブゴール配列に分解する。kindは次から選ぶ: eligible(手帳と等級で受けられるサービス列挙), "
    "service_detail(特定サービスの詳細), contact(窓口), exclusion(受けられない条件), compat(2制度の併給可否), "
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
    "extra_subgoalのkindは必ず次のいずれか: eligible/service_detail/contact/exclusion/compat/free_hours/mcc/category/abuse。"
    "ユーザーの属性(年齢等)が不明なだけで、グラフから追加で引けるものが無い場合は ok:true とし extra_subgoal:null にする。"
    'JSON形式: {"ok":true/false,"missing":"不足の説明or空","extra_subgoal":null or {"kind":"...","notebook":null,"grade":null,"category":null,"service_hint":null}}'
)

ANSWER_SYS = (
    "あなたは文京区の障害者福祉の案内担当。与えられた【根拠】だけを使って、日本語で簡潔に答える。"
    "重要ルール: (1)根拠に無い金額・等級・電話番号を創作しない。(2)金額や窓口(電話)、根拠(wikiページ/原本ページ)を含める。"
    "(3)金額・時間上限・等級には『※令和7年5月末時点、要確認』を一言添える。(4)根拠が不足なら『分かりません/窓口にご確認を』と述べる。"
    "(5)Markdownの見出しや箇条書きで読みやすく。"
)


# ── State ──

class AgentState(TypedDict):
    query: str
    subgoals: list
    facts: Annotated[list, add]
    trace: Annotated[list, add]
    round: int
    answer: str


# ── Nodes ──

def planner_node(state: AgentState, llm) -> dict:
    query = state["query"]
    try:
        plan = llm.chat_json(PLANNER_SYS, f"質問: {query}")
        subgoals = plan.get("subgoals") or []
    except Exception as ex:
        subgoals = [{"kind": "service_detail", "service_hint": query}]
        trace = [{"node": "Planner", "txt": f"(fallback) {ex}"}]
        return {"subgoals": subgoals, "trace": [trace]}

    if not subgoals:
        subgoals = [{"kind": "service_detail", "service_hint": query}]

    for s in subgoals:
        s.setdefault("_query", query)

    kinds = ", ".join(s.get("kind", "?") for s in subgoals)
    return {
        "subgoals": subgoals,
        "trace": [{"node": "Planner(LLM)", "txt": f"{len(subgoals)} subgoals: {kinds}"}],
    }


def retrieve_node(state: AgentState, llm) -> dict:
    subgoals = state.get("subgoals") or []
    facts = []
    traces = []
    for sg in subgoals:
        kind = sg.get("kind")
        nb = NBS.get(sg.get("notebook")) if sg.get("notebook") else None
        cat = CATS.get(sg.get("category")) if sg.get("category") else None
        grade = sg.get("grade")
        used = ""

        if kind == "eligible" and nb:
            ids = services_by_notebook_grade(nb, grade, cat)
            used = "graph:REQUIRES"
            facts.extend(svc_card(i) for i in ids)
        elif kind == "category" and cat:
            facts.extend(svc_card(i) for i in by_category(cat))
            used = "graph:HAS_CATEGORY"
        elif kind == "mcc":
            facts.extend(svc_card(i) for i in mcc())
            used = "graph:medical_care_child"
        elif kind == "compat":
            a, _ = resolve_service(sg.get("service_hint") or "", llm)
            b, _ = resolve_service(sg.get("service_hint2") or "", llm)
            basis = mutual(a, b) if a and b else None
            facts.append({
                "compat": True, "a": name(a), "b": name(b),
                "exclusive": bool(basis), "basis": basis,
            })
            used = "graph:MUTUALLY_EXCLUSIVE_WITH"
        elif kind == "abuse":
            facts.append(svc_card("svc_gyakutai"))
            used = "graph:neighbors"
        elif kind in ("service_detail", "contact", "exclusion", "free_hours"):
            sid, how = resolve_service(
                sg.get("service_hint") or sg.get("_query") or "", llm
            )
            if how and how.startswith("vector"):
                traces.append({
                    "node": "VectorSeed",
                    "txt": f'"{sg.get("service_hint")}" → {name(sid)} [{how}]',
                })
            if sid:
                facts.append(svc_card(sid))
                used = "graph:neighbors"
        else:
            used = "skip(unknown kind)"

        traces.append({
            "node": "Retrieval",
            "txt": f'{kind} [{used}] fetched {len(facts)}',
            "touched": [f.get("name") for f in facts if f.get("name")][:8],
        })

    return {"facts": facts, "trace": traces}


def critic_node(state: AgentState, llm) -> dict:
    query = state["query"]
    facts = state.get("facts") or []
    try:
        crit = llm.chat_json(
            CRITIC_SYS,
            f"質問: {query}\n根拠: {json.dumps(facts, ensure_ascii=False)[:6000]}",
        )
    except Exception:
        crit = {"ok": True}

    txt = "sufficient ✓" if crit.get("ok") else f'insufficient ✗ — {crit.get("missing","")}'
    return {
        "trace": [{"node": "Critic(LLM)", "txt": txt}],
        "subgoals": [crit["extra_subgoal"]] if crit.get("extra_subgoal") else [],
        "round": (state.get("round") or 0) + 1,
    }


def answer_node(state: AgentState, llm) -> dict:
    query = state["query"]
    facts = state.get("facts") or []
    ctx = json.dumps(facts, ensure_ascii=False)[:9000]
    try:
        answer = llm.chat(ANSWER_SYS, f"質問: {query}\n\n【根拠】\n{ctx}")
    except Exception as ex:
        answer = f"(answer generation failed: {ex})"
    return {
        "answer": answer,
        "trace": [{"node": "Answer(LLM)", "txt": f"generated from {len(facts)} facts"}],
    }


# ── Graph builder ──

def should_continue(state: AgentState) -> str:
    max_rounds = 2
    if state.get("subgoals") and (state.get("round") or 0) < max_rounds:
        return "retrieve"
    return "answer"


def build_graph(llm):
    from langgraph.graph import END, StateGraph

    builder = StateGraph(AgentState)

    builder.add_node("planner", lambda s: planner_node(s, llm))
    builder.add_node("retrieve", lambda s: retrieve_node(s, llm))
    builder.add_node("critic", lambda s: critic_node(s, llm))
    builder.add_node("answer", lambda s: answer_node(s, llm))

    builder.set_entry_point("planner")
    builder.add_edge("planner", "retrieve")
    builder.add_edge("retrieve", "critic")
    builder.add_conditional_edges("critic", should_continue, {"retrieve": "retrieve", "answer": "answer"})
    builder.add_edge("answer", END)

    return builder.compile()


# ── CLI entry point ──

def run(query: str, provider: str = "anthropic") -> dict:
    llm = _LLMClient(provider)
    graph = build_graph(llm)
    result = graph.invoke({
        "query": query,
        "subgoals": [],
        "facts": [],
        "trace": [{"node": "Query", "txt": query}],
        "round": 0,
        "answer": "",
    })
    return {
        "answer": result.get("answer", ""),
        "trace": result.get("trace", []),
        "n_facts": len(result.get("facts", [])),
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="LangGraph agentic search")
    p.add_argument("query", nargs="?", default="身体障害者手帳2級で受けられる手当は？")
    p.add_argument("--provider", choices=["anthropic", "gemini"], default="anthropic",
                   help="LLM provider (default: anthropic). Set ANTHROPIC_API_KEY or GEMINI_API_KEY in env/.env")
    args = p.parse_args()

    res = run(args.query, args.provider)
    out = [f"Q: {args.query}", "", "TRACE:"]
    for t in res["trace"]:
        out.append(f'  [{t["node"]}] {t.get("txt","")}')
    out += ["", "ANSWER:", res.get("answer", "")]
    report = os.path.join(HERE, "langgraph_last_output.txt")
    open(report, "w", encoding="utf-8").write("\n".join(out))
    print(f"n_facts={res.get('n_facts')} trace_steps={len(res.get('trace',[]))} -> {report}")