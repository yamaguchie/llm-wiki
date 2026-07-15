# -*- coding: utf-8 -*-
"""KG/Neo4j ユーティリティ — ナレッジグラフ操作・Cypher生成・Neo4j連携"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))

def load_kg():
    path = os.path.join(HERE, "..", "graph", "kg.json")
    return json.load(open(path, encoding="utf-8"))

def _sanitize_label(s):
    return re.sub(r'[^a-zA-Z0-9_]', '', s.replace(' ', '_'))

def _flatten_props(props):
    return {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in props.items()}

def _cy_scalar(v):
    if v is None: return "null"
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, (int, float)): return str(v)
    if isinstance(v, (list, dict)): return json.dumps(v, ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False)

def _cy_map(d):
    return "{" + ", ".join(f"{k}: {_cy_scalar(v)}" for k, v in d.items()) + "}"

def build_cypher(kg):
    lines = []
    for n in kg["nodes"]:
        labels = ":".join(n["labels"])
        props = _flatten_props(n["props"])
        lines.append(f"MERGE (n:{labels} {{id: {_cy_scalar(n['id'])}}}) SET n += {_cy_map(props)};")
    for e in kg["edges"]:
        from_ids = e["from"].split(",")
        for fid in from_ids:
            props = e.get("props", {})
            pstr = f" {_cy_map(props)}" if props else ""
            lines.append(f"MATCH (a {{id: {_cy_scalar(fid.strip())}}}), (b {{id: {_cy_scalar(e['to'])}}}) CREATE (a)-[:{e['type']}{pstr}]->(b);")
    return "\n".join(lines)

def push_to_neo4j(kg):
    from neo4j import GraphDatabase
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "password123")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session(database="neo4j") as session:
        for n in kg["nodes"]:
            labels = ":".join(n["labels"])
            props = _flatten_props(n["props"])
            session.run(f"MERGE (n:{labels} {{id: $id}}) SET n += $props", id=n["id"], props=props)
        for e in kg["edges"]:
            session.run(
                f"MATCH (a {{id: $fid}}), (b {{id: $tid}}) MERGE (a)-[:{e['type']}]->(b)",
                fid=e["from"], tid=e["to"]
            )
    driver.close()

def _node_name(n):
    return n["props"].get("name") or n["props"].get("dept") or n["id"]

def _kg_indexes(kg):
    by_id = {n["id"]: n for n in kg["nodes"]}
    adj = {}
    for e in kg["edges"]:
        adj.setdefault(e["from"], []).append(e)
        adj.setdefault(e["to"], []).append(e)
    labels = sorted(set(l for n in kg["nodes"] for l in n["labels"]))
    rel_types = sorted(set(e["type"] for e in kg["edges"]))
    return by_id, adj, labels, rel_types