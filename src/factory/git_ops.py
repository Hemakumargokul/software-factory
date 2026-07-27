"""Git operations for the product sandbox.

Thin, explicit wrappers over the git CLI. Every mutating operation returns
the resulting commit SHA so callers can record it in the audit trail. Git is
the factory's artifact lineage and rollback mechanism: commit per verified
step, diff at gates, reset on failure, merge to main only through the
approved integrate stage.
"""

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """A git command failed; carries the command and stderr."""


def git(path: Path | str, *args: str) -> str:
    """Run a git command in `path` and return stdout, raising GitError on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed in {path}: {result.stderr.strip()}"
        )
    return result.stdout


def init_repo(path: Path | str) -> str:
    """Initialize a repo on `main` with an empty initial commit.

    The initial commit matters: without it there is no SHA for rollback to
    target on the first iteration. Identity is set locally so sandbox repos
    work on machines with no global git config.
    """
    Path(path).mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Software Factory")
    git(path, "config", "user.email", "factory@localhost")
    git(path, "commit", "--allow-empty", "-m", "factory: initial commit")
    return current_sha(path)


def current_sha(path: Path | str) -> str:
    return git(path, "rev-parse", "HEAD").strip()


def stage_all(path: Path | str) -> None:
    """Stage every change, including new and deleted files. Diffs and commits
    both operate on the fully staged tree so untracked files are never
    invisible to policy scans."""
    git(path, "add", "-A")


def diff(path: Path | str, base_sha: str) -> str:
    """Full diff of the working tree against `base_sha`, new files included."""
    stage_all(path)
    return git(path, "diff", base_sha)


def changed_files(path: Path | str, base_sha: str) -> list[str]:
    """Paths changed since `base_sha`, new files included."""
    stage_all(path)
    out = git(path, "diff", "--name-only", base_sha)
    return [line for line in out.splitlines() if line.strip()]


def commit_all(path: Path | str, message: str, trailers: dict[str, str] | None = None) -> str:
    """Stage everything and commit. Trailers (e.g. Factory-Trace-Id) are
    appended in git's trailer format so `git log --format=%(trailers...)`
    can recover them for trace-to-commit correlation."""
    stage_all(path)
    full_message = message
    if trailers:
        trailer_block = "\n".join(f"{key}: {value}" for key, value in trailers.items())
        full_message = f"{message}\n\n{trailer_block}"
    git(path, "commit", "-m", full_message)
    return current_sha(path)


def get_trailer(path: Path | str, key: str, sha: str = "HEAD") -> str | None:
    """Read a trailer value back from a commit, or None if absent."""
    out = git(
        path, "log", "-1", sha, f"--format=%(trailers:key={key},valueonly)"
    ).strip()
    return out or None


def reset_hard(path: Path | str, sha: str) -> str:
    """Rollback: restore the tree to `sha` and remove untracked leftovers
    (reset --hard alone would leave newly created files behind)."""
    git(path, "reset", "--hard", sha)
    git(path, "clean", "-fd")
    return current_sha(path)


def create_branch(path: Path | str, name: str) -> None:
    git(path, "switch", "-c", name)


def merge_to_main(path: Path | str, branch: str, message: str | None = None) -> str:
    """Change control: the only path by which work reaches main. --no-ff so
    every integration is a distinct, auditable merge commit."""
    git(path, "switch", "main")
    msg = message or f"factory: integrate {branch}"
    git(path, "merge", "--no-ff", branch, "-m", msg)
    return current_sha(path)
