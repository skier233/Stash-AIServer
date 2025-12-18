#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_root_dir() {
  if [[ -n "${STASH_AI_ROOT:-}" ]]; then
    ROOT_DIR="$(cd -- "$STASH_AI_ROOT" && pwd)"
    return
  fi
  local candidate="$SCRIPT_DIR"
  while true; do
    if [[ -f "$candidate/docker-compose.yml" || -f "$candidate/config.env" || -f "$candidate/environment.yml" || -d "$candidate/backend" ]]; then
      ROOT_DIR="$candidate"
      return
    fi
    local parent="$(dirname "$candidate")"
    if [[ "$parent" == "$candidate" ]]; then
      break
    fi
    candidate="$parent"
  done
  echo "Unable to locate project root. Set STASH_AI_ROOT." >&2
  exit 1
}

resolve_root_dir

COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
SERVICE="backend_prod"
FOLLOW_LOGS=false
DETACH=true

usage() {
  cat <<'EOF'
Pull the latest image and restart the Docker-based Stash AI Server deployment.

Usage: bash scripts/install/docker/update.sh [options]

Options:
  -f, --compose-file <path>   Use a custom docker-compose.yml
  -s, --service <name>        Service name to update (default: backend_prod)
  -l, --logs                  Follow logs after the service restarts
      --no-detach             Run docker compose up without -d
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

echo "Pulling latest image for $SERVICE"
compose pull "$SERVICE"

UP_ARGS=(up)
if [[ "$DETACH" == true ]]; then
  UP_ARGS+=(-d)
fi
if [[ -n "$SERVICE" ]]; then
  UP_ARGS+=("$SERVICE")
fi

echo "Restarting $SERVICE"
compose "${UP_ARGS[@]}"

if [[ "$FOLLOW_LOGS" == true ]]; then
  echo
  echo "Tailing logs (Ctrl+C to stop)"
  compose logs -f "$SERVICE"
fi
