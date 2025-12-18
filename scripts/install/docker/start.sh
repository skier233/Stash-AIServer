#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
SERVICE="backend_prod"
FOLLOW_LOGS=false
DETACH=true

usage() {
  cat <<'EOF'
Start the Docker-based Stash AI Server deployment.

Usage: bash scripts/install/docker/start.sh [options]

Options:
  -f, --compose-file <path>   Use a custom docker-compose.yml
  -s, --service <name>        Service name to start (default: backend_prod)
  -l, --logs                  Follow logs after the service starts
      --no-detach             Run without -d (foreground)
  -h, --help                  Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--compose-file)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      COMPOSE_FILE="$(cd -- "$(dirname "$2")" && pwd)/$(basename "$2")"
      shift 2
      ;;
    -s|--service)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      SERVICE="$2"
      shift 2
      ;;
    -l|--logs)
      FOLLOW_LOGS=true
      shift
      ;;
    --no-detach)
      DETACH=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "docker-compose file not found at $COMPOSE_FILE" >&2
  exit 1
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_BIN=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN=(docker-compose)
else
  echo "docker compose (or docker-compose) is required." >&2
  exit 1
fi

compose() {
  "${COMPOSE_BIN[@]}" -f "$COMPOSE_FILE" "$@"
}

UP_ARGS=(up)
if [[ "$DETACH" == true ]]; then
  UP_ARGS+=(-d)
fi
if [[ -n "$SERVICE" ]]; then
  UP_ARGS+=("$SERVICE")
fi

echo "Starting $SERVICE via docker compose"
compose "${UP_ARGS[@]}"

if [[ "$FOLLOW_LOGS" == true ]]; then
  echo
  echo "Tailing logs (Ctrl+C to stop)"
  compose logs -f "$SERVICE"
fi
