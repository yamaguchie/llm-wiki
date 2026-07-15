# -*- coding: utf-8 -*-
"""Load kg.cypher into Neo4j via Bolt, then verify schema constraints.

Usage:
    py -3.14 load_kg.py                  # default: bolt://localhost:7687, neo4j/password123
    py -3.14 load_kg.py --uri bolt://... --user neo4j --password secret

Requires: pip install neo4j

Flow:
  1. Connect to Neo4j via Bolt.
  2. Execute kg.cypher statements in a single transaction.
  3. Run schema DDL (constraints + indexes) from create_graph_type.cypher.
  4. Verify constraints with intentional violations.
  5. Generate report.
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
CYPHER_FILE = os.path.join(HERE, "..", "graph", "kg.cypher")
DDL_FILE = os.path.join(HERE, "create_graph_type.cypher")
GENERATED_CONSTRAINTS_FILE = os.path.join(HERE, "generated_constraints.cypher")
REPORT_FILE = os.path.join(HERE, "load_report.txt")

R = []


def parse_args():
    p = argparse.ArgumentParser(description="Load kg.cypher into Neo4j")
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", default="password123")
    p.add_argument("--cypher", default=CYPHER_FILE)
    p.add_argument("--ddl", default=DDL_FILE)
    return p.parse_args()


def run_stmts(driver, stmts, label):
    """Execute a list of semicolon-separated statements, consuming results."""
    with driver.session(database="neo4j") as session:
        for stmt in stmts:
            if not stmt.strip():
                continue
            try:
                result = session.run(stmt.strip())
                result.consume()  # Force execution to surface errors
            except Exception as ex:
                R.append(f"  ! {label}: {ex}")
                return False
    return True


def load_kg(driver, cypher_path):
    with open(cypher_path, encoding="utf-8") as f:
        content = f.read()
    stmts = [s.strip() for s in content.split(";") if s.strip()]
    R.append(f"kg.cypher statements: {len(stmts)}")

    with driver.session(database="neo4j") as session:
        for i, stmt in enumerate(stmts):
            try:
                session.run(stmt)
            except Exception as ex:
                R.append(f"  ✗ Statement #{i + 1}: {ex}")
                return False
    R.append("  ✓ All statements executed successfully")
    return True


def load_ddl(driver, ddl_path):
    with open(ddl_path, encoding="utf-8") as f:
        content = f.read()
    # Strip block comments (/* ... */)
    import re
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    # Split by semicolons and filter
    raw_stmts = content.split(";")
    stmts = []
    for rs in raw_stmts:
        lines = [l.strip() for l in rs.strip().split("\n")]
        # Remove comment-only lines
        clean = "\n".join(l for l in lines if not l.startswith("//"))
        clean = clean.strip()
        if clean:
            stmts.append(clean)

    if not stmts:
        R.append("  (No runnable DDL statements — GRAPH TYPE is commented out as reference)")
        return True

    R.append(f"DDL statements: {len(stmts)}")
    ok = run_stmts(driver, stmts, "DDL")
    if ok:
        R.append("  ✓ DDL applied")
    return ok


def verify_write_time_validation(driver):
    """
    Verify that constraints are enforced at write time.

    Since Neo4j 2026.02 does not support GRAPH TYPE, we test:
    - Uniqueness constraint on Service.id
    - Property existence constraint on Service.name
    - Property existence constraint on TargetCategory.code
    """
    R.append("\n-- Write-time validation tests (constraint enforcement) --")

    tests = [
        ("Uniqueness: duplicate Service.id",
         "CREATE (n:Service {id: 'svc_shinshin_teate', name: 'dup'})",
         True),
        ("Existence: missing Service.name",
         "CREATE (n:Service {id: 'bad_svc'})",
         True),
        ("Existence: missing TargetCategory.code",
         "CREATE (n:TargetCategory {id: 'bad_tc', name: 'Bad'})",
         True),
        ("Valid: Service with all required props",
         "CREATE (n:Service {id: 'test_valid', name: 'Test Service'})",
         False),
    ]

    passed = 0
    total = 0
    for desc, stmt, should_fail in tests:
        total += 1
        with driver.session(database="neo4j") as session:
            try:
                result = session.run(stmt)
                result.consume()
                if should_fail:
                    R.append(f"  ✗ {desc}: was NOT rejected (constraint may be missing)")
                else:
                    R.append(f"  ✓ {desc}: accepted (expected)")
                    passed += 1
            except Exception as ex:
                if should_fail:
                    R.append(f"  ✓ {desc}: correctly rejected — {ex}")
                    passed += 1
                else:
                    R.append(f"  ✗ {desc}: unexpectedly rejected — {ex}")

    # Clean up test nodes
    with driver.session(database="neo4j") as session:
        session.run("MATCH (n:Service) WHERE n.id IN ['test_valid', 'bad_svc'] DETACH DELETE n").consume()
        session.run("MATCH (n:TargetCategory) WHERE n.id = 'bad_tc' DETACH DELETE n").consume()

    R.append(f"  Write-time validation: {passed}/{total} tests passed")
    return passed == total


def count_nodes_edges(driver):
    with driver.session(database="neo4j") as session:
        nn = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        ne = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        labels = session.run("CALL db.labels() YIELD label RETURN label").values()
        rels = session.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType").values()
        return nn, ne, [l[0] for l in labels], [r[0] for r in rels]


def run():
    args = parse_args()

    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("neo4j driver not installed. Run: pip install neo4j")
        sys.exit(1)

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))

    R.append(f"Connecting to {args.uri} ...")
    for attempt in range(30):
        try:
            driver.verify_connectivity()
            R.append("  Connected.")
            break
        except Exception:
            if attempt == 29:
                R.append("  ✗ Could not connect after 30s")
                driver.close()
                open(REPORT_FILE, "w", encoding="utf-8").write("\n".join(R))
                sys.exit(1)
            time.sleep(1)

    # Step 1: Apply DDL FIRST (constraints + indexes)
    R.append("\n-- Schema DDL --")
    load_ddl(driver, args.ddl)

    # Step 2: Load data (DDL ensures write-time validation)
    ok = load_kg(driver, args.cypher)
    if not ok:
        R.append("\n⚠ Load incomplete. Review errors above.")
        open(REPORT_FILE, "w", encoding="utf-8").write("\n".join(R))
        driver.close()
        sys.exit(1)

    # Step 2b: Apply generated constraints (from ontology definition)
    if os.path.isfile(GENERATED_CONSTRAINTS_FILE):
        with open(GENERATED_CONSTRAINTS_FILE, encoding="utf-8") as f:
            gen_content = f.read()
        gen_stmts = [s.strip() for s in gen_content.split(";") if s.strip() and not s.strip().startswith("//")]
        if gen_stmts:
            R.append(f"Generated constraints: {len(gen_stmts)} statements")
            run_stmts(driver, gen_stmts, "generated_constraints")

    # Step 3: Clean up test residues (from previous runs)
    with driver.session(database="neo4j") as session:
        session.run("MATCH (n:Service) WHERE n.id IN ['test_valid', 'bad_svc'] DETACH DELETE n")
        session.run("MATCH (n:TargetCategory) WHERE n.id = 'bad_tc' DETACH DELETE n")

    # Step 4: Count
    nn, ne, labels, rels = count_nodes_edges(driver)
    R.append(f"\nNodes: {nn}  Edges: {ne}")
    R.append(f"Labels: {', '.join(sorted(labels))}")
    R.append(f"Rel types: {', '.join(sorted(rels))}")

    # Step 4: Verify write-time validation
    all_pass = verify_write_time_validation(driver)

    driver.close()
    verdict = "✅ ALL PASS" if all_pass else "⚠ 一部失敗"
    R.append(f"\n{verdict}")
    open(REPORT_FILE, "w", encoding="utf-8").write("\n".join(R))
    print(f"Report -> {REPORT_FILE}")


if __name__ == "__main__":
    run()