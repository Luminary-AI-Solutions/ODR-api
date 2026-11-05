# ODR evals

Two evals for the deep-research agency, both built on published benchmarks and judged by Claude
(`claude-sonnet-5-5`):

| eval | what it measures | method |
|---|---|---|
| `search_quality` | Are the search results, the reranker's ordering, and the context handed to the writer relevant? | [UMBRELA](https://github.com/castorini/umbrela) (open reproduction of Bing's LLM relevance assessor, used for TREC 2024 RAG): 0-3 label per query-passage pair, then nDCG@10, P@10, reranker recall, SERP precision, context precision |
| `report_quality` | Is the final report good, and are its citations real? | [DeepResearch Bench](https://github.com/Ayanami0730/deep_research_bench) RACE (task-specific criteria + weights, scored against the benchmark's reference report; 0.5 = on par) and FACT (each cited statement checked against the cited source) |

Inputs: 20 English DeepResearch Bench tasks, topic-stratified (`INPUTS.md`, `data/drb/`, picked by
`select_tasks.py`). Prompts are copied verbatim from the upstream repos into `prompts.py` (Apache-2.0).

## Running

```bash
uv run python evals/sanity_checks.py                    # known-good / known-bad through the graders (~$0.65)
uv run python evals/run_pipeline.py --variant baseline  # the real pipeline on all 20 tasks
uv run python evals/grade_search.py --variant baseline
uv run python evals/grade_report.py --variant baseline
```

Everything is resumable per (task, rep): re-running skips what is already done. Runs that crash, time out,
or are served by the wrong model go to `errors.jsonl` and are never scored.

To compare a change, run it as a new variant (`v1`, `v2`, ...; the report builder only recognises those
names) and put the rationale in `.claude/hillclimb/<flow>/v1/change.md`:

```bash
uv run python evals/run_pipeline.py --variant v1 --writer-model anthropic:claude-opus-5-5
uv run python evals/grade_search.py --variant v1 && uv run python evals/grade_report.py --variant v1
```

UMBRELA labels are cached by (query, passage) in `.claude/hillclimb/search_quality/_labels.jsonl`, so a
variant only pays for passages it has not seen and identical passages always get identical labels.

## Harness gate

The scripts refuse to run if any file in `common.HARNESS_FILES` changed since the last approval.
After reviewing a change, approve it once with `--approve-harness` on any script.

## Things to know when reading the numbers

- **RACE is relative to a long reference.** DRB's reference reports are 5k-15k words; this pipeline
  writes far less, which caps comprehensiveness. That is how the official leaderboard scores too.
- **Judge = writer model.** Sonnet 5.5 writes the reports and judges them, by choice. LLM judges tend
  to prefer their own model's style, so treat absolute RACE scores as optimistic. Comparisons between
  variants that share the writer are less affected.
- **FACT checks grounding in the pipeline's own evidence** (the summaries/chunks the writer was given),
  not a fresh scrape of the URL as upstream does. A citation to a reference number the writer never had
  counts as unsupported.
- **RACE is scored in both article orders and averaged** (upstream uses one order); `meta.race_position_gap`
  shows how much the order mattered per case.
- With 20 tasks x 1 rep, the noise floor on RACE is roughly the reported ± half-width; differences
  smaller than that are not meaningful. Add `--reps 2` to tighten it.
