# -*- coding: utf-8 -*-
"""Backend for the Gemini-mode chat.
- Serves the repo statically (with UTF-8 charset + BOM for .md, like serve_md.py)
- POST /api/ask {"query": "..."} -> {"answer","trace","n_facts"} via the Gemini agent
Run: py -3.14 server.py <port> <docroot>
Default: port 8790, docroot = repo root (llm_wiki/)
"""
import http.server, socketserver, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
ROOT = sys.argv[2] if len(sys.argv) > 2 else os.path.abspath(os.path.join(HERE, "..", ".."))
os.chdir(ROOT)

TEXT_EXT = {".md", ".txt", ".yaml", ".yml", ".cypher"}
BOM = b"\xef\xbb\xbf"

class Handler(http.server.SimpleHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.rstrip("/") == "/api/ask":
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
                q = (data.get("query") or "").strip()
                if not q:
                    return self._json(400, {"error": "empty query"})
                res = agent.run(q)
                return self._json(200, res)
            except Exception as ex:
                return self._json(500, {"error": f"{type(ex).__name__}: {ex}"})
        self._json(404, {"error": "not found"})

    def do_GET(self):
        path = self.translate_path(self.path)
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            if ext in TEXT_EXT or ext == ".json":
                with open(path, "rb") as f:
                    body = f.read()
                if ext != ".json" and not body.startswith(BOM):
                    body = BOM + body
                ctype = "application/json" if ext == ".json" else "text/plain"
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
        return super().do_GET()

    def end_headers(self):
        if self.command == "GET":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

print(f"Gemini backend on http://127.0.0.1:{PORT}  (docroot={ROOT})")
print(f"Open: http://127.0.0.1:{PORT}/graphrag/chat/index.html  (Geminiモードをオン)")
with Server(("127.0.0.1", PORT), Handler) as httpd:
    httpd.serve_forever()
