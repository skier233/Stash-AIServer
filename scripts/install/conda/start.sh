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
CONFIG_OVERRIDE=""
ENV_NAME="$DEFAULT_ENV_NAME"
ENTRYPOINT_ARGS=()

if ! command -v conda >/dev/null 2>&1; then
  echo "conda is required but was not found in PATH." >&2
  exit 1
fi

CONDA_CMD=(conda --no-plugins)

usage() {
  cat <<'EOF'
Start the Stash AI Server from a Conda environment.

Usage: bash scripts/install/conda/start.sh [options] [-- backend args]

Options:
  -n, --name <env>       Conda environment name (default: stash-ai-server)
  -c, --config <path>    Override config.env path (sets AI_SERVER_CONFIG_FILE)
  -h, --help             Show this help message

All remaining arguments after "--" are passed to python -m stash_ai_server.entrypoint.
EOF
}

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
    -c|--config)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      CONFIG_OVERRIDE="$(cd -- "$(dirname "$2")" && pwd)/$(basename "$2")"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      ENTRYPOINT_ARGS=("$@")
      break
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -n "$CONFIG_OVERRIDE" ]]; then
  export AI_SERVER_CONFIG_FILE="$CONFIG_OVERRIDE"
fi

CMD=("${CONDA_CMD[@]}" run --no-capture-output --cwd "$ROOT_DIR" -n "$ENV_NAME" python -m stash_ai_server.entrypoint)
if [[ ${#ENTRYPOINT_ARGS[@]} -gt 0 ]]; then
  CMD+=("${ENTRYPOINT_ARGS[@]}")
fi

"${CMD[@]}"
