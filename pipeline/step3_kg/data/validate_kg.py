# -*- coding: utf-8 -*-
"""
Phase3-2 / Phase4 / Phase5 相当:
  - kg.json をオントロジー制約に対して検証（参照整合性・制約C01-C10）
  - Neo4j 用 kg.cypher を生成
  - competency_questions.yaml の CQ を実グラフに問い合わせる回帰テスト
LLM API / Neo4j を使わずローカルで動く（本番は Neo4j + GRAPH TYPE + LangGraph に置換）。
実行: py -3.14 validate_kg.py
"""
import json, os, sys, io

HERE = os.path.dirname(os.path.abspath(__file__))
KG = os.path.join(HERE, "kg.json")
OUT_CYPHER = os.path.join(HERE, "kg.cypher")
OUT_REPORT = os.path.join(HERE, "validation_report.txt")

R = []  # report lines
def log(s=""): R.append(s)

kg = json.load(open(KG, encoding="utf-8"))
nodes = {n["id"]: n for n in kg["nodes"]}
edges = kg["edges"]

# ---------- 1. 参照整合性 ----------
log("== 1. 参照整合性（全エッジの端点ノードが存在するか） ==")
ref_errors = []
for e in edges:
    if e["from"] not in nodes: ref_errors.append(f"未定義ノード from={e['from']} ({e['type']})")
    if e["to"] not in nodes:   ref_errors.append(f"未定義ノード to={e['to']} ({e['type']})")
log(f"  エッジ数={len(edges)} / 参照エラー={len(ref_errors)}")
for m in ref_errors: log(f"  ✗ {m}")
if not ref_errors: log("  ✓ 参照整合性OK")

# ---------- 2. スキーマ整合（許可された端点ラベルか） ----------
ALLOWED = {
    "HAS_CATEGORY": ("Service","ServiceCategory"),
    "TARGETS": ("Service","TargetCategory"),
    "REQUIRES": ("Service","Notebook"),
    "ADMINISTERED_BY": ("Service","Contact"),
    "DEFINED_BY": ("Service","Reference"),
    "MUTUALLY_EXCLUSIVE_WITH": ("Service","Service"),
    "RELATED_TO": ("Service","Service"),
    "PROVIDED_AT": ("Facility","Service"),
}
def has_label(nid, lab): return lab in nodes[nid]["labels"]
log("\n== 2. スキーマ整合（GRAPH TYPE 端点制約） ==")
schema_errors = []
for e in edges:
    if e["type"] not in ALLOWED:
        schema_errors.append(f"未知の関係型 {e['type']}"); continue
    sl, tl = ALLOWED[e["type"]]
    if e["from"] in nodes and not has_label(e["from"], sl):
        schema_errors.append(f"{e['type']}: from {e['from']} が :{sl} でない")
    if e["to"] in nodes and not has_label(e["to"], tl):
        schema_errors.append(f"{e['type']}: to {e['to']} が :{tl} でない")
log(f"  スキーマ違反={len(schema_errors)}")
for m in schema_errors: log(f"  ✗ {m}")
if not schema_errors: log("  ✓ 端点ラベル制約OK")

# ---------- 3. 値制約 C01-C10（human_required の値を検証） ----------
log("\n== 3. 値制約（ontology.md C01-C10） ==")
def svc(nid): return nodes[nid]["props"]
def tiers(nid): return svc(nid).get("benefit_tiers", [])
def amount_for(nid, kw):
    for t in tiers(nid):
        if kw in t["criteria"]: return t["amount"]
    return None
def edges_from(nid, typ=None):
    return [e for e in edges if e["from"]==nid and (typ is None or e["type"]==typ)]
def has_mutual(a, b):
    return any((e["from"]==a and e["to"]==b) or (e["from"]==b and e["to"]==a)
               for e in edges if e["type"]=="MUTUALLY_EXCLUSIVE_WITH")

checks = []
def check(cid, cond, desc):
    checks.append((cid, bool(cond), desc))

check("C01", amount_for("svc_shinshin_teate","身体手帳1・2級")==15500, "心身福祉手当 身体1・2級/愛1-3度=15,500円")
check("C02", amount_for("svc_shinshin_teate","身体手帳3級")==13500, "心身福祉手当 身体3級/愛4度=13,500円")
check("C03", (amount_for("svc_seishin_teate","精神障害者保健福祉手帳1級")==10000
             and any(e["to"]=="nb_mental" and "1" in e.get("props",{}).get("grades",[]) for e in edges_from("svc_seishin_teate","REQUIRES"))),
      "精神福祉手当=精神1級・10,000円")
check("C04", set(["施設入所者"]).issubset(set(svc("svc_shinshin_teate")["excludes"]))
             and any("65歳以上" in x for x in svc("svc_shinshin_teate")["excludes"]),
      "心身福祉手当 除外条件（65歳以上取得・施設入所 等）")
check("C05", any(e["to"]=="nb_body" for e in edges_from("svc_marusho","REQUIRES"))
             and any(e["to"]=="nb_mental" for e in edges_from("svc_marusho","REQUIRES")),
      "マル障 対象手帳（身体/愛/精神）")
check("C06", svc("svc_ido_shien").get("free_hours_per_month")==36, "移動支援/同行援護=月36時間まで負担なし")
check("C07", any(e["to"]=="ref_nanbyo" for e in edges_from("svc_hosougu","DEFINED_BY")),
      "補装具 対象疾病→難病一覧(199p)")
check("C08", all(any(e["to"]=="ref_income" for e in edges_from(s,"DEFINED_BY"))
                 for s in nodes if "Allowance" in nodes[s]["labels"] and svc(s).get("income_limit")),
      "所得制限ありの手当は全て所得制限限度額表を参照")
check("C09", has_mutual("svc_seishin_teate","svc_shinshin_teate"), "精神福祉手当×心身福祉手当=併給不可")
check("C10", "288" in json.dumps(tiers("svc_respite"), ensure_ascii=False), "在宅レスパイト 年288時間上限")

npass = sum(1 for _,ok,_ in checks if ok)
for cid, ok, desc in checks:
    log(f"  {'✓' if ok else '✗'} {cid}: {desc}")
log(f"  制約充足 {npass}/{len(checks)}")

# ---------- 4. CQ 回帰テスト（グラフに実問い合わせ） ----------
log("\n== 4. CQ 回帰テスト（グラフ探索で回答が得られるか） ==")
def services_by_notebook_grade(nb_id, grade, category=None):
    res = []
    for e in edges:
        if e["type"]=="REQUIRES" and e["to"]==nb_id and grade in e.get("props",{}).get("grades",[]):
            s = e["from"]
            if category:
                cat_ok = any(x["to"]==category for x in edges_from(s,"HAS_CATEGORY"))
                if not cat_ok: continue
            res.append(s)
    return sorted(set(res))

cq = []
def cqtest(cid, ok, detail):
    cq.append((cid, bool(ok), detail))

# CQ01 身体2級で受けられる手当年金
r = services_by_notebook_grade("nb_body","2","cat_allowance")
cqtest("CQ01", "svc_shinshin_teate" in r and "svc_tokubetsu_jido" in r,
       f"身体2級の手当年金 {[svc(x)['name'] for x in r]}")
# CQ02 移動支援 free hours
cqtest("CQ02", svc("svc_ido_shien").get("free_hours_per_month")==36, "月36時間")
# CQ03 補装具(難病)窓口 + 対象疾病
admins = [(e["to"], e["props"].get("for")) for e in edges_from("svc_hosougu","ADMINISTERED_BY")]
nan_contact = [nodes[c]["props"]["phone"] for c,f in admins if f=="難病"]
ref = [nodes[e["to"]]["props"]["booklet_page"] for e in edges_from("svc_hosougu","DEFINED_BY")]
cqtest("CQ03", nan_contact==["03-5803-1847"] and "199" in ref, f"窓口={nan_contact} 対象疾病→{ref}p")
# CQ04 マル障対象手帳
req = {(e["to"], tuple(e.get("props",{}).get("grades",[]))) for e in edges_from("svc_marusho","REQUIRES")}
cqtest("CQ04", ("nb_body",("1","2","3")) in req and svc("svc_marusho")["income_limit"], f"マル障 {req} income_limit={svc('svc_marusho')['income_limit']}")
# CQ05 医療的ケア児サービス集約
mcc = sorted(s for s in nodes if svc(s).get("medical_care_child") if "Service" in nodes[s]["labels"])
cqtest("CQ05", len(mcc)>=4, f"医療的ケア児サービス {[svc(x)['name'] for x in mcc]}")
# CQ06 除外条件
cqtest("CQ06", len(svc("svc_shinshin_teate")["excludes"])>=4, f"{svc('svc_shinshin_teate')['excludes']}")
# CQ07 併給可否
cqtest("CQ07", has_mutual("svc_seishin_teate","svc_shinshin_teate"), "併給不可")
# CQ08 精神1級で受けられる
r8 = services_by_notebook_grade("nb_mental","1")
cqtest("CQ08", "svc_seishin_teate" in r8 and "svc_marusho" in r8, f"精神1級 {[svc(x)['name'] for x in r8]}")
# CQ09 虐待 夜間通報先
night = [nodes[e["to"]]["props"]["phone"] for e in edges_from("svc_gyakutai","ADMINISTERED_BY") if "夜間" in e["props"].get("hours","")]
cqtest("CQ09", night==["03-5940-2903"], f"夜間通報先={night}")
# CQ10 身体2級の交通
r10 = services_by_notebook_grade("nb_body","2","cat_transport")
cqtest("CQ10", "svc_toei_free" in r10 and "svc_taxi_wari" in r10, f"身体2級の交通 {[svc(x)['name'] for x in r10]}")
# CQ11 特児扶養手当 窓口
c11 = [nodes[e["to"]]["props"]["phone"] for e in edges_from("svc_tokubetsu_jido","ADMINISTERED_BY")]
cqtest("CQ11", "03-5803-1288" in c11, f"窓口={c11}")
# CQ12 所得制限ありの手当
inc = sorted(s for s in nodes if "Allowance" in nodes[s]["labels"] and svc(s).get("income_limit"))
cqtest("CQ12", len(inc)>=6, f"所得制限あり手当 {len(inc)}件")

ncq = sum(1 for _,ok,_ in cq if ok)
for cid, ok, detail in cq:
    log(f"  {'✓' if ok else '✗'} {cid}: {detail}")
log(f"  CQ通過 {ncq}/{len(cq)}")

# ---------- 5. Neo4j Cypher 生成（Phase4） ----------
def cyval(v):
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, (int, float)): return str(v)
    if isinstance(v, list): return "[" + ",".join(cyval(x) for x in v) + "]"
    if isinstance(v, dict): return "'" + json.dumps(v, ensure_ascii=False).replace("'", "\\'") + "'"
    return "'" + str(v).replace("\\","\\\\").replace("'", "\\'") + "'"
lines = ["// Generated from kg.json by validate_kg.py — Neo4j MERGE statements (Phase4)"]
for n in kg["nodes"]:
    labs = ":".join(n["labels"])
    props = ", ".join(f"{k}: {cyval(v)}" for k,v in n["props"].items())
    lines.append(f"MERGE (n:{labs} {{id: '{n['id']}'}}) SET n += {{{props}}};")
for e in edges:
    props = e.get("props", {})
    pstr = (" {" + ", ".join(f"{k}: {cyval(v)}" for k,v in props.items()) + "}") if props else ""
    lines.append(f"MATCH (a {{id:'{e['from']}'}}),(b {{id:'{e['to']}'}}) MERGE (a)-[:{e['type']}{pstr}]->(b);")
open(OUT_CYPHER, "w", encoding="utf-8").write("\n".join(lines))
log(f"\n== 5. Neo4j Cypher 生成 ==\n  {OUT_CYPHER} ({len(lines)} statements)")

# ---------- サマリ ----------
ok_all = (not ref_errors and not schema_errors and npass==len(checks) and ncq==len(cq))
log("\n== サマリ ==")
log(f"  参照整合性: {'OK' if not ref_errors else 'NG'}")
log(f"  スキーマ整合: {'OK' if not schema_errors else 'NG'}")
log(f"  値制約: {npass}/{len(checks)}")
log(f"  CQ回帰: {ncq}/{len(cq)}")
log(f"  判定: {'✅ ALL PASS' if ok_all else '⚠ 要修正'}")

open(OUT_REPORT, "w", encoding="utf-8").write("\n".join(R))
# コンソールは cp932 で日本語が化けるため ASCII サマリのみ出力
print(f"nodes={len(nodes)} edges={len(edges)} constraints={npass}/{len(checks)} CQ={ncq}/{len(cq)} allpass={ok_all}")
print(f"report -> {OUT_REPORT}")
print(f"cypher -> {OUT_CYPHER}")
