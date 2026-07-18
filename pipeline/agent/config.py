# -*- coding: utf-8 -*-
"""Config loader for the graphrag pipeline.
Prefers values from graphrag/.env (the user's explicit choice), then falls back to
process environment variables. Dependency-free (no python-dotenv needed).
"""
import os

def _parse_env_file(path=None):
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "..", ".env")
    d = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    return d

_ENV = _parse_env_file()

def _get(name, default=""):
    v = _ENV.get(name, "").strip()
    if v:
        return v
    return os.environ.get(name, default).strip() if os.environ.get(name) else default

def get_key():
    k = _get("GEMINI_API_KEY", "")
    if not k:
        raise RuntimeError(
            "GEMINI_API_KEY is empty. Edit graphrag/.env and paste your key after 'GEMINI_API_KEY='."
        )
    return k

def get_model():
    return _get("GEMINI_MODEL", "gemini-2.5-flash")

def get_embed_model():
    return _get("GEMINI_EMBED_MODEL", "gemini-embedding-001")

def get_kb_label():
    """LLMプロンプトのペルソナに使う知識ベースの表示名。ドメイン名をコードに直書きせず、
    .env の KB_LABEL で別プロジェクトでも使い回せるようにする。"""
    return _get("KB_LABEL", "この知識ベース")

# backward-compat
def load_env(path=None):
    global _ENV
    _ENV = _parse_env_file(path)
    return _ENV
