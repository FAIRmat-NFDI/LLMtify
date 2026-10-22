#!/usr/bin/env bash
# hackframe launcher
#   ./run.sh          setup + services + DevUI   (default: all)
#   ./run.sh setup    venv + install + .env
#   ./run.sh up       docker services (LiteLLM, Postgres, Aspire) + wait until ready
#   ./run.sh devui    start DevUI only
#   ./run.sh down     stop docker services
set -euo pipefail
cd "$(dirname "$0")"

VENV=.venv
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
  devui) devui ;;
  down)  down ;;
  all)   setup; up; devui ;;
  *)     die "usage: ./run.sh [all|setup|up|devui|down]" ;;
esac