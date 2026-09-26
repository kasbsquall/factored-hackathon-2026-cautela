"""LLM port: masking before any adapter, schema validation, usage and cost, config, adapters. No network."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agent.clock import FrozenClock
from agent.llm.adapters import AnthropicAdapter, FakeAdapter, OpenAICompatibleAdapter
from agent.llm.config import LLMConfigError, build_adapter, load_settings
from agent.llm.port import (
    PROMPT_VERSION,
    LLMOutputError,
    LLMUnavailable,
    MaskedLLM,
    MaskedPrompt,
    UnmaskedInputError,
)
from agent.llm.pricing import PriceTable
from agent.security.audit import AuditLog

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["intent", "amount"],
    "properties": {"intent": {"type": "string"}, "amount": {"type": ["number", "null"]},
                   "merchant": {"type": ["string", "null"]}},
}
DOC, CARD, EMAIL, PHONE = "1.023.456.789", "4111 1111 1111 1234", "ana.ruiz@correo.example", "+57 300 123 4567"
MESSAGE = (f"Hola, me llamo Ana Ruiz, mi cédula es {DOC}, tarjeta {CARD}, correo {EMAIL}, cel {PHONE}. "
           "No reconozco un cargo de COP 2.140.000 en Farmacia San Rafael.")
RAW_VALUES = [DOC, "1023456789", CARD, "4111111111111234", EMAIL, "300 123 4567", "Ruiz"]


def _llm(adapter, **kwargs) -> MaskedLLM:
    return MaskedLLM(adapter, PriceTable.load(), **kwargs)


def _everything_sent(adapter: FakeAdapter) -> str:
    return " ".join(p.system + " " + p.user for p in adapter.received)


def test_unmasked_document_never_reaches_the_adapter():
    adapter = FakeAdapter(extract_output={"intent": "unrecognized_charge", "amount": 2140000})
    llm = _llm(adapter)
    llm.extract(MESSAGE, SCHEMA, known_names=["Ruiz"])
    llm.reply({"document_number": "1023456789", "note": MESSAGE, "amount_text": "COP 2.140.000"}, "es",
              known_names=["Ruiz"])
    sent = _everything_sent(adapter)
    assert len(adapter.received) == 2
    for raw in RAW_VALUES:
        assert raw not in sent, raw
    assert "COP 2.140.000" in sent  # the amount survives, so the model can still extract it


def test_adapter_refuses_prompts_not_built_by_the_port():
    adapter = FakeAdapter()
    with pytest.raises(UnmaskedInputError):
        adapter.complete(MESSAGE, 100)  # raw string
    with pytest.raises(UnmaskedInputError):
        MaskedPrompt("system", MESSAGE, None)  # cannot be constructed without the port's seal
    assert adapter.received == []


def test_extract_returns_schema_valid_dict():
    llm = _llm(FakeAdapter(extract_output="```json\n{\"intent\": \"unrecognized_charge\", \"amount\": 120.5}\n```"))
    assert llm.extract("no reconozco 120,50", SCHEMA) == {"intent": "unrecognized_charge", "amount": 120.5}


@pytest.mark.parametrize("output", ['{"intent": 3, "amount": null}', "not json", "[1, 2]",
                                    '{"intent": "x", "amount": 1, "customer_id": "C000002"}'],
                         ids=["wrong_type", "not_json", "not_object", "extra_field"])
def test_extract_rejects_output_that_breaks_the_schema(output):
    with pytest.raises(LLMOutputError):
        _llm(FakeAdapter(extract_output=output)).extract("hola", SCHEMA)


def test_reply_only_in_supported_languages_and_uses_masked_facts():
    adapter = FakeAdapter(reply_output="  Tu caso fue registrado.  ")
    llm = _llm(adapter)
    assert llm.reply({"case_status": "verified"}, "pt") == "Tu caso fue registrado."
    assert "Portuguese" in adapter.received[0].system
    with pytest.raises(ValueError):
        llm.reply({}, "en")


def test_usage_records_tokens_latency_cost_and_prompt_version():
    ticks = iter([10.0, 10.25])
    llm = MaskedLLM(FakeAdapter(extract_output={"intent": "x", "amount": None}), PriceTable.load(),
                    timer=lambda: next(ticks))
    llm.extract("hola", SCHEMA, trace_id="tr_x")
    call = llm.usage.calls[0]
    assert call.trace_id == "tr_x" and call.prompt_version == PROMPT_VERSION
    assert call.input_tokens > 0 and call.output_tokens > 0 and call.latency_ms == 250.0
    assert (call.cost_usd, call.cost_status) == (0.0, "estimated")  # fake is priced at zero
    assert llm.usage.summary()["calls"] == 1


def test_cost_is_unknown_when_price_is_a_placeholder(tmp_path):
    adapter = FakeAdapter(provider="anthropic", model="some-model")
    llm = _llm(adapter)
    llm.reply({"a": 1}, "es")
    assert llm.usage.calls[0].cost_usd is None and llm.usage.calls[0].cost_status == "price_unknown"
    assert llm.usage.summary()["cost_usd"] is None


def test_cost_is_computed_from_a_filled_price_row(tmp_path):
    table = tmp_path / "prices.yaml"
    table.write_text("prices:\n  - {provider: p, model: m, input_per_mtok: 2.0, output_per_mtok: 10.0, "
                     "source: test value}\n", encoding="utf-8")
    assert PriceTable.load(table).cost("p", "m", 1_000_000, 100_000) == (3.0, "estimated")
    assert PriceTable.load(table).cost("p", "m", None, 10) == (None, "tokens_unknown")
    assert PriceTable.load(table).cost("p", "other", 1, 1) == (None, "price_unknown")


def test_price_file_invents_no_prices():
    # A real provider row is either an unfilled placeholder or carries its official source URL and read date.
    for entry in PriceTable.load().entries:
        if entry.provider in ("fake", "ollama"):
            continue
        if entry.input_per_mtok is None or entry.output_per_mtok is None:
            assert "TODO" in entry.source
        else:
            assert "https://" in entry.source and "read 2026-" in entry.source


def test_provider_failure_is_recorded_and_raised_as_unavailable():
    llm = _llm(FakeAdapter(fail_with=TimeoutError("slow")))
    with pytest.raises(LLMUnavailable):
        llm.reply({"a": 1}, "es")
    assert llm.usage.calls[0].ok is False and llm.usage.calls[0].error == "TimeoutError"


def test_llm_calls_are_audited_without_prompt_text(tmp_path):
    audit = AuditLog(secrets.token_bytes(48), tmp_path, FrozenClock(datetime(2026, 6, 1, tzinfo=UTC)))
    _llm(FakeAdapter(extract_output={"intent": "x", "amount": None}), audit=audit).extract(MESSAGE, SCHEMA)
    record = audit.records()[-1]
    assert record.step == "llm.extract" and record.masked_args["prompt_version"] == PROMPT_VERSION
    assert "Farmacia" not in str(record.masked_args)


# ---- configuration -----------------------------------------------------------------------------------
def test_settings_for_local_ollama_need_no_key():
    s = load_settings({"LLM_PROVIDER": "ollama", "LLM_MODEL": "llama3.1"})
    assert s.base_url == "http://localhost:11434/v1" and s.api_key is None
    adapter = build_adapter(s)
    assert isinstance(adapter, OpenAICompatibleAdapter) and adapter.provider == "ollama"


@pytest.mark.parametrize(("env", "missing"), [
    ({"LLM_PROVIDER": "anthropic", "LLM_MODEL": "m"}, "ANTHROPIC_API_KEY"),
    ({"LLM_PROVIDER": "groq", "LLM_MODEL": "m"}, "GROQ_API_KEY"),
    ({"LLM_PROVIDER": "openai_compatible", "LLM_MODEL": "m"}, "LLM_BASE_URL"),
    ({"LLM_PROVIDER": "openai"}, "LLM_MODEL"),
    ({"LLM_PROVIDER": "somebody"}, "LLM_PROVIDER"),
    ({}, "LLM_PROVIDER"),
])
def test_missing_configuration_names_the_variable(env, missing):
    with pytest.raises(LLMConfigError) as exc:
        load_settings(env)
    assert missing in str(exc.value)


def test_key_is_read_from_the_named_variable_and_never_shown():
    key = "sk-test-" + secrets.token_hex(8)
    s = load_settings({"LLM_PROVIDER": "gemini", "LLM_MODEL": "m", "MY_GEMINI": key, "LLM_API_KEY_ENV": "MY_GEMINI"})
    assert s.api_key == key and key not in repr(s)
    assert s.base_url.startswith("https://generativelanguage.googleapis.com")
    assert key not in repr(build_adapter(s))


# ---- adapters, with injected transports ------------------------------------------------------------------
def _sealed_prompt(schema=None) -> MaskedPrompt:
    adapter = FakeAdapter(extract_output={"intent": "x", "amount": None})
    llm = _llm(adapter)
    if schema:
        llm.extract("hola", schema)
    else:
        llm.reply({"a": 1}, "es")
    return adapter.received[0]


def test_openai_compatible_adapter_builds_the_request_and_reads_usage():
    seen = {}

    def transport(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body, timeout=timeout)
        return {"model": "m-1", "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3}}

    adapter = OpenAICompatibleAdapter("groq", "m", "https://api.groq.com/openai/v1/", "k", 5.0, transport)
    completion = adapter.complete(_sealed_prompt(SCHEMA), 256)
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["headers"] == {"Authorization": "Bearer k"} and seen["body"]["max_tokens"] == 256
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    assert (completion.input_tokens, completion.output_tokens, completion.model) == (12, 3, "m-1")


def test_anthropic_adapter_uses_messages_api_shape():
    calls = {}

    def create(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(stop_reason="end_turn", model="m", content=[SimpleNamespace(type="text", text="ok")],
                               usage=SimpleNamespace(input_tokens=7, output_tokens=2))

    adapter = AnthropicAdapter("m", "unused", client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    completion = adapter.complete(_sealed_prompt(SCHEMA), 128)
    assert calls["system"] and calls["messages"][0]["role"] == "user"
    assert calls["output_config"]["format"]["type"] == "json_schema"
    assert (completion.text, completion.input_tokens, completion.output_tokens) == ("ok", 7, 2)


def test_anthropic_refusal_becomes_unavailable():
    def create(**_):
        return SimpleNamespace(stop_reason="refusal", content=[], usage=None, model="m")

    adapter = AnthropicAdapter("m", "unused", client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    with pytest.raises(LLMUnavailable):
        _llm(adapter).reply({"a": 1}, "es")
