# Deploying the Cautela API behind OpenLiteSpeed

Runbook for the public hackathon demo of the backend on the shared VPS (Ubuntu 22.04, Docker with Compose,
CyberPanel and OpenLiteSpeed owning 80 and 443 next to mail and client sites). The API runs in one container
published on `127.0.0.1:8330`; OpenLiteSpeed terminates TLS for `cautela.107-172-6-206.sslip.io` and proxies to it.
Nothing of this project listens on a public interface, and no firewall rule is opened for it.

| File | Purpose |
|---|---|
| `Dockerfile` | Python 3.12 slim image: runtime dependencies from `uv.lock` (mlflow and the dev group left out), `agent/`, `api/`, `ml/`, and the demo warehouse built during the image build. Runs as uid 10001 |
| `Dockerfile.dockerignore` | Build-context allowlist, so `.env`, `data/`, `.venv/` and `app/` never reach the builder |
| `docker-compose.behind-proxy.yml` | Loopback port, read-only root, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`, 640 MB and 0.75 CPU, no swap, healthcheck, `restart: unless-stopped`, one named volume |
| `serve.py` | Container entry point: daily LLM cap, `llm_budget` in `/health`, 30-minute demo reset, client address from the proxy |
| `.env.example` | Variable names for `deploy/.env` (git-ignored, never in the image or the tarball) |
| `audit.sh` | Post-deploy audit: exposure, ceilings, hardening, secrets, `/health`, proxy and ACME path |
| `vhost.conf.example` | OpenLiteSpeed virtual host: reverse proxy, `/.well-known/` from disk, security headers, TLS placeholders |

## What is live, what is off

**Live**

- The FastAPI service (`api/`) over the orchestrator, in demo mode: the five synthetic demo logins, the mock
  one-time-code outbox, conversations, confirmation, read-back, handoff queue, and `/console/*` behind
  `X-Console-Key`. `/docs` is public.
- Data: the synthetic fixture (team-generated, seed 42, no organizer data), taken through bronze, silver and gold
  inside `docker build`, so gold is never older than silver and the tool repository's freshness check passes.
- The model: `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-6-luna`, `LLM_REASONING_EFFORT=none`, under a daily cap of
  2000 calls and USD 1.00 (estimated) by default. When the cap is reached, the deterministic parser and the reply
  templates answer every turn until 00:00 UTC, and `GET /health` says so in `llm_budget.mode`
  (`llm` or `deterministic_fallback`) with today's counters and `resets_at`.
- Demo reset every 30 minutes (see known limits).

**Off**

- The learned disposition model. Its artifacts are trained on organizer-derived cases and stay out of the image;
  the service uses the labeled rule baseline, and `/health` reports `disposition_model: rules_fixed_baseline`.
- Organizer data of any kind, the S3 pipeline, MLflow, the evaluation harness.
- The frontend (`app/`). This runbook deploys the API only. The Next.js live mode reaches the API through its own
  server-side proxy (`CAUTELA_API_URL`), so `CAUTELA_CORS_ORIGINS` matters only for browsers calling the API
  directly.
- Any real banking system: the case store is the in-memory sandbox.

## Known limits

- **The reset is a restart, and it is not atomic.** In demo mode `serve.py` ends the process every
  `CAUTELA_DEMO_RESET_S` seconds (1800) and Docker starts a fresh one. Sessions, conversations, the handoff queue,
  the sandbox case store, rate-limit counters and the demo outbox live in process memory, so the new process starts
  clean, and the audit JSONL files are deleted on start. Requests in flight get up to 10 s to finish. Measured
  locally, the gap between shutdown and the next start was about 5 s; during it the proxy answers with an error.
  A visitor in the middle of a conversation loses it and has to log in again. The LLM budget file is kept.
- **There is no helper container for the reset.** Restarting another container from a helper needs the Docker
  socket, which is root on a host that also runs mail and client sites. The self-restart needs nothing.
- **The USD cap is an estimate**, from `agent/llm/prices.yaml` (gpt-6-luna at USD 0.10 and 0.50 per million input
  and output tokens, read 2026-09-25) and the token counts OpenAI returns. It is not the invoice. The last call of a
  day can overshoot the cap by one call's cost. A failed call counts as a call with unknown cost. Set a hard monthly
  limit on the OpenAI project as well; that is the only limit the provider enforces.
- **Wiring lives in `deploy/`.** The cap and `llm_budget` are added by `serve.py`, because `api/app.py`,
  `api/models.py` and the orchestrator had uncommitted work by another author. Only the deployed process is capped;
  `python -m api` and `python -m agent.demo` are not. TODO in `serve.py`: move both into `llm_setup.py` and
  `HealthResponse`.
- **Rate limits depend on X-Forwarded-For.** The API trusts that header only from the Docker gateway
  (`10.83.30.1`), which is where OpenLiteSpeed's connections arrive. If OpenLiteSpeed does not send it (check the
  logs, step 5), every visitor shares one bucket: 10 logins and 30 turns per minute in total.
- Demo mode publishes the demo logins and their one-time codes, so anyone can log in as the synthetic customers.
  That is the point of the demo; none of them is a real person.
- The console key is one shared secret, and the console endpoints are reachable from the internet (key-gated).
- One process, one lock: turns are serialized. Memory after startup and one conversation was about 130 MB
  locally; the 640 MB ceiling kills and restarts the container if it is reached, rather than letting it swap.
- The audit trail is emptied at every reset. It is demo evidence, not the retention the code describes.
- Image: about 216 MB compressed, 920 MB on disk (scipy, scikit-learn, pyarrow and duckdb are most of it). The
  build took about 3 minutes on a laptop; its memory use on the server was not measured.
- Not verified from here, because nothing connected to the server: the OpenLiteSpeed header syntax, whether it
  sends X-Forwarded-For, and the exact paths CyberPanel writes. Steps 4 and 5 check each one.

## 1. Build the release tarball (on the laptop)

From a clean, committed state. The tarball holds `HEAD` only, so uncommitted work is not in it. `core.autocrlf=false`
keeps LF endings on Windows, which `audit.sh` needs.

```bash
SHA=$(git rev-parse --short HEAD)
git -c core.autocrlf=false archive --format=tar.gz --prefix="cautela-$SHA/" -o "cautela-$SHA.tar.gz" \
  HEAD pyproject.toml uv.lock agent api ml data_engineering deploy
tar -tzf "cautela-$SHA.tar.gz" | grep -E '(^|/)\.env$|/data/|\.duckdb$|\.pkl$' || echo "tarball clean"
scp "cautela-$SHA.tar.gz" <ssh-user>@107.172.6.206:/tmp/
```

## 2. Prepare the server (first time only)

```bash
ss -tlnp | grep ':8330 ' || echo "8330 free"
ip route | grep '10\.83\.30\.' || echo "10.83.30.0/24 free"     # else set CAUTELA_SUBNET and CAUTELA_PROXY_GATEWAY
free -m                                                          # look before building; see step 3 for the alternative
sudo mkdir -p /opt/cautela/releases /opt/cautela/shared
```

Create the environment file once, outside any release, and fill it with an editor on the server:

```bash
SHA=<sha>
sudo tar -xzf /tmp/cautela-$SHA.tar.gz -C /opt/cautela/releases
sudo install -m 600 /opt/cautela/releases/cautela-$SHA/deploy/.env.example /opt/cautela/shared/cautela.env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # once for SESSION_SECRET, once for CAUTELA_CONSOLE_KEY
sudo nano /opt/cautela/shared/cautela.env
```

Values: `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-6-luna`, `LLM_REASONING_EFFORT=none`, the OpenAI project key,
the two generated secrets, `CAUTELA_CORS_ORIGINS` (the frontend's https origin, or leave empty), `CAUTELA_DEMO_MODE=1`.
Leave the caps empty for 2000 calls and USD 1.00, or set smaller ones.

## 3. Deploy a release

```bash
SHA=<sha>
[ -d /opt/cautela/releases/cautela-$SHA ] || sudo tar -xzf /tmp/cautela-$SHA.tar.gz -C /opt/cautela/releases
cd /opt/cautela/releases/cautela-$SHA
sudo ln -sfn /opt/cautela/shared/cautela.env deploy/.env
sudo docker compose -f deploy/docker-compose.behind-proxy.yml config --quiet      # never without --quiet
sudo CAUTELA_IMAGE_TAG=$SHA docker compose -f deploy/docker-compose.behind-proxy.yml build
sudo CAUTELA_IMAGE_TAG=$SHA docker compose -f deploy/docker-compose.behind-proxy.yml up -d
sudo ln -sfn /opt/cautela/releases/cautela-$SHA /opt/cautela/current
curl -s http://127.0.0.1:8330/health; echo
```

Drop `sudo` before `docker` if your user is in the `docker` group. If the server is short of memory for the
build, build on the laptop instead (only when both are x86_64: `uname -m` on the server) and ship the image:
`docker save cautela-api:$SHA | gzip > img.tar.gz`, copy it, then `gunzip -c img.tar.gz | sudo docker load` and run
`up -d --no-build`.

## 4. Point OpenLiteSpeed at it

1. In CyberPanel, create the website `cautela.107-172-6-206.sslip.io` (sslip.io resolves that name to the
   server's address, so no DNS record is needed) and issue its SSL certificate from the panel.
2. Back up the generated file and add the blocks marked `ADD` in `deploy/vhost.conf.example`:
   ```bash
   V=/usr/local/lsws/conf/vhosts/cautela.107-172-6-206.sslip.io/vhost.conf
   sudo cp "$V" "$V.bak-$(date +%F)"
   sudo nano "$V"
   sudo /usr/local/lsws/bin/lswsctrl restart
   ```
   Keep CyberPanel's own ACME context and `vhssl` paths. CyberPanel may rewrite this file when the site is changed
   in the panel; check it again after any panel change.
3. Check from outside:
   ```bash
   curl -sI https://cautela.107-172-6-206.sslip.io/health        # 200, Strict-Transport-Security present
   curl -s  https://cautela.107-172-6-206.sslip.io/.well-known/acme-challenge/probe | head -c 200   # web server 404, not JSON
   ```

## 5. Audit

```bash
cd /opt/cautela/current
sudo sh deploy/audit.sh deploy/docker-compose.behind-proxy.yml cautela.107-172-6-206.sslip.io
sudo docker logs --tail 5 cautela-api      # client addresses must be real visitors, not 10.83.30.1
```

The exit code is the number of failed checks. A deploy is finished when the audit is clean and the log shows
visitor addresses. If the log shows only `10.83.30.1`, OpenLiteSpeed is not sending X-Forwarded-For: the rate limits
are then shared by everyone (see known limits).

## Operating it

- Logs: `sudo docker logs --tail 100 cautela-api`. Two lines per reset (`demo reset: ...`) and a restart count that
  grows by about 48 a day are expected.
- Budget: `curl -s http://127.0.0.1:8330/health` shows `llm_budget`. To change the caps or rotate a key, edit
  `/opt/cautela/shared/cautela.env` and run `up -d` again from `/opt/cautela/current` with the same
  `CAUTELA_IMAGE_TAG`; the container is recreated with the new values.
- Stop it without removing anything: `sudo docker compose -f deploy/docker-compose.behind-proxy.yml stop`. The proxy
  then answers with an error for this name only; other sites are not affected.

## Rollback

The previous release directory and its image stay on the server until removed. The compose project name is fixed
(`cautela`), so the volume, and with it the budget counters, carries over.

```bash
PREV=<previous sha>
cd /opt/cautela/releases/cautela-$PREV
sudo CAUTELA_IMAGE_TAG=$PREV docker compose -f deploy/docker-compose.behind-proxy.yml up -d --no-build
sudo ln -sfn /opt/cautela/releases/cautela-$PREV /opt/cautela/current
sudo sh deploy/audit.sh deploy/docker-compose.behind-proxy.yml cautela.107-172-6-206.sslip.io
```

If the vhost edit is the problem: `sudo cp "$V.bak-<date>" "$V" && sudo /usr/local/lsws/bin/lswsctrl restart`.
Old images: `sudo docker image ls cautela-api` and `sudo docker image rm cautela-api:<sha>` for releases no longer
needed.

## Shutting it down after the hackathon

A demo left running is a liability. In order:

```bash
cd /opt/cautela/current
sudo docker compose -f deploy/docker-compose.behind-proxy.yml down       # container and network
sudo docker volume rm cautela_cautela_state                              # audit files and budget counters
sudo docker image rm $(sudo docker image ls cautela-api -q)
ss -tlnp | grep ':8330 ' || echo "8330 closed"
```

Then delete the website in CyberPanel (or remove the `extprocessor` and the proxy context from its vhost and
restart OpenLiteSpeed), revoke the OpenAI project key in the OpenAI console, and remove `/opt/cautela` once nothing
there is needed. The volume removal and the directory removal cannot be undone.
