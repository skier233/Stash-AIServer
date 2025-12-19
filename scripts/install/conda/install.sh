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

CONDA_CMD=(conda)

if "${CONDA_CMD[@]}" env list | awk '{print $1}' | grep -Fxq "$ENV_NAME"; then
  echo "Updating existing environment '$ENV_NAME' with $ENV_FILE"
  UPDATE_CMD=("${CONDA_CMD[@]}" env update --name "$ENV_NAME" --file "$ENV_FILE")
  if [[ $PRUNE_ENV -ne 0 ]]; then
    UPDATE_CMD+=(--prune)
  fi
  "${UPDATE_CMD[@]}"
else
  echo "Creating environment '$ENV_NAME' from $ENV_FILE"
  "${CONDA_CMD[@]}" env create --name "$ENV_NAME" --file "$ENV_FILE"
fi

echo
echo "Environment '$ENV_NAME' is ready."
echo "Use scripts/conda/start.sh --name $ENV_NAME to launch the server (scripts/install/conda/start.sh in source)."
