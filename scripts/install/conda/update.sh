#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
DEFAULT_ENV_FILE="$ROOT_DIR/environment.yml"
DEFAULT_ENV_NAME="stash-ai-server"

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
ENV_FILE="$DEFAULT_ENV_FILE"
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

if ! conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME"; then
  echo "Environment '$ENV_NAME' does not exist. Run install.sh first." >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  echo "Refreshing dependencies from $ENV_FILE"
  conda env update --name "$ENV_NAME" --file "$ENV_FILE" --prune
fi

echo "Forcing pip to pull the newest stash-ai-server wheel"
conda run -n "$ENV_NAME" python -m pip install --upgrade --no-cache-dir stash-ai-server

echo "Restart the server with scripts/install/conda/start.sh --name $ENV_NAME"
