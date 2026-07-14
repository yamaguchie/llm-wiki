# -*- coding: utf-8 -*-
"""Static server that force-serves text files (.md/.txt/.yaml/.json/.cypher) as UTF-8.
Fixes browser mojibake by (1) declaring charset=utf-8, (2) prepending a UTF-8 BOM so the
browser cannot mis-detect the encoding, and (3) Cache-Control: no-store to avoid stale copies.
Usage: py -3.14 serve_md.py <port> <docroot>"""
import http.server, socketserver, sys, os

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8788
ROOT = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
os.chdir(ROOT)

TEXT_EXT = {".md", ".txt", ".yaml", ".yml", ".cypher"}
BOM = b"\xef\xbb\xbf"

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.translate_path(self.path)
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            if ext in TEXT_EXT or ext == ".json":
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                except OSError:
                    return super().do_GET()
                if not data.startswith(BOM):
                    data = BOM + data
                ctype = "application/json" if ext == ".json" else "text/plain"
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()   # Cache-Control added by end_headers() override
                self.wfile.write(data)
                return
        return super().do_GET()

    def end_headers(self):
        # index.html etc. also no-store so re-loads always fetch fresh
        if self.command == "GET":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

class Server(socketserver.TCPServer):
    allow_reuse_address = True

with Server(("127.0.0.1", PORT), Handler) as httpd:
    httpd.serve_forever()
