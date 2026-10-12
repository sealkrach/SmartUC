"""LLM access through LiteLLM (any provider) + embeddings with a key-less fallback."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from typing import Any

import numpy as np

from ..config import get_settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # find the first {...} or [...] block
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"LLM did not return JSON: {text[:300]}")


async def complete(messages: list[dict], *, fast: bool = False, temperature: float | None = None,
                   max_tokens: int = 2000) -> str:
    s = get_settings()
    try:
        import litellm
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("litellm is not installed") from e
    litellm.suppress_debug_info = True
    model = s.fast_model if fast else s.llm_model
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=s.llm_temperature if temperature is None else temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:  # provider/auth errors -> clear message for the UI
        raise LLMUnavailable(f"LLM call failed for model '{model}': {e}") from e
    return resp.choices[0].message.content or ""


async def complete_json(system: str, user: str, *, fast: bool = False, max_tokens: int = 3000) -> Any:
    messages = [
        {"role": "system", "content": system + "\n\nRespond with valid JSON only, no prose, no markdown fences."},
        {"role": "user", "content": user},
    ]
    text = await complete(messages, fast=fast, temperature=0.0, max_tokens=max_tokens)
    try:
        return _extract_json(text)
    except ValueError:
        # one repair attempt
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": "That was not valid JSON. Return only the JSON object."})
        text = await complete(messages, fast=fast, temperature=0.0, max_tokens=max_tokens)
        return _extract_json(text)


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-zà-ÿ0-9]{3,}", re.IGNORECASE)
_STOP = set("""the and for with that this from are was were been have has will would could should into
over under than then them they their there here where when what which while about after before
des les une aux pour avec dans sur par est sont ont été être cette ces leur nous vous elle elles ils
use case uses using used system systems data based""".split())


def hash_embedding(text: str, dims: int) -> list[float]:
    """Feature-hashed bag of words + bigrams. Not semantic, but key-less and deterministic.

    Good enough to surface lexical overlaps in demos; swap EMBEDDING_MODEL for a real model in production.
    """
    vec = np.zeros(dims, dtype=np.float64)
    tokens = [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOP]
    grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    for g in grams:
        h = hashlib.blake2b(g.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % dims
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        weight = 1.0 if "_" not in g else 0.6
        vec[idx] += sign * weight
    # sub-linear scaling, then L2 normalise
    vec = np.sign(vec) * np.log1p(np.abs(vec))
    norm = np.linalg.norm(vec)
    if norm == 0:
        return vec.tolist()
    return (vec / norm).tolist()


async def embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    if not texts:
        return []
    if s.embedding_model == "hash":
        return [hash_embedding(t, s.embedding_dims) for t in texts]
    try:
        import litellm

        resp = await litellm.aembedding(model=s.embedding_model, input=texts)
    except Exception as e:
        log.warning("Embedding model failed (%s); falling back to hash embeddings", e)
        return [hash_embedding(t, s.embedding_dims) for t in texts]
    vectors = [d["embedding"] for d in resp.data]
    out = []
    for v in vectors:
        v = list(v)
        if len(v) != s.embedding_dims:  # keep the DB column consistent
            v = (v + [0.0] * s.embedding_dims)[: s.embedding_dims]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a), np.asarray(b)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
