"""
Report-quality eval: DeepResearch Bench RACE (report quality vs. the task's reference report, using the
task's published criteria and weights) and FACT (are cited claims supported by the cited source?).

    uv run python evals/grade_report.py --variant baseline

Reads pipeline artifacts from .claude/hillclimb/pipeline/<variant>/ and writes
.claude/hillclimb/report_quality/<variant>/{results.jsonl,traces/,errors.jsonl}.

RACE (as upstream): the judge scores both articles 0-10 on every criterion; each dimension is the
criterion-weighted mean, the total is the dimension-weighted sum, and the reported score is
target / (target + reference), so 0.5 means "as good as the reference". Two deliberate changes:
  - the judge sees both orders (ours as article_1, then as article_2) and the two are averaged, to cancel
    position bias; upstream runs only ours-first.
  - citation markers are stripped with a regex instead of upstream's LLM cleaning pass (ours are structured).

FACT (adapted): every cited statement is checked with the upstream validation prompt against the
summaries/chunks the writer was actually given for that reference number (not a fresh scrape of the URL),
so it measures grounding in the pipeline's own evidence. Statements are taken deterministically from our
structured [[CITATION:n]] markers instead of upstream's LLM extraction, which skipped most citations on
long reports. Each statement is verified in its own call (batching made the judge over-strict).
Citing a reference number the writer was never given counts as unsupported.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
from collections import defaultdict

from common import (HC, JUDGE_MODEL, Judge, JudgeError, add_usage, append_jsonl, case_key, check_harness, done_keys,
                    load_by_id, load_jsonl, mean_ci, usage_cost, write_json)
from prompts import FACT_VALIDATE_PROMPT, RACE_MERGED_SCORE_PROMPT

DIMS = ("comprehensiveness", "insight", "instruction_following", "readability")
CITATION_RE = re.compile(r"\[\[CITATION:([\d,\s]+)\]\]")
ROLE_OF_USAGE_KEY = {"planner": "planner", "summarizer": "summarizer", "writer_initial": "writer"}


# --------------------------------------------------------------------------- RACE

def clean_article(draft: str) -> str:
    return re.sub(r"[ \t]+([.,;:])", r"\1", CITATION_RE.sub("", draft)).strip()


def crit_name(c: dict) -> str:
    """Criterion name as used in prompt, schema and weights. Double quotes become single quotes: an enum value
    containing '"' came back unescaped from the judge (task 86), breaking the JSON."""
    return c["criterion"].replace('"', "'")


def criteria_prompt_list(criteria: dict) -> str:
    """Same as upstream format_criteria_list: criterion + explanation, no weights."""
    return json.dumps(
        {dim: [{"criterion": crit_name(c), "explanation": c["explanation"]} for c in items]
         for dim, items in criteria["criterions"].items()},
        ensure_ascii=False, indent=2,
    )


def race_schema(criteria: dict) -> dict:
    def dim_schema(items: list[dict]) -> dict:
        return {"type": "array", "items": {
            "type": "object",
            "properties": {
                "criterion": {"type": "string", "enum": [crit_name(c) for c in items]},
                "analysis": {"type": "string"},
                "article_1_score": {"type": "number"},
                "article_2_score": {"type": "number"},
            },
            "required": ["criterion", "analysis", "article_1_score", "article_2_score"],
            "additionalProperties": False,
        }}
    dims = criteria["criterions"]
    return {"type": "object", "properties": {d: dim_schema(dims[d]) for d in dims},
            "required": list(dims), "additionalProperties": False}


def weighted_scores(judged: dict, criteria: dict) -> dict:
    """Upstream calculate_weighted_scores, with exact criterion matching (the schema enforces the text)."""
    out = {"article_1": {"dims": {}, "total": 0.0}, "article_2": {"dims": {}, "total": 0.0}, "coverage": {}}
    for dim, items in criteria["criterions"].items():
        weights = {crit_name(c): c["weight"] for c in items}
        seen, s1, s2, wsum = set(), 0.0, 0.0, 0.0
        for row in judged.get(dim, []):
            crit = row["criterion"]
            if crit in seen or crit not in weights:
                continue
            seen.add(crit)
            w = weights[crit]
            s1 += min(10.0, max(0.0, float(row["article_1_score"]))) * w
            s2 += min(10.0, max(0.0, float(row["article_2_score"]))) * w
            wsum += w
        a1, a2 = (s1 / wsum, s2 / wsum) if wsum else (0.0, 0.0)
        out["article_1"]["dims"][dim], out["article_2"]["dims"][dim] = a1, a2
        dw = criteria["dimension_weight"].get(dim, 0.0)
        out["article_1"]["total"] += a1 * dw
        out["article_2"]["total"] += a2 * dw
        out["coverage"][dim] = f"{len(seen)}/{len(weights)}"
    return out


def ratio(a: float, b: float) -> float:
    return a / (a + b) if a + b > 0 else 0.0


async def race(judge: Judge, task: dict, criteria: dict, ours: str, reference: str) -> dict:
    schema, crit_list = race_schema(criteria), criteria_prompt_list(criteria)

    async def order(ours_first: bool):
        a1, a2 = (ours, reference) if ours_first else (reference, ours)
        prompt = RACE_MERGED_SCORE_PROMPT.format(task_prompt=task["prompt"], article_1=a1, article_2=a2, criteria_list=crit_list)
        res = await judge.json(prompt, schema, effort="high", max_tokens=64000)
        scores = weighted_scores(res.data, criteria)
        mine, ref = ("article_1", "article_2") if ours_first else ("article_2", "article_1")
        return res, {
            "overall": ratio(scores[mine]["total"], scores[ref]["total"]),
            "dims": {d: ratio(scores[mine]["dims"][d], scores[ref]["dims"][d]) for d in DIMS},
            "ours_raw": scores[mine], "reference_raw": scores[ref], "coverage": scores["coverage"],
            "judged": res.data,
        }

    (r1, fwd), (r2, rev) = await asyncio.gather(order(True), order(False))
    usage = add_usage(add_usage({}, r1.usage), r2.usage)
    return {
        "overall": (fwd["overall"] + rev["overall"]) / 2,
        "dims": {d: (fwd["dims"][d] + rev["dims"][d]) / 2 for d in DIMS},
        "position_gap": fwd["overall"] - rev["overall"],
        "orders": {"ours_first": fwd, "reference_first": rev},
        "usage": usage,
    }


# --------------------------------------------------------------------------- FACT

VALIDATE_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {"idx": {"type": "integer"}, "result": {"type": "string", "enum": ["supported", "unsupported", "unknown"]}},
        "required": ["idx", "result"], "additionalProperties": False,
    }}},
    "required": ["results"], "additionalProperties": False,
}


SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?!\[\[CITATION)")


def extract_pairs(draft: str, sources: dict) -> list[dict]:
    """
    Every (statement, reference) pair in the draft. Our citations are structured [[CITATION:n,m]] markers, so
    the statement is the sentence (or bullet / table row) carrying the marker; one pair per cited reference.
    Replaces upstream's LLM extraction, which stopped early on long reports (23 of 184 pairs in the pilot).
    """
    url_of = {s["ref_num"]: s["link"] for s in sources.values()}
    pairs, seen = [], set()
    for line in draft.splitlines():
        segments = [line] if line.lstrip().startswith("|") else SENTENCE_SPLIT_RE.split(line)
        for seg in segments:
            refs = [int(n) for m in CITATION_RE.findall(seg) for n in m.split(",") if n.strip().isdigit()]
            statement = re.sub(r"\*\*|__", "", clean_article(seg)).strip(" -*|#>")
            for ref in refs:
                if statement and (statement, ref) not in seen:
                    seen.add((statement, ref))
                    pairs.append({"fact": statement, "ref": ref, "url": url_of.get(ref)})
    return pairs


async def fact(judge: Judge, draft: str, sources: dict, materials: list[dict], sample: int | None = None, seed: int = 0) -> dict:
    usage: dict = {}
    pairs = extract_pairs(draft, sources)
    n_total = len(pairs)
    if sample and n_total > sample:
        # A fixed per-task random sample keeps citation accuracy unbiased at a fraction of the judge cost.
        pairs = random.Random(seed).sample(pairs, sample)

    evidence = defaultdict(list)
    for m in materials:
        evidence[m.get("source_ref_num")].append(m["content"])

    by_ref = defaultdict(list)
    for p in pairs:
        by_ref[p["ref"]].append(p)

    async def check(ref: int, group: list[dict]) -> None:
        if ref not in evidence:
            for p in group:
                p["result"], p["note"] = "unsupported", "reference number not in writer context"
            return
        reference = "\n\n---\n\n".join(evidence[ref])

        # One statement per call: batching many statements made the judge far stricter than FACT's own
        # "entirely or partially found = supported" rule (19 of 48 pilot verdicts flipped when unbatched).
        # The shared reference is cached; the first call warms the cache before the rest run.
        async def one(p: dict) -> None:
            res = await judge.json(FACT_VALIDATE_PROMPT.format(reference=reference, statements=f"1. {p['fact']}"),
                                   VALIDATE_SCHEMA, effort="medium", max_tokens=16000, cache_split="<statements>")
            add_usage(usage, res.usage)
            verdicts = {r["idx"]: r["result"] for r in res.data["results"]}
            p["result"] = verdicts.get(1, "unknown")

        await one(group[0])
        await asyncio.gather(*(one(p) for p in group[1:]))

    await asyncio.gather(*(check(ref, group) for ref, group in by_ref.items()))
    supported = sum(p["result"] == "supported" for p in pairs)
    unsupported = sum(p["result"] == "unsupported" for p in pairs)
    markers = len(CITATION_RE.findall(draft))
    return {
        "citation_accuracy": supported / (supported + unsupported) if supported + unsupported else 0.0,
        # Extrapolated to all pairs when only a sample was checked.
        "effective_citations": supported * n_total / len(pairs) if pairs else 0,
        "n_pairs": n_total, "n_checked": len(pairs), "n_unknown": len(pairs) - supported - unsupported,
        "n_invalid_ref": sum(p.get("note") == "reference number not in writer context" for p in pairs),
        "n_citation_markers": markers,
        "pairs": pairs, "usage": usage,
    }


# --------------------------------------------------------------------------- driver

def pipeline_cost(run: dict) -> float:
    models = {role: m.removeprefix("anthropic:") for role, m in run["requested_models"].items()}
    total = 0.0
    for key, u in (run["usage_statistics"].get("token_usage") or {}).items():
        role = ROLE_OF_USAGE_KEY.get(key) or ("refiner" if key.startswith("refiner") else None)
        if role is None:
            raise KeyError(f"Unknown usage key {key!r}")
        total += usage_cost(models[role], {"input_tokens": u["prompt_tokens"], "output_tokens": u["completion_tokens"]})
    return total


def trace_for(task: dict, ours: str, race_res: dict, fact_res: dict) -> list[dict]:
    fwd = race_res["orders"]["ours_first"]
    crit_rows = []
    for dim in DIMS:
        for row in fwd["judged"].get(dim, []):
            crit_rows.append(f"| {dim} | {row['criterion'].replace('|', '/')} | {row['article_1_score']} | {row['article_2_score']} |")
    race_md = (
        f"**RACE overall {race_res['overall']:.3f}** (ours-first {fwd['overall']:.3f}, reference-first "
        f"{race_res['orders']['reference_first']['overall']:.3f}); 0.5 = on par with the reference.\n\n"
        + " · ".join(f"{d} {race_res['dims'][d]:.3f}" for d in DIMS)
        + "\n\nPer-criterion scores from the ours-first pass:\n\n| dimension | criterion | ours | reference |\n|---|---|---|---|\n"
        + "\n".join(crit_rows)
    )
    analyses = "\n\n".join(f"**{d} · {r['criterion']}**: {r['analysis']}" for d in DIMS for r in fwd["judged"].get(d, []))
    fact_md = (
        f"**FACT**: {fact_res['effective_citations']} supported of {fact_res['n_pairs']} statement/citation pairs "
        f"(accuracy {fact_res['citation_accuracy']:.3f}, {fact_res['n_unknown']} unknown, {fact_res['n_invalid_ref']} cite a "
        f"reference the writer never had).\n\n| ref | result | statement |\n|---|---|---|\n"
        + "\n".join(f"| {p['ref']} | {p['result']} | {p['fact'][:300].replace('|', '/')} |" for p in fact_res["pairs"])
    )
    return [
        {"role": "system", "content": RACE_MERGED_SCORE_PROMPT},
        {"role": "user", "content": task["prompt"]},
        {"role": "assistant", "content": ours},
        {"role": "assistant", "content": race_md},
        {"role": "assistant", "content": "## RACE judge analyses (ours-first)\n\n" + analyses},
        {"role": "assistant", "content": fact_md},
    ]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--approve-harness", action="store_true")
    ap.add_argument("--fact-sample", type=int, default=None, help="Check a fixed random sample of N citations per report (default: all)")
    ap.add_argument("--budget-usd", type=float, default=None, help="Start no new case once judge spend reaches this")
    args = ap.parse_args()
    check_harness(args.approve_harness)

    tasks, criteria, references = load_by_id("tasks.jsonl"), load_by_id("criteria.jsonl"), load_by_id("reference.jsonl")
    runs = load_jsonl(HC / "pipeline" / args.variant / "runs.jsonl")
    out = HC / "report_quality" / args.variant
    results_path, errors_path = out / "results.jsonl", out / "errors.jsonl"
    finished = done_keys(results_path)
    todo = [r for r in runs if case_key(r["prompt_id"], r["rep"]) not in finished]
    print(f"report_quality/{args.variant}: grading {len(todo)} runs ({len(finished)} already graded)")
    judge = Judge(model=JUDGE_MODEL, concurrency=args.concurrency)

    case_sem = asyncio.Semaphore(3)  # few cases in flight, so the budget check bites before spend overshoots

    async def one(run: dict) -> None:
        async with case_sem:
            if args.budget_usd is not None and judge.spend_usd() >= args.budget_usd:
                print(f"  {case_key(run['prompt_id'], run['rep'])}: skipped, judge budget ${args.budget_usd:.2f} reached")
                return
            await grade_one(run)

    async def grade_one(run: dict) -> None:
        artifact = json.loads((HC / run["artifact"]).read_text(encoding="utf-8"))
        tid, key = run["prompt_id"], case_key(run["prompt_id"], run["rep"])
        tr = artifact["trace"]
        draft = tr.get("raw_draft", "")
        ours = clean_article(draft)
        materials = list(tr.get("writer_context", [])) + [m for rnd in tr.get("refinement_rounds", []) for m in rnd.get("new_context", [])]
        t0 = time.monotonic()
        try:
            race_res, fact_res = await asyncio.gather(
                race(judge, tasks[tid], criteria[tid], ours, references[tid]["article"]),
                fact(judge, draft, tr.get("unique_sources", {}), materials, sample=args.fact_sample, seed=tid),
            )
        except JudgeError as e:
            append_jsonl(errors_path, {"prompt_id": tid, "rep": run["rep"], "failure_class": e.failure_class,
                                       "message": str(e), "model": e.model, "usage": e.usage})
            print(f"  {key}: judge {e.failure_class}: {e}")
            return
        except Exception as e:  # grader crash: record and keep going, never score it
            append_jsonl(errors_path, {"prompt_id": run["prompt_id"], "rep": run["rep"], "failure_class": "grader_error",
                                       "message": f"{type(e).__name__}: {e}"})
            print(f"  {case_key(run['prompt_id'], run['rep'])}: grader error {type(e).__name__}: {e}")
            return
        judge_usage = add_usage(dict(race_res["usage"]), fact_res["usage"])
        write_json(out / "traces" / f"{key}.json", trace_for(tasks[tid], ours, race_res, fact_res))
        write_json(out / "details" / f"{key}.json", {"race": race_res, "fact": fact_res})
        grade = {
            "race_overall": race_res["overall"],
            **{f"race_{d}": race_res["dims"][d] for d in DIMS},
            "fact_citation_accuracy": fact_res["citation_accuracy"],
            "fact_effective_citations": float(fact_res["effective_citations"]),
        }
        append_jsonl(results_path, {
            "prompt_id": tid, "rep": run["rep"], "prompt": tasks[tid]["prompt"], "tags": [tasks[tid]["topic"]],
            "status": "ok", "stop_reason": "end_turn", "grade": grade,
            "model": JUDGE_MODEL, "judge_model": JUDGE_MODEL, "judge_usage": judge_usage,
            "judge_cost_usd": round(usage_cost(JUDGE_MODEL, judge_usage), 4),
            "pipeline_cost_usd": round(pipeline_cost(run), 4), "pipeline_latency_s": run["latency_s"],
            "report_words": len(ours.split()), "latency_s": round(time.monotonic() - t0, 1),
            "meta": {"pipeline_status": run["status"], "race_position_gap": round(race_res["position_gap"], 4),
                     "race_coverage": race_res["orders"]["ours_first"]["coverage"],
                     "fact": {k: fact_res[k] for k in ("n_pairs", "n_checked", "n_unknown", "n_invalid_ref", "n_citation_markers")}},
        })
        print(f"  {key}: RACE {race_res['overall']:.3f} (gap {race_res['position_gap']:+.3f}) "
              f"FACT acc {fact_res['citation_accuracy']:.2f} eff {fact_res['effective_citations']} / {fact_res['n_pairs']}, "
              f"{len(ours.split())} words")

    await asyncio.gather(*(one(r) for r in todo))

    rows = load_jsonl(results_path)
    print(f"\n{len(rows)} cases graded; judge spend this run ${judge.spend_usd():.2f}")
    for metric in ["race_overall", *[f"race_{d}" for d in DIMS], "fact_citation_accuracy", "fact_effective_citations"]:
        m, ci = mean_ci([r["grade"][metric] for r in rows])
        print(f"  {metric:<26} {m:.3f} ± {ci:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
