"""LLM client: structured JSON output, validation, retries, rate limiting, caching, cost.

Every pipeline stage calls `LLMClient.complete(...)`; nothing else talks to a provider SDK.
`llm.provider` selects the backend: `anthropic` (Claude, default) or `groq`.

Call flow:
  1. Truncate the input; build the cache key (model + prompt version + input).
  2. Cache hit -> return the validated object with zero new tokens.
  3. Budget check (per-run ceiling and project-wide budget, counting the worst-case cost of
     the next call), then call the provider under the per-model rate limiter with JSON Schema
     structured output. Anthropic: the system prompt is marked for prompt caching. Groq:
     strict schema where supported, falling back to JSON mode if the model rejects it.
  4. 429 -> honor `retry-after` (pausing every thread); 5xx / overloaded / network ->
     exponential backoff. A `retry-after` longer than `max_retry_wait_seconds` (or an
     Anthropic spend-limit 429, which has none) raises `LLMRateLimitExhausted` so the stage
     can stop cleanly and resume next run.
  5. Validate with Pydantic; on failure retry once with the error message.
  6. Record tokens and cost; store the validated response in `llm_cache`.

Backfills can go through the Anthropic Message Batches API instead (`prepare`,
`submit_batch`, `wait_for_batch`, `batch_results`): same request, same cache key, cost
recorded at `llm.batch_price_factor`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

import anthropic
import groq
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from discovery.ai.prompts import Prompt
from discovery.ai.rate_limiter import RateLimiter
from discovery.config import LLM_API_KEY_ENV, LLMSettings
from discovery.db import session_scope
from discovery.models.orm import LLMCacheRow

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

TRUNCATION_MARKER = "\n[...truncated]"
CHARS_PER_TOKEN = 4
OUTPUT_TOKEN_ESTIMATE = 512

KEY_HELP = {
    "anthropic": "Get a key at https://console.anthropic.com/settings/keys",
    "groq": "Get a key at https://console.groq.com/keys",
}


class LLMError(Exception):
    pass


class LLMConfigError(LLMError):
    """Missing/invalid API key, unknown model, or no credit. Not retryable; fix and re-run."""


class LLMRateLimitExhausted(LLMError):
    """A request/token/spend limit is used up. Stop the stage; the next run resumes from cache."""


class LLMBudgetExceeded(LLMError):
    """The run's cost ceiling or the project budget would be exceeded."""


class LLMValidationError(LLMError):
    """The model kept returning output that does not match the schema."""


class LLMBatchPending(LLMError):
    """A Message Batch did not finish within the wait limit. Resume later with its id."""

    def __init__(self, message: str, batch_id: str):
        super().__init__(message)
        self.batch_id = batch_id


@dataclass
class LLMUsage:
    requests: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResult(Generic[T]):
    value: T
    model: str
    prompt_version: str
    cache_key: str
    cached: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class PreparedCall:
    """One request ready for the Message Batches API. `key` doubles as its custom_id."""

    key: str
    model: str
    prompt_version: str
    params: dict[str, Any]
    max_cost_usd: float


@dataclass
class BatchOutcome(Generic[T]):
    value: T | None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class _CallTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


def _split(totals: _CallTotals) -> tuple[int, int, float]:
    return totals.input_tokens, totals.output_tokens, totals.cost_usd


@dataclass
class _Reply:
    """One provider response. `input_tokens` excludes prompt-cache writes and reads."""

    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def all_input_tokens(self) -> int:
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens


def cache_key(model: str, prompt_version: str, user_input: str) -> str:
    blob = json.dumps(
        {"model": model, "prompt_version": prompt_version, "input": user_input},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON Schema acceptable to strict structured output: inline `$ref`s,
    close every object (`additionalProperties: false`), mark every property required, and
    drop `default`/`title` keys. Optional fields stay required but nullable."""
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def walk(node: Any, seen: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [walk(n, seen) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            if name in seen:
                raise ValueError(f"Recursive schema reference not supported: {name}")
            merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
            return walk(merged, (*seen, name))
        out = {k: walk(v, seen) for k, v in node.items() if k not in ("default", "title")}
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}))
        return out

    return walk(schema, ())


_ANTHROPIC_UNSUPPORTED = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "maxItems",
}


def to_anthropic_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strict schema minus the constraints Anthropic structured output rejects (numeric
    bounds, string lengths, array sizes beyond minItems 0/1). Pydantic still enforces them
    when the response is validated, and a violation triggers the validation retry."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: walk(sub) for name, sub in v.items()}
            elif k in _ANTHROPIC_UNSUPPORTED or (k == "minItems" and v not in (0, 1)):
                continue
            else:
                out[k] = walk(v)
        return out

    return walk(to_strict_schema(schema))


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def parse_duration(value: str | None) -> float | None:
    """Parse reset headers such as '7.66s', '2m59.56s', '1h2m', or plain seconds."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    parts = _DURATION_PART.findall(value)
    return sum(float(n) * scale[u] for n, u in parts) if parts else None


def seconds_until(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds from now until an RFC 3339 timestamp (Anthropic `*-reset` headers)."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def _retry_after(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    return parse_duration(response.headers.get("retry-after"))


_RETRYABLE = (
    groq.RateLimitError,
    groq.APIConnectionError,
    groq.InternalServerError,
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
)
_RATE_LIMITED = (groq.RateLimitError, anthropic.RateLimitError)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, _RETRYABLE):
        return True
    return isinstance(exc, anthropic.APIStatusError) and exc.status_code in (500, 502, 503, 529)


def _json_schema_unsupported(exc: groq.BadRequestError) -> bool:
    msg = str(exc).lower()
    return "json_schema" in msg or ("response_format" in msg and "support" in msg)


def _schema_mismatch(exc: groq.BadRequestError) -> bool:
    msg = str(exc).lower()
    return (
        "does not match the expected schema" in msg
        or "json_validate_failed" in msg
        or "failed to generate json" in msg
    )


class LLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        session_factory: sessionmaker[Session],
        *,
        api_key: str | None = None,
        sdk_client: Any | None = None,
        spent_before_usd: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """`spent_before_usd`: LLM spend already recorded toward `llm.project_budget_usd`."""
        self.provider = settings.provider
        if sdk_client is None:
            if not api_key:
                raise LLMConfigError(
                    f"{LLM_API_KEY_ENV[self.provider]} is not set. Add it to .env (local) or "
                    f"the GitHub Actions secrets (pipeline). {KEY_HELP[self.provider]}"
                )
            if self.provider == "anthropic":
                sdk_client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=120.0)
            else:
                sdk_client = groq.Groq(api_key=api_key, max_retries=0, timeout=60.0)
        self.settings = settings
        self.spent_before_usd = spent_before_usd
        self._client = sdk_client
        self._sessions = session_factory
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._limiters: dict[str, RateLimiter] = {}
        self._no_json_schema: set[str] = set()
        self.usage = LLMUsage()

    # --- public ------------------------------------------------------------

    def complete(
        self,
        *,
        prompt: Prompt,
        user_input: str,
        response_model: type[T],
        model: str | None = None,
        temperature: float | None = None,
        use_cache: bool = True,
        max_input_chars: int | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResult[T]:
        """`max_input_chars` / `max_output_tokens` override the settings for one call
        (batched prompts carry several items and need more room)."""
        model = model or self.settings.small_model
        user_input = truncate(user_input, max_input_chars or self.settings.max_input_chars)
        key = cache_key(model, prompt.id, user_input)

        if use_cache:
            hit = self._cache_get(key, response_model)
            if hit is not None:
                with self._lock:
                    self.usage.cache_hits += 1
                return LLMResult(hit, model, prompt.id, key, cached=True)

        totals = _CallTotals()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": user_input},
        ]
        value: T | None = None
        for attempt in range(2):
            content = self._call(
                model, messages, response_model, temperature, totals, max_output_tokens
            )
            try:
                value = response_model.model_validate(json.loads(content))
                break
            except (json.JSONDecodeError, ValidationError) as exc:
                totals.errors.append(str(exc))
                log.warning(
                    "Invalid %s from %s (attempt %d): %s",
                    response_model.__name__,
                    model,
                    attempt + 1,
                    exc,
                )
                messages = [
                    *messages,
                    {"role": "assistant", "content": content or "{}"},
                    {
                        "role": "user",
                        "content": "That response was not valid against the required JSON "
                        f"schema:\n{exc}\nReturn only the corrected JSON object.",
                    },
                ]
        if value is None:
            raise LLMValidationError(
                f"{model} returned invalid {response_model.__name__} twice: {totals.errors[-1]}"
            )

        if use_cache:
            self._cache_put(key, model, prompt.id, value, totals)
        return LLMResult(
            value,
            model,
            prompt.id,
            key,
            cached=False,
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cost_usd=totals.cost_usd,
        )

    # --- Message Batches API (Anthropic) -----------------------------------

    def prepare(
        self,
        *,
        prompt: Prompt,
        user_input: str,
        response_model: type[BaseModel],
        model: str | None = None,
        max_input_chars: int | None = None,
        max_output_tokens: int | None = None,
    ) -> PreparedCall:
        """Build the same request `complete` would send, with the same cache key."""
        if self.provider != "anthropic":
            raise LLMConfigError("The Message Batches API needs llm.provider: anthropic")
        model = model or self.settings.small_model
        user_input = truncate(user_input, max_input_chars or self.settings.max_input_chars)
        max_tokens = max_output_tokens or self.settings.max_output_tokens
        messages = [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": user_input},
        ]
        params = self._anthropic_kwargs(model, messages, response_model, max_tokens)
        est_in = estimate_tokens(json.dumps(params["system"]) + json.dumps(params["messages"]))
        return PreparedCall(
            key=cache_key(model, prompt.id, user_input),
            model=model,
            prompt_version=prompt.id,
            params=params,
            max_cost_usd=self._max_call_cost(model, est_in, max_tokens),
        )

    def cached(self, call: PreparedCall, response_model: type[T]) -> T | None:
        hit = self._cache_get(call.key, response_model)
        if hit is not None:
            with self._lock:
                self.usage.cache_hits += 1
        return hit

    def submit_batch(self, calls: list[PreparedCall]) -> tuple[str, list[PreparedCall]]:
        """Submit as many calls as the remaining budget allows at their worst-case
        (discounted) cost. Returns the batch id and the calls that were submitted."""
        factor = self.settings.batch_price_factor
        room = self._budget_room()
        fitted: list[PreparedCall] = []
        seen: set[str] = set()
        reserved = 0.0
        for call in calls:
            if call.key in seen:
                continue
            cost = call.max_cost_usd * factor
            if reserved + cost > room:
                break
            fitted.append(call)
            seen.add(call.key)
            reserved += cost
        if not fitted:
            self._check_budget(calls[0].max_cost_usd * factor if calls else 0.0)
            raise LLMBudgetExceeded("No room in the LLM budget for a Message Batch")
        requests = [{"custom_id": c.key, "params": c.params} for c in fitted]
        for attempt in self._retrying():
            with attempt:
                batch = self._batches_call("create", requests=requests)
        log.info(
            "Submitted Message Batch %s: %d requests (worst case $%.4f)",
            batch.id,
            len(fitted),
            reserved,
        )
        return batch.id, fitted

    def wait_for_batch(
        self, batch_id: str, *, poll_seconds: float, max_wait_seconds: float
    ) -> None:
        started = self._clock()
        while True:
            for attempt in self._retrying():
                with attempt:
                    batch = self._batches_call("retrieve", batch_id)
            if batch.processing_status == "ended":
                return
            counts = getattr(batch, "request_counts", None)
            log.info(
                "Message Batch %s: %s (%s processing, %s succeeded)",
                batch_id,
                batch.processing_status,
                getattr(counts, "processing", "?"),
                getattr(counts, "succeeded", "?"),
            )
            if self._clock() - started + poll_seconds > max_wait_seconds:
                raise LLMBatchPending(
                    f"Message Batch {batch_id} is still {batch.processing_status} after "
                    f"{max_wait_seconds / 60:.0f} minutes. Re-run with --batch-id {batch_id} "
                    "to collect it.",
                    batch_id,
                )
            self._sleep(poll_seconds)

    def batch_results(
        self, batch_id: str, calls: list[PreparedCall], response_model: type[T]
    ) -> dict[str, BatchOutcome[T]]:
        """Validate and cache every result that belongs to `calls`, keyed by cache key.
        Cost is recorded at the batch price."""
        by_key = {c.key: c for c in calls}
        factor = self.settings.batch_price_factor
        out: dict[str, BatchOutcome[T]] = {}
        for entry in self._batches_call("results", batch_id):
            call = by_key.get(entry.custom_id)
            if call is None:
                continue
            result = entry.result
            if result.type != "succeeded":
                detail = getattr(result, "error", None)
                out[call.key] = BatchOutcome(None, error=f"batch result {result.type}: {detail}")
                continue
            message = result.message
            usage = message.usage
            reply = _Reply(
                content="".join(
                    getattr(b, "text", "")
                    for b in message.content
                    if getattr(b, "type", "") == "text"
                ),
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            )
            cost = self._cost(call.model, reply) * factor
            with self._lock:
                self.usage.requests += 1
                self.usage.input_tokens += reply.all_input_tokens
                self.usage.output_tokens += reply.output_tokens
                self.usage.cached_input_tokens += reply.cache_read_tokens
                self.usage.cost_usd += cost
            totals = _CallTotals(reply.all_input_tokens, reply.output_tokens, cost)
            try:
                value = response_model.model_validate(json.loads(reply.content))
            except (json.JSONDecodeError, ValidationError) as exc:
                out[call.key] = BatchOutcome(
                    None, f"invalid {response_model.__name__}: {exc}", *_split(totals)
                )
                continue
            self._cache_put(call.key, call.model, call.prompt_version, value, totals)
            out[call.key] = BatchOutcome(value, None, *_split(totals))
        return out

    def _batches_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._client.messages.batches, method)(*args, **kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMConfigError(
                f"Anthropic rejected the API key (ANTHROPIC_API_KEY): {exc}"
            ) from exc
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc).lower():
                raise LLMConfigError(
                    f"Anthropic account has no remaining credit: {exc}. Add credit in the "
                    "Console (Plans & Billing) and re-run."
                ) from exc
            raise

    # --- shared call path --------------------------------------------------

    def _limiter(self, model: str) -> RateLimiter:
        with self._lock:
            if model not in self._limiters:
                rl = self.settings.rate_limit_for(model)
                self._limiters[model] = RateLimiter(
                    rl.requests_per_minute,
                    rl.tokens_per_minute,
                    self.settings.max_concurrency,
                    clock=self._clock,
                    sleep=self._sleep,
                )
            return self._limiters[model]

    def _budget_room(self) -> float:
        """USD that can still be spent before the run ceiling or project budget is crossed."""
        room = float("inf")
        spent = self.usage.cost_usd
        if self.settings.max_cost_usd_per_run is not None:
            room = min(room, self.settings.max_cost_usd_per_run - spent)
        if self.settings.project_budget_usd is not None:
            room = min(room, self.settings.project_budget_usd - self.spent_before_usd - spent)
        return room

    def _check_budget(self, next_call_max_usd: float = 0.0) -> None:
        """Refuse a call whose worst-case cost would cross the run ceiling or project budget."""
        spent = self.usage.cost_usd
        ceiling = self.settings.max_cost_usd_per_run
        if ceiling is not None and spent + next_call_max_usd > ceiling:
            raise LLMBudgetExceeded(
                f"LLM spend this run ${spent:.4f} plus the next call (up to "
                f"${next_call_max_usd:.4f}) would exceed the run ceiling ${ceiling:.2f} "
                "(llm.max_cost_usd_per_run)"
            )
        budget = self.settings.project_budget_usd
        total = self.spent_before_usd + spent
        if budget is not None and total + next_call_max_usd > budget:
            raise LLMBudgetExceeded(
                f"Project LLM spend ${total:.4f} plus the next call (up to "
                f"${next_call_max_usd:.4f}) would exceed the project budget ${budget:.2f} "
                "(llm.project_budget_usd). Get PM approval before raising it."
            )

    def _cost(self, model: str, reply: _Reply) -> float:
        price = self.settings.pricing.get(model)
        if price is None:
            return 0.0
        write = price.cache_write if price.cache_write is not None else price.input
        read = price.cache_read if price.cache_read is not None else price.input
        return (
            reply.input_tokens * price.input
            + reply.cache_write_tokens * write
            + reply.cache_read_tokens * read
            + reply.output_tokens * price.output
        ) / 1_000_000

    def _max_call_cost(self, model: str, est_input_tokens: int, max_tokens: int) -> float:
        """Upper bound for one call: the whole prompt written to cache, every output token."""
        price = self.settings.pricing.get(model)
        if price is None:
            return 0.0
        write = price.cache_write if price.cache_write is not None else price.input
        return (est_input_tokens * max(price.input, write) + max_tokens * price.output) / 1e6

    def _retrying(self) -> Retrying:
        def wait(state) -> float:
            exc = state.outcome.exception()
            if isinstance(exc, _RATE_LIMITED):
                ra = _retry_after(exc)
                if ra is not None:
                    return ra
            return wait_exponential_jitter(initial=1, max=30)(state)

        return Retrying(
            stop=stop_after_attempt(self.settings.max_attempts),
            retry=retry_if_exception(_is_retryable),
            wait=wait,
            sleep=self._sleep,
            reraise=True,
        )

    def _call(
        self,
        model: str,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        temperature: float | None,
        totals: _CallTotals,
        max_output_tokens: int | None = None,
    ) -> str:
        """One logical completion (with transport retries). Returns the raw message content."""
        limiter = self._limiter(model)
        max_tokens = max_output_tokens or self.settings.max_output_tokens
        if self.provider == "anthropic":
            reply = self._call_anthropic(model, messages, response_model, max_tokens, limiter)
        else:
            reply = self._call_groq(
                model, messages, response_model, temperature, max_tokens, limiter, totals
            )
        if reply is None:
            return "{}"  # best-effort mode miss: let validation trigger the one retry

        cost = self._cost(model, reply)
        totals.input_tokens += reply.all_input_tokens
        totals.output_tokens += reply.output_tokens
        totals.cost_usd += cost
        with self._lock:
            self.usage.requests += 1
            self.usage.input_tokens += reply.all_input_tokens
            self.usage.output_tokens += reply.output_tokens
            self.usage.cached_input_tokens += reply.cache_read_tokens
            self.usage.cost_usd += cost
        return reply.content

    # --- Anthropic ---------------------------------------------------------

    def _anthropic_kwargs(
        self,
        model: str,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        max_tokens: int,
    ) -> dict[str, Any]:
        system, turns = messages[0]["content"], messages[1:]
        output_config: dict[str, Any] = {
            "format": {
                "type": "json_schema",
                "schema": to_anthropic_schema(response_model.model_json_schema()),
            }
        }
        if self.settings.reasoning_effort:
            output_config["effort"] = self.settings.reasoning_effort
        return {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": m["role"], "content": m["content"]} for m in turns],
            "output_config": output_config,
        }

    def _call_anthropic(
        self,
        model: str,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        max_tokens: int,
        limiter: RateLimiter,
    ) -> _Reply:
        kwargs = self._anthropic_kwargs(model, messages, response_model, max_tokens)
        est_in = estimate_tokens(json.dumps(kwargs["system"]) + json.dumps(kwargs["messages"]))
        self._check_budget(self._max_call_cost(model, est_in, max_tokens))
        est = est_in + OUTPUT_TOKEN_ESTIMATE
        for attempt in self._retrying():
            with attempt, limiter.slot(est):
                raw = self._send_anthropic(model, limiter, kwargs)

        message = raw.parse()
        usage = message.usage
        reply = _Reply(
            content="".join(
                getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text"
            ),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
        if message.stop_reason in ("max_tokens", "refusal"):
            log.warning(
                "%s stopped with %s after %d output tokens; output may be incomplete",
                model,
                message.stop_reason,
                reply.output_tokens,
            )
        # Cache reads do not count toward Anthropic input-token rate limits.
        limiter.adjust_tokens(
            reply.input_tokens + reply.cache_write_tokens + reply.output_tokens - est
        )
        self._observe_anthropic_headers(limiter, raw.headers, est)
        return reply

    def _send_anthropic(self, model: str, limiter: RateLimiter, kwargs: dict[str, Any]) -> Any:
        try:
            return self._client.messages.with_raw_response.create(**kwargs)
        except anthropic.RateLimitError as exc:
            ra = _retry_after(exc)
            if ra is None:
                raise LLMRateLimitExhausted(
                    f"Anthropic refused {model} with no retry-after (workspace spend limit "
                    f"reached?): {exc}. Check the Console limits page before re-running."
                ) from exc
            if ra > self.settings.max_retry_wait_seconds:
                raise LLMRateLimitExhausted(
                    f"Anthropic limit for {model} exhausted (retry-after {ra:.0f}s). Stopping; "
                    "completed calls are cached, so the next run resumes from here."
                ) from exc
            limiter.pause(ra)
            raise
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMConfigError(
                f"Anthropic rejected the API key (ANTHROPIC_API_KEY): {exc}"
            ) from exc
        except anthropic.NotFoundError as exc:
            raise LLMConfigError(
                f"Anthropic model '{model}' not found or retired. Update llm.small_model / "
                "llm.large_model in config/settings.yaml and run the gold-set check."
            ) from exc
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc).lower():
                raise LLMConfigError(
                    f"Anthropic account has no remaining credit: {exc}. Add credit in the "
                    "Console (Plans & Billing) and re-run."
                ) from exc
            raise

    @staticmethod
    def _observe_anthropic_headers(limiter: RateLimiter, headers: Any, est: int) -> None:
        """If the next call likely won't fit the remaining token allowance, wait for reset."""
        try:
            remaining = float(headers.get("anthropic-ratelimit-tokens-remaining"))
        except (TypeError, ValueError):
            return
        if remaining < est:
            reset = seconds_until(headers.get("anthropic-ratelimit-tokens-reset"))
            if reset:
                limiter.pause(reset)

    # --- Groq --------------------------------------------------------------

    def _request_kwargs(
        self,
        model: str,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        temperature: float | None,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        use_schema = (
            self.settings.structured_output == "json_schema" and model not in self._no_json_schema
        )
        if use_schema:
            raw = response_model.model_json_schema()
            strict = self.settings.strict_schema
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": strict,
                    "schema": to_strict_schema(raw) if strict else raw,
                },
            }
        else:
            # JSON mode needs the schema (and the word "JSON") in the prompt itself.
            schema = json.dumps(to_strict_schema(response_model.model_json_schema()))
            messages = [
                {
                    "role": "system",
                    "content": f"{messages[0]['content']}\n\nRespond with a single JSON object "
                    f"that conforms to this JSON Schema:\n{schema}",
                },
                *messages[1:],
            ]
            response_format = {"type": "json_object"}

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_completion_tokens": max_output_tokens or self.settings.max_output_tokens,
            "response_format": response_format,
        }
        if self.settings.reasoning_effort in ("low", "medium", "high") and "gpt-oss" in model:
            kwargs["reasoning_effort"] = self.settings.reasoning_effort
        return kwargs

    def _call_groq(
        self,
        model: str,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        temperature: float | None,
        max_tokens: int,
        limiter: RateLimiter,
        totals: _CallTotals,
    ) -> _Reply | None:
        while True:
            kwargs = self._request_kwargs(model, messages, response_model, temperature, max_tokens)
            est_in = estimate_tokens(json.dumps(kwargs["messages"]))
            self._check_budget(self._max_call_cost(model, est_in, max_tokens))
            est = est_in + OUTPUT_TOKEN_ESTIMATE
            try:
                for attempt in self._retrying():
                    with attempt, limiter.slot(est):
                        raw = self._send_groq(model, limiter, kwargs)
                break
            except groq.BadRequestError as exc:
                if kwargs["response_format"]["type"] == "json_schema" and _json_schema_unsupported(
                    exc
                ):
                    log.warning("%s rejected json_schema output; falling back to JSON mode", model)
                    self._no_json_schema.add(model)
                    continue
                if _schema_mismatch(exc):
                    totals.errors.append(str(exc))
                    return None
                raise

        completion = raw.parse()
        usage = completion.usage
        reply = _Reply(
            content=completion.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
        limiter.adjust_tokens(reply.input_tokens + reply.output_tokens - est)
        self._observe_groq_headers(limiter, raw.headers, est)
        return reply

    def _send_groq(self, model: str, limiter: RateLimiter, kwargs: dict[str, Any]) -> Any:
        try:
            return self._client.chat.completions.with_raw_response.create(**kwargs)
        except groq.RateLimitError as exc:
            ra = _retry_after(exc)
            if ra is not None and ra > self.settings.max_retry_wait_seconds:
                raise LLMRateLimitExhausted(
                    f"Groq limit for {model} exhausted (retry-after {ra:.0f}s). Stopping; "
                    "completed calls are cached, so the next run resumes from here."
                ) from exc
            limiter.pause(ra if ra is not None else 5.0)
            raise
        except (groq.AuthenticationError, groq.PermissionDeniedError) as exc:
            raise LLMConfigError(f"Groq rejected the API key (GROQ_API_KEY): {exc}") from exc
        except groq.NotFoundError as exc:
            raise LLMConfigError(
                f"Groq model '{model}' not found or retired. Update llm.small_model / "
                "llm.large_model in config/settings.yaml and run the gold-set check."
            ) from exc
        except groq.BadRequestError as exc:
            if "decommissioned" in str(exc).lower():
                raise LLMConfigError(
                    f"Groq model '{model}' has been decommissioned. Update config/settings.yaml."
                ) from exc
            raise

    @staticmethod
    def _observe_groq_headers(limiter: RateLimiter, headers: Any, est: int) -> None:
        """x-ratelimit-*-tokens refer to TPM: if the next call likely won't fit, wait it out."""
        try:
            remaining = float(headers.get("x-ratelimit-remaining-tokens"))
        except (TypeError, ValueError):
            return
        if remaining < est:
            reset = parse_duration(headers.get("x-ratelimit-reset-tokens"))
            if reset:
                limiter.pause(reset)

    # --- cache -------------------------------------------------------------

    def _cache_get(self, key: str, response_model: type[T]) -> T | None:
        with session_scope(self._sessions) as s:
            row = s.get(LLMCacheRow, key)
            data = row.response if row else None
        if data is None:
            return None
        try:
            return response_model.model_validate(data)
        except ValidationError:
            log.info(
                "Cached response %s no longer matches %s; refetching",
                key[:12],
                response_model.__name__,
            )
            return None

    def _cache_put(
        self, key: str, model: str, prompt_version: str, value: BaseModel, totals: _CallTotals
    ) -> None:
        try:
            with session_scope(self._sessions) as s:
                s.merge(
                    LLMCacheRow(
                        cache_key=key,
                        model=model,
                        prompt_version=prompt_version,
                        response=value.model_dump(mode="json"),
                        input_tokens=totals.input_tokens,
                        output_tokens=totals.output_tokens,
                    )
                )
        except IntegrityError:
            log.debug("Cache key %s written concurrently; keeping existing row", key[:12])
