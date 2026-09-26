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
    proxy's connection arrives from), so the per-address rate limits see visitors instead of the proxy.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import uvicorn

from agent.llm.budget import budget_status, with_budget
from api.app import create_app
from api.runtime import Runtime
from api.settings import ApiSettings

log = logging.getLogger("cautela.deploy")


class HealthWithBudget:
    """ASGI wrapper that adds `llm_budget` to the JSON body of GET /health and leaves every other request alone."""

    def __init__(self, app) -> None:
        self.app = app

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


def build_app(settings: ApiSettings, environ: dict[str, str] | None = None) -> HealthWithBudget:
    runtime = Runtime(settings, environ=environ)
    llm = runtime.llm_choice.llm
    if llm is not None:
        llm.adapter = with_budget(llm.adapter, environ)
    return HealthWithBudget(create_app(runtime=runtime))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ApiSettings.from_env()
    reset_s = int(os.environ.get("CAUTELA_DEMO_RESET_S") or 1800)
    if settings.demo_mode:
        clear_audit(settings.audit_dir)
    config = uvicorn.Config(build_app(settings), host=settings.host, port=settings.port, log_level="info",
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
