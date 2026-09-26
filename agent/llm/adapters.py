"""Provider adapters. Each one only moves an already-masked prompt to a provider and back.

AnthropicAdapter          the official `anthropic` SDK (Messages API); JSON via output_config json_schema.
OpenAICompatibleAdapter   POST {base_url}/chat/completions, which OpenAI, Groq, Gemini's OpenAI-compatible
                          endpoint and Ollama (http://localhost:11434/v1) all accept. JSON via
                          response_format json_object, the mode those endpoints share.
FakeAdapter               deterministic, no network; records every prompt it receives so tests can prove
                          what reached the "provider".

Adapters are thin stubs for the prototype: no streaming, no tool use, no provider-side retries beyond what the
SDK does by default. Keys are passed in by agent/llm/config.py from environment variables and never logged.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent.llm.port import Completion, MaskedPrompt, require_masked

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def urllib_post(url: str, headers: dict[str, str], body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 (URL comes from config)
        return json.loads(response.read().decode())


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(self, model: str, api_key: str, timeout_s: float = 30.0, client: Any = None) -> None:
        self.model = model
        if client is None:
            import anthropic  # imported lazily so the port works without the SDK installed

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)
        self._client = client

    def complete(self, prompt: MaskedPrompt, max_tokens: int) -> Completion:
        prompt = require_masked(prompt)
        kwargs: dict[str, Any] = {
            "model": self.model, "max_tokens": max_tokens, "system": prompt.system,
            "messages": [{"role": "user", "content": prompt.user}],
        }
        if prompt.json_schema is not None:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": prompt.json_schema}}
        response = self._client.messages.create(**kwargs)
        if getattr(response, "stop_reason", None) == "refusal":
            raise RuntimeError("model refused the request")
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        usage = getattr(response, "usage", None)
        return Completion(text, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
                          getattr(response, "model", self.model))


class OpenAICompatibleAdapter:
    def __init__(self, provider: str, model: str, base_url: str, api_key: str | None = None,
                 timeout_s: float = 30.0, transport: Transport = urllib_post,
                 reasoning_effort: str | None = None) -> None:
        self.provider = provider
        self.reasoning_effort = reasoning_effort
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._transport = transport

    def __repr__(self) -> str:  # never show the key
        return f"OpenAICompatibleAdapter(provider={self.provider!r}, model={self.model!r}, url={self._url!r})"

    def complete(self, prompt: MaskedPrompt, max_tokens: int) -> Completion:
        prompt = require_masked(prompt)
        body: dict[str, Any] = {
            "model": self.model,
            # OpenAI's current models reject max_tokens; the other OpenAI-compatible endpoints expect it.
            ("max_completion_tokens" if self.provider == "openai" else "max_tokens"): max_tokens,
            "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}],
        }
        if prompt.json_schema is not None:
            body["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:  # reasoning tokens bill as output, so the default is kept low where supported
            body["reasoning_effort"] = self.reasoning_effort
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        data = self._transport(self._url, headers, body, self._timeout_s)
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage") or {}
        return Completion(text, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                          data.get("model", self.model))


@dataclass
class FakeAdapter:
    """Deterministic adapter for tests and offline evaluation. Responses are scripted, never generated."""

    extract_output: dict[str, Any] | str = field(default_factory=dict)
    reply_output: str = "respuesta de prueba"
    fail_with: Exception | None = None
    provider: str = "fake"
    model: str = "fake-deterministic-v1"
    received: list[MaskedPrompt] = field(default_factory=list)

    def complete(self, prompt: MaskedPrompt, max_tokens: int) -> Completion:
        prompt = require_masked(prompt)
        self.received.append(prompt)
        if self.fail_with is not None:
            raise self.fail_with
        if prompt.json_schema is not None:
            text = self.extract_output if isinstance(self.extract_output, str) else json.dumps(self.extract_output)
        else:
            text = self.reply_output
        # Token counts are a whitespace-word proxy, not a real tokenizer; the fake is priced at zero.
        return Completion(text, len((prompt.system + " " + prompt.user).split()), len(text.split()), self.model)
