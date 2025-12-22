#!/usr/bin/env python3
"""Utility helpers to manage the embedded Postgres instance for Conda installs."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class PgError(RuntimeError):
    pass


def run(cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=check, env=env)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - surfaced to user
        raise PgError(f"Command failed: {' '.join(cmd)}") from exc


def ensure_cluster(data_dir: Path, user: str, password: str, port: int, log_file: Path) -> None:
    if (data_dir / "PG_VERSION").exists():
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False) as tmp:
        tmp.write(password)
        pwfile = tmp.name
    try:
        run([
            "initdb",
            "-D",
            str(data_dir),
            "--username",
            user,
            "--pwfile",
            pwfile,
        ])
    finally:
        try:
            os.remove(pwfile)
        except OSError:
            pass

    # Update configs for localhost-only access and fixed port
    with (data_dir / "postgresql.conf").open("a", encoding="utf-8") as fh:
        fh.write("\n# Stash AI overrides\n")
        fh.write("listen_addresses = '127.0.0.1'\n")
        fh.write(f"port = {port}\n")
        fh.write("logging_collector = on\n")
        fh.write("log_filename = 'postgresql-%Y-%m-%d.log'\n")

    hba = data_dir / "pg_hba.conf"
    with hba.open("a", encoding="utf-8") as fh:
        fh.write("host all all 127.0.0.1/32 scram-sha-256\n")
    log_file.touch(exist_ok=True)


def pg_ctl(action: str, data_dir: Path, port: int, log_file: Path) -> None:
    args = [
        "pg_ctl",
        "-D",
        str(data_dir),
        "-o",
        f"-p {port} -h 127.0.0.1",
        "-l",
        str(log_file),
        action,
    ]
    if action == "stop":
        args.extend(["-m", "fast"])
    run(args)


def _maybe_clear_stale_pid(data_dir: Path) -> None:
    """Remove postmaster.pid if it points to a non-running process.

    Stale PID files cause pg_ctl start to no-op and pg_isready to hang until
    timeout. We only remove the PID file when the recorded PID is not alive.
    """

    pid_file = data_dir / "postmaster.pid"
    if not pid_file.exists():
        return

    try:
        pid_text = pid_file.read_text().splitlines()
        recorded_pid = int(pid_text[0]) if pid_text else None
    except (OSError, ValueError):
        recorded_pid = None

    if recorded_pid is None:
        pid_file.unlink(missing_ok=True)
        return

    try:
        # os.kill(pid, 0) checks liveness without terminating the process.
        os.kill(recorded_pid, 0)
        # Process exists; keep the PID file.
        return
    except OSError:
        # No such process; safe to clear the stale PID file.
        pid_file.unlink(missing_ok=True)


def wait_for_ready(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = subprocess.run(
            ["pg_isready", "-h", "127.0.0.1", "-p", str(port)],
            capture_output=True,
        )
        if proc.returncode == 0:
            return
        time.sleep(1)
    raise PgError("Postgres did not become ready in time")


def ensure_database(user: str, password: str, db_name: str, port: int) -> None:
    base_cmd = [
        "psql",
        "-h",
        "127.0.0.1",
        "-p",
        str(port),
        "-U",
        user,
        "-At",
    ]
    env = os.environ.copy()
    env["PGPASSWORD"] = password

    db_exists = subprocess.run(
        base_cmd + ["-d", "postgres", "-c", f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'"],
        capture_output=True,
        env=env,
    )
    if db_exists.returncode != 0:
        raise PgError(db_exists.stderr.decode() or "Failed to query database list")
    if b"1" not in db_exists.stdout:
        run(
            base_cmd + ["-d", "postgres", "-c", f"CREATE DATABASE \"{db_name}\" OWNER \"{user}\""],
            env=env,
        )

    run(
        base_cmd + ["-d", db_name, "-c", "CREATE EXTENSION IF NOT EXISTS vector"],
        env=env,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Manage embedded Postgres for Conda installs")
    parser.add_argument("command", choices=["init", "start", "stop", "ensure-db"], help="Action to perform")
    parser.add_argument("--data-dir", required=True, help="Postgres data directory")
    parser.add_argument("--user", required=False, default="stash_ai_server")
    parser.add_argument("--password", required=False, default="stash_ai_server")
    parser.add_argument("--database", required=False, default="stash_ai_server")
    parser.add_argument("--port", required=False, type=int, default=5544)
    parser.add_argument("--log-file", required=False, help="Log file path")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    log_file = Path(args.log_file) if args.log_file else data_dir / "postgres.log"

    try:
        if args.command == "init":
            ensure_cluster(data_dir, args.user, args.password, args.port, log_file)
        elif args.command == "start":
            _maybe_clear_stale_pid(data_dir)
            if not (data_dir / "postmaster.pid").exists():
                pg_ctl("start", data_dir, args.port, log_file)
            wait_for_ready(args.port)
        elif args.command == "stop":
            if (data_dir / "postmaster.pid").exists():
                pg_ctl("stop", data_dir, args.port, log_file)
        elif args.command == "ensure-db":
            ensure_database(args.user, args.password, args.database, args.port)
    except PgError as exc:
        print(f"[postgres service] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
