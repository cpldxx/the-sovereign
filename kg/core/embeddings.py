"""Text embeddings via an OpenAI-compatible /embeddings endpoint (Ollama by default).

The model comes from OLLAMA_MODEL_EMBEDDING. Switching models usually changes the
vector dimension; a domain database whose vector index was built with the old
dimension then rejects new vectors until it is re-embedded.
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
MODEL = os.getenv("OLLAMA_MODEL_EMBEDDING", "nomic-embed-text-v2-moe").removeprefix("ollama:")


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts in one request. Output order matches input order."""
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        r = await client.post(f"{BASE_URL}/embeddings", json={"model": MODEL, "input": texts})
        r.raise_for_status()
    return [item["embedding"] for item in r.json()["data"]]
