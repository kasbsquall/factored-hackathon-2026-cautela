"""LLM configuration from environment variables (loaded from the git-ignored .env). No key is hardcoded.

Variables:
  LLM_PROVIDER     anthropic | openai | groq | gemini | ollama | openai_compatible | fake
  LLM_MODEL        model id for that provider (required except for fake)
  LLM_BASE_URL     overrides the provider's default base URL (required for openai_compatible)
  LLM_API_KEY_ENV  name of the variable that holds the key, when it differs from the provider default
  LLM_TIMEOUT_S    request timeout in seconds (default 30)
Provider key variables: ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, GEMINI_API_KEY, LLM_API_KEY
(openai_compatible). Ollama needs no key.

Error messages name the missing variable and never include a value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agent.llm.adapters import AnthropicAdapter, FakeAdapter, OpenAICompatibleAdapter
from agent.llm.port import LLMAdapter

# provider -> (default key variable, default base URL). Base URLs are the providers' documented
# OpenAI-compatible endpoints; override with LLM_BASE_URL if a provider changes it.
PROVIDERS: dict[str, tuple[str | None, str | None]] = {
    "anthropic": ("ANTHROPIC_API_KEY", None),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "ollama": (None, "http://localhost:11434/v1"),
    "openai_compatible": ("LLM_API_KEY", None),
    "fake": (None, None),
}


class LLMConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    base_url: str | None
    api_key_env: str | None
    timeout_s: float
    api_key: str | None = field(default=None, repr=False)


def load_settings(environ: Mapping[str, str] | None = None, env_file: str | Path = ".env") -> LLMSettings:
    if environ is None:
        from data_engineering.pipelines.env import load_env_file

        load_env_file(env_file)
        environ = os.environ
    provider = environ.get("LLM_PROVIDER", "").strip().lower()
    if provider not in PROVIDERS:
        raise LLMConfigError(f"LLM_PROVIDER must be one of {sorted(PROVIDERS)}")
    default_key_env, default_url = PROVIDERS[provider]
    model = environ.get("LLM_MODEL", "").strip() or ("fake-deterministic-v1" if provider == "fake" else "")
    if not model:
        raise LLMConfigError("LLM_MODEL is not set")
    base_url = environ.get("LLM_BASE_URL", "").strip() or default_url
    if provider == "openai_compatible" and not base_url:
        raise LLMConfigError("LLM_BASE_URL is required for openai_compatible")
    key_env = environ.get("LLM_API_KEY_ENV", "").strip() or default_key_env
    api_key = environ.get(key_env, "").strip() if key_env else None
    if key_env and provider != "ollama" and not api_key:
        raise LLMConfigError(f"{key_env} is not set")
    try:
        timeout = float(environ.get("LLM_TIMEOUT_S", "") or 30)
    except ValueError as exc:
        raise LLMConfigError("LLM_TIMEOUT_S must be a number") from exc
    return LLMSettings(provider, model, base_url, key_env, timeout, api_key or None)


def build_adapter(settings: LLMSettings) -> LLMAdapter:
    if settings.provider == "fake":
        return FakeAdapter()
    if settings.provider == "anthropic":
        return AnthropicAdapter(settings.model, settings.api_key or "", settings.timeout_s)
    return OpenAICompatibleAdapter(settings.provider, settings.model, settings.base_url or "", settings.api_key,
                                   settings.timeout_s)
