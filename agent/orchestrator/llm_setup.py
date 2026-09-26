"""Build the orchestrator's language model from LLM_* settings (agent/llm/config.py).

LLM_PROVIDER=fake (or unset) means no model: understanding uses the deterministic parser and replies use the
templates, and every trail entry says so. The FakeAdapter's scripted outputs are for tests, where they are
injected explicitly; a demo never presents scripted text as model output.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agent.llm.config import LLMConfigError, build_adapter, load_settings
from agent.llm.port import MaskedLLM
from agent.security.audit import AuditLog


@dataclass(frozen=True)
class LLMChoice:
    llm: MaskedLLM | None
    provider: str
    model: str | None
    note: str


def build_language_model(audit: AuditLog | None = None, environ: Mapping[str, str] | None = None,
                         max_tokens: int = 512) -> LLMChoice:
    if environ is not None and not environ.get("LLM_PROVIDER", "").strip():
        return LLMChoice(None, "fake", None, "LLM_PROVIDER not set: no model; deterministic parser and templates")
    try:
        settings = load_settings(environ)
    except LLMConfigError as exc:
        return LLMChoice(None, "fake", None, f"no model ({exc}); deterministic parser and templates")
    if settings.provider == "fake":
        return LLMChoice(None, "fake", None, "LLM_PROVIDER=fake: deterministic parser and templates")
    llm = MaskedLLM(build_adapter(settings), audit=audit, max_tokens=max_tokens)
    return LLMChoice(llm, settings.provider, settings.model, f"{settings.provider}:{settings.model}")
