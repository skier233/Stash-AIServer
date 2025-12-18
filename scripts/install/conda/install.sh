#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
DEFAULT_ENV_FILE="$ROOT_DIR/environment.yml"
DEFAULT_ENV_NAME="stash-ai-server"
PRUNE_ENV=1

usage() {
  cat <<'EOF'
Install or refresh the Stash AI Server Conda environment.

Usage: bash scripts/install/conda/install.sh [options]

Options:
  -n, --name <env>       Conda environment name (default: stash-ai-server)
  -f, --file <path>      Path to environment.yml (default: repo root environment.yml)
      --no-prune         Skip --prune when updating an existing environment
  -h, --help             Show this help message
EOF
}

ENV_NAME="$DEFAULT_ENV_NAME"
ENV_FILE="$DEFAULT_ENV_FILE"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--name)
      ENV_NAME="$2"
      shift 2
      ;;
    -f|--file)
      ENV_FILE="$(cd -- "$(dirname "$2")" && pwd)/$(basename "$2")"
      shift 2
      ;;
    --no-prune)
      PRUNE_ENV=0
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

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file not found: $ENV_FILE" >&2
  exit 1
fi

if ! command -v conda >/dev/null 2>&1; then
  echo "conda is required but was not found in PATH." >&2
  exit 1
fi

if conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME"; then
  echo "Updating existing environment '$ENV_NAME' with $ENV_FILE"
  UPDATE_CMD=(conda env update --name "$ENV_NAME" --file "$ENV_FILE")
  if [[ $PRUNE_ENV -ne 0 ]]; then
    UPDATE_CMD+=(--prune)
  fi
  "${UPDATE_CMD[@]}"
else
  echo "Creating environment '$ENV_NAME' from $ENV_FILE"
  conda env create --name "$ENV_NAME" --file "$ENV_FILE"
fi

echo "Installing latest stash-ai-server package from PyPI"
conda run -n "$ENV_NAME" python -m pip install --upgrade --no-cache-dir stash-ai-server

echo
echo "Environment '$ENV_NAME' is ready."
echo "Use scripts/install/conda/start.sh --name $ENV_NAME to launch the server."
