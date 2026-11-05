"""
Run the real deep-research pipeline over the eval tasks and save everything the graders need.

    uv run python evals/run_pipeline.py --variant baseline              # all 20 tasks, 1 rep
    uv run python evals/run_pipeline.py --variant baseline --ids 52 53  # a pilot
    uv run python evals/run_pipeline.py --variant v1 --writer-model anthropic:claude-opus-5-5

Calls `run_deep_research_orchestration` (the same entry point the WebSocket route uses) with a trace
dict, no Firestore and no WebSocket. Resumable: finished (task, rep) pairs are skipped on restart.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
import traceback

from common import HC, append_jsonl, case_key, check_harness, done_keys, load_tasks, with_timeout, write_json

ROLES = ("planner", "summarizer", "writer", "refiner")
DEFAULT_MODELS = {
    "planner": "anthropic:claude-sonnet-5-5",
    "summarizer": "anthropic:claude-haiku-5-5",
    "writer": "anthropic:claude-sonnet-5-5",
    "refiner": "anthropic:claude-sonnet-5-5",
}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--ids", type=int, nargs="*", help="Only these DRB task ids")
    ap.add_argument("--concurrency", type=int, default=2, help="Parallel pipeline runs (Cohere trial keys allow ~10 reranks/min)")
    ap.add_argument("--timeout-s", type=float, default=1800, help="Hard wall-clock ceiling per run")
    ap.add_argument("--approve-harness", action="store_true", help="Record the current harness digest as approved")
    for role in ROLES:
        ap.add_argument(f"--{role}-model", default=DEFAULT_MODELS[role])
    return ap.parse_args()


async def run_one(task: dict, rep: int, models: dict[str, str]) -> dict:
    # Imported here so the DEEP_RESEARCH_* env overrides set in main() are in place first.
    from app.agencies.deep_research.agents import get_agency_agents
    from app.agencies.deep_research.config import DeepResearchConfig
    from app.agencies.deep_research.orchestrator import run_deep_research_orchestration
    from app.core.dependencies import get_settings

    config = DeepResearchConfig()
    settings = get_settings()
    trace: dict = {}
    t0 = time.monotonic()
    response = await run_deep_research_orchestration(
        user_query=task["prompt"],
        agents_collection=get_agency_agents(config, settings),
        config=config,
        app_settings=settings,
        trace=trace,
    )
    return {
        "prompt_id": task["id"],
        "rep": rep,
        "prompt": task["prompt"],
        "topic": task["topic"],
        "latency_s": round(time.monotonic() - t0, 1),
        "requested_models": models,
        "config": {
            "rerank_provider": config.rerank_provider,
            "reranker_model": config.reranker_model,
            "max_refinement_loops": config.max_refinement_loops,
            "top_n_chunks_per_source": config.top_n_chunks_per_source,
        },
        "report": response.report,
        "usage_statistics": response.usage_statistics.model_dump(mode="json"),
        "trace": trace,
    }


def served_model_mismatch(artifact: dict) -> str | None:
    served = artifact["trace"].get("served_models", {})
    for role, requested in artifact["requested_models"].items():
        want = requested.removeprefix("anthropic:")
        got = served.get(role)
        if got and got != {want}:
            return f"{role}: requested {want}, served {sorted(map(str, got))}"
    return None


async def main() -> None:
    args = parse_args()
    check_harness(args.approve_harness)

    models = {role: getattr(args, f"{role}_model") for role in ROLES}
    for role, model_id in models.items():
        os.environ[f"DEEP_RESEARCH_{role.upper()}_MODEL_ID"] = model_id

    out = HC / "pipeline" / args.variant
    index, errors = out / "runs.jsonl", out / "errors.jsonl"
    finished = done_keys(index)
    todo = [(t, r) for t in load_tasks(args.ids) for r in range(args.reps) if case_key(t["id"], r) not in finished]
    print(f"{args.variant}: {len(todo)} runs to do ({len(finished)} already done), models={models}")

    sem = asyncio.Semaphore(args.concurrency)

    async def worker(task: dict, rep: int) -> None:
        key = case_key(task["id"], rep)
        async with sem:
            t0 = time.monotonic()
            try:
                artifact = await with_timeout(run_one(task, rep, models), args.timeout_s)
            except asyncio.TimeoutError:
                append_jsonl(errors, {"prompt_id": task["id"], "rep": rep, "failure_class": "timeout",
                                      "message": f"exceeded {args.timeout_s}s"})
                print(f"  {key}: TIMEOUT")
                return
            except Exception as e:  # pipeline crashed: harness/serving failure, not a graded outcome
                append_jsonl(errors, {"prompt_id": task["id"], "rep": rep, "failure_class": "pipeline_error",
                                      "message": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-4000:]})
                print(f"  {key}: ERROR {type(e).__name__}: {e}")
                return

        mismatch = served_model_mismatch(artifact)
        if mismatch:
            append_jsonl(errors, {"prompt_id": task["id"], "rep": rep, "failure_class": "model_mismatch", "message": mismatch})
            print(f"  {key}: MODEL MISMATCH {mismatch}")
            return

        draft = artifact["trace"].get("raw_draft", "")
        if artifact["report"].startswith("Could not generate report"):
            status = "no_context"  # nothing survived search + rerank; still scored, as a genuine failure
        elif draft.startswith("**Warning:** The report writer failed"):
            status = "writer_failed"
        else:
            status = "ok"
        path = out / "runs" / f"{key}.json"
        write_json(path, artifact)
        append_jsonl(index, {
            "prompt_id": task["id"], "rep": rep, "status": status, "latency_s": artifact["latency_s"],
            "wall_s": round(time.monotonic() - t0, 1), "artifact": str(path.relative_to(HC)),
            "requested_models": models, "usage_statistics": artifact["usage_statistics"],
        })
        u = artifact["usage_statistics"]
        print(f"  {key}: {status} in {artifact['latency_s']}s, {u.get('sources_processed_count')} sources, "
              f"{u.get('refinement_iterations_run')} refinement loops, report {len(artifact['report'])} chars")

    await asyncio.gather(*(worker(t, r) for t, r in todo))


if __name__ == "__main__":
    asyncio.run(main())
