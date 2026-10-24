"""
Pick a topic-stratified subset of DeepResearch Bench's English tasks (deterministic).

Source: https://github.com/Ayanami0730/deep_research_bench (Apache-2.0). Expects the upstream
query.jsonl, criteria.jsonl and reference.jsonl in --src; writes the subset to evals/data/drb/.

    uv run python evals/select_tasks.py --src <dir with upstream files> --n 20
"""
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).parent / "data" / "drb"


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stratified(tasks: list[dict], n: int, seed: int) -> list[dict]:
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(tasks, key=lambda t: t["id"]):
        by_topic[t["topic"]].append(t)
    rng = random.Random(seed)
    for group in by_topic.values():
        rng.shuffle(group)
    # Proportional allocation (largest remainder), at least 0 per topic.
    quotas = {topic: n * len(g) / len(tasks) for topic, g in by_topic.items()}
    alloc = {topic: int(q) for topic, q in quotas.items()}
    for topic in sorted(quotas, key=lambda t: (-(quotas[t] - alloc[t]), t))[: n - sum(alloc.values())]:
        alloc[topic] += 1
    picked = [t for topic, g in by_topic.items() for t in g[: alloc[topic]]]
    return sorted(picked, key=lambda t: t["id"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    english = [t for t in load(args.src / "query.jsonl") if t["language"] == "en"]
    picked = stratified(english, args.n, args.seed)
    ids = {t["id"] for t in picked}

    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in [
        ("tasks.jsonl", picked),
        ("criteria.jsonl", [r for r in load(args.src / "criteria.jsonl") if r["id"] in ids]),
        ("reference.jsonl", [r for r in load(args.src / "reference.jsonl") if r["id"] in ids]),
    ]:
        assert len(rows) == len(ids), f"{name}: expected {len(ids)} rows, got {len(rows)}"
        (OUT / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"Selected {len(picked)} tasks: {sorted(ids)}")


if __name__ == "__main__":
    main()
