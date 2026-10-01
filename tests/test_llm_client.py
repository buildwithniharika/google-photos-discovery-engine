from __future__ import annotations

import json
from types import SimpleNamespace

import groq
import httpx
import pytest

from discovery.ai.llm_client import (
    TRUNCATION_MARKER,
    LLMBudgetExceeded,
    LLMClient,
    LLMConfigError,
    LLMRateLimitExhausted,
    LLMValidationError,
    parse_duration,
    to_strict_schema,
)
from discovery.ai.prompts import Prompt, load_prompt
from discovery.models.schemas import Insight, RelevanceResult
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


@pytest.fixture
def make_client(cfg, session_factory, fake_time):
    def _make(fake: FakeGroq, **overrides) -> LLMClient:
        settings = cfg.settings.llm.model_copy(update=overrides)
        return LLMClient(
            settings,
            session_factory,
            groq_client=fake,
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
    with pytest.raises(LLMConfigError, match="GROQ_API_KEY"):
        LLMClient(cfg.settings.llm, session_factory, api_key=None)


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
