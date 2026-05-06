#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-https://github.com/Player93-13/traffic-collector.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/traffic-collector}"
HEALTH_URL="${HEALTH_URL:-http://localhost:9229/health}"

MODE="install"

for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    --upgrade) MODE="upgrade" ;;
    --uninstall) MODE="uninstall" ;;
  esac
done

log() {
  printf '%s %s\n' "$(date -Iseconds)" "$*"
}

run() {
  if [ "$MODE" = "dry-run" ]; then
    echo "[DRY-RUN] $*"
  else
    "$@"
  fi
}

require() {
  command -v "$1" >/dev/null 2>&1 || {
    log "ERROR: required command not found: $1"
    exit 1
  }
}

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

# ---------------- UNINSTALL ----------------
if [ "$MODE" = "uninstall" ]; then
  log "Uninstalling..."

  if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR"
    if docker compose version >/dev/null 2>&1; then
      run $SUDO docker compose down -v || true
    elif command -v docker-compose >/dev/null 2>&1; then
      run $SUDO docker-compose down -v || true
    fi
  fi

  run $SUDO rm -rf "$APP_DIR"

  log "Uninstalled"
  exit 0
fi

# ---------------- REQUIREMENTS ----------------
require git
require curl

# ---------------- DOCKER ----------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker..."
  run curl -fsSL https://get.docker.com -o get-docker.sh
  run $SUDO sh get-docker.sh
fi

run $SUDO systemctl enable --now docker || true

if [ -n "$SUDO" ]; then
  run $SUDO usermod -aG docker "$USER" || true
  log "NOTE: re-login required for docker group changes"
fi

# ---------------- COMPOSE DETECTION ----------------
COMPOSE=""
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  log "ERROR: docker compose not found"
  exit 1
fi

# ---------------- REPO ----------------
if [ -d "$APP_DIR/.git" ]; then
  log "Updating repo..."
  run git -C "$APP_DIR" fetch --all --prune
  run git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  log "Cloning repo..."
  run $SUDO mkdir -p "$APP_DIR"
  run $SUDO chown -R "$(id -u):$(id -g)" "$APP_DIR"
  run git clone --depth 1 --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

cd "$APP_DIR"

# ---------------- ENV ----------------
if [ "$MODE" = "install" ]; then
  if [ ! -f .env ] && [ -f .env.example ]; then
    log "Creating .env..."

    cp .env.example .env

    if command -v openssl >/dev/null 2>&1; then
      DB_PASS_GEN=$(openssl rand -hex 16)
    else
      DB_PASS_GEN=$(tr -dc A-Za-z0-9 </dev/urandom | head -c 32)
    fi

    # safe replace (no sed injection risk)
    awk -v pass="$DB_PASS_GEN" '
      /^DB_PASS=/ {$0="DB_PASS="pass}
      {print}
    ' .env > .env.tmp && mv .env.tmp .env

    log "DB password generated"
  fi
fi

# ---------------- START ----------------
log "Starting services..."

run $COMPOSE pull
run $COMPOSE up -d

# ---------------- VERIFY ----------------
log "Verifying startup..."

for i in $(seq 1 15); do
  if curl -fs "$HEALTH_URL" >/dev/null 2>&1; then
    log "Collector is healthy"
    break
  fi
  sleep 2
done

# ---------------- STATUS ----------------
run $COMPOSE ps

log "Done ($MODE)"