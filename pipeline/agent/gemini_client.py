# -*- coding: utf-8 -*-
"""Thin wrapper over google-genai for embeddings + chat, using the .env key.
Functions:
  embed_texts(list[str]) -> list[list[float]]
  chat(system, user, temperature=0.0) -> str
  chat_json(system, user) -> dict     (asks the model for strict JSON and parses it)
"""
import os, sys, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import get_key, get_model, get_embed_model

from google import genai
from google.genai import types

_client = None
def client():
    global _client
    if _client is None:
        # timeout未設定だと応答待ちのまま無限にハングしうるため必ず設定する（ミリ秒単位）
        _client = genai.Client(api_key=get_key(), http_options=types.HttpOptions(timeout=120000))
    return _client

def embed_texts(texts, task_type="RETRIEVAL_DOCUMENT"):
    """Return one embedding vector per input text."""
    if isinstance(texts, str):
        texts = [texts]
    r = client().models.embed_content(
        model=get_embed_model(),
        contents=texts,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return [list(e.values) for e in r.embeddings]

def chat(system, user, temperature=0.0):
    r = client().models.generate_content(
        model=get_model(),
        contents=user,
        config=types.GenerateContentConfig(system_instruction=system, temperature=temperature),
    )
    return (r.text or "").strip()

def chat_json(system, user, temperature=0.0):
    txt = chat(system + "\n\nReturn ONLY valid minified JSON, no markdown fences.", user, temperature)
    m = re.search(r"\{.*\}|\[.*\]", txt, re.S)
    raw = m.group(0) if m else txt
    return json.loads(raw)
