# -*- coding: utf-8 -*-
"""Verify .env setup WITHOUT printing the secret and WITHOUT any network call.
Run: py -3.14 check_key.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_env

load_env()
key = os.environ.get("GEMINI_API_KEY", "").strip()
model = os.environ.get("GEMINI_MODEL", "(default gemini-2.5-flash)")
embed = os.environ.get("GEMINI_EMBED_MODEL", "(default text-embedding-004)")

if key:
    masked = key[:4] + "..." + key[-4:] if len(key) > 8 else "****"
    print(f"GEMINI_API_KEY : SET ({masked}, len={len(key)})")
else:
    print("GEMINI_API_KEY : EMPTY  -> edit graphrag/.env and paste your key")
print(f"GEMINI_MODEL   : {model}")
print(f"EMBED_MODEL    : {embed}")

# check whether the Google GenAI SDK is installed (no network)
try:
    import google.genai  # noqa: F401
    print("google-genai   : installed")
except Exception:
    print("google-genai   : NOT installed  -> pip install google-genai")
