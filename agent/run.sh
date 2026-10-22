#!/usr/bin/env bash
# hackframe launcher
#   ./run.sh          setup + services + checks + DevUI   (default: all)
#   ./run.sh setup    venv + install + .env
#   ./run.sh up       docker services (LiteLLM, Postgres, Aspire) + wait until ready
#   ./run.sh check    config, PubChem tool, HU reachability, LLM probe
#   ./run.sh devui    start DevUI only
#   ./run.sh down     stop docker services
set -euo pipefail
cd "$(dirname "$0")"

VENV=.venv
HU_URL="https://llm3-compute.cms.hu-berlin.de/v1/models"
LITELLM_HEALTH="http://localhost:4000/health/liveliness"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✘ %s\033[0m\n' "$*"; exit 1; }

activate() {
  [ -d "$VENV" ] || die "no venv - run: ./run.sh setup"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
}

setup() {
  say "Python venv + install"
  command -v python3 >/dev/null || die "python3 not found"
  if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV" || die "venv failed - try: sudo apt install python3-venv"
  fi
  activate
  pip install -q -U pip
  pip install -q -e ".[dev]"
  ok "installed into $VENV"

  if [ ! -f .env ]; then
    cp .env.example .env
    warn ".env created from .env.example - add OPENAI_API_KEY if you use OpenAI models"
  else
    ok ".env exists"
  fi
}

up() {
  say "Docker services"
  command -v docker >/dev/null || die "docker not found"
  [ -f .env ] || die ".env missing - run: ./run.sh setup"
  docker compose up -d
  printf '  waiting for LiteLLM'
  for _ in $(seq 1 60); do
    if curl -sf -m 2 "$LITELLM_HEALTH" >/dev/null 2>&1; then
      echo; ok "LiteLLM ready  http://localhost:4000  (UI: /ui)"
      ok "Aspire         http://localhost:18888"
      return 0
    fi
    printf '.'; sleep 2
  done
  echo
  docker compose logs --tail 30 litellm || true
  die "LiteLLM not ready after 120 s (logs above)"
}

check() {
  activate
  say "Config"
  python -m hackframe --no-telemetry list >/dev/null && ok "config loads" || die "config error (see above)"

  say "PubChem tool (no LLM)"
  if python -m hackframe pubchem "CCO" | grep -q '"found": true'; then ok "PubChem reachable"
  else warn "PubChem lookup failed (network/proxy?) - agents still work without it"; fi

  say "HU Berlin LLM reachability"
  if curl -sf -m 8 "$HU_URL" >/dev/null 2>&1; then
    ok "host reaches HU (VPN/HU network ok)"
    if docker compose exec -T litellm python -c \
      "import urllib.request; urllib.request.urlopen('$HU_URL', timeout=8)" >/dev/null 2>&1; then
      ok "LiteLLM container reaches HU"
    else
      warn "LiteLLM container cannot reach HU -> use 'network_mode: host' for litellm or model: hu_direct"
    fi
  else
    warn "host cannot reach HU - connect VPN (HU models will fail)"
  fi

  say "LLM probe (workflow llm_check)"
  local out; out="$(mktemp -t hackframe_check.XXXX.json)"
  python -m hackframe --no-telemetry run llm_check "Say this is a test" >"$out" 2>&1 || true
  if grep -q '"summary": "check:ok"' "$out"; then
    ok "LLM answers"
    if grep -q '"structured_ok": true' "$out"; then ok "structured output works"
    else warn "structured output NOT working on this model"; fi
  else
    warn "LLM probe failed - see $out"
  fi
}

devui() {
  activate
  say "DevUI  http://localhost:${DEVUI_PORT:-8080}   (Ctrl+C to stop)"
  python -m hackframe devui
}

down() {
  say "Stopping docker services"
  docker compose down
  ok "stopped"
}

case "${1:-all}" in
  setup) setup ;;
  up)    up ;;
  check) check ;;
  devui) devui ;;
  down)  down ;;
  all)   setup; up; check; devui ;;
  *)     die "usage: ./run.sh [all|setup|up|check|devui|down]" ;;
esac
