#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
STASH_ROOT=""

usage() {
  cat <<'EOF'
Configure and pull the Docker-based Stash AI Server deployment.

Usage: bash scripts/install/docker/install.sh [options]

Options:
  -s, --stash-root <path>   Path to your Stash library root (required if the
                             docker-compose.yml still contains the placeholder)
  -f, --compose-file <path> Use a custom docker-compose.yml
  -h, --help                Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--stash-root)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      STASH_ROOT="$(cd -- "$2" && pwd)"
      shift 2
      ;;
    -f|--compose-file)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      COMPOSE_FILE="$(cd -- "$(dirname "$2")" && pwd)/$(basename "$2")"
      shift 2
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

# shellcheck disable=SC2068
compose() {
  "${COMPOSE_BIN[@]}" -f "$COMPOSE_FILE" "$@"
}

PLACEHOLDER="/path/to/your/stash_root_folder"
if grep -q "$PLACEHOLDER" "$COMPOSE_FILE"; then
  if [[ -z "$STASH_ROOT" ]]; then
    echo "The compose file still references the placeholder path. Re-run with --stash-root <path>." >&2
    exit 1
  fi
  ESCAPED_PATH=$(printf '%s
' "$STASH_ROOT" | sed 's/[&/\\]/\\&/g')
  if sed --version >/dev/null 2>&1; then
    sed -i "s|$PLACEHOLDER|$ESCAPED_PATH|" "$COMPOSE_FILE"
  else
    sed -i '' "s|$PLACEHOLDER|$ESCAPED_PATH|" "$COMPOSE_FILE"
  fi
  echo "Updated docker-compose.yml to mount $STASH_ROOT"
fi

mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/plugins"

echo "Pulling latest ghcr.io stash-ai-server image"
compose pull backend_prod

echo
cat <<EOF
Docker install ready.
Use scripts/install/docker/start.sh to launch the container.
EOF
