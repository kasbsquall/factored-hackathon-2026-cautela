#!/usr/bin/env sh
# Post-deploy audit for the Cautela API. Run it on the server from the release directory after `up -d`, and read
# the output rather than assuming. A deploy that has not been audited is not finished.
#
#   sh deploy/audit.sh [compose file] [public host name]
#   sh deploy/audit.sh deploy/docker-compose.behind-proxy.yml cautela.107-172-6-206.sslip.io
#
# Exit code = number of failed checks. It never prints a value from deploy/.env.
set -u
CF=${1:-deploy/docker-compose.behind-proxy.yml}
HOST_NAME=${2:-}
DIR=$(dirname "$CF")
PORT=8330
fallos=0
di() { printf '  %-6s %s\n' "$1" "$2"; }
fail() { di FAIL "$1"; fallos=$((fallos+1)); }

echo "== ports the internet can reach (this project) =="
puertos=$(docker compose -f "$CF" ps --format '{{.Service}} {{.Ports}}' 2>/dev/null)
echo "${puertos:-  (no containers running)}" | sed 's/^/  /'
if printf '%s\n' "$puertos" | grep -Eq '0\.0\.0\.0:|\[::\]:|:::'; then
  fail "a port of this project is published on every interface"
elif [ -z "$puertos" ]; then
  fail "no container of this project is running"
else
  di ok "published on 127.0.0.1 only"
fi
escucha=$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep -E ":$PORT\$" || true)
if printf '%s\n' "$escucha" | grep -Evq "^127\.0\.0\.1:$PORT\$|^$"; then
  fail "port $PORT is listening on a non-loopback address: $escucha"
else
  di ok "host socket for $PORT: ${escucha:-none}"
fi

echo
echo "== resource ceilings =="
for c in $(docker compose -f "$CF" ps -q 2>/dev/null); do
  nombre=$(docker inspect -f '{{.Name}}' "$c" | tr -d /)
  mem=$(docker inspect -f '{{.HostConfig.Memory}}' "$c")
  cpu=$(docker inspect -f '{{.HostConfig.NanoCpus}}' "$c")
  if [ "$mem" = "0" ] || [ "$cpu" = "0" ]; then
    fail "$nombre has no memory or cpu ceiling"
  else
    di ok "$nombre  mem=$((mem/1024/1024))m  cpus=$(awk "BEGIN{print $cpu/1000000000}")"
  fi
done

echo
echo "== container hardening =="
for c in $(docker compose -f "$CF" ps -q 2>/dev/null); do
  nombre=$(docker inspect -f '{{.Name}}' "$c" | tr -d /)
  ro=$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$c")
  caps=$(docker inspect -f '{{.HostConfig.CapDrop}}' "$c")
  sec=$(docker inspect -f '{{.HostConfig.SecurityOpt}}' "$c")
  usr=$(docker inspect -f '{{.Config.User}}' "$c")
  if [ "$ro" = "true" ] && echo "$caps" | grep -q ALL && echo "$sec" | grep -q no-new-privileges \
     && [ -n "$usr" ] && [ "${usr%%:*}" != "0" ] && [ "${usr%%:*}" != "root" ]; then
    di ok "$nombre  read-only root, cap_drop ALL, no-new-privileges, user $usr"
  else
    fail "$nombre  read_only=$ro cap_drop=$caps security_opt=$sec user=${usr:-root}"
  fi
done

echo
echo "== secrets =="
# The compose file and the Dockerfile must not hold a literal credential. (`docker compose config` is not used
# here: it merges deploy/.env into the output.)
if grep -Eiq '(api_key|secret|password|token|console_key)[A-Z_]*[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9_+/-]{8,}' \
     "$CF" "$DIR/Dockerfile" 2>/dev/null; then
  fail "a literal credential is in the compose file or the Dockerfile"
else
  di ok "no literal credentials in the compose file or the Dockerfile"
fi
if [ -f "$DIR/.env" ]; then
  modo=$(stat -L -c '%a' "$DIR/.env")
  if [ "$modo" = "600" ] || [ "$modo" = "400" ]; then di ok "$DIR/.env mode $modo"
  else fail "$DIR/.env mode is $modo; run chmod 600 $DIR/.env"; fi
  for nombre in SESSION_SECRET CAUTELA_CONSOLE_KEY; do
    largo=$(sed -n "s/^$nombre=//p" "$DIR/.env" | tr -d '\r\n' | wc -c)
    if [ "$largo" -ge 32 ]; then di ok "$nombre is set ($largo characters)"
    else fail "$nombre is missing or shorter than 32 characters"; fi
  done
else
  fail "$DIR/.env not found"
fi
envs=$(docker compose -f "$CF" exec -T api sh -c 'find /app -name ".env*" 2>/dev/null' 2>/dev/null | tr -d '\r')
if [ -n "$envs" ]; then fail "an .env file is inside the image: $envs"; else di ok "no .env file inside the image"; fi

echo
echo "== the service answers =="
salud=$(curl -fsS --max-time 5 "http://127.0.0.1:$PORT/health" 2>/dev/null || true)
if [ -n "$salud" ]; then
  printf '%s' "$salud" | python3 -c '
import json, sys
h = json.load(sys.stdin)
b = h.get("llm_budget", {})
print("  ok     /health on 127.0.0.1:8330")
print("         model: %s %s, disposition: %s, demo_mode: %s" % (h.get("llm_provider"), h.get("llm_model"),
      h.get("disposition_model"), h.get("demo_mode")))
print("         llm budget: mode=%s calls=%s/%s usd=%s/%s resets_at=%s" % (b.get("mode"), b.get("calls"),
      b.get("max_calls"), b.get("usd_estimated"), b.get("max_usd"), b.get("resets_at")))'
else
  fail "/health did not answer on 127.0.0.1:$PORT"
fi
if [ -n "$HOST_NAME" ]; then
  codigo=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$HOST_NAME/health")
  if [ "$codigo" = "200" ]; then di ok "https://$HOST_NAME/health answers 200 through the proxy"
  else fail "https://$HOST_NAME/health answered $codigo"; fi
  acme=$(curl -s --max-time 10 "https://$HOST_NAME/.well-known/acme-challenge/cautela-audit-probe" || true)
  if printf '%s' "$acme" | grep -q '"error"'; then
    fail "/.well-known/ reaches the app; ACME renewal would be answered by the API"
  else
    di ok "/.well-known/ is served by the web server, not the app"
  fi
  hsts=$(curl -sI --max-time 10 "https://$HOST_NAME/health" | grep -ci '^strict-transport-security' || true)
  if [ "$hsts" -ge 1 ]; then di ok "HSTS header present"; else fail "no Strict-Transport-Security header"; fi
else
  di note "pass the public host name as the second argument to check the proxy, ACME path and headers"
fi

echo
echo "== host firewall =="
if command -v ufw >/dev/null 2>&1; then ufw status 2>/dev/null | sed 's/^/  /'
else di note "ufw not installed; check the provider's firewall by hand"; fi
di note "this is a shared host: mail and the panel keep their own ports; $PORT must not appear in any allow rule"

echo
if [ "$fallos" -eq 0 ]; then echo "audit clean."; else echo "$fallos problem(s). Fix before leaving this running."; fi
exit "$fallos"
