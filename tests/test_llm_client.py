from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import anthropic
import groq
import httpx
import httpx2
import pytest

from discovery.ai.llm_client import (
    TRUNCATION_MARKER,
    LLMBudgetExceeded,
    LLMClient,
    LLMConfigError,
    LLMRateLimitExhausted,
    LLMValidationError,
    parse_duration,
    seconds_until,
    to_anthropic_schema,
    to_strict_schema,
)
from discovery.ai.prompts import Prompt, load_prompt
from discovery.config import ModelPricing
from discovery.models.schemas import Insight, RelevanceBatch, RelevanceResult
from tests.test_rate_limiter import FakeTime, max_in_window
from tests.test_schemas import RELEVANCE_EXAMPLE

PROMPT = Prompt(name="smoke_test", version=1, text="Classify the feedback.")


def ok(
    content: dict | str = RELEVANCE_EXAMPLE,
    in_tok: int = 300,
    out_tok: int = 80,
    headers: dict | None = None,
):
    body = content if isinstance(content, str) else json.dumps(content)
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=body))],
        usage=SimpleNamespace(prompt_tokens=in_tok, completion_tokens=out_tok),
    )
    return SimpleNamespace(parse=lambda: completion, headers=headers or {})


def api_error(cls, status: int, message: str = "error", headers: dict | None = None):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return cls(message, response=response, body=None)


class FakeGroq:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        create = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0) if self.responses else ok()
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def fake_time():
    return FakeTime()


GROQ_SETTINGS = {
    "provider": "groq",
    "small_model": "openai/gpt-oss-20b",
    "large_model": "openai/gpt-oss-120b",
    "pricing": {
        "openai/gpt-oss-20b": ModelPricing(input=0.075, output=0.30),
        "openai/gpt-oss-120b": ModelPricing(input=0.15, output=0.60),
    },
}


@pytest.fixture
def make_client(cfg, session_factory, fake_time):
    """Groq-backed client (the Anthropic path has its own fixture below)."""

    def _make(fake: FakeGroq, **overrides) -> LLMClient:
        settings = cfg.settings.llm.model_copy(update={**GROQ_SETTINGS, **overrides})
        return LLMClient(
            settings,
            session_factory,
            sdk_client=fake,
            clock=fake_time.clock,
            sleep=fake_time.sleep,
        )

    return _make


def complete(client: LLMClient, text: str = "I can't find that photo from the Goa trip", **kw):
    return client.complete(prompt=PROMPT, user_input=text, response_model=RelevanceResult, **kw)


# --- caching -----------------------------------------------------------------


def test_repeated_call_is_served_from_cache_with_zero_tokens(make_client):
    fake = FakeGroq(ok())
    client = make_client(fake)
    first = complete(client)
    second = complete(client)
    assert isinstance(first.value, RelevanceResult)
    assert not first.cached and first.input_tokens == 300
    assert second.cached and second.input_tokens == second.output_tokens == 0
    assert second.value == first.value
    assert len(fake.calls) == 1
    assert client.usage.cache_hits == 1 and client.usage.requests == 1


def test_cache_survives_a_new_client(make_client):
    complete(make_client(FakeGroq(ok())))
    fake = FakeGroq()
    assert complete(make_client(fake)).cached
    assert fake.calls == []


def test_prompt_version_change_misses_cache(make_client):
    fake = FakeGroq(ok(), ok())
    client = make_client(fake)
    complete(client)
    client.complete(
        prompt=Prompt("smoke_test", 2, "v2"),
        user_input="I can't find that photo from the Goa trip",
        response_model=RelevanceResult,
    )
    assert len(fake.calls) == 2


def test_model_change_misses_cache(make_client):
    fake = FakeGroq(ok(), ok())
    client = make_client(fake)
    complete(client)
    complete(client, model="openai/gpt-oss-120b")
    assert len(fake.calls) == 2


# --- structured output -------------------------------------------------------


def test_sends_strict_json_schema(make_client):
    fake = FakeGroq(ok())
    complete(make_client(fake))
    rf = fake.calls[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    schema = rf["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(RelevanceResult.model_fields)
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema


def test_strict_schema_closes_nested_objects():
    schema = to_strict_schema(Insight.model_json_schema())
    cue = schema["properties"]["remembered_cues"]["items"]
    assert cue["additionalProperties"] is False
    assert set(cue["required"]) == {"cue", "cue_type"}
    assert "enum" in cue["properties"]["cue_type"]
    assert "default" not in json.dumps(schema)


def test_falls_back_to_json_mode_when_schema_unsupported(make_client):
    err = api_error(
        groq.BadRequestError, 400, "response_format `json_schema` is not supported with this model"
    )
    fake = FakeGroq(err, ok(), ok())
    client = make_client(fake)
    result = complete(client)
    assert result.value.retrieval_type == "vague_memory_retrieval"
    assert fake.calls[1]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in fake.calls[1]["messages"][0]["content"]
    complete(client, text="another review")  # model remembered as unsupported
    assert fake.calls[2]["response_format"] == {"type": "json_object"}


def test_invalid_output_is_retried_once_with_the_error(make_client):
    bad = {**RELEVANCE_EXAMPLE, "confidence": 3}
    fake = FakeGroq(ok(bad), ok())
    result = complete(make_client(fake))
    assert result.value.confidence == 0.9
    assert len(fake.calls) == 2
    assert "not valid" in fake.calls[1]["messages"][-1]["content"]
    assert result.input_tokens == 600  # both attempts are billed


def test_groq_json_validate_failed_is_retried_once(make_client):
    err = api_error(
        groq.BadRequestError,
        400,
        "Failed to generate JSON. Please adjust your prompt. code: json_validate_failed",
    )
    fake = FakeGroq(err, ok())
    result = complete(make_client(fake))
    assert result.value.retrieval_type.value == RELEVANCE_EXAMPLE["retrieval_type"]
    assert len(fake.calls) == 2
    assert "not valid against the required JSON schema" in fake.calls[1]["messages"][-1]["content"]


def test_invalid_output_twice_raises(make_client):
    fake = FakeGroq(ok("not json"), ok("{}"))
    with pytest.raises(LLMValidationError):
        complete(make_client(fake))


def test_reasoning_effort_only_for_gpt_oss(make_client):
    fake = FakeGroq(ok(), ok())
    client = make_client(fake)
    complete(client)
    complete(client, model="qwen/qwen3.8-27b")
    assert fake.calls[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in fake.calls[1]


def test_long_input_is_truncated(make_client, cfg):
    fake = FakeGroq(ok())
    complete(make_client(fake), text="x" * 50_000)
    sent = fake.calls[0]["messages"][1]["content"]
    assert len(sent) == cfg.settings.llm.max_input_chars
    assert sent.endswith(TRUNCATION_MARKER)


def test_per_call_input_and_output_limits_override_settings(make_client, cfg):
    fake = FakeGroq(ok())
    long_text = "x" * (cfg.settings.llm.max_input_chars + 500)
    complete(
        make_client(fake), text=long_text, max_input_chars=len(long_text), max_output_tokens=4096
    )
    assert fake.calls[0]["messages"][1]["content"] == long_text
    assert fake.calls[0]["max_completion_tokens"] == 4096


# --- rate limits and errors --------------------------------------------------


def test_429_honors_retry_after(make_client, fake_time):
    err = api_error(groq.RateLimitError, 429, "rate limited", {"retry-after": "7"})
    fake = FakeGroq(err, ok())
    result = complete(make_client(fake))
    assert result.value.is_retrieval
    assert 7.0 in fake_time.sleeps
    assert len(fake.calls) == 2


def test_long_retry_after_stops_cleanly(make_client):
    err = api_error(groq.RateLimitError, 429, "daily limit", {"retry-after": "3600"})
    fake = FakeGroq(err)
    with pytest.raises(LLMRateLimitExhausted):
        complete(make_client(fake))
    assert len(fake.calls) == 1


def test_server_errors_retry_with_backoff(make_client, fake_time):
    fake = FakeGroq(
        api_error(groq.InternalServerError, 503), api_error(groq.InternalServerError, 500), ok()
    )
    assert complete(make_client(fake)).value.is_retrieval
    assert len(fake.calls) == 3
    assert len([s for s in fake_time.sleeps if s >= 1]) >= 2


def test_gives_up_after_max_attempts(make_client):
    fake = FakeGroq(*[api_error(groq.InternalServerError, 503) for _ in range(10)])
    with pytest.raises(groq.InternalServerError):
        complete(make_client(fake, max_attempts=3))
    assert len(fake.calls) == 3


def test_unknown_model_fails_fast_with_clear_message(make_client):
    fake = FakeGroq(api_error(groq.NotFoundError, 404, "model not found"))
    with pytest.raises(LLMConfigError, match="config/settings.yaml"):
        complete(make_client(fake))
    assert len(fake.calls) == 1


def test_invalid_key_fails_fast(make_client):
    fake = FakeGroq(api_error(groq.AuthenticationError, 401, "invalid api key"))
    with pytest.raises(LLMConfigError, match="GROQ_API_KEY"):
        complete(make_client(fake))


def test_missing_api_key(cfg, session_factory):
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        LLMClient(cfg.settings.llm, session_factory, api_key=None)
    groq_settings = cfg.settings.llm.model_copy(update=GROQ_SETTINGS)
    with pytest.raises(LLMConfigError, match="GROQ_API_KEY"):
        LLMClient(groq_settings, session_factory, api_key=None)


def test_low_remaining_tokens_header_pauses(make_client, fake_time):
    headers = {"x-ratelimit-remaining-tokens": "10", "x-ratelimit-reset-tokens": "9.5s"}
    fake = FakeGroq(ok(headers=headers), ok())
    client = make_client(fake)
    complete(client)
    before = fake_time.now
    complete(client, text="a different review")
    assert fake_time.now - before >= 9.5


def test_burst_stays_within_rate_limit(make_client, fake_time, cfg):
    """Acceptance: a burst of calls never exceeds the configured requests per minute."""
    fake = FakeGroq()
    client = make_client(fake)
    times = []
    original = fake._create

    def timed(**kwargs):
        times.append(fake_time.now)
        return original(**kwargs)

    fake.chat.completions.with_raw_response.create = timed
    for i in range(60):
        complete(client, text=f"review {i}", use_cache=False)
    rpm = cfg.settings.llm.rate_limits["default"].requests_per_minute
    assert len(times) == 60
    assert max_in_window(times) <= rpm


# --- cost and budget ---------------------------------------------------------


def test_cost_uses_configured_pricing(make_client):
    fake = FakeGroq(ok(in_tok=1_000_000, out_tok=1_000_000))
    result = complete(make_client(fake))
    assert result.cost_usd == pytest.approx(0.075 + 0.30)  # openai/gpt-oss-20b


def test_budget_ceiling_stops_further_calls(make_client):
    fake = FakeGroq(ok(in_tok=1_000_000, out_tok=0), ok())
    client = make_client(fake, max_cost_usd_per_run=0.05)
    complete(client)
    with pytest.raises(LLMBudgetExceeded):
        complete(client, text="another review")
    assert len(fake.calls) == 1


# --- Anthropic ---------------------------------------------------------------


def a_ok(
    content: dict | str = RELEVANCE_EXAMPLE,
    in_tok: int = 300,
    out_tok: int = 80,
    cache_write: int = 0,
    cache_read: int = 0,
    stop_reason: str = "end_turn",
    headers: dict | None = None,
):
    body = content if isinstance(content, str) else json.dumps(content)
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=body)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_input_tokens=cache_write,
            cache_read_input_tokens=cache_read,
        ),
    )
    return SimpleNamespace(parse=lambda: message, headers=headers or {})


def a_error(cls, status: int, message: str = "error", headers: dict | None = None):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, headers=headers or {}, request=request)
    return cls(message, response=response, body=None)


class FakeAnthropic:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0) if self.responses else a_ok()
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def make_claude(cfg, session_factory, fake_time):
    def _make(fake: FakeAnthropic, spent_before_usd: float = 0.0, **overrides) -> LLMClient:
        settings = cfg.settings.llm.model_copy(update=overrides)
        assert settings.provider == "anthropic"
        return LLMClient(
            settings,
            session_factory,
            sdk_client=fake,
            spent_before_usd=spent_before_usd,
            clock=fake_time.clock,
            sleep=fake_time.sleep,
        )

    return _make


def test_anthropic_request_caches_system_prompt_and_uses_json_schema(make_claude, cfg):
    fake = FakeAnthropic(a_ok())
    result = complete(make_claude(fake), max_output_tokens=4096)
    assert result.value.retrieval_type.value == RELEVANCE_EXAMPLE["retrieval_type"]
    call = fake.calls[0]
    assert call["model"] == cfg.settings.llm.small_model
    assert call["max_tokens"] == 4096
    assert call["system"] == [
        {"type": "text", "text": PROMPT.text, "cache_control": {"type": "ephemeral"}}
    ]
    assert [m["role"] for m in call["messages"]] == ["user"]
    assert call["output_config"]["effort"] == "low"
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert "temperature" not in call and "response_format" not in call


def test_anthropic_schema_drops_unsupported_constraints():
    for model in (RelevanceResult, RelevanceBatch, Insight):
        text = json.dumps(to_anthropic_schema(model.model_json_schema()))
        for key in ("minimum", "maximum", "minLength", "maxLength", "maxItems", "$ref"):
            assert f'"{key}"' not in text, (model.__name__, key)


def test_anthropic_cost_counts_cache_writes_and_reads(make_claude):
    fake = FakeAnthropic(
        a_ok(in_tok=1_000_000, out_tok=1_000_000, cache_write=1_000_000, cache_read=1_000_000)
    )
    client = make_claude(fake)
    result = complete(client)
    assert result.cost_usd == pytest.approx(2.0 + 10.0 + 2.5 + 0.2)  # claude-sonnet-5-5
    assert result.input_tokens == 3_000_000
    assert client.usage.cached_input_tokens == 1_000_000


def test_anthropic_invalid_output_retry_keeps_turns_alternating(make_claude):
    fake = FakeAnthropic(a_ok({**RELEVANCE_EXAMPLE, "confidence": 3}), a_ok())
    assert complete(make_claude(fake)).value.confidence == 0.9
    roles = [m["role"] for m in fake.calls[1]["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert "not valid" in fake.calls[1]["messages"][-1]["content"]


def test_anthropic_429_honors_retry_after(make_claude, fake_time):
    err = a_error(anthropic.RateLimitError, 429, "rate limited", {"retry-after": "7"})
    fake = FakeAnthropic(err, a_ok())
    assert complete(make_claude(fake)).value.is_retrieval
    assert 7.0 in fake_time.sleeps
    assert len(fake.calls) == 2


def test_anthropic_429_without_retry_after_is_a_spend_limit(make_claude):
    fake = FakeAnthropic(a_error(anthropic.RateLimitError, 429, "spend limit reached"))
    with pytest.raises(LLMRateLimitExhausted, match="spend limit"):
        complete(make_claude(fake))
    assert len(fake.calls) == 1


def test_anthropic_overloaded_is_retried(make_claude):
    fake = FakeAnthropic(
        a_error(anthropic.OverloadedError, 529, "overloaded"),
        a_error(anthropic.APIStatusError, 529, "overloaded"),
        a_ok(),
    )
    assert complete(make_claude(fake)).value.is_retrieval
    assert len(fake.calls) == 3


def test_anthropic_auth_and_credit_errors_fail_fast(make_claude):
    fake = FakeAnthropic(a_error(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        complete(make_claude(fake))
    fake = FakeAnthropic(
        a_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access")
    )
    with pytest.raises(LLMConfigError, match="no remaining credit"):
        complete(make_claude(fake), text="another review")
    assert len(fake.calls) == 1


def test_project_budget_refuses_a_call_that_could_cross_it(make_claude):
    fake = FakeAnthropic()
    client = make_claude(fake, spent_before_usd=4.99, project_budget_usd=5.0)
    with pytest.raises(LLMBudgetExceeded, match="project budget"):
        complete(client)
    assert fake.calls == []
    assert complete(make_claude(fake, spent_before_usd=1.0, project_budget_usd=5.0)).value


def test_anthropic_low_remaining_tokens_header_pauses(make_claude, fake_time):
    reset = (datetime.now(UTC) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    headers = {
        "anthropic-ratelimit-tokens-remaining": "10",
        "anthropic-ratelimit-tokens-reset": reset,
    }
    fake = FakeAnthropic(a_ok(headers=headers), a_ok())
    client = make_claude(fake)
    complete(client)
    before = fake_time.now
    complete(client, text="a different review")
    assert fake_time.now - before >= 25


def test_seconds_until():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    assert seconds_until("2026-10-02T12:00:30Z", now) == pytest.approx(30)
    assert seconds_until("2026-10-02T11:59:00Z", now) == 0.0
    assert seconds_until("not a time", now) is None
    assert seconds_until(None, now) is None


# --- helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("7", 7.0),
        ("7.66s", 7.66),
        ("2m59.56s", 179.56),
        ("1h2m", 3720.0),
        ("250ms", 0.25),
        (None, None),
        ("", None),
        ("soon", None),
    ],
)
def test_parse_duration(value, seconds):
    result = parse_duration(value)
    assert result == (pytest.approx(seconds) if seconds is not None else None)


def test_smoke_prompt_file_loads(cfg):
    prompt = load_prompt("smoke_test", 1, cfg.prompts_dir)
    assert prompt.id == "smoke_test_v1"
    assert "vague_memory_retrieval" in prompt.text
