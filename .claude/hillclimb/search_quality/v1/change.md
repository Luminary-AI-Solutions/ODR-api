Rerank search results and chunks against the sub-query that retrieved them, with a top-3 floor per sub-query.

Baseline reranked every search result against the full multi-part research prompt with a fixed 0.25 threshold.
Long prompts get low absolute Cohere scores, so on 10/20 tasks the reranker kept <=8 of ~60 results (task 75: none,
no report). v1 scores each result against its own planner sub-query, always keeps each sub-query's top 3, merges
round-robin so the summarized top-10 covers every aspect, and reranks each source's chunks against that same
sub-query. Applied to refinement searches too. Code: rerank_search_results_by_query in deep_research/helpers.py;
config rerank_against_subquery / rerank_min_results_per_query (false / 0 reproduces baseline).
