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

CONFIG_PATH="${CONFIG_OVERRIDE:-$ROOT_DIR/config.env}"
if [[ -f "$CONFIG_PATH" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_PATH"
  set +a
fi

PG_DATA_DIR="${AI_SERVER_PG_DATA_DIR:-$ROOT_DIR/data/postgres}"
PG_USER="${AI_SERVER_DB_USER:-stash_ai_server}"
PG_PASSWORD="${AI_SERVER_DB_PASSWORD:-stash_ai_server}"
PG_DB="${AI_SERVER_DB_NAME:-stash_ai_server}"
PG_PORT="${AI_SERVER_DB_PORT:-5544}"
PG_LOG_FILE="$PG_DATA_DIR/postgres.log"
POSTGRES_SERVICE_SCRIPT="$SCRIPT_DIR/postgres_service.py"
if [[ ! -f "$POSTGRES_SERVICE_SCRIPT" && -f "$SCRIPT_DIR/conda/postgres_service.py" ]]; then
  POSTGRES_SERVICE_SCRIPT="$SCRIPT_DIR/conda/postgres_service.py"
fi

if [[ ! -f "$POSTGRES_SERVICE_SCRIPT" ]]; then
  echo "postgres_service.py not found at $POSTGRES_SERVICE_SCRIPT" >&2
  exit 1
fi

pg_service() {
  "${CONDA_CMD[@]}" run --no-capture-output -n "$ENV_NAME" python "$POSTGRES_SERVICE_SCRIPT" "$@"
}

start_postgres() {
  pg_service init --data-dir "$PG_DATA_DIR" --user "$PG_USER" --password "$PG_PASSWORD" --port "$PG_PORT" --log-file "$PG_LOG_FILE"
  pg_service start --data-dir "$PG_DATA_DIR" --port "$PG_PORT" --log-file "$PG_LOG_FILE"
  pg_service ensure-db --data-dir "$PG_DATA_DIR" --user "$PG_USER" --password "$PG_PASSWORD" --database "$PG_DB" --port "$PG_PORT"
}

stop_postgres() {
  if [[ -f "$PG_DATA_DIR/postmaster.pid" ]]; then
    pg_service stop --data-dir "$PG_DATA_DIR" --port "$PG_PORT" --log-file "$PG_LOG_FILE" || true
  fi
}

trap stop_postgres EXIT INT TERM
start_postgres

CMD=("${CONDA_CMD[@]}" run --no-capture-output --cwd "$ROOT_DIR" -n "$ENV_NAME" python -m stash_ai_server.entrypoint)
if [[ ${#ENTRYPOINT_ARGS[@]} -gt 0 ]]; then
  CMD+=("${ENTRYPOINT_ARGS[@]}")
fi

"${CMD[@]}"
