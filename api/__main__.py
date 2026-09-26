"""Run the API: `uv run python -m api`. Binds to 127.0.0.1 unless CAUTELA_API_HOST says otherwise."""

from __future__ import annotations

import logging

import uvicorn

from api.app import create_app
from api.settings import ApiSettings
from data_engineering.pipelines.env import load_env_file

log = logging.getLogger("cautela.api")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_env_file(".env")  # variables already in the environment win; values are never printed
    settings = ApiSettings.from_env()
    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        log.warning("binding to a non-loopback address: put a TLS reverse proxy and a firewall in front")
    uvicorn.run(create_app(settings=settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
