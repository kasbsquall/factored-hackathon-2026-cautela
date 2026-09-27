"""Process-wide state behind the API: the service stack, the orchestrator, the handoff queue, the demo outbox,
the translation cache.

Every call into the orchestrator or the service runs under one lock: the in-memory stores (sessions, replay ids,
conversations) are single-process by design, which is a documented capacity limit. Two things run outside it: the
translation model call (the audit log and the LLM budget lock themselves) and the verification of the stored audit
files (api/views.py).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.clock import Clock
from agent.demo_scenarios import default_as_of
from agent.orchestrator import Orchestrator, build_language_model, load_default
from agent.orchestrator.disposition import DispositionModel
from agent.orchestrator.llm_setup import LLMChoice
from agent.orchestrator.state import ConversationState
from agent.orchestrator.wiring import OffsetClock, Stack, build_stack, session_secret
from api.ratelimit import RateLimiter
from api.settings import ROOT, ApiSettings

log = logging.getLogger("cautela.api")
DEMO_CODE_TTL_S = 300
TRANSLATION_CACHE_MAX = 2000


@dataclass
class QueueItem:
    conversation_id: str
    received_at: datetime
    handoff: dict[str, Any]


class Runtime:
    def __init__(self, settings: ApiSettings, environ: dict[str, str] | None = None, clock: Clock | None = None,
                 llm_choice: LLMChoice | None = None, disposition: DispositionModel | None = None,
                 stack: Stack | None = None) -> None:
        env = dict(os.environ if environ is None else environ)
        self.settings = settings
        self.clock = clock or OffsetClock(settings.as_of or default_as_of(settings.warehouse))
        secret, from_env = session_secret(env)
        if not from_env:
            log.warning("SESSION_SECRET not set or shorter than 32 bytes: using a random per-process secret")
        self.stack = stack or build_stack(settings.warehouse, self.clock, secret, settings.cases_db,
                                          settings.audit_dir)
        self.llm_choice = llm_choice or build_language_model(self.stack.audit, environ=env)
        self.queue: list[QueueItem] = []
        self.lock = threading.RLock()
        self.orchestrator = Orchestrator(self.stack.service, self.llm_choice.llm, disposition or load_default(),
                                         handoff_sink=self._on_handoff)
        self.demo_codes: dict[str, tuple[str, datetime]] = {}
        self.translations: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
        self.translating: dict[tuple[str, str, str], threading.Event] = {}  # model calls in flight, per message
        self.translate_used: dict[str, int] = {}  # model calls for translation per login session
        self.translate_limit = RateLimiter(*settings.translate_rate)  # model calls for translation, all callers
        self.demo_customers: set[str] = set()
        self.console_key = settings.console_key or (self._demo_console_key() if settings.demo_mode else None)
        self.identities = self._load_identities() if settings.demo_mode else []

    @property
    def service(self):
        return self.stack.service

    def close(self) -> None:
        self.stack.close()

    # ---- handoffs ------------------------------------------------------------------------------------------
    def _on_handoff(self, state: ConversationState, handoff: dict[str, Any]) -> None:
        self.queue.append(QueueItem(state.conversation_id, self.clock(), handoff))

    def handoff(self, handoff_id: str) -> QueueItem | None:
        return next((i for i in self.queue if i.handoff["handoff_id"] == handoff_id), None)

    # ---- audit index and translation cache ----------------------------------------------------------------
    def conversations(self) -> list[ConversationState]:
        """Every conversation of this process, newest first, for the audit index."""
        return list(reversed(self.orchestrator.store.all()))  # the store keeps creation order

    def remember_translation(self, key: tuple[str, str, str], value: dict[str, Any]) -> None:
        """Keep a translation so the same message never costs twice; the oldest entry goes past the bound."""
        self.translations[key] = value
        while len(self.translations) > TRANSLATION_CACHE_MAX:
            self.translations.popitem(last=False)

    # ---- demo helpers -------------------------------------------------------------------------------------
    def start_login(self, document_number: str):
        """Start a login; in demo mode remember the code the mock channel delivered, keyed by challenge.

        Only the seeded demo identities get a readable code. Any other customer in the warehouse still receives a
        challenge, whose code stays in the mock channel as it would stay on the customer's phone, so a document
        number alone never logs anyone in.
        """
        before = len(self.stack.channel.outbox)
        challenge = self.service.start_login(document_number)
        delivered = len(self.stack.channel.outbox) > before
        customer_id = self.stack.channel.outbox[-1][0] if delivered else None
        if self.settings.demo_mode and customer_id in self.demo_customers:
            code = self.stack.channel.last_code_for(customer_id)
            if code:
                self.demo_codes[challenge.challenge_id] = (code, challenge.expires_at)
        now = self.clock()
        self.demo_codes = {k: v for k, v in self.demo_codes.items() if v[1] > now}
        return challenge

    def demo_code(self, challenge_id: str) -> str | None:
        item = self.demo_codes.get(challenge_id)
        return item[0] if item and item[1] > self.clock() else None

    def _demo_console_key(self) -> str:
        key = secrets.token_urlsafe(24)
        path = ROOT / "data" / "demo" / "console_key.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(key + "\n", encoding="utf-8")
            log.warning("CAUTELA_CONSOLE_KEY not set: a demo console key was written to data/demo/console_key.txt")
        except OSError:
            log.warning("CAUTELA_CONSOLE_KEY not set and the demo key file could not be written")
        return key

    def _load_identities(self) -> list[dict[str, Any]]:
        path = Path(self.settings.seed_file)
        if not path.is_file():
            log.warning("demo seed file not found; run `uv run python -m api.seed`")
            return []
        seed = json.loads(path.read_text(encoding="utf-8"))
        entries = [(i, self.stack.repo.lookup_by_document(i["document_number"])) for i in seed.get("identities", [])]
        usable = [i for i, entry in entries if entry is not None]
        self.demo_customers = {entry.customer_id for _, entry in entries if entry is not None}
        if len(usable) < len(seed.get("identities", [])):
            log.warning("some demo identities are not in this warehouse; regenerate the seed for it")
        return usable
