# -*- coding: utf-8 -*-
"""Backend for the Gemini-mode chat.
- Serves the repo statically (with UTF-8 charset + BOM for .md, like serve_md.py)
- POST /api/ask {"query": "..."} -> {"answer","trace","n_facts"} via the Gemini agent
Run: py -3.14 server.py <port> <docroot>
Default: port 8790, docroot = repo root (llm_wiki/)
"""
import http.server, socketserver, json, os, sys, re
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent, naive_rag

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

                # Stream NDJSON response
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                for event in agent.run_stream(q):
                    line = json.dumps(event, ensure_ascii=False) + "\n"
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()

            except Exception as ex:
                self._json(500, {"error": f"{type(ex).__name__}: {ex}"})
        elif self.path.rstrip("/") == "/api/rag":
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
                q = (data.get("query") or "").strip()
                if not q:
                    return self._json(400, {"error": "empty query"})
                from gemini_client import embed_texts  # ensure embedding model loaded
                res = naive_rag.ask(q)
                return self._json(200, res)
            except Exception as ex:
                return self._json(500, {"error": f"{type(ex).__name__}: {ex}"})
        elif self.path.rstrip("/") == "/review" or self.path.startswith("/review/"):
            # Proxy POST to the FastAPI review UI (single-origin support)
            import urllib.request, urllib.error
            target = "http://127.0.0.1:8789" + (self.path[len("/review"):] or "/")
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n) if n else b""
                req = urllib.request.Request(
                    target, data=body, method="POST",
                    headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rbody, status = resp.read(), resp.status
                    ctype = resp.headers.get("Content-Type", "application/json; charset=utf-8")
            except urllib.error.HTTPError as he:
                rbody, status = he.read(), he.code
                ctype = he.headers.get("Content-Type", "application/json")
            except Exception as ex:
                return self._json(502, {"error": f"review proxy: {ex}"})
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(rbody)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(rbody)
            return
        self._json(404, {"error": "not found"})

    def do_GET(self):
        path = self.translate_path(self.path)

        # Serve chat at root /
        if self.path.rstrip("/") == "" or self.path.rstrip("/") == "/graphrag/chat/index.html":
            chat_path = os.path.join(ROOT, "graphrag", "chat", "index.html")
            if os.path.isfile(chat_path):
                with open(chat_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

        # Serve review UI at /review/
        if self.path.rstrip("/") == "/review" or self.path.startswith("/review/"):
            # Proxy to the FastAPI review UI
            import urllib.request
            target = "http://127.0.0.1:8789" + self.path.replace("/review", "", 1) or "/"
            try:
                with urllib.request.urlopen(target, timeout=5) as resp:
                    body = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "text/html; charset=utf-8"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_response(502)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"<h1>502</h1><p>Review UI not available. Start: uvicorn review.main:app --port 8789</p>")
            return

        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            # Serve .md as HTML preview
            if ext == ".md":
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = f.read()
                    html = _md_to_html(raw, path)
                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as ex:
                    self._json(500, {"error": str(ex)})
                return
            # Serve .yaml/.yml as HTML with CQ management link
            if ext in (".yaml", ".yml") and "competency_questions" in path:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = f.read()
                    html = _yaml_to_html(raw, path)
                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as ex:
                    self._json(500, {"error": str(ex)})
                return
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


def _md_to_html(raw, filepath):
    """Convert markdown (with [[wikilinks]]) to styled HTML preview."""
    # Convert [[wikilink]] → [wikilink](/pages/wikilink.md)
    text = re.sub(r'\[\[([\w-]+)\]\]', r'[\1](/pages/\1.md)', raw)

    # Strip YAML frontmatter
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL)

    # Escape HTML entities
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = text.split("\n")
    html_parts = []
    in_table = False
    in_list = [False]  # track nesting depth

    for line in lines:
        stripped = line.strip()

        # Headings
        m = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if m:
            level = len(m.group(1))
            html_parts.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue

        # Horizontal rule
        if re.match(r'^---+$', stripped):
            html_parts.append("<hr>")
            continue

        # Table row
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if not in_table:
                html_parts.append("<table>")
                in_table = True
            # Detect header row (separator row like |---|---|---|)
            if re.match(r'^[\s|:\-]+$', stripped):
                continue
            tag = "th" if not in_table or html_parts[-1].startswith("<table>") or html_parts[-1].strip() == "<table>" else "td"
            html_parts.append(f"<tr><{'><'.join([tag]*len(cells))}>" + f"</{tag}><{tag}>".join(_inline(c) for c in cells) + f"</{tag}></tr>")
            continue
        if in_table:
            html_parts.append("</table>")
            in_table = False

        # Unordered list
        m = re.match(r'^(\s*)[-*]\s+(.+)$', line)
        if m:
            depth = len(m.group(1)) // 2
            content = _inline(m.group(2))
            while len(in_list) <= depth:
                in_list.append(False)
            if not in_list[depth]:
                html_parts.append("<ul>" * (1 if depth == 0 or not in_list[depth-1] else 0) + "<li>" + content)
                in_list[depth] = True
            else:
                html_parts.append("<li>" + content)
            continue
        # Close lists
        for d in range(len(in_list)):
            if in_list[d]:
                html_parts.append("</li></ul>")
                in_list[d] = False

        # Empty line → paragraph break
        if not stripped:
            html_parts.append("<div class='pbreak'></div>")
            continue

        # Regular paragraph
        html_parts.append(f"<p>{_inline(stripped)}</p>")

    if in_table:
        html_parts.append("</table>")

    body = "\n".join(html_parts)

    # Extract title from first h1
    title_m = re.search(r'<h1>(.*?)</h1>', body)
    title = title_m.group(1) if title_m else os.path.basename(filepath)

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — 文京区障害者福祉</title>
<style>
*{{box-sizing:border-box}} body{{font-family:system-ui,-apple-system,'Hiragino Sans',sans-serif;max-width:900px;margin:0 auto;padding:16px;background:#f6f7f9;color:#1b1f24;line-height:1.7}}
h1{{font-size:1.3rem;border-bottom:2px solid #2b6cb0;padding-bottom:6px}} h2{{font-size:1.1rem;margin-top:1.2em}}
h3{{font-size:1rem}} h4{{font-size:.95rem;color:#555}}
a{{color:#2b6cb0;text-decoration:none}} a:hover{{text-decoration:underline}}
table{{border-collapse:collapse;width:100%;margin:8px 0;font-size:.85rem}}
th,td{{border:1px solid #ddd;padding:5px 8px;text-align:left;vertical-align:top}}
th{{background:#eef2f7;font-weight:600}}
tr:nth-child(even){{background:#fafbfc}}
.pbreak{{height:8px}}
hr{{border:0;border-top:1px solid #ddd;margin:12px 0}}
code{{background:#eee;padding:1px 4px;border-radius:3px;font-size:.85rem}}
</style>
</head><body>
<div style="font-size:.75rem;color:#888;margin-bottom:8px">
📄 <a href="/">チャット</a> / <a href="{os.path.relpath(filepath, os.path.join(os.path.dirname(__file__),"..",".."))}">原文.md</a>
</div>
{body}
</body></html>"""


def _yaml_to_html(raw, filepath):
    """Render YAML (competency questions) as readable HTML with CQ management link."""
    text = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Highlight YAML keys
    text = re.sub(r'(^|\n)(\w[\w-]*)(:)', r'\1<span style="color:#2563eb;font-weight:600">\2</span>\3', text)
    # Highlight comments
    text = re.sub(r'(#.*)', r'<span style="color:#9ca3af;font-style:italic">\1</span>', text)
    # Highlight values in quotes
    text = re.sub(r'("(?:[^"\\]|\\.)*")', r'<span style="color:#16a34a">\1</span>', text)
    cq_count = len(re.findall(r'^\s+id\s*:', raw, re.MULTILINE))
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>コンピテンシー質問（CQ） — 文京区障害者福祉</title>
<style>
*{{box-sizing:border-box}} body{{font-family:system-ui,-apple-system,'Hiragino Sans',sans-serif;max-width:960px;margin:0 auto;padding:16px;background:#f6f7f9;color:#1b1f24;line-height:1.7}}
pre{{background:#f8f9fb;border:1px solid #e2e6ea;border-radius:8px;padding:14px;overflow-x:auto;font-size:.82rem;line-height:1.5;white-space:pre-wrap;word-break:break-all}}
a{{color:#2563eb;text-decoration:none}} a:hover{{text-decoration:underline}}
.btn{{display:inline-block;padding:8px 18px;border-radius:6px;font-weight:600;font-size:.85rem;text-decoration:none}}
</style>
</head><body>
<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:12px">
  <div>
    <h1 style="font-size:1.2rem;margin:0">📋 コンピテンシー質問（CQ）</h1>
    <div style="font-size:.78rem;color:#888;margin-top:2px">全{cq_count}件 — LLM-Wikiから自動生成</div>
  </div>
  <a href="http://127.0.0.1:8789/" class="btn" style="background:#2563eb;color:#fff">🔍 CQ管理画面を開く</a>
</div>
<div style="font-size:.82rem;color:#888;margin-bottom:10px">
  <a href="/">チャット</a> / <a href="/graphrag/ontology/competency_questions.yaml" style="color:#888">原文.yaml</a>
</div>
<pre>{text}</pre>
</body></html>"""


def _inline(text):
    """Convert inline markdown to HTML."""
    # Images
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" style="max-width:100%">', text)
    # Links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    return text

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

print(f"Server: http://127.0.0.1:{PORT}  (docroot={ROOT})")
print(f"  Chat:  http://127.0.0.1:{PORT}/")
print(f"  Review: http://127.0.0.1:{PORT}/review/")
with Server(("127.0.0.1", PORT), Handler) as httpd:
    httpd.serve_forever()
