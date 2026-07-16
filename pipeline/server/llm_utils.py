# -*- coding: utf-8 -*-
"""LLMユーティリティ — Gemini API 呼び出しのラッパー"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))

_GENAI_CACHE = None

def _load_env():
    env_path = os.path.join(HERE, "..", ".env")
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                if line.startswith("NEO4J_URI="):
                    os.environ["NEO4J_URI"] = line.split("=", 1)[1].strip().strip('"').strip("'")

def genai():
    global _GENAI_CACHE
    if _GENAI_CACHE:
        return _GENAI_CACHE
    _load_env()
    import google.genai as genai
    from google.genai import types
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    _GENAI_CACHE = (client, model, types)
    return _GENAI_CACHE

def llm_json(sys_prompt, user_prompt):
    client, model, types = genai()
    resp = client.models.generate_content(
        model=model, contents=f"{sys_prompt}\n\n{user_prompt}",
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    raw = resp.text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    # Try to parse JSON, with fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Try stripping trailing commas (common issue)
        import re as _re2
        raw_fixed = _re2.sub(r',\s*}', '}', raw)
        raw_fixed = _re2.sub(r',\s*]', ']', raw_fixed)
        try:
            return json.loads(raw_fixed)
        except:
            raise e

def llm_text(sys_prompt, user_prompt):
    client, model, types = genai()
    resp = client.models.generate_content(model=model, contents=f"{sys_prompt}\n\n{user_prompt}")
    return resp.text.strip()