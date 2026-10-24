"""
Offline chunk-selection experiment: does a different selector pick more relevant chunks from the SAME pool?

    uv run python evals/chunk_selection.py --source-variant v1 build     # re-scrape + chunk, cache the pool
    uv run python evals/chunk_selection.py --source-variant v1 select    # run every selector on the pool
    uv run python evals/chunk_selection.py --source-variant v1 grade     # UMBRELA-grade each selection

No summarizer or writer is run. For each task in a recorded pipeline variant, the sources that went to
chunking are re-scraped and re-chunked with the pipeline's own helpers (cached, so every selector sees the
identical pool), then each selector picks chunks:

    cohere       what the pipeline does: Cohere rerank vs the source's sub-query, keep score >= the chunk
                 threshold, top-N per source. By default these are the chunks the recorded run actually
                 selected, matched by text (--cohere-live re-scores instead; the trial key's monthly quota ran out)
    jev          TypeSafe Jev score question over (task, sub-question, passage); keep level >= "partly
                 answers", top-N per source
    jev_budget   Jev scores, but per source exactly as many chunks as Cohere kept (equal-volume comparison)

Each selection is graded with UMBRELA against both the sub-query (aspect) and the full task, reusing the
search eval's label cache. This measures chunk relevance only, not report quality.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import statistics as st
import time

import httpx

from common import HC, JUDGE_MODEL, Judge, append_jsonl, load_jsonl, mean_ci, usage_cost, write_json
from grade_search import RELEVANT, Labeler

OUT = HC / "chunk_selection"
logger = logging.getLogger("chunk_selection")
logging.basicConfig(level=logging.WARNING)

# Jev through OpenRouter's Decisions API (native Jev request format, OpenRouter key):
# https://openrouter.ai/docs/guides/community/jev
JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_LEVELS = [
    "Unrelated: nothing in the passage bears on the sub-question",
    "Related: on the topic, but does not help answer the sub-question",
    "Partly answers: contains some facts or evidence that help answer the sub-question",
    "Directly answers: substantially answers the sub-question with specific facts or evidence",
]
JEV_MIN_LEVEL = 2  # "Partly answers"
COHERE_PACE_S = 7.0
COHERE_BATCH = 100  # trial keys also throttle large batches; scores are per pair, so batch size does not change them


def load_runs(variant: str) -> list[dict]:
    runs = []
    for r in load_jsonl(HC / "pipeline" / variant / "runs.jsonl"):
        a = json.loads((HC / r["artifact"]).read_text(encoding="utf-8"))
        runs.append(a)
    return runs


def subquery_for(trace: dict) -> dict[str, str]:
    """First sub-query that retrieved each link (the pipeline's round-robin merge keeps the earliest)."""
    out: dict[str, str] = {}
    for q, links in trace.get("search_queries", {}).items():
        for link in links:
            out.setdefault(link, q)
    return out


# --------------------------------------------------------------------------- build the pool

async def build(variant: str) -> None:
    from app.agencies.deep_research.config import DeepResearchConfig
    from app.agencies.deep_research.helpers import batch_scrape_urls_helper, chunk_content_helper
    from app.core.dependencies import get_settings

    config, settings = DeepResearchConfig(), get_settings()
    for a in load_runs(variant):
        path = OUT / variant / "pools" / f"{a['prompt_id']}.json"
        if path.exists():
            continue
        tr = a["trace"]
        links = tr.get("chunking_links", [])
        sq = subquery_for(tr)
        t0 = time.monotonic()
        scraped = await batch_scrape_urls_helper(urls=links, settings=settings, logger=logger)
        docs = [{"content": r.content, "metadata": {"link": link, "title": link}}
                for link, r in scraped.items() if r and r.status == "success" and r.content]
        grouped = await chunk_content_helper(documents_to_chunk=docs, chunk_settings=config.default_chunk_settings.model_dump(),
                                             max_chunks=config.max_total_chunks, logger=logger) if docs else {}
        pool = [{"id": f"{link}#{i}", "link": link, "subquery": sq.get(link, a["prompt"]), "content": c["content"]}
                for link, chunks in grouped.items() for i, c in enumerate(chunks)]
        write_json(path, {"prompt_id": a["prompt_id"], "prompt": a["prompt"], "topic": a["topic"], "pool": pool,
                          "n_links": len(links), "n_scraped": len(docs)})
        print(f"  {a['prompt_id']}: {len(docs)}/{len(links)} sources scraped, {len(pool)} chunks ({time.monotonic() - t0:.0f}s)")


def load_pools(variant: str) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((OUT / variant / "pools").glob("*.json"))]


# --------------------------------------------------------------------------- selectors

def per_source_top(pool: list[dict], scores: dict[str, float], keep, n_by_link: dict[str, int]) -> list[str]:
    by_link: dict[str, list[dict]] = {}
    for c in pool:
        by_link.setdefault(c["link"], []).append(c)
    picked = []
    for link, chunks in by_link.items():
        ranked = sorted((c for c in chunks if keep(scores[c["id"]])), key=lambda c: scores[c["id"]], reverse=True)
        picked += [c["id"] for c in ranked[: n_by_link.get(link, 0)]]
    return picked


async def cohere_scores(task: dict) -> dict[str, float]:
    from app.agencies.deep_research.config import DeepResearchConfig
    from app.agencies.services.ranking import rerank_documents
    from app.core.exceptions import RankingAPIError
    from app.core.dependencies import get_settings

    config, settings = DeepResearchConfig(), get_settings()
    scores: dict[str, float] = {}
    by_q: dict[str, list[dict]] = {}
    for c in task["pool"]:
        by_q.setdefault(c["subquery"], []).append(c)
    for q, chunks in by_q.items():
        for start in range(0, len(chunks), COHERE_BATCH):
            batch = chunks[start:start + COHERE_BATCH]
            for attempt in range(6):
                await asyncio.sleep(COHERE_PACE_S)  # stay under the trial key's ~10 calls/min
                try:
                    ranked = await rerank_documents(query=q, documents=[c["content"] for c in batch], model=config.reranker_model,
                                                    api_key=settings.get_rerank_api_key(config.rerank_provider),
                                                    provider=config.rerank_provider,
                                                    base_url=settings.get_rerank_api_base(config.rerank_provider),
                                                    relevance_threshold=0.0)
                    break
                except RankingAPIError:
                    if attempt == 5:
                        raise
                    await asyncio.sleep(65)  # let the per-minute window reset
            for item in ranked:
                scores[batch[item.index]["id"]] = item.score
    return scores


class Jev:
    def __init__(self, model: str, concurrency: int):
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise SystemExit("OPENROUTER_API_KEY is not set")
        self.model = model
        self.client = httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=60)
        self.sem = asyncio.Semaphore(concurrency)
        self.input_tokens = 0
        self.cost_usd = 0.0
        self.served: set[str] = set()

    async def score(self, task: str, subquery: str, passage: str) -> float:
        body = {
            "model": self.model,
            "state": {"research_task": task, "sub_question": subquery, "passage": passage},
            "questions": {"relevance": {
                "type": "score",
                "instructions": "How well does the passage help answer the sub_question, which is one part of the research_task?",
                "criteria": JEV_LEVELS,
            }},
        }
        for attempt in range(6):
            async with self.sem:
                r = await self.client.post(JEV_URL, json=body)
            if r.status_code in (429, 529) or r.status_code >= 500:
                await asyncio.sleep(float(r.headers.get("retry-after") or min(30, 2 ** attempt)) + random.uniform(0, 1))
                continue
            if r.status_code != 200:
                raise RuntimeError(f"Jev {r.status_code}: {r.text[:300]}")
            data = r.json()
            usage = data.get("usage", {})
            self.input_tokens += usage.get("input_tokens", 0)
            self.cost_usd += usage.get("cost", 0.0)
            served = data.get("model", "")
            if not served.startswith(self.model):  # e.g. typesafe/jev-1.13 -> typesafe/jev-1.13-20260917
                raise RuntimeError(f"Jev served by {served!r}, requested {self.model!r}")
            self.served.add(served)
            return float(data["answers"]["relevance"]["score"])
        raise RuntimeError("Jev: gave up after retries")


async def select(variant: str, jev_model: str, cohere_live: bool) -> None:
    from app.agencies.deep_research.config import DeepResearchConfig

    config = DeepResearchConfig()
    top_n, threshold = config.top_n_chunks_per_source, config.chunk_rerank_relevance_threshold
    jev = Jev(jev_model, concurrency=16)
    recorded_chunks = {
        a["prompt_id"]: [m for m in a["trace"].get("writer_context", [])
                         if m["type"] == "chunk" and m["link"] in set(a["trace"].get("chunking_links", []))]
        for a in load_runs(variant)
    }
    for task in load_pools(variant):
        path = OUT / variant / "selections" / f"{task['prompt_id']}.json"
        if path.exists() or not task["pool"]:
            continue
        links = {c["link"] for c in task["pool"]}
        t0 = time.monotonic()
        if cohere_live:
            co = await cohere_scores(task)
            cohere_pick = per_source_top(task["pool"], co, lambda s: s >= threshold, {l: top_n for l in links})
        else:
            # Cohere's monthly trial quota is spent, so use what the pipeline actually picked in the recorded run,
            # matched to the rebuilt pool by chunk text (initial chunking only; refinement chunks are not in the pool).
            co = {}
            recorded = {(m["link"], m["content"]) for m in recorded_chunks[task["prompt_id"]]}
            cohere_pick = [c["id"] for c in task["pool"] if (c["link"], c["content"]) in recorded]
            print(f"    recorded Cohere picks matched: {len(cohere_pick)}/{len(recorded)}")
        t_co = time.monotonic() - t0
        cohere_n = {l: sum(cid.startswith(l + "#") for cid in cohere_pick) for l in links}
        t0 = time.monotonic()
        jv_list = await asyncio.gather(*(jev.score(task["prompt"], c["subquery"], c["content"]) for c in task["pool"]))
        t_jev = time.monotonic() - t0
        jv = {c["id"]: s for c, s in zip(task["pool"], jv_list)}
        write_json(path, {
            "prompt_id": task["prompt_id"],
            "scores": {"cohere": co, "jev": jv},
            "selections": {
                "cohere": cohere_pick,
                "jev": per_source_top(task["pool"], jv, lambda s: s >= JEV_MIN_LEVEL, {l: top_n for l in links}),
                "jev_budget": per_source_top(task["pool"], jv, lambda s: True, cohere_n),
            },
            "latency_s": {"cohere": round(t_co, 1), "jev": round(t_jev, 1)},
        })
        print(f"  {task['prompt_id']}: {len(task['pool'])} chunks; cohere {t_co:.0f}s, jev {t_jev:.0f}s")
    print(f"Jev input tokens {jev.input_tokens:,}, cost ${jev.cost_usd:.3f} (OpenRouter usage.cost); served by {sorted(map(str, jev.served))}")


# --------------------------------------------------------------------------- grade

async def grade(variant: str) -> None:
    labeler = Labeler(Judge(model=JUDGE_MODEL, concurrency=16))
    rows = []
    for task in load_pools(variant):
        sel_path = OUT / variant / "selections" / f"{task['prompt_id']}.json"
        if not sel_path.exists():
            continue
        sel = json.loads(sel_path.read_text(encoding="utf-8"))
        chunk = {c["id"]: c for c in task["pool"]}
        row = {"prompt_id": task["prompt_id"], "topic": task["topic"]}
        for name, ids in sel["selections"].items():
            picked = [chunk[i] for i in ids]
            aspect = await asyncio.gather(*(labeler.label(c["subquery"], c["content"]) for c in picked))
            whole = await asyncio.gather(*(labeler.label(task["prompt"], c["content"]) for c in picked))
            ga, gt = [l["grade"] for l in aspect], [l["grade"] for l in whole]
            row[name] = {
                "n": len(picked),
                "precision": sum(g >= RELEVANT for g in ga) / len(ga) if ga else 0.0,
                "relevance": st.mean(ga) if ga else 0.0,
                "task_precision": sum(g >= RELEVANT for g in gt) / len(gt) if gt else 0.0,
                "task_relevance": st.mean(gt) if gt else 0.0,
                "relevant_count": sum(g >= RELEVANT for g in ga),
            }
        row["latency_s"] = sel["latency_s"]
        rows.append(row)
    write_json(OUT / variant / "results.json", rows)

    print(f"\n{len(rows)} tasks; {labeler.calls} new UMBRELA labels, judge spend ${labeler.judge.spend_usd():.2f}\n")
    print("| selector | chunks/task | precision (aspect) | relevance 0-3 | task precision | task relevance | relevant chunks/task |")
    print("|---|---|---|---|---|---|---|")
    for name in ("cohere", "jev", "jev_budget"):
        def m(key: str) -> str:
            mu, ci = mean_ci([r[name][key] for r in rows])
            return f"{mu:.3f} ± {ci:.3f}"
        print(f"| {name} | {st.mean(r[name]['n'] for r in rows):.0f} | {m('precision')} | {m('relevance')} | "
              f"{m('task_precision')} | {m('task_relevance')} | {st.mean(r[name]['relevant_count'] for r in rows):.1f} |")
    for name in ("jev", "jev_budget"):
        d, ci = mean_ci([r[name]["precision"] - r["cohere"]["precision"] for r in rows])
        print(f"paired precision delta {name} - cohere: {d:+.3f} ± {ci:.3f}")
    print(f"median selection latency per task: cohere {st.median(r['latency_s']['cohere'] for r in rows):.0f}s, "
          f"jev {st.median(r['latency_s']['jev'] for r in rows):.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["build", "select", "grade"])
    ap.add_argument("--source-variant", default="v1")
    ap.add_argument("--cohere-live", action="store_true", help="Re-run Cohere (needs quota) instead of using the recorded picks")
    ap.add_argument("--jev-model", default="typesafe/jev-1.13", help="Pinned, so the result is reproducible (jev-latest moves)")
    args = ap.parse_args()
    if args.step == "build":
        asyncio.run(build(args.source_variant))
    elif args.step == "select":
        asyncio.run(select(args.source_variant, args.jev_model, args.cohere_live))
    else:
        asyncio.run(grade(args.source_variant))


if __name__ == "__main__":
    main()
