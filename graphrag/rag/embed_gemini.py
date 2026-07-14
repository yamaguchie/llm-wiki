# -*- coding: utf-8 -*-
"""Phase5 (ⓐ): 各Serviceノードを Gemini 神経埋め込みでベクトル化。
=> 文字n-gram TF-IDF では橋渡しできない同義語(叩く↔たたく 等)も意味的に近づく。
出力: rag/embeddings.json  {model, dim, nodes:{id:{name, vec:[...]}}}
実行: py -3.14 embed_gemini.py
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent"))
import gemini_client as gc
from config import get_embed_model

kg = json.load(open(os.path.join(HERE, "..", "graph", "kg.json"), encoding="utf-8"))
N = {n["id"]: n for n in kg["nodes"]}
def name(nid): return N[nid]["props"].get("name") or N[nid]["props"].get("dept") or nid
def efrom(nid, t): return [e for e in kg["edges"] if e["from"]==nid and e["type"]==t]

def searchable_text(nid):
    p = N[nid]["props"]; parts = [p.get("name",""), p.get("desc",""), p.get("note","")]
    for t in p.get("benefit_tiers", []): parts.append(t.get("criteria",""))
    if p.get("age_range"): parts.append(p["age_range"])
    for e in efrom(nid, "HAS_CATEGORY"): parts.append(name(e["to"]))
    for e in efrom(nid, "TARGETS"): parts.append(N[e["to"]]["props"].get("name",""))
    for e in efrom(nid, "ADMINISTERED_BY"): parts.append(N[e["to"]]["props"].get("dept",""))
    return " ".join([x for x in parts if x])

svc_ids = [n["id"] for n in kg["nodes"] if "Service" in n["labels"]]
texts = [searchable_text(s) for s in svc_ids]

# バッチ埋め込み（安全のため小さめのチャンク）
vecs = []
CH = 8
for i in range(0, len(texts), CH):
    vecs.extend(gc.embed_texts(texts[i:i+CH], task_type="RETRIEVAL_DOCUMENT"))
    print(f"  embedded {min(i+CH,len(texts))}/{len(texts)}")

nodes = {sid: {"name": name(sid), "vec": v} for sid, v in zip(svc_ids, vecs)}
out = {"model": get_embed_model(), "dim": len(vecs[0]), "nodes": nodes}
open(os.path.join(HERE, "embeddings.json"), "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False))
print(f"n={len(svc_ids)} dim={out['dim']} -> embeddings.json")
