"""
Search-quality eval: UMBRELA relevance labels (0-3) on every search result and every piece of writer
context, then standard IR metrics over them.

    uv run python evals/grade_search.py --variant baseline

Reads pipeline artifacts from .claude/hillclimb/pipeline/<variant>/ (run evals/run_pipeline.py first) and
writes .claude/hillclimb/search_quality/<variant>/{results.jsonl,traces/,errors.jsonl}.

Each passage gets two UMBRELA labels:
    aspect label  vs. the planner sub-query that retrieved it (max over queries if several did). UMBRELA is
                  built for one focused query, and a research task has many aspects: a result about one of
                  three investors is a perfect hit for its sub-query but only "related" to the whole task.
    task label    vs. the full research task, for a task-level view of ranking and context.

Metrics per case (relevant = aspect grade >= 2, i.e. the passage contains some answer):
    rerank_ndcg10      nDCG@10 of the reranker's order on aspect grades, ideal ranking over ALL initial results
                       (so dropping a relevant result costs as much as ranking it low)
    rerank_p10         share of the reranker's top 10 that are relevant
    rerank_recall      share of relevant initial results the reranker kept
    serp_precision     share of raw (pre-rerank) results that are relevant: search API + query-writing quality
    serp_relevance     mean raw-result aspect grade, 0-3
    context_precision  share of summaries/chunks handed to the writer that are relevant
    context_relevance  mean writer-context aspect grade, 0-3
    task_ndcg10        nDCG@10 of the reranker's order on full-task grades
    context_task_rel   mean writer-context full-task grade, 0-3
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import time

from common import HC, JUDGE_MODEL, Judge, JudgeError, append_jsonl, case_key, check_harness, done_keys, load_jsonl, mean_ci, usage_cost, write_json
from prompts import UMBRELA_PROMPT

RELEVANT = 2
UMBRELA_SCHEMA = {
    "type": "object",
    "properties": {"final_score": {"type": "integer", "enum": [0, 1, 2, 3]}},
    "required": ["final_score"],
    "additionalProperties": False,
}
# Bump when the prompt, passage construction or judge settings change, so cached labels are not reused.
LABEL_VERSION = "umbrela-bing-zeroshot/v1"


def passage_for_result(r: dict) -> str:
    return f"{r.get('title', '')}\n{r.get('snippet') or ''}".strip()


def label_key(query: str, passage: str, model: str) -> str:
    return hashlib.sha256(f"{LABEL_VERSION}|{model}|{query}|{passage}".encode()).hexdigest()


def dcg(grades: list[int]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(grades))


class Labeler:
    """UMBRELA labels with an on-disk cache shared across variants and reps."""

    def __init__(self, judge: Judge):
        self.judge = judge
        self.path = HC / "search_quality" / "_labels.jsonl"
        self.cache = {r["key"]: r for r in load_jsonl(self.path)}
        self.inflight: dict[str, asyncio.Task] = {}
        self.calls = 0

    async def label(self, query: str, passage: str) -> dict:
        key = label_key(query, passage, self.judge.model)
        if key in self.cache:
            return {**self.cache[key], "usage": {}}  # already paid for in an earlier run
        if key not in self.inflight:
            self.inflight[key] = asyncio.ensure_future(self._fetch(key, query, passage))
        return await self.inflight[key]

    async def _fetch(self, key: str, query: str, passage: str) -> dict:
        res = await self.judge.json(UMBRELA_PROMPT.format(query=query, passage=passage), UMBRELA_SCHEMA, effort="low", max_tokens=4000)
        row = {"key": key, "grade": int(res.data["final_score"]), "model": res.model, "usage": res.usage, "attempts": res.attempts}
        self.cache[key] = row
        append_jsonl(self.path, row)
        self.calls += 1
        return row


async def grade_case(artifact: dict, labeler: Labeler) -> tuple[dict, list]:
    task = artifact["prompt"]
    tr = artifact["trace"]

    initial = tr.get("initial_search_results", [])
    rounds = tr.get("refinement_rounds", [])
    refinement_results = [r for rnd in rounds for r in rnd.get("search_results", [])]
    context = list(tr.get("writer_context", [])) + [m for rnd in rounds for m in rnd.get("new_context", [])]

    # Which sub-queries retrieved each link (initial search plus every refinement round).
    queries_for: dict[str, list[str]] = {}
    for qmap in [tr.get("search_queries", {})] + [rnd.get("search_queries", {}) for rnd in rounds]:
        for q, links in qmap.items():
            for link in links:
                queries_for.setdefault(link, [])
                if q not in queries_for[link]:
                    queries_for[link].append(q)

    async def aspect(link: str, passage: str) -> list[dict]:
        # Fall back to the task for a link no recorded query produced (should not happen).
        return list(await asyncio.gather(*(labeler.label(q, passage) for q in queries_for.get(link) or [task])))

    def best(labs: list[dict]) -> int:
        return max(lab["grade"] for lab in labs)

    res_passages = [passage_for_result(r) for r in initial]
    ref_passages = [passage_for_result(r) for r in refinement_results]
    res_aspect, res_task, ref_aspect, ctx_aspect, ctx_task = await asyncio.gather(
        asyncio.gather(*(aspect(r["link"], p) for r, p in zip(initial, res_passages))),
        asyncio.gather(*(labeler.label(task, p) for p in res_passages)),
        asyncio.gather(*(aspect(r["link"], p) for r, p in zip(refinement_results, ref_passages))),
        asyncio.gather(*(aspect(m.get("link", ""), m["content"]) for m in context)),
        asyncio.gather(*(labeler.label(task, m["content"]) for m in context)),
    )

    aspect_by_link = {r["link"]: best(labs) for r, labs in zip(initial, res_aspect)}
    task_by_link = {r["link"]: lab["grade"] for r, lab in zip(initial, res_task)}
    reranked = [l for l in tr.get("reranked_initial_links", []) if l in aspect_by_link]
    ranked = [aspect_by_link[l] for l in reranked]
    all_grades = list(aspect_by_link.values())
    ctx_grades = [best(labs) for labs in ctx_aspect]
    ctx_task_grades = [lab["grade"] for lab in ctx_task]
    relevant_links = {l for l, g in aspect_by_link.items() if g >= RELEVANT}

    def ndcg10(order: list[int], pool: list[int]) -> float:
        ideal = dcg(sorted(pool, reverse=True)[:10])
        return dcg(order[:10]) / ideal if ideal else 0.0

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    grade = {
        "rerank_ndcg10": ndcg10(ranked, all_grades),
        "rerank_p10": mean([g >= RELEVANT for g in ranked[:10]]),
        "rerank_recall": len(relevant_links & set(reranked)) / len(relevant_links) if relevant_links else 1.0,
        "serp_precision": mean([g >= RELEVANT for g in all_grades]),
        "serp_relevance": mean(all_grades),
        "context_precision": mean([g >= RELEVANT for g in ctx_grades]),
        "context_relevance": mean(ctx_grades),
        "task_ndcg10": ndcg10([task_by_link[l] for l in reranked], list(task_by_link.values())),
        "context_task_rel": mean(ctx_task_grades),
    }

    every = [lab for labs in [*res_aspect, *ref_aspect, *ctx_aspect] for lab in labs] + [*res_task, *ctx_task]
    judge_usage: dict = {}
    for lab in {lab["key"]: lab for lab in every}.values():
        for k, v in lab["usage"].items():
            judge_usage[k] = judge_usage.get(k, 0) + v

    # Human-readable trace: what was judged and how.
    rank_of = {l: i + 1 for i, l in enumerate(reranked)}
    serp_rows = sorted(
        ((rank_of.get(r["link"]), aspect_by_link[r["link"]], task_by_link[r["link"]], (queries_for.get(r["link"]) or ["?"])[0],
          r.get("title", ""), r["link"]) for r in initial),
        key=lambda x: (x[0] is None, x[0] or 0),
    )
    serp_md = "| rerank pos | aspect | task | sub-query | title | link |\n|---|---|---|---|---|---|\n" + "\n".join(
        f"| {pos or 'dropped'} | {ga} | {gt} | {q[:60].replace('|', '/')} | {t.replace('|', '/')} | {l} |"
        for pos, ga, gt, q, t, l in serp_rows
    )
    ctx_md = "\n\n".join(
        f"**[{m.get('source_ref_num')}] {m.get('type')} · aspect {best(a)} · task {t['grade']}** · {m.get('link')}\n\n{m['content'][:1500]}"
        for m, a, t in zip(context, ctx_aspect, ctx_task)
    )
    trace = [
        {"role": "system", "content": UMBRELA_PROMPT},
        {"role": "user", "content": task},
        {"role": "assistant", "content": f"## Search results ({len(initial)}), in reranker order\n\n{serp_md}"},
        {"role": "assistant", "content": f"## Writer context ({len(context)} items)\n\n{ctx_md}"},
    ]
    meta = {
        "n_results": len(initial), "n_reranked": len(reranked), "n_relevant_results": len(relevant_links),
        "n_context": len(ctx_grades), "n_refinement_results": len(refinement_results),
        "refinement_relevant": sum(best(labs) >= RELEVANT for labs in ref_aspect),
        "n_subqueries": len(tr.get("search_queries", {})),
    }
    return {"grade": grade, "judge_usage": judge_usage, "meta": meta}, trace


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--approve-harness", action="store_true")
    args = ap.parse_args()
    check_harness(args.approve_harness)

    runs = [r for r in load_jsonl(HC / "pipeline" / args.variant / "runs.jsonl")]
    out = HC / "search_quality" / args.variant
    results_path, errors_path = out / "results.jsonl", out / "errors.jsonl"
    finished = done_keys(results_path)
    todo = [r for r in runs if case_key(r["prompt_id"], r["rep"]) not in finished]
    print(f"search_quality/{args.variant}: grading {len(todo)} runs ({len(finished)} already graded)")

    labeler = Labeler(Judge(model=JUDGE_MODEL, concurrency=args.concurrency))

    async def one(run: dict) -> None:
        import json
        artifact = json.loads((HC / run["artifact"]).read_text(encoding="utf-8"))
        key = case_key(run["prompt_id"], run["rep"])
        t0 = time.monotonic()
        try:
            graded, trace = await grade_case(artifact, labeler)
        except JudgeError as e:
            append_jsonl(errors_path, {"prompt_id": run["prompt_id"], "rep": run["rep"], "failure_class": e.failure_class,
                                       "message": str(e), "model": e.model, "usage": e.usage})
            print(f"  {key}: judge {e.failure_class}: {e}")
            return
        except Exception as e:  # grader crash: record and keep going, never score it
            append_jsonl(errors_path, {"prompt_id": run["prompt_id"], "rep": run["rep"], "failure_class": "grader_error",
                                       "message": f"{type(e).__name__}: {e}"})
            print(f"  {case_key(run['prompt_id'], run['rep'])}: grader error {type(e).__name__}: {e}")
            return
        write_json(out / "traces" / f"{key}.json", trace)
        append_jsonl(results_path, {
            "prompt_id": run["prompt_id"], "rep": run["rep"], "prompt": artifact["prompt"], "tags": [artifact["topic"]],
            "status": "ok", "stop_reason": "end_turn", **graded,
            "model": JUDGE_MODEL, "judge_model": JUDGE_MODEL,
            "judge_cost_usd": round(usage_cost(JUDGE_MODEL, graded["judge_usage"]), 4),
            "latency_s": round(time.monotonic() - t0, 1),
        })
        g = graded["grade"]
        print(f"  {key}: ndcg10={g['rerank_ndcg10']:.2f} p10={g['rerank_p10']:.2f} recall={g['rerank_recall']:.2f} "
              f"serp_prec={g['serp_precision']:.2f} ctx_prec={g['context_precision']:.2f} ({graded['meta']})")

    await asyncio.gather(*(one(r) for r in todo))

    rows = load_jsonl(results_path)
    print(f"\n{len(rows)} cases graded; {labeler.calls} new UMBRELA labels this run, judge spend ${labeler.judge.spend_usd():.2f}")
    for metric in ("rerank_ndcg10", "rerank_p10", "rerank_recall", "serp_precision", "serp_relevance",
                   "context_precision", "context_relevance", "task_ndcg10", "context_task_rel"):
        m, ci = mean_ci([r["grade"][metric] for r in rows])
        print(f"  {metric:<18} {m:.3f} ± {ci:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
