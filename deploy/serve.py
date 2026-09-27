"""Container entry point for the public demo. Used only by deploy/Dockerfile; `python -m api` stays the local way.

What it adds around api.app.create_app, without changing api/:

  - Demo reset. In demo mode the process ends itself every CAUTELA_DEMO_RESET_S seconds (default 1800) and Docker's
    restart policy starts a fresh one. Sessions, conversations, the handoff queue, the case store (:memory:) and the
    demo outbox live in process memory, so a new process is the reset; on start the audit directory is emptied, so
    each hash chain starts at genesis again. The LLM budget file is kept: a reset must not refill the daily cap.
  - Daily LLM cap (agent/llm/budget.py): the model's adapter is wrapped in BudgetedAdapter, and GET /health gains
    `llm_budget`: whether the model or the deterministic fallback is answering, today's counters, when they reset.
    TODO(agent, api): wrap the adapter in agent/orchestrator/llm_setup.py and add `llm_budget` to
    api.models.HealthResponse, then drop the wrapping below and HealthWithBudget. Those files had uncommitted work by
    another author when this was written, so the wiring stays here and only the deployed process is capped.
  - Client addresses from X-Forwarded-For, trusted only from CAUTELA_TRUSTED_PROXY (the Docker gateway the reverse
    proxy's connection arrives from), so the per-address rate limits see whoever reached the reverse proxy. Requests
    that come through the Vercel frontend reach it from Vercel's addresses; for those the frontend names the visitor
    in X-Cautela-Client, which api/app.py trusts only with CAUTELA_PROXY_KEY in X-Cautela-Proxy-Key.
  - Demo mode is explicit: CAUTELA_DEMO_MODE comes from the environment (docker-compose.behind-proxy.yml sets 1
    unless deploy/.env says otherwise). ApiSettings keeps it off by default everywhere else.
  - Demo bundle (deploy/bundle.py). When CAUTELA_BUNDLE_DIR is set, the bundle is checked against its lock
    (CAUTELA_BUNDLE_LOCK, default deploy/demo-bundle.lock.json) before anything else: exact file set, sizes and
    sha256, the pickles included, so no unverified pickle is ever loaded. The service then reads the bundle's slim
    warehouse and seed, starts its clock at the seed's `as_of` (the charges sit inside their claim windows only
    then), and loads the learned disposition model from the bundle. If any of that fails the process exits instead
    of serving: the demo never falls back to the rule baseline. GET /health gains `demo_bundle`. Without
    CAUTELA_BUNDLE_DIR nothing changes: the configured warehouse (the synthetic fixture) and load_default().
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

from agent.llm.budget import budget_status, with_budget
from agent.orchestrator.disposition import LEARNED_SYSTEM, LearnedDisposition
from api.app import create_app
from api.runtime import Runtime
from api.settings import ROOT, ApiSettings
from deploy.lock import SEED, WAREHOUSE, BundleError, sha256, verify

DEFAULT_LOCK = ROOT / "deploy" / "demo-bundle.lock.json"

log = logging.getLogger("cautela.deploy")


class HealthWithBudget:
    """ASGI wrapper that adds `llm_budget` and `demo_bundle` to the JSON body of GET /health and leaves every other
    request alone."""

    def __init__(self, app, bundle: dict[str, Any] | None = None) -> None:
        self.app = app
        self.bundle = bundle

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] != "/health" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return
        start: dict = {}
        chunks: list[bytes] = []

        async def capture(message) -> None:
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, capture)
        body = b"".join(chunks)
        if start.get("status") == 200:
            runtime = getattr(self.app.state, "runtime", None)
            payload = json.loads(body)
            payload["llm_budget"] = budget_status(runtime.llm_choice.llm if runtime else None)
            payload["demo_bundle"] = self.bundle
            body = json.dumps(payload).encode()
        headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode()))
        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def clear_audit(directory: Path | None) -> None:
    if directory is None or not directory.is_dir():
        return
    removed = 0
    for path in directory.glob("*.jsonl"):
        path.unlink()
        removed += 1
    log.info("demo reset: %d audit file(s) removed", removed)


def load_bundle(settings: ApiSettings, environ: Mapping[str, str]
                ) -> tuple[ApiSettings, LearnedDisposition | None, dict[str, Any] | None]:
    """Settings, disposition model and /health summary for the demo bundle, or the inputs unchanged without one.
    Raises BundleError when the bundle does not match its lock or the learned model does not load."""
    directory = (environ.get("CAUTELA_BUNDLE_DIR") or "").strip()
    if not directory:
        return settings, None, None
    bundle = Path(directory)
    lock_path = Path((environ.get("CAUTELA_BUNDLE_LOCK") or "").strip() or DEFAULT_LOCK)
    locked = verify(bundle, lock_path)  # before any pickle is opened
    try:
        disposition = LearnedDisposition.load(bundle / "models")
    except Exception as exc:  # noqa: BLE001 - any failure means no learned model, and then no service
        raise BundleError(f"the learned disposition model did not load: {type(exc).__name__}") from exc
    if not disposition.name.startswith(LEARNED_SYSTEM) or disposition.name != locked["disposition_model"]:
        raise BundleError(f"loaded {disposition.name}, the lock names {locked['disposition_model']}")
    as_of = datetime.fromisoformat(locked["as_of"])
    as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    if settings.as_of is not None and settings.as_of != as_of:
        log.warning("CAUTELA_AS_OF is ignored: the demo bundle fixes the clock at %s", as_of.isoformat())
    settings = replace(settings, warehouse=bundle / WAREHOUSE, seed_file=bundle / SEED, as_of=as_of)
    summary = {"verified": True, "lock_sha256": sha256(lock_path), "as_of": as_of.isoformat(),
               "files": len(locked["files"]), "scenarios": len(locked["scenarios"])}
    log.info("demo bundle verified: %s, clock %s", disposition.name, as_of.isoformat())
    return settings, disposition, summary


def build_app(settings: ApiSettings, environ: dict[str, str] | None = None) -> HealthWithBudget:
    settings, disposition, bundle = load_bundle(settings, os.environ if environ is None else environ)
    runtime = Runtime(settings, environ=environ, disposition=disposition)
    llm = runtime.llm_choice.llm
    if llm is not None:
        llm.adapter = with_budget(llm.adapter, environ)
    return HealthWithBudget(create_app(runtime=runtime), bundle)


def check_runtime_files() -> None:
    """Fail at startup, not at the first handoff: every handoff is validated against this schema."""
    from agent.handoff import _validator

    _validator()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ApiSettings.from_env()
    reset_s = int(os.environ.get("CAUTELA_DEMO_RESET_S") or 1800)
    if settings.demo_mode:
        clear_audit(settings.audit_dir)
    try:
        check_runtime_files()
        app = build_app(settings)
    except (BundleError, OSError, ValueError) as exc:
        log.error("refusing to start: %s", exc)
        raise SystemExit(1) from exc
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info",
                            proxy_headers=True, server_header=False, timeout_graceful_shutdown=10,
                            forwarded_allow_ips=os.environ.get("CAUTELA_TRUSTED_PROXY") or "127.0.0.1")
    server = uvicorn.Server(config)
    if settings.demo_mode and reset_s > 0:
        def end() -> None:
            log.info("demo reset: ending the process after %d s; the restart policy starts a fresh one", reset_s)
            server.should_exit = True

        timer = threading.Timer(reset_s, end)
        timer.daemon = True
        timer.start()
    server.run()


if __name__ == "__main__":
    main()
