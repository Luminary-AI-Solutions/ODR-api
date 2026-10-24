"""
Relevance scoring with TypeSafe's Jev decision model, via OpenRouter's Decisions API
(https://openrouter.ai/docs/guides/community/jev). Uses the OpenRouter key the agents already use.

Jev answers a typed `score` question with a probability-weighted position on an ordered scale, so a
passage's score here is 0.0 (first level) to 3.0 (last level) on RELEVANCE_LEVELS.
"""
import asyncio
import logging
import random
from typing import List, Optional

import httpx

from app.core.exceptions import RankingAPIError

logger = logging.getLogger(__name__)

DECISIONS_PATH = "/api/alpha/decisions"
RELEVANCE_LEVELS = [
    "Unrelated: nothing in the passage bears on the sub-question",
    "Related: on the topic, but does not help answer the sub-question",
    "Partly answers: contains some facts or evidence that help answer the sub-question",
    "Directly answers: substantially answers the sub-question with specific facts or evidence",
]
RELEVANCE_INSTRUCTIONS = "How well does the passage help answer the sub_question, which is one part of the research_task?"

# One process-wide cap on in-flight Jev requests, shared by every pipeline run.
_semaphore: Optional[asyncio.Semaphore] = None


def _sem(concurrency: int) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(concurrency)
    return _semaphore


def decisions_url(openrouter_api_base: str) -> str:
    """https://openrouter.ai/api/v1 -> https://openrouter.ai/api/alpha/decisions"""
    base = openrouter_api_base.rstrip("/")
    root = base[: -len("/api/v1")] if base.endswith("/api/v1") else base.rsplit("/api", 1)[0]
    return root + DECISIONS_PATH


async def score_passages(
    task: str,
    sub_question: str,
    passages: List[str],
    *,
    api_key: str,
    url: str,
    model: str,
    concurrency: int = 16,
    max_attempts: int = 6,
    timeout: float = 60.0,
) -> List[float]:
    """Score each passage 0-3 for how well it answers `sub_question` within `task`. Raises RankingAPIError."""
    sem = _sem(concurrency)

    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout) as client:
        async def one(passage: str) -> float:
            body = {
                "model": model,
                "state": {"research_task": task, "sub_question": sub_question, "passage": passage},
                "questions": {"relevance": {"type": "score", "instructions": RELEVANCE_INSTRUCTIONS,
                                            "criteria": RELEVANCE_LEVELS}},
            }
            last = ""
            for attempt in range(max_attempts):
                try:
                    async with sem:
                        r = await client.post(url, json=body)
                except httpx.TransportError as e:
                    last = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(min(30, 2 ** attempt) + random.uniform(0, 1))
                    continue
                if r.status_code in (429, 529) or r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                    await asyncio.sleep(float(r.headers.get("retry-after") or min(30, 2 ** attempt)) + random.uniform(0, 1))
                    continue
                if r.status_code != 200:
                    raise RankingAPIError(f"Jev returned {r.status_code}: {r.text[:300]}")
                try:
                    return float(r.json()["answers"]["relevance"]["score"])
                except (KeyError, TypeError, ValueError) as e:
                    raise RankingAPIError(f"Unexpected Jev response: {r.text[:300]}") from e
            raise RankingAPIError(f"Jev failed after {max_attempts} attempts ({last})")

        return list(await asyncio.gather(*(one(p) for p in passages)))
