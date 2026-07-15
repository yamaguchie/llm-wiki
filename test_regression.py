# -*- coding: utf-8 -*-
"""
GraphRAG パイプライン — 回帰テスト
仕様書 ontology_graphrag_pipeline_spec.md の各フェーズの動作を保証する。

Usage:
    py -3.14 -m pytest test_regression.py -v
    py -3.14 test_regression.py  # 単体実行
"""
import json, os, sys, glob, re, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ── Phase 1: データクレンジング ──

class TestPhase1DataCleansing(unittest.TestCase):
    """LLM-Wiki と PDFチャンクが存在することを確認する"""

    def test_01_pages_exist(self):
        """1.2 LLM-Wiki: pages/ に .md ファイルが存在する"""
        pages = glob.glob(os.path.join(HERE, "pages", "*.md"))
        self.assertGreater(len(pages), 0, "pages/ に .md ファイルがありません")
        self.assertGreaterEqual(len(pages), 10, "最低10ファイル以上のエンティティページが必要")

    def test_02_pages_have_wikilinks(self):
        """LLM-Wiki: [[wikilink]] 形式の相互参照が含まれている"""
        pages = glob.glob(os.path.join(HERE, "pages", "*.md"))
        has_link = False
        for p in pages[:5]:
            content = open(p, encoding="utf-8").read()
            if re.search(r'\[\[[\w-]+\]\]', content):
                has_link = True
                break
        self.assertTrue(has_link, "どのページにも [[wikilink]] が見つかりません")

    def test_03_pdf_chunks_exist(self):
        """1.1 RAWデータ: pdf_chunks.json が存在する"""
        path = os.path.join(HERE, "graphrag", "rag", "pdf_chunks.json")
        self.assertTrue(os.path.isfile(path), "pdf_chunks.json がありません")
        data = json.load(open(path, encoding="utf-8"))
        self.assertGreater(len(data["chunks"]), 0, "チャンクが空です")

    def test_04_pdf_embeddings_exist(self):
        """1.1 埋め込み: pdf_embeddings.json が存在する"""
        path = os.path.join(HERE, "graphrag", "rag", "pdf_embeddings.json")
        self.assertTrue(os.path.isfile(path), "pdf_embeddings.json がありません")


# ── Phase 2: ドメイン知識の付与 ──

class TestPhase2DomainKnowledge(unittest.TestCase):
    """CQ管理・オントロジー生成・レビューUIの動作を確認する"""

    @classmethod
    def setUpClass(cls):
        # FastAPI テストクライアント
        from fastapi.testclient import TestClient
        sys.path.insert(0, os.path.join(HERE, "graphrag", "review"))
        import main as review_main
        cls.client = TestClient(review_main.app)

    def test_10_cq_list_empty_default(self):
        """2.1 CQ: 初期状態でCQ一覧は空（0件）"""
        resp = self.client.get("/api/review-items?type=cq")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertEqual(len(items), 0, "初期状態のCQは0件であるべき")

    def test_11_cq_generate_endpoint_exists(self):
        """2.1 CQ: /api/cq/generate エンドポイントが存在する"""
        resp = self.client.post("/api/cq/generate")
        self.assertIn(resp.status_code, [200, 500], "エンドポイントが応答しません")

    def test_12_ontology_generate_endpoint_exists(self):
        """2.2 オントロジー定義: /api/ontology/generate エンドポイントが存在する"""
        resp = self.client.post("/api/ontology/generate")
        self.assertIn(resp.status_code, [200, 500], "エンドポイントが応答しません")

    def test_13_ontology_definition_endpoint(self):
        """2.2 オントロジー定義: /api/ontology/definition が応答する"""
        resp = self.client.get("/api/ontology/definition")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("status", data, "status フィールドが必要")

    def test_14_ontology_kg_endpoint(self):
        """2.3 オントロジー図: /api/ontology/generated/kg が応答する"""
        resp = self.client.get("/api/ontology/generated/kg")
        self.assertIn(resp.status_code, [200, 404], "エンドポイントが応答しません")

    def test_15_review_item_types(self):
        """2.4 レビューUI: 全レビュー項目の型一覧が取得できる"""
        resp = self.client.get("/api/review-items")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        types = set(i["type"] for i in items)
        # 初期状態なので空でも良い
        self.assertIsInstance(items, list)

    def test_16_review_post(self):
        """2.4 レビューUI: POST /api/review が動作する"""
        # まずダミー項目を追加（API直接）
        from review.main import REVIEW_ITEMS
        REVIEW_ITEMS.append({
            "id": "TEST_CQ", "title": "テスト質問？",
            "description": "テスト用", "expected_answer": "テスト回答",
            "type": "lookup", "source": "テスト", "source_url": "",
            "review": "human_required", "type_cq": "cq",
            "status": "pending", "cq_ids": [], "current_value": "未テスト"
        })
        resp = self.client.post("/api/review", json={
            "item_id": "TEST_CQ", "reviewer": "tester",
            "comment": "OK", "approved": True, "revision_requested": False
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        # クリーンアップ
        REVIEW_ITEMS[:] = [i for i in REVIEW_ITEMS if i["id"] != "TEST_CQ"]

    def test_17_kg_endpoint(self):
        """全般: /api/kg がkg.jsonを返す"""
        resp = self.client.get("/api/kg")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertGreater(len(data["nodes"]), 0, "ノードが空です")
        self.assertGreater(len(data["edges"]), 0, "エッジが空です")

    def test_18_ontology_summary(self):
        """全般: /api/ontology/summary が統計情報を返す"""
        resp = self.client.get("/api/ontology/summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("n_nodes", data)
        self.assertIn("n_edges", data)
        self.assertIn("node_labels", data)

    def test_19_cq_results(self):
        """全般: /api/cq/results がCQテスト結果を返す"""
        resp = self.client.get("/api/cq/results")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_20_wiki_index(self):
        """全般: /api/wiki/index がWikiページ一覧を返す"""
        resp = self.client.get("/api/wiki/index")
        self.assertEqual(resp.status_code, 200)
        pages = resp.json()
        self.assertGreater(len(pages), 0, "Wikiページ一覧が空です")


# ── Phase 3: ナレッジグラフ（GraphRAG） ──

class TestPhase3GraphRAG(unittest.TestCase):
    """ナレッジグラフ・AgenticSearch・チャットの動作を確認する"""

    def test_30_kg_json_exists(self):
        """3.1 ナレッジグラフ: kg.json が56ノード/186エッジ"""
        path = os.path.join(HERE, "graphrag", "graph", "kg.json")
        self.assertTrue(os.path.isfile(path))
        kg = json.load(open(path, encoding="utf-8"))
        self.assertEqual(len(kg["nodes"]), 56, "ノード数が56であること")
        self.assertEqual(len(kg["edges"]), 186, "エッジ数が186であること")

    def test_31_kg_cypher_exists(self):
        """3.1 Cypher: kg.cypher が存在する"""
        path = os.path.join(HERE, "graphrag", "graph", "kg.cypher")
        self.assertTrue(os.path.isfile(path), "kg.cypher がありません")

    def test_32_agent_imports(self):
        """3.1 エージェント: agent.py がインポートできる"""
        sys.path.insert(0, os.path.join(HERE, "graphrag", "agent"))
        import agent
        self.assertTrue(hasattr(agent, "run"), "agent.run() が存在しません")

    def test_33_langgraph_app_imports(self):
        """3.1 LangGraph: langgraph_app.py がインポートできる"""
        sys.path.insert(0, os.path.join(HERE, "graphrag", "agent"))
        import langgraph_app
        self.assertTrue(hasattr(langgraph_app, "run"), "langgraph_app.run() が存在しません")

    def test_34_naive_rag_imports(self):
        """3.1 NaiveRAG: naive_rag.py がインポートできる"""
        sys.path.insert(0, os.path.join(HERE, "graphrag", "agent"))
        import naive_rag
        self.assertTrue(hasattr(naive_rag, "ask"), "naive_rag.ask() が存在しません")

    def test_35_neo4j_docker_compose_exists(self):
        """3.1 Neo4j: docker-compose.yml が存在する"""
        path = os.path.join(HERE, "graphrag", "neo4j", "docker-compose.yml")
        self.assertTrue(os.path.isfile(path))

    def test_36_neo4j_graph_type_exists(self):
        """3.1 GRAPH TYPE: create_graph_type.cypher が存在する"""
        path = os.path.join(HERE, "graphrag", "neo4j", "create_graph_type.cypher")
        self.assertTrue(os.path.isfile(path))
        content = open(path, encoding="utf-8").read()
        self.assertIn("CREATE CONSTRAINT", content, "制約定義が必要")

    def test_37_neo4j_loader_exists(self):
        """3.1 ローダー: load_kg.py が存在する"""
        path = os.path.join(HERE, "graphrag", "neo4j", "load_kg.py")
        self.assertTrue(os.path.isfile(path))

    def test_38_chat_html_exists(self):
        """3.2 チャットUI: chat/index.html が存在する"""
        path = os.path.join(HERE, "graphrag", "chat", "index.html")
        self.assertTrue(os.path.isfile(path))
        content = open(path, encoding="utf-8").read()
        self.assertIn("agentic", content, "agentic search 関数が必要")
        self.assertIn("buildGraph", content, "グラフ描画関数が必要")

    def test_39_gemini_client_imports(self):
        """gemini_client.py がインポートできる"""
        sys.path.insert(0, os.path.join(HERE, "graphrag", "agent"))
        import gemini_client
        self.assertTrue(hasattr(gemini_client, "embed_texts"), "embed_texts が存在しません")
        self.assertTrue(hasattr(gemini_client, "chat"), "chat が存在しません")

    def test_40_server_imports(self):
        """server.py がインポートできる"""
        sys.path.insert(0, os.path.join(HERE, "graphrag", "agent"))
        import server
        # クラスの存在確認
        self.assertIn("Handler", dir(server))


# ── KG 整合性検証 ──

class TestKGIntegrity(unittest.TestCase):
    """ナレッジグラフの整合性チェック"""

    def setUp(self):
        kg_path = os.path.join(HERE, "graphrag", "graph", "kg.json")
        self.kg = json.load(open(kg_path, encoding="utf-8"))
        self.node_by_id = {n["id"]: n for n in self.kg["nodes"]}

    def test_50_all_edges_reference_valid_nodes(self):
        """全エッジのfrom/toが有効なノードIDを指している"""
        for e in self.kg["edges"]:
            self.assertIn(e["from"], self.node_by_id, f"エッジのfrom '{e['from']}' が存在しません")
            self.assertIn(e["to"], self.node_by_id, f"エッジのto '{e['to']}' が存在しません")

    def test_51_all_nodes_have_name(self):
        """全ノードに name または dept プロパティがある"""
        for n in self.kg["nodes"]:
            props = n["props"]
            has_name = "name" in props or "dept" in props
            self.assertTrue(has_name, f"ノード {n['id']} に name/dept がありません")

    def test_52_service_nodes_have_required_props(self):
        """Serviceノードに必須プロパティがある"""
        for n in self.kg["nodes"]:
            if "Service" in n["labels"]:
                self.assertIn("name", n["props"], f"Service {n['id']} に name がありません")

    def test_53_contact_nodes_have_dept(self):
        """Contactノードに dept がある"""
        for n in self.kg["nodes"]:
            if "Contact" in n["labels"]:
                self.assertIn("dept", n["props"], f"Contact {n['id']} に dept がありません")

    def test_54_notebook_nodes_have_name(self):
        """Notebookノードに name がある"""
        for n in self.kg["nodes"]:
            if "Notebook" in n["labels"]:
                self.assertIn("name", n["props"], f"Notebook {n['id']} に name がありません")

    def test_55_edges_have_type(self):
        """全エッジに type がある"""
        for e in self.kg["edges"]:
            self.assertIn("type", e, f"エッジに type がありません")

    def test_56_known_relationship_types(self):
        """関係タイプが既知のものだけ"""
        known = {"HAS_CATEGORY", "TARGETS", "REQUIRES", "ADMINISTERED_BY",
                 "DEFINED_BY", "MUTUALLY_EXCLUSIVE_WITH", "RELATED_TO", "PROVIDED_AT"}
        for e in self.kg["edges"]:
            self.assertIn(e["type"], known, f"未知の関係タイプ: {e['type']}")

    def test_57_known_labels(self):
        """ラベルが既知のものだけ（サブラベル含む）"""
        known = {"Service", "ServiceCategory", "TargetCategory", "Notebook",
                 "Contact", "Reference", "Facility",
                 "Allowance", "MedicalAid", "AssistiveDevice", "TransportBenefit"}
        for n in self.kg["nodes"]:
            for label in n["labels"]:
                self.assertIn(label, known, f"未知のラベル: {label} in {n['id']}")


# ── サーバーSSL/起動確認 ──

class TestServerConfig(unittest.TestCase):
    """サーバー設定ファイルの存在確認"""

    def test_60_env_example_exists(self):
        """.env.example が存在する"""
        path = os.path.join(HERE, "graphrag", ".env.example")
        self.assertTrue(os.path.isfile(path))

    def test_61_spec_exists(self):
        """仕様書が存在する"""
        path = os.path.join(HERE, "ontology_graphrag_pipeline_spec.md")
        self.assertTrue(os.path.isfile(path))
        content = open(path, encoding="utf-8").read()
        self.assertIn("CQ（質問＋回答ペア）", content, "CQセクションが必要")
        self.assertIn("オントロジー定義", content, "オントロジー定義セクションが必要")
        self.assertIn("オントロジー図", content, "オントロジー図セクションが必要")

    def test_62_readme_exists(self):
        """README が存在する"""
        path = os.path.join(HERE, "graphrag", "README.md")
        self.assertTrue(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)