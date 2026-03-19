"""Git operations tool: add, commit, push via subprocess.

Provides git_add, git_commit, and git_push functions for use by the
admin agent.  Each function shells out to git and returns stdout/stderr.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_git(args: list[str], *, cwd: str | Path) -> str:
    """Run a git command and return its combined output.

    Raises
    ------
    subprocess.CalledProcessError
        If the git command exits with a non-zero status.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return (result.stdout + result.stderr).strip()


def git_add(paths: list[str], *, cwd: str | Path) -> str:
    """Stage files for commit.

    Args:
        paths: File paths to add (relative to *cwd*).
        cwd: Working directory (repository root).

    Returns:
        Git command output.
    """
    return _run_git(["add", "--", *paths], cwd=cwd)


def git_commit(message: str, *, cwd: str | Path) -> str:
    """Create a commit with the given message.

    Args:
        message: Commit message.
        cwd: Working directory (repository root).

    Returns:
        Git command output.
    """
    return _run_git(["commit", "-m", message], cwd=cwd)


def git_push(*, remote: str = "origin", branch: str | None = None, cwd: str | Path) -> str:
    """Push commits to the remote.

    Args:
        remote: Remote name (default ``"origin"``).
        branch: Branch name.  If ``None``, pushes the current branch.
        cwd: Working directory (repository root).

    Returns:
        Git command output.
    """
    args = ["push", remote]
    if branch is not None:
        args.append(branch)
    return _run_git(args, cwd=cwd)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Git operations tool")
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="Stage files")
    add_p.add_argument("--cwd", required=True)
    add_p.add_argument("paths", nargs="+")

    commit_p = sub.add_parser("commit", help="Create a commit")
    commit_p.add_argument("--cwd", required=True)
    commit_p.add_argument("--message", "-m", required=True)

    push_p = sub.add_parser("push", help="Push to remote")
    push_p.add_argument("--cwd", required=True)
    push_p.add_argument("--remote", default="origin")
    push_p.add_argument("--branch", default=None)

    args = parser.parse_args()

    try:
        if args.command == "add":
            print(git_add(args.paths, cwd=args.cwd))
        elif args.command == "commit":
            print(git_commit(args.message, cwd=args.cwd))
        elif args.command == "push":
            print(git_push(remote=args.remote, branch=args.branch, cwd=args.cwd))
    except subprocess.CalledProcessError as exc:
        print(f"git {args.command} failed: {exc.stderr or exc.stdout}", file=sys.stderr)
        sys.exit(exc.returncode)
