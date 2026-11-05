"""
Shared plumbing for the ODR evals: paths, task data, the Claude judge client, cost, resume and the harness gate.

Layout (all under ODR-api/.claude/hillclimb/):
    pipeline/<variant>/runs/<id>_rep<k>.json   full pipeline artifact per (task, rep)
    pipeline/<variant>/runs.jsonl              one index row per finished run
    pipeline/<variant>/errors.jsonl            failed runs (never scored)
    search_quality/<variant>/...               UMBRELA results.jsonl + traces/
    report_quality/<variant>/...               RACE + FACT results.jsonl + traces/
    _harness.json                              approved harness digest (see check_harness)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.core.config  # noqa: E402,F401  (loads ODR-api.env into os.environ, incl. ANTHROPIC_API_KEY)
import anthropic  # noqa: E402
import httpx  # noqa: E402

EVALS = ROOT / "evals"
DATA = EVALS / "data" / "drb"
HC = ROOT / ".claude" / "hillclimb"

JUDGE_MODEL = "claude-sonnet-5-5"

# USD per million tokens (first-party Claude API). Cache writes bill at 1.25x input, cache reads at 0.1x input.
PRICES = {
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50},
    "claude-opus-5-5": {"in": 4.00, "out": 20.00},
}


# --------------------------------------------------------------------------- data

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _json_default(o: Any) -> Any:
    if isinstance(o, set):
        return sorted(o, key=str)
    return str(o)


def load_tasks(ids: Optional[Iterable[int]] = None) -> list[dict]:
    tasks = load_jsonl(DATA / "tasks.jsonl")
    if ids:
        wanted = set(ids)
        tasks = [t for t in tasks if t["id"] in wanted]
    return tasks


def load_by_id(name: str) -> dict[int, dict]:
    return {r["id"]: r for r in load_jsonl(DATA / name)}


def case_key(prompt_id: Any, rep: int) -> str:
    return f"{prompt_id}_rep{rep}"


def done_keys(results_path: Path) -> set[str]:
    return {case_key(r["prompt_id"], r.get("rep", 0)) for r in load_jsonl(results_path)}


# --------------------------------------------------------------------------- cost

def usage_cost(model: Optional[str], usage: dict) -> float:
    """Cost of one usage block. Unknown models raise so a silent $0 can't slip into a report."""
    if not usage:
        return 0.0
    if model not in PRICES:
        raise KeyError(f"No price for model {model!r}; add it to PRICES in evals/common.py")
    p = PRICES[model]
    return (
        usage.get("input_tokens", 0) * p["in"]
        + usage.get("cache_creation_input_tokens", 0) * p["in"] * 1.25
        + usage.get("cache_read_input_tokens", 0) * p["in"] * 0.1
        + usage.get("output_tokens", 0) * p["out"]
    ) / 1_000_000


def add_usage(total: dict, usage: dict) -> dict:
    for k, v in usage.items():
        if isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v
    return total


# --------------------------------------------------------------------------- harness gate

HARNESS_FILES = [
    "evals/common.py",
    "evals/prompts.py",
    "evals/run_pipeline.py",
    "evals/grade_search.py",
    "evals/grade_report.py",
    "evals/sanity_checks.py",
    "evals/data/drb/tasks.jsonl",
    "evals/data/drb/criteria.jsonl",
    "evals/data/drb/reference.jsonl",
    "uv.lock",
]


def harness_digest() -> str:
    h = hashlib.sha256()
    for rel in sorted(HARNESS_FILES):
        p = ROOT / rel
        h.update(rel.encode())
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()


def check_harness(approve: bool) -> None:
    """
    Refuse to run if the eval harness changed since the user last approved it.
    `--approve-harness` records the current digest; it is the user's call, so scripts never pass it themselves.
    """
    gate = HC / "_harness.json"
    digest = harness_digest()
    if approve:
        write_json(gate, {"sha256": digest, "files": HARNESS_FILES, "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        print(f"Harness approved: {digest[:12]}")
        return
    approved = json.loads(gate.read_text(encoding="utf-8"))["sha256"] if gate.exists() else None
    if approved != digest:
        print(
            "Eval harness changed since it was last approved (or was never approved).\n"
            "Review the diff, then re-run with --approve-harness.",
            file=sys.stderr,
        )
        sys.exit(2)


# --------------------------------------------------------------------------- judge

class JudgeError(Exception):
    """A judge call that produced no scorable output. `failure_class` goes into errors.jsonl."""

    def __init__(self, failure_class: str, message: str, usage: Optional[dict] = None, model: Optional[str] = None, attempts: int = 0):
        super().__init__(message)
        self.failure_class = failure_class
        self.usage = usage or {}
        self.model = model
        self.attempts = attempts


@dataclass
class JudgeResult:
    data: Any
    usage: dict
    model: str
    attempts: int
    latency_s: float


@dataclass
class Judge:
    """
    Claude-as-judge with structured outputs, bounded concurrency, visible retries, and server-side
    refusal fallback. Every call's usage is accumulated in `spend` so judge cost is always reported.
    """

    model: str = JUDGE_MODEL
    concurrency: int = 8
    max_attempts: int = 6
    spend: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Retries are done here (not in the SDK) so the attempt count is recorded per call.
        self.client = anthropic.AsyncAnthropic(max_retries=0, timeout=900.0)
        self.sem = asyncio.Semaphore(self.concurrency)

    async def json(
        self,
        prompt: str,
        schema: dict,
        *,
        system: Optional[str] = None,
        effort: str = "low",
        max_tokens: int = 16000,
        cache_split: Optional[str] = None,
    ) -> JudgeResult:
        """`cache_split`: cache the prompt up to (not including) this marker, e.g. a shared reference document."""
        content: Any = prompt
        if cache_split and cache_split in prompt:
            i = prompt.index(cache_split)
            content = [{"type": "text", "text": prompt[:i], "cache_control": {"type": "ephemeral"}},
                       {"type": "text", "text": prompt[i:]}]
        params: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
        if system:
            params["system"] = system

        last: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with self.sem:
                    t0 = time.monotonic()
                    # Stream so long judge outputs never hit the non-streaming request timeout.
                    async with self.client.beta.messages.stream(**params) as stream:
                        msg = await stream.get_final_message()
                    latency = time.monotonic() - t0
            except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError, httpx.TransportError) as e:
                # httpx.TransportError covers a stream the server drops mid-body (RemoteProtocolError).
                last = e
                response = getattr(e, "response", None)
                retry_after = response.headers.get("retry-after") if response is not None else None
                delay = float(retry_after) if retry_after else min(60.0, 2.0 * 2 ** (attempt - 1))
                await asyncio.sleep(delay + random.uniform(0, 1.0))
                continue
            except anthropic.APIStatusError as e:
                if e.status_code == 529:  # overloaded
                    last = e
                    await asyncio.sleep(min(60.0, 2.0 * 2 ** (attempt - 1)) + random.uniform(0, 1.0))
                    continue
                raise JudgeError("api_error", f"{e.status_code}: {e.message}", attempts=attempt) from e

            usage = _usage_dict(msg.usage)
            served = msg.model
            add_usage(self.spend.setdefault(served, {}), usage)
            if msg.stop_reason == "refusal":
                raise JudgeError("refusal", f"judge refused: {getattr(msg, 'stop_details', None)}", usage, served, attempt)
            if msg.stop_reason == "max_tokens":
                raise JudgeError("truncated", f"judge hit max_tokens={max_tokens}", usage, served, attempt)
            text = next((b.text for b in msg.content if b.type == "text"), "")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise JudgeError("unparseable", f"judge output not JSON: {text[:200]!r}", usage, served, attempt) from e
            return JudgeResult(data=data, usage=usage, model=served, attempts=attempt, latency_s=latency)

        raise JudgeError("api_error", f"gave up after {self.max_attempts} attempts: {last!r}", attempts=self.max_attempts)

    def spend_usd(self) -> float:
        return sum(usage_cost(m, u) for m, u in self.spend.items())


def _usage_dict(usage: Any) -> dict:
    return {
        "input_tokens": usage.input_tokens or 0,
        "output_tokens": usage.output_tokens or 0,
        "cache_read_input_tokens": usage.cache_read_input_tokens or 0,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens or 0,
    }


async def with_timeout(coro, seconds: float):
    """Hard wall-clock ceiling for one case, independent of stream liveness."""
    return await asyncio.wait_for(coro, timeout=seconds)


def mean_ci(values: list[float]) -> tuple[float, float]:
    """Mean and 95% normal-approximation half-width."""
    n = len(values)
    if n == 0:
        return float("nan"), float("nan")
    m = sum(values) / n
    if n < 2:
        return m, float("nan")
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return m, 1.96 * (var / n) ** 0.5
