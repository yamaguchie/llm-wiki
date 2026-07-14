# -*- coding: utf-8 -*-
"""Phase5: ノード埋め込みパイプライン（オフライン版）
外部埋め込みAPIを使わず、文字 2/3-gram の TF-IDF ベクトルで各 Service ノードを埋め込む。
=> ベクトル入口（意味検索）で「言い換え・同義語・口語」からでも起点ノードに着地できる。
出力: rag/vectors.json （chat/build_chat.py が読み込みインライン化する）
実行: py -3.14 build_vectors.py
"""
import json, os, math, re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
kg = json.load(open(os.path.join(HERE, "..", "graph", "kg.json"), encoding="utf-8"))
N = {n["id"]: n for n in kg["nodes"]}

# JS の norm() と一致させる（全角/漢数字→半角）
ZEN = "０１２３４５６７８９"; KAN = "〇一二三四五六七八九"
def norm(s):
    for i in range(10):
        s = s.replace(ZEN[i], str(i)).replace(KAN[i], str(i))
    return s
def ngrams(s):
    s = re.sub(r"\s+", "", norm(s))
    out = []
    for n in (2, 3):
        for i in range(len(s) - n + 1):
            out.append(s[i:i+n])
    return out

def name(nid): return N[nid]["props"].get("name") or N[nid]["props"].get("dept") or nid
def edges_from(nid, t): return [e for e in kg["edges"] if e["from"]==nid and e["type"]==t]

# --- 各 Service ノードの検索対象テキスト（name+desc+給付内容+注記+カテゴリ+対象+窓口） ---
def searchable_text(nid):
    p = N[nid]["props"]; parts = [p.get("name",""), p.get("desc",""), p.get("note","")]
    for t in p.get("benefit_tiers", []): parts.append(t.get("criteria",""))
    if p.get("age_range"): parts.append(p["age_range"])
    for e in edges_from(nid, "HAS_CATEGORY"): parts.append(name(e["to"]))
    for e in edges_from(nid, "TARGETS"):      parts.append(N[e["to"]]["props"].get("name",""))
    for e in edges_from(nid, "ADMINISTERED_BY"): parts.append(N[e["to"]]["props"].get("dept",""))
    return " ".join(parts)

svc_ids = [n["id"] for n in kg["nodes"] if "Service" in n["labels"]]
docs = {sid: ngrams(searchable_text(sid)) for sid in svc_ids}
Nn = len(docs)

# --- df / idf ---
df = Counter()
for toks in docs.values():
    for g in set(toks): df[g] += 1
idf = {g: math.log((Nn + 1) / (c + 1)) + 1.0 for g, c in df.items()}

# --- 各ノードの tf-idf ベクトル + L2ノルム ---
nodes_out = {}
for sid, toks in docs.items():
    tf = Counter(toks)
    vec = {g: tf[g] * idf[g] for g in tf}
    nrm = math.sqrt(sum(w*w for w in vec.values())) or 1e-9
    nodes_out[sid] = {"name": name(sid), "norm": nrm, "vec": vec}

out = {"meta": {"method": "char-2/3-gram TF-IDF (offline embedding)", "n_docs": Nn, "vocab": len(idf)},
       "idf": idf, "nodes": nodes_out}
open(os.path.join(HERE, "vectors.json"), "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False))
print(f"docs={Nn} vocab={len(idf)} -> vectors.json")

# --- 簡易確認: パラフレーズ質問での上位ヒット（keyword辞書に無い言い方） ---
def query_top(q, k=3):
    qtf = Counter(ngrams(q)); qv = {}; qn = 0.0
    for g, c in qtf.items():
        if g in idf:
            w = c * idf[g]; qv[g] = w; qn += w*w
    qn = math.sqrt(qn) or 1e-9
    scored = []
    for sid, nd in nodes_out.items():
        dot = sum(qv[g]*nd["vec"].get(g,0.0) for g in qv)
        scored.append((sid, dot/(qn*nd["norm"])))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]

tests = ["車いすの部品を作り直す費用の補助はどこ",
         "うつ病で通院してるけど医療費の助成ある",
         "家で叩かれてる人を見た どこに言えばいい",
         "外出のとき付き添ってくれるヘルパー",
         "ガソリン代の補助"]
lines = ["\n=== ベクトル入口テスト（keyword辞書に無い言い方） ==="]
for q in tests:
    top = query_top(q)
    lines.append(f"Q: {q}")
    for sid, sc in top:
        lines.append(f"   {sc:.3f}  {name(sid)}")
open(os.path.join(HERE, "vector_smoketest.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("smoketest -> vector_smoketest.txt")
