"""
Paired comparison of two variants on both evals: per-metric means, the mean per-task difference and its
95% CI (a difference is only called real when the CI excludes zero).

    uv run python evals/compare.py baseline v1
"""
from __future__ import annotations

import argparse
import statistics as st

from common import HC, load_jsonl, mean_ci


def by_task(flow: str, variant: str) -> dict[int, dict]:
    rows: dict[int, list[dict]] = {}
    for r in load_jsonl(HC / flow / variant / "results.jsonl"):
        if r.get("status") == "ok":
            rows.setdefault(r["prompt_id"], []).append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    args = ap.parse_args()

    for flow, extra in (("search_quality", []), ("report_quality", ["pipeline_cost_usd", "pipeline_latency_s", "report_words"])):
        a, b = by_task(flow, args.a), by_task(flow, args.b)
        common = sorted(set(a) & set(b))
        if not common:
            print(f"{flow}: no tasks graded in both variants")
            continue
        metrics = list(a[common[0]][0]["grade"])
        print(f"\n## {flow}: {args.a} vs {args.b} on {len(common)} paired tasks\n")
        print(f"| metric | {args.a} | {args.b} | paired delta (95% CI) | |")
        print("|---|---|---|---|---|")

        def val(rows: list[dict], key: str) -> float:
            return st.mean(r["grade"][key] if key in r["grade"] else r[key] for r in rows)

        for key in metrics + extra:
            va = [val(a[t], key) for t in common]
            vb = [val(b[t], key) for t in common]
            d, ci = mean_ci([y - x for x, y in zip(va, vb)])
            sig = "significant" if abs(d) > ci else "within noise"
            print(f"| {key} | {st.mean(va):.3f} | {st.mean(vb):.3f} | {d:+.3f} ± {ci:.3f} | {sig} |")


if __name__ == "__main__":
    main()
