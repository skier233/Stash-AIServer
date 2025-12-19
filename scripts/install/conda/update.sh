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

DEFAULT_ENV_NAME="stash-ai-server"

find_env_file() {
  local explicit="$1"

  if [[ -n "$explicit" ]]; then
    if [[ -f "$explicit" ]]; then
      printf '%s\n' "$explicit"
      return 0
    fi
    echo "Environment file not found: $explicit" >&2
    exit 1
  fi

  local root_candidate="$ROOT_DIR/environment.yml"
  local backend_candidate="$ROOT_DIR/backend/environment.yml"

  if [[ -f "$root_candidate" ]]; then
    printf '%s\n' "$root_candidate"
    return 0
  fi
  if [[ -f "$backend_candidate" ]]; then
    printf '%s\n' "$backend_candidate"
    return 0
  fi

  echo "Environment file not found. Pass --file <path> to specify one." >&2
  exit 1
}

usage() {
  cat <<'EOF'
Update an existing Stash AI Server Conda environment to the latest release.

Usage: bash scripts/install/conda/update.sh [options]

Options:
  -n, --name <env>       Conda environment name (default: stash-ai-server)
  -f, --file <path>      Path to environment.yml for dependency refresh
  -h, --help             Show this help message
EOF
}

ENV_NAME="$DEFAULT_ENV_NAME"
ENV_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--name)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      ENV_NAME="$2"
      shift 2
      ;;
    -f|--file)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      ENV_FILE="$(cd -- "$(dirname "$2")" && pwd)/$(basename "$2")"
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

if ! command -v conda >/dev/null 2>&1; then
  echo "conda is required but was not found in PATH." >&2
  exit 1
fi

ENV_FILE="$(find_env_file "$ENV_FILE")"

if ! conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME"; then
  echo "Environment '$ENV_NAME' does not exist. Run install.sh first." >&2
  exit 1
fi

echo "Refreshing environment '$ENV_NAME' with $ENV_FILE"
conda env update --name "$ENV_NAME" --file "$ENV_FILE" --prune

echo "Restart the server with scripts/conda/start.sh --name $ENV_NAME (scripts/install/conda/start.sh in source)."
