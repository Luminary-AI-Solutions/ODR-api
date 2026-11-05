# v2: Jev scores search results and chunks (2026-10-24)

Change: TypeSafe's Jev decision model (via OpenRouter) replaces Cohere for scoring search results and chunks,
on top of v1's sub-query reranking. Jev scores each passage 0-3 for how well it answers its sub-question within
the full task. Cohere's trial quota was exhausted, so both stages switched together.
Report eval: RACE as before; FACT on a fixed random sample of 40 citations per report (v1 checked all).

| metric | v1 | v2 | paired delta (95% CI, 19 tasks) |
|---|---|---|---|
| RACE overall | 0.489 | 0.488 | -0.001 ± 0.014 (within noise) |
| FACT citation accuracy | 0.876 | 0.866 | -0.011 ± 0.035 (within noise) |
| pipeline latency / task (median) | 619 s | 277 s | **-296 ± 75 s** |
| pipeline cost / task | $0.42 | $0.36 | -$0.06 ± 0.07 (within noise) |

v2 vs baseline on all 20 tasks: RACE 0.365 -> 0.485 (+0.120 ± 0.056). Task 91, which timed out under v1,
finished in 4.8 min (RACE 0.424). 7/20 reports beat the DRB reference.

Reading: offline, Jev picked far more relevant chunks than Cohere from the same pool (precision 0.76 vs 0.44,
`evals/chunk_selection.py`), but report quality did not move. Once v1 stopped starving the writer, chunk
precision stopped being the bottleneck. What v2 does buy is operational: about 2x faster runs, no rate-limit
stalls, no dependency on the Cohere quota, and every task now completes.

Spend for v2 (Anthropic): pipeline $7.26 + judges $8.01. Jev on OpenRouter: about $0.03-0.05 per task.

---

# v1: sub-query reranking (2026-10-24)

Change: rerank each search result, and each source's chunks, against the planner sub-query that retrieved it
(not the full research prompt), always keep each sub-query's top 3, and merge round-robin so the summarized
top-10 covers every aspect. Same models and judge as the baseline. `rerank_against_subquery=false` reproduces the baseline.

Paired over the 19 tasks graded in both (task 91 timed out in v1, see below):

| metric | baseline | v1 | paired delta (95% CI) |
|---|---|---|---|
| **RACE overall** | 0.362 | **0.489** | **+0.127 ± 0.061** |
| comprehensiveness | 0.334 | 0.476 | +0.142 ± 0.065 |
| insight | 0.368 | 0.510 | +0.141 ± 0.065 |
| instruction following | 0.358 | 0.484 | +0.126 ± 0.065 |
| readability | 0.401 | 0.472 | +0.071 ± 0.052 |
| FACT citation accuracy | 0.848 | 0.876 | +0.028 ± 0.103 (within noise) |
| FACT supported citations | 82 | 152 | +70 ± 31 |
| **reranker recall** | 0.406 | **0.858** | +0.452 ± 0.171 |
| reranker nDCG@10 | 0.574 | 0.773 | +0.199 ± 0.121 |
| context precision | 0.389 | 0.472 | +0.084 ± 0.141 (within noise) |
| pipeline cost / task | $0.24 | $0.42 | +$0.18 |
| pipeline latency / task | 228 s | 572 s | +344 s |

17/19 tasks improved (the other two moved < 0.006); 9 now beat the DRB reference report (baseline: 2).
The previously starved tasks gained most: 81 0.19 -> 0.52, 83 0.26 -> 0.51, 87 0.29 -> 0.53, 75 no report -> 0.51.

Cost of the change: one Cohere call per sub-query and per source. On the trial key (~10 calls/min) runs are
~2.5x slower, and the largest task (91: 56+ sources, 2 refinement loops) did not finish within 60 minutes
(179 rate-limit hits; 21 chunk reranks fell back to unranked). Batching chunk reranks per sub-query, or a
production Cohere key, should remove most of this.

---

# Baseline results (2026-10-23)

Pipeline: planner/writer/refiner `claude-sonnet-5-5`, summarizer `claude-haiku-5-5`, Cohere `rerank-english-v3.0`
(threshold 0.25). Judge: `claude-sonnet-5-5`. 20 DeepResearch Bench tasks x 1 rep, no harness errors.
Per-case detail: `.claude/hillclimb/{search_quality,report_quality}/report.html` and `traces/`.

## Report quality (DeepResearch Bench RACE + FACT)

| metric | mean ± 95% CI |
|---|---|
| RACE overall (0.5 = on par with DRB reference) | **0.365 ± 0.054** |
| comprehensiveness | 0.337 ± 0.058 |
| insight | 0.370 ± 0.057 |
| instruction following | 0.360 ± 0.058 |
| readability | 0.403 ± 0.049 |
| FACT citation accuracy | **0.847 ± 0.093** |
| FACT supported citations per report | 87.4 ± 21.8 |

Two reports beat the reference (tasks 68: 0.534, 66: 0.514). Task 75 produced no report (0.0, see below).
RACE position bias was small: mean |ours-first − reference-first| = 0.015 (max 0.046).

## Search quality (UMBRELA)

| metric | mean ± 95% CI |
|---|---|
| reranker nDCG@10 | 0.578 ± 0.112 |
| reranker P@10 | 0.470 ± 0.129 |
| **reranker recall** (relevant results kept) | **0.426 ± 0.149** |
| SERP precision (raw results relevant to their sub-query) | 0.344 ± 0.071 |
| context precision (writer context relevant) | 0.376 ± 0.135 |
| task-level nDCG@10 | 0.655 ± 0.119 |

## Main finding: the reranker threshold starves the writer

On 10 of 20 tasks the Cohere reranker (threshold 0.25 against the full task prompt) kept 8 or fewer of
~60 results, and on task 75 it kept none, so no report was written. Those 10 tasks kept only 16% of their
relevant results (vs 70% on the rest) and scored **RACE 0.280 vs 0.449**. Across tasks, RACE correlates
0.69 with reranker recall. Long, multi-part DRB prompts get low absolute rerank scores, so a fixed
threshold drops relevant material; the obvious next variant is a top-k floor (or a lower threshold) on
the rerank step, measured against this baseline.

## Cost and time (measured)

| | total | per task |
|---|---|---|
| pipeline (Claude via Anthropic API) | $5.64 | median $0.22 (range $0.03-$1.03), median 213 s (21-1040 s) |
| UMBRELA judging (4,552 labels, cached) | $9.82 | ~$0.49 |
| RACE + FACT judging | $10.22 | ~$0.51 |

Re-running the graders on a new variant re-uses cached UMBRELA labels for unchanged passages.
