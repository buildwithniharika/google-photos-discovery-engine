"""Groq LLM client: structured JSON output, validation, retries, rate limiting, caching, cost.

Every pipeline stage calls `LLMClient.complete(...)`; nothing else talks to the Groq SDK.

Call flow:
  1. Truncate the input; build the cache key (model + prompt version + input).
  2. Cache hit -> return the validated object with zero new tokens.
  3. Budget check, then call Groq under the per-model rate limiter, using JSON Schema
     structured output (strict where supported), falling back to JSON mode if the model
     rejects `json_schema`.
  4. 429 -> honor `retry-after` (pausing every thread); 5xx / network -> exponential backoff.
     A `retry-after` longer than `max_retry_wait_seconds` means a daily limit is exhausted:
     raise `LLMRateLimitExhausted` so the stage can stop cleanly and resume next run.
  5. Validate with Pydantic; on failure retry once with the error message.
  6. Record tokens and cost; store the validated response in `llm_cache`.
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
from typing import Any, Generic, TypeVar

import groq
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from discovery.ai.prompts import Prompt
from discovery.ai.rate_limiter import RateLimiter
from discovery.config import LLMSettings
from discovery.db import session_scope
from discovery.models.orm import LLMCacheRow

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

TRUNCATION_MARKER = "\n[...truncated]"
CHARS_PER_TOKEN = 4
OUTPUT_TOKEN_ESTIMATE = 512


class LLMError(Exception):
    pass


class LLMConfigError(LLMError):
    """Missing/invalid API key or unknown model. Not retryable; fix config and re-run."""


class LLMRateLimitExhausted(LLMError):
    """A daily request/token limit is used up. Stop the stage; the next run resumes from cache."""


class LLMBudgetExceeded(LLMError):
    """The run's configured cost ceiling was reached."""


class LLMValidationError(LLMError):
    """The model kept returning output that does not match the schema."""


@dataclass
class LLMUsage:
    requests: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
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


@dataclass
class _CallTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


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
    """Make a Pydantic JSON Schema acceptable to Groq strict mode: inline `$ref`s, close every
    object (`additionalProperties: false`), mark every property required, and drop
    `default`/`title` keys. Optional fields stay required but nullable (`anyOf [..., null]`)."""
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


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def parse_duration(value: str | None) -> float | None:
    """Parse Groq reset headers such as '7.66s', '2m59.56s', '1h2m', or plain seconds."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    parts = _DURATION_PART.findall(value)
    return sum(float(n) * scale[u] for n, u in parts) if parts else None


def _retry_after(exc: groq.APIStatusError) -> float | None:
    return parse_duration(exc.response.headers.get("retry-after"))


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, groq.RateLimitError | groq.APIConnectionError | groq.InternalServerError)


def _json_schema_unsupported(exc: groq.BadRequestError) -> bool:
    msg = str(exc).lower()
    return "json_schema" in msg or ("response_format" in msg and "support" in msg)


def _schema_mismatch(exc: groq.BadRequestError) -> bool:
    return "does not match the expected schema" in str(exc).lower()


class LLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        session_factory: sessionmaker[Session],
        *,
        api_key: str | None = None,
        groq_client: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if groq_client is None:
            if not api_key:
                raise LLMConfigError(
                    "GROQ_API_KEY is not set. Add it to .env (local) or the GitHub Actions "
                    "secrets (pipeline). Get a key at https://console.groq.com/keys"
                )
            groq_client = groq.Groq(api_key=api_key, max_retries=0, timeout=60.0)
        self.settings = settings
        self._client = groq_client
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
    ) -> LLMResult[T]:
        model = model or self.settings.small_model
        user_input = truncate(user_input, self.settings.max_input_chars)
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
            content = self._call(model, messages, response_model, temperature, totals)
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
                    {"role": "assistant", "content": content},
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

    # --- Groq call ---------------------------------------------------------

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

    def _check_budget(self) -> None:
        ceiling = self.settings.max_cost_usd_per_run
        if ceiling is not None and self.usage.cost_usd >= ceiling:
            raise LLMBudgetExceeded(
                f"LLM spend ${self.usage.cost_usd:.4f} reached the run ceiling ${ceiling:.2f} "
                "(llm.max_cost_usd_per_run)"
            )

    def _cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.settings.pricing.get(model)
        if price is None:
            return 0.0
        return (input_tokens * price.input + output_tokens * price.output) / 1_000_000

    def _request_kwargs(
        self,
        model: str,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        temperature: float | None,
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
            "max_completion_tokens": self.settings.max_output_tokens,
            "response_format": response_format,
        }
        if self.settings.reasoning_effort and "gpt-oss" in model:
            kwargs["reasoning_effort"] = self.settings.reasoning_effort
        return kwargs

    def _call(
        self,
        model: str,
        messages: list[dict[str, str]],
        response_model: type[BaseModel],
        temperature: float | None,
        totals: _CallTotals,
    ) -> str:
        """One logical completion (with transport retries). Returns the raw message content."""
        limiter = self._limiter(model)

        def wait(state) -> float:
            exc = state.outcome.exception()
            if isinstance(exc, groq.RateLimitError):
                ra = _retry_after(exc)
                if ra is not None:
                    return ra
            return wait_exponential_jitter(initial=1, max=30)(state)

        retrying = Retrying(
            stop=stop_after_attempt(self.settings.max_attempts),
            retry=retry_if_exception(_is_retryable),
            wait=wait,
            sleep=self._sleep,
            reraise=True,
        )
        while True:
            self._check_budget()
            kwargs = self._request_kwargs(model, messages, response_model, temperature)
            est = estimate_tokens(json.dumps(kwargs["messages"])) + OUTPUT_TOKEN_ESTIMATE
            try:
                for attempt in retrying:
                    with attempt, limiter.slot(est):
                        raw = self._send(model, limiter, kwargs)
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
                    return "{}"  # best-effort mode miss: let validation trigger the one retry
                raise

        completion = raw.parse()
        usage = completion.usage
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        cost = self._cost(model, in_tok, out_tok)
        limiter.adjust_tokens(in_tok + out_tok - est)
        self._observe_headers(limiter, raw.headers, est)

        totals.input_tokens += in_tok
        totals.output_tokens += out_tok
        totals.cost_usd += cost
        with self._lock:
            self.usage.requests += 1
            self.usage.input_tokens += in_tok
            self.usage.output_tokens += out_tok
            self.usage.cost_usd += cost
        return completion.choices[0].message.content or ""

    def _send(self, model: str, limiter: RateLimiter, kwargs: dict[str, Any]) -> Any:
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
    def _observe_headers(limiter: RateLimiter, headers: Any, est: int) -> None:
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
