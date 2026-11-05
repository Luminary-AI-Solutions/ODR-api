"""
Known-good / known-bad inputs pushed through the real graders, to catch a broken or lenient judge
before paying for a full pass.

    uv run python evals/sanity_checks.py

Expectations:
    UMBRELA   on-topic answer -> 3; empty / off-topic passage -> 0
    FACT      statement copied from the source -> supported; contradicted number -> unsupported
    RACE      reference vs itself ~0.5; empty, "I don't know", and another task's report all well below 0.5
"""
from __future__ import annotations

import asyncio

from common import JUDGE_MODEL, Judge, check_harness, load_by_id
from grade_report import race
from grade_search import UMBRELA_SCHEMA
from prompts import FACT_VALIDATE_PROMPT, UMBRELA_PROMPT
from grade_report import VALIDATE_SCHEMA

RACE_TASK = 52        # shortest reference report in the set
WRONG_TASK_REF = 99   # a different task's reference report: confident answer to the wrong question


async def main() -> None:
    check_harness(False)
    judge = Judge(model=JUDGE_MODEL, concurrency=8)
    checks: list[tuple[str, bool, str]] = []

    query = "What is the capital of France?"
    umbrela_cases = [("on-topic", "Paris is the capital and largest city of France.", lambda g: g == 3),
                     ("empty", "", lambda g: g == 0),
                     ("off-topic", "Bananas are an excellent source of potassium.", lambda g: g == 0)]
    res = await asyncio.gather(*(judge.json(UMBRELA_PROMPT.format(query=query, passage=p), UMBRELA_SCHEMA, effort="low", max_tokens=4000)
                                 for _, p, _ in umbrela_cases))
    for (name, _, ok), r in zip(umbrela_cases, res):
        g = r.data["final_score"]
        checks.append((f"UMBRELA {name}", ok(g), f"grade {g}"))

    reference = "In 2023 the Hornsdale plant expanded its battery capacity to 150 MW and supplied grid stabilisation services."
    statements = "1. Hornsdale's battery capacity reached 150 MW in 2023.\n2. Hornsdale's battery capacity reached 500 MW in 2023."
    r = await judge.json(FACT_VALIDATE_PROMPT.format(reference=reference, statements=statements), VALIDATE_SCHEMA, effort="medium")
    verdicts = {v["idx"]: v["result"] for v in r.data["results"]}
    checks.append(("FACT copied statement", verdicts.get(1) == "supported", verdicts.get(1)))
    checks.append(("FACT contradicted number", verdicts.get(2) == "unsupported", verdicts.get(2)))

    tasks, criteria, refs = load_by_id("tasks.jsonl"), load_by_id("criteria.jsonl"), load_by_id("reference.jsonl")
    t, c, ref = tasks[RACE_TASK], criteria[RACE_TASK], refs[RACE_TASK]["article"]
    race_cases = [("reference vs itself", ref, lambda s: 0.42 <= s <= 0.58),
                  ("empty article", "", lambda s: s < 0.15),
                  ("'I don't know'", "I don't know.", lambda s: s < 0.15),
                  ("another task's report", refs[WRONG_TASK_REF]["article"], lambda s: s < 0.35)]
    res = await asyncio.gather(*(race(judge, t, c, art, ref) for _, art, _ in race_cases))
    for (name, _, ok), r in zip(race_cases, res):
        checks.append((f"RACE {name}", ok(r["overall"]), f"{r['overall']:.3f} (position gap {r['position_gap']:+.3f})"))

    width = max(len(n) for n, _, _ in checks)
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    print(f"\njudge spend ${judge.spend_usd():.2f}; {sum(ok for _, ok, _ in checks)}/{len(checks)} passed")


if __name__ == "__main__":
    asyncio.run(main())
