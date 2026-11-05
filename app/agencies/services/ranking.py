"""
Reranking via Cohere or Together Rerank APIs (same response shape: results[].index, relevance_score).
"""

import os
import logging
from typing import List, Dict, Any, Optional, Literal
import httpx # Using httpx for async API calls
import asyncio # For testing
import random # For jitter

# Pydantic for local type definition
from pydantic import BaseModel

# Import custom exceptions using absolute path
from app.core.exceptions import ConfigurationError, RankingAPIError

# Configure logger
logger = logging.getLogger(__name__)

DEFAULT_TOGETHER_API_BASE = "https://api.together.ai"
DEFAULT_COHERE_API_BASE = "https://api.cohere.com"

# Define the output structure for a ranked item
class RankedItem(BaseModel):
    index: int
    score: float


def _ranked_items_from_api_results(
    raw_results: List[Dict[str, Any]],
    relevance_threshold: float,
) -> List[RankedItem]:
    """Parse Together/Cohere-style rerank payloads: each item has index and relevance_score."""
    formatted_results: List[RankedItem] = []
    for result_item in raw_results:
        if not isinstance(result_item, dict) or "index" not in result_item or "relevance_score" not in result_item:
            logger.warning(f"Skipping malformed rerank result item: {result_item}")
            continue
        score = result_item["relevance_score"]
        index = result_item["index"]
        try:
            score_float = float(score)
        except (ValueError, TypeError):
            logger.warning(f"Could not convert score '{score}' to float for index {index}. Skipping.")
            continue
        if score_float >= relevance_threshold:
            formatted_results.append(RankedItem(index=index, score=score_float))
        else:
            logger.debug(
                f"Filtering out document index {index} with score {score_float:.4f} (below threshold {relevance_threshold})"
            )
    formatted_results.sort(key=lambda x: x.score, reverse=True)
    return formatted_results


# Rerank rate limits are per minute (Cohere trial keys allow ~10 calls/min), so a
# 1-2s backoff on 429 just burns through the retries. Honor Retry-After when sent.
RATE_LIMIT_BASE_DELAY_S = 6.0
MAX_RETRY_DELAY_S = 60.0


def _retry_delay(attempt: int, initial_delay_s: float, exc: Optional[Exception]) -> float:
    """Seconds to wait before the next rerank attempt."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        retry_after = exc.response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), MAX_RETRY_DELAY_S)
            except ValueError:
                pass
        return min(RATE_LIMIT_BASE_DELAY_S * (2**attempt), MAX_RETRY_DELAY_S) + random.uniform(0, 1.0)
    return (initial_delay_s * (2**attempt)) + random.uniform(0, 0.5)


async def rerank_with_cohere_api(
    query: str,
    documents: List[str],
    model: str,
    api_key: str,
    base_url: Optional[str] = None,
    relevance_threshold: float = 0.1,
    top_n: Optional[int] = None,
    timeout: int = 20,
    max_retries: int = 4,
    initial_delay_s: float = 1.0,
) -> List[RankedItem]:
    """Rerank documents using Cohere's /v1/rerank API."""
    if not api_key:
        raise ConfigurationError("Cohere API key must be provided.")
    if not query:
        raise ValueError("Query cannot be empty for reranking.")
    if not documents:
        raise ValueError("Documents list cannot be empty for reranking.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "query": query,
        "documents": documents,
    }
    if top_n is not None:
        payload["top_n"] = top_n
    else:
        payload["top_n"] = len(documents)

    rerank_endpoint = f"{(base_url or DEFAULT_COHERE_API_BASE).rstrip('/')}/v1/rerank"
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                logger.info(
                    f"Cohere rerank (Attempt {attempt + 1}/{max_retries + 1}) {len(documents)} docs, model: {model}"
                )
                response = await client.post(rerank_endpoint, headers=headers, json=payload)
                response.raise_for_status()

                response_data = response.json()
                if (
                    not isinstance(response_data, dict)
                    or "results" not in response_data
                    or not isinstance(response_data["results"], list)
                ):
                    error_msg = f"Unexpected response format from Cohere Rerank API: {response_data}"
                    logger.error(error_msg)
                    raise RankingAPIError(error_msg)

                formatted_results = _ranked_items_from_api_results(
                    response_data["results"], relevance_threshold
                )
                logger.info(
                    f"Cohere reranking complete. Returning {len(formatted_results)} documents above threshold {relevance_threshold}."
                )
                return formatted_results

        except (httpx.TimeoutException, httpx.ReadError) as e:
            last_exception = e
            logger.warning(f"Cohere rerank Attempt {attempt + 1} failed (Retryable Network Error): {type(e).__name__}: {e}")
        except httpx.HTTPStatusError as e:
            last_exception = e
            if 500 <= e.response.status_code <= 599:
                logger.warning(
                    f"Cohere rerank Attempt {attempt + 1} failed (Server {e.response.status_code}): {e.response.text[:200]}..."
                )
            elif e.response.status_code == 429:
                logger.warning(f"Cohere rerank Attempt {attempt + 1} failed (429). Retrying...")
            else:
                response_text = e.response.text
                logger.error(
                    f"Cohere Rerank API non-retryable status {e.response.status_code}: {response_text}",
                    exc_info=False,
                )
                raise RankingAPIError(
                    f"Cohere Rerank API returned status {e.response.status_code}: {response_text}"
                ) from e
        except httpx.RequestError as e:
            last_exception = e
            logger.warning(f"Cohere rerank Attempt {attempt + 1} failed (Request Error): {type(e).__name__}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during Cohere Rerank processing: {e}", exc_info=True)
            raise RankingAPIError(f"Unexpected error processing Cohere Rerank response: {e}") from e

        if attempt < max_retries:
            delay = _retry_delay(attempt, initial_delay_s, last_exception)
            logger.info(f"Retrying Cohere rerank in {delay:.2f} seconds...")
            await asyncio.sleep(delay)

    # Raise rather than return [] so callers can tell "nothing relevant" apart from "rerank unavailable".
    raise RankingAPIError(
        f"Cohere reranking failed after {max_retries + 1} attempts. Last: {type(last_exception).__name__}: {last_exception}"
    )


async def rerank_documents(
    query: str,
    documents: List[str],
    model: str,
    api_key: str,
    *,
    provider: Literal["cohere", "together"],
    base_url: Optional[str] = None,
    relevance_threshold: float = 0.1,
    top_n: Optional[int] = None,
    timeout: int = 20,
    max_retries: int = 4,
    initial_delay_s: float = 1.0,
) -> List[RankedItem]:
    """Dispatch to Cohere or Together rerank implementation."""
    if provider == "cohere":
        return await rerank_with_cohere_api(
            query=query,
            documents=documents,
            model=model,
            api_key=api_key,
            base_url=base_url,
            relevance_threshold=relevance_threshold,
            top_n=top_n,
            timeout=timeout,
            max_retries=max_retries,
            initial_delay_s=initial_delay_s,
        )
    return await rerank_with_together_api(
        query=query,
        documents=documents,
        model=model,
        api_key=api_key,
        base_url=base_url,
        relevance_threshold=relevance_threshold,
        top_n=top_n,
        timeout=timeout,
        max_retries=max_retries,
        initial_delay_s=initial_delay_s,
    )


async def rerank_with_together_api(
    query: str,
    documents: List[str],
    model: str,
    api_key: str,
    base_url: Optional[str] = None,
    relevance_threshold: float = 0.1,
    top_n: Optional[int] = None,
    timeout: int = 20,
    max_retries: int = 4,  # total attempts = max_retries + 1
    initial_delay_s: float = 1.0
) -> List[RankedItem]: # Updated return type hint
    """
    Reranks a list of document strings based on a query using the Together Rerank API.

    Filters results based on a relevance score threshold.

    Args:
        query: The search query used for relevance comparison.
        documents: A list of document strings to be reranked.
        model: The identifier for the Together AI reranker model (e.g., 'Salesforce/Llama-Rank-v1').
        api_key: Your Together AI API key (must be provided).
        relevance_threshold: Minimum relevance score for a document to be included in the results.
        top_n: Optional. If specified, requests the API to return only the top N scored documents 
               (before threshold filtering). If None, requests scores for all documents.
        timeout: Timeout in seconds for the HTTP request to the Together API.
        max_retries: Maximum number of retries on transient errors.
        initial_delay_s: Initial delay in seconds before the first retry.

    Returns:
        A list of RankedItem instances, each containing the original 'index' of the document 
        in the input list and its 'score'. The list is sorted by score (descending) 
        and only includes documents meeting the relevance_threshold.

    Raises:
        ValueError: If query or documents list is empty.
        ConfigurationError: If the api_key is not provided.
        RankingAPIError: If the API call fails with a non-retryable error, 
                       or if the API returns an unexpected response format.
                       Also raised once retries on transient errors are exhausted.
    """
    if not api_key:
        raise ConfigurationError("Together API key must be provided.")
    if not query:
        raise ValueError("Query cannot be empty for reranking.")
    if not documents:
        # Return empty list instead of raising error? Agent might handle empty results gracefully.
        # Raising ValueError for now, as it indicates invalid input to *this* function.
        raise ValueError("Documents list cannot be empty for reranking.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "query": query,
        "documents": documents,
    }
    if top_n is not None:
        payload["top_n"] = top_n
    else:
        # Explicitly ask for all scores if top_n is not specified by caller.
        payload["top_n"] = len(documents)

    rerank_endpoint = f"{(base_url or DEFAULT_TOGETHER_API_BASE).rstrip('/')}/v1/rerank"

    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                logger.info(f"Sending rerank request (Attempt {attempt + 1}/{max_retries + 1}) to {rerank_endpoint} for {len(documents)} docs, model: {model}")
                logger.debug(f"Rerank payload (excluding docs): {{model: {model}, query: {query[:50]}..., top_n: {payload.get('top_n')}}}")
                response = await client.post(rerank_endpoint, headers=headers, json=payload)
                response.raise_for_status() # Raise HTTPStatusError for 4xx/5xx

                response_data = response.json()
                logger.debug("Received successful response from Together Rerank API.")

                # Validate response structure
                if not isinstance(response_data, dict) or 'results' not in response_data or not isinstance(response_data['results'], list):
                    error_msg = f"Unexpected response format from Together Rerank API. Missing 'results' list. Response: {response_data}"
                    logger.error(error_msg)
                    raise RankingAPIError(error_msg) # Non-retryable format error

                formatted_results = _ranked_items_from_api_results(response_data['results'], relevance_threshold)

                logger.info(f"Reranking complete. Returning {len(formatted_results)} documents above threshold {relevance_threshold}.")
                return formatted_results # Success!

        except (httpx.TimeoutException, httpx.ReadError) as e:
            last_exception = e
            logger.warning(f"Rerank Attempt {attempt + 1} failed (Retryable Network Error): {type(e).__name__}: {e}")
        except httpx.HTTPStatusError as e:
            last_exception = e
            # Retry on 5xx server errors AND 429 rate limit errors
            if 500 <= e.response.status_code <= 599:
                logger.warning(f"Rerank Attempt {attempt + 1} failed (Retryable Server Error {e.response.status_code}): {e.response.text[:200]}...")
            elif e.response.status_code == 429:
                logger.warning(f"Rerank Attempt {attempt + 1} failed (Rate Limit Error 429). Retrying...")
                # Optionally parse retry-after header if present, but simple backoff is fine too
            else:
                # Non-retryable client error (other 4xx) or other status
                response_text = e.response.text
                logger.error(f"Together Rerank API request failed with non-retryable status {e.response.status_code}: {response_text}", exc_info=False)
                # Together often has no serverless rerank (model_not_available) or account credits (402);
                # keep the pipeline usable by preserving input order. See serverless rerank note:
                # https://docs.together.ai/docs/serverless-models#rerank-models
                if e.response.status_code == 402:
                    logger.warning(
                        "Together rerank returned 402; using original document order without neural reranking."
                    )
                    return [RankedItem(index=i, score=1.0) for i in range(len(documents))]
                if e.response.status_code == 400:
                    try:
                        err_json = e.response.json()
                        err = err_json.get("error") or {}
                        if err.get("code") == "model_not_available":
                            logger.warning(
                                "Together rerank model not available serverless; using original document order."
                            )
                            return [RankedItem(index=i, score=1.0) for i in range(len(documents))]
                    except Exception:
                        pass
                raise RankingAPIError(f"Together Rerank API returned status {e.response.status_code}: {response_text}") from e
        except httpx.RequestError as e:
            # General request errors (e.g., DNS resolution failed) - potentially retryable?
            # Let's retry these for now.
            last_exception = e
            logger.warning(f"Rerank Attempt {attempt + 1} failed (Retryable Request Error): {type(e).__name__}: {e}")
        except Exception as e:
            # Catch unexpected errors during processing, but don't retry these
            logger.error(f"Unexpected error during Together Rerank processing: {e}", exc_info=True)
            raise RankingAPIError(f"Unexpected error processing Together Rerank response: {e}") from e

        # If we are here, a retryable error occurred
        if attempt < max_retries:
            delay = _retry_delay(attempt, initial_delay_s, last_exception)
            logger.info(f"Retrying in {delay:.2f} seconds...")
            await asyncio.sleep(delay)

    raise RankingAPIError(
        f"Together reranking failed after {max_retries + 1} attempts. Last error: {type(last_exception).__name__}: {last_exception}"
    )

# Example Usage (Async)
async def _test_async():
    # Setup basic logging FOR THE TEST RUN ONLY if not already configured
    if not logger.hasHandlers():
        logging.basicConfig(level=logging.INFO)
        log = logging.getLogger(__name__) # Get logger again after basicConfig
    else:
        log = logger # Use existing logger
        log.setLevel(logging.INFO) # Ensure level is appropriate for test
        
    provider = os.getenv("RERANK_PROVIDER", "cohere")
    if provider == "cohere":
        api_key = os.getenv("COHERE_API_KEY")
        default_model = "rerank-english-v3.0"
    else:
        api_key = os.getenv("TOGETHER_API_KEY")
        default_model = "Salesforce/Llama-Rank-v1"
    if not api_key:
        log.warning("Skipping test: set COHERE_API_KEY (or TOGETHER_API_KEY if RERANK_PROVIDER=together).")
        return

    test_query = "What is the capital of the United States?"
    test_docs = [
        "Carson City is the capital city of the American state of Nevada.",
        "The Commonwealth of the Northern Mariana Islands is a group of islands in the Pacific Ocean. Its capital is Saipan.",
        "Capitalization or capitalisation in English grammar is the use of a capital letter at the start of a word. English usage varies from capitalization in other languages.",
        "Washington, D.C. (also known as simply Washington or D.C., and officially as the District of Columbia) is the capital of the United States. It is a federal district.",
        "Capital punishment (the death penalty) has existed in the United States since before the United States was a country. As of 2017, capital punishment is legal in 30 of the 50 states.",
    ]
    model_name = os.getenv("RERANKER_MODEL", default_model)

    try:
        log.info(f"--- Starting Rerank Service Test ({provider}, model: {model_name}) --- ")
        reranked = await rerank_documents(
            query=test_query,
            documents=test_docs,
            model=model_name,
            api_key=api_key,
            provider=provider,
            relevance_threshold=0.1,
        )
        log.info("Reranked Results (Above Threshold):")
        if not reranked:
            log.info("  No results above threshold.")
        for result in reranked:
            log.info(f"  Index: {result.index}, Score: {result.score:.4f} - Document: {test_docs[result.index]}")

    except (ConfigurationError, RankingAPIError, ValueError) as e:
        log.error(f"Test failed: {type(e).__name__}: {e}")
    except Exception as e:
        log.error(f"Test failed with unexpected error: {type(e).__name__}: {e}", exc_info=True)
    log.info("--- Finished Rerank Service Test --- ")

if __name__ == '__main__':
    asyncio.run(_test_async()) 