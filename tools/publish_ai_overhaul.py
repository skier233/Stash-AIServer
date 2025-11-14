#!/usr/bin/env python3
"""Publish the AI Overhaul frontend bundle to stashapp/CommunityScripts."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


def run(cmd: Iterable[str], cwd: Path | None = None) -> None:
    display = " ".join(cmd)
    print(f"→ {display}")
    subprocess.run(cmd, cwd=cwd, check=True)


def sanitize_branch(label: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z.-]+", "-", label.strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return f"release/ai-overhaul-{slug.lower()}"


def ensure_path(path: Path, description: str) -> None:
    if not path.exists():
        raise SystemExit(f"{description} not found: {path}")


def copy_dist(dist_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(dist_dir, target_dir, ignore=shutil.ignore_patterns("__pycache__"))


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    frontend_dir = repo_root / "frontend"
    dist_dir = frontend_dir / "dist"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, help="Release tag or version (used in branch name)")
    parser.add_argument(
        "--community-repo",
        type=Path,
        default=repo_root.parent / "CommunityScripts",
        help="Path to local clone of stashapp/CommunityScripts",
    )
    parser.add_argument("--skip-build", action="store_true", help="Skip running npm build")
    parser.add_argument("--no-branch", action="store_true", help="Skip creating the release branch")
    args = parser.parse_args()

    community_repo = args.community_repo.resolve()
    ensure_path(community_repo, "CommunityScripts repo")
    ensure_path(community_repo / "plugins", "CommunityScripts plugins folder")

    if not args.skip_build:
        ensure_path(frontend_dir / "package.json", "frontend package.json")
        run(["npm", "install"], cwd=frontend_dir)
        run(["npm", "run", "build"], cwd=frontend_dir)
    else:
        ensure_path(dist_dir, "frontend/dist build directory")

    target_dir = community_repo / "plugins" / "AIOverhaul"
    print(f"Copying {dist_dir} -> {target_dir}")
    copy_dist(dist_dir, target_dir)

    if not args.no_branch:
        branch = sanitize_branch(args.release)
        run(["git", "fetch", "origin"], cwd=community_repo)
        run(["git", "checkout", "-B", branch], cwd=community_repo)
    else:
        branch = "(branch creation skipped)"

    print("\nNext steps:")
    print(f"  1. Inspect {target_dir} to confirm the copied bundle is correct.")
    print("  2. Run 'git status' inside the CommunityScripts repo to review changes.")
    print(f"  3. Commit with a message like 'Update AI Overhaul plugin for {args.release}'.")
    print(f"  4. Push and open a PR against stashapp/CommunityScripts using branch {branch}.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}", file=sys.stderr)
        sys.exit(exc.returncode)
