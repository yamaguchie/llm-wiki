# -*- coding: utf-8 -*-
"""Naive RAG: PDF chunks + Gemini embeddings + direct LLM answer.

Usage:
    py -3.14 naive_rag.py "（質問文）"
"""
import json, math, os, sys, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
from gemini_client import embed_texts, chat

CHUNK_PATH = os.path.join(HERE, "..", "step1_data", "raw", "chunks.json")
EMB_PATH = os.path.join(HERE, "..", "step1_data", "raw", "embeddings.json")
TOP_K = 5

def _cos(a, b):
    d = sum(x * y for x, y in zip(a, b))
    return d / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) + 1e-9)

def load_or_build():
    """Load cached embeddings or build from scratch."""
    if os.path.isfile(EMB_PATH):
        data = json.load(open(EMB_PATH, encoding="utf-8"))
        return data["chunks"], data["embeddings"]

    print("Embedding PDF chunks with Gemini...")
    chunks = json.load(open(CHUNK_PATH, encoding="utf-8"))["chunks"]
    texts = [c["text"] for c in chunks]
    # Embed in batches
    batch = 50
    all_vecs = []
    for i in range(0, len(texts), batch):
        batch_texts = texts[i:i + batch]
        vecs = embed_texts(batch_texts)
        all_vecs.extend(vecs)
        print(f"  {min(i + batch, len(texts))}/{len(texts)}")

    data = {"chunks": chunks, "embeddings": all_vecs}
    json.dump(data, open(EMB_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Saved {EMB_PATH}")
    return chunks, all_vecs


def retrieve(query, k=TOP_K):
    """Retrieve top-k chunks for a query."""
    chunks, embs = load_or_build()
    qv = embed_texts([query], task_type="RETRIEVAL_QUERY")[0]
    scored = [(i, _cos(qv, embs[i])) for i in range(len(embs))]
    scored.sort(key=lambda x: -x[1])
    top = scored[:k]
    results = []
    for idx, score in top:
        c = chunks[idx]
        results.append({"page": c["page"], "text": c["text"][:500], "score": round(score, 3)})
    return results


SYSTEM_PROMPT = (
    f"あなたは、{config.get_kb_label()}の案内アシスタントです。以下の【参考資料】だけを使って、"
    "日本語で簡潔に答えてください。資料にない情報は創作せず「分かりません」と述べてください。"
    "回答には出典のページ番号を含めてください。"
)


def ask(query):
    """Naive RAG: retrieve chunks → LLM answer."""
    chunks_data = load_or_build()
    results = retrieve(query)
    ctx = "\n\n".join(f"[p.{r['page']}] {r['text']}" for r in results)
    from gemini_client import chat as gc_chat
    answer = gc_chat(SYSTEM_PROMPT, f"【質問】\n{query}\n\n【参考資料】\n{ctx}")
    return {
        "answer": answer,
        "sources": [{"page": r["page"], "score": r["score"]} for r in results],
    }


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "（質問文を引数で指定してください）"
    res = ask(q)
    print(f"Answer:\n{res['answer']}\n")
    print(f"Sources: {res['sources']}")