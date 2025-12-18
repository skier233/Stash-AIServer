#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
DEFAULT_ENV_NAME="stash-ai-server"
CONFIG_OVERRIDE=""

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

ENV_NAME="$DEFAULT_ENV_NAME"
ENTRYPOINT_ARGS=()

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

if [[ -n "${CONFIG_OVERRIDE:-}" ]]; then
  export AI_SERVER_CONFIG_FILE="$CONFIG_OVERRIDE"
fi

if [[ ${#ENTRYPOINT_ARGS[@]} -gt 0 ]]; then
  conda run --no-capture-output --cwd "$ROOT_DIR" -n "$ENV_NAME" python -m stash_ai_server.entrypoint "${ENTRYPOINT_ARGS[@]}"
else
  conda run --no-capture-output --cwd "$ROOT_DIR" -n "$ENV_NAME" python -m stash_ai_server.entrypoint
fi
