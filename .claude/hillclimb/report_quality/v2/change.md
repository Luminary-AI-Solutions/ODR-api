Jev (TypeSafe decision model, via OpenRouter) replaces Cohere for scoring search results and chunks.

Same sub-query reranking and top-3 floor as v1, but each search result and chunk is scored 0-3 by Jev for how well
it answers its sub-question within the full research task (search: keep >= 1 "related"; chunks: keep >= 2
"partly answers", top 5 per source). Offline on v1's chunk pool, Jev picked chunks with 0.76 precision vs Cohere's
0.44. Cohere's trial quota was exhausted, so v2 switches both stages (chunk-only is not separable this month).
Report eval: FACT checks a fixed random sample of 40 citations per report (v1 checked all); RACE unchanged.
Config: DEEP_RESEARCH_SEARCH_SELECTOR=jev, DEEP_RESEARCH_CHUNK_SELECTOR=jev.
