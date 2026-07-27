"""Policy scanners: pure functions over a unified diff.

These run in the verification fan-out (policy_stage), after the agent has
edited but before anything is committed. They are language-agnostic by
construction — the profile supplies the forbidden-construct patterns and
the dependency allowlist, this module supplies the mechanics.

All scanners look at ADDED lines only: pre-existing code is the previous
iteration's problem, and flagging it would make every retry noisier than
the last.
"""

import re
from dataclasses import dataclass
from typing import Iterable, Iterator


@dataclass(frozen=True)
class Violation:
    rule: str          # "secret" | "forbidden" | "dependency"
    detail: str
    file: str | None
    line: str


_FILE_HEADER = re.compile(r"^\+\+\+ b/(.*)$")

# Secret shapes worth blocking regardless of language.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key material", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}")),
    # Covers assignments (password = "x", password: "x") and call-style
    # pairs like props.put("password", "x") common in Java.
    (
        "hardcoded password",
        re.compile(r"""(?i)\bpassword["']?\s*[=:,]\s*["'][^"']{4,}["']"""),
    ),
    (
        "hardcoded api key",
        re.compile(r"""(?i)\bapi[_-]?key\s*[=:]\s*["'][^"']{8,}["']"""),
    ),
)


def added_lines(diff: str) -> Iterator[tuple[str | None, str]]:
    """Yield (file, content) for every added line in a unified diff."""
    current_file: str | None = None
    for raw in diff.splitlines():
        header = _FILE_HEADER.match(raw)
        if header:
            current_file = header.group(1)
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            yield current_file, raw[1:]


def scan_secrets(diff: str) -> list[Violation]:
    violations = []
    for file, line in added_lines(diff):
        for name, pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                violations.append(
                    Violation(rule="secret", detail=name, file=file, line=line.strip())
                )
    return violations


def scan_forbidden(diff: str, patterns: Iterable[str]) -> list[Violation]:
    """Flag profile-defined forbidden constructs (process spawning,
    reflection classloading, System.exit ...) in added lines."""
    compiled = [re.compile(p) for p in patterns]
    violations = []
    for file, line in added_lines(diff):
        for pattern in compiled:
            if pattern.search(line):
                violations.append(
                    Violation(
                        rule="forbidden",
                        detail=f"matches {pattern.pattern!r}",
                        file=file,
                        line=line.strip(),
                    )
                )
    return violations


_GROUP_ID = re.compile(r"<groupId>\s*([^<]+?)\s*</groupId>")
_ARTIFACT_ID = re.compile(r"<artifactId>\s*([^<]+?)\s*</artifactId>")


def scan_dependencies(
    diff: str, dependency_files: Iterable[str], allowlist: frozenset[str] | set[str]
) -> list[Violation]:
    """Flag new dependency coordinates not on the profile allowlist.

    For Maven files, added <groupId>/<artifactId> lines are paired in
    document order into group:artifact coordinates. An off-list coordinate
    is a high-impact action for the gate to decide on, not proof of malice.
    """
    dependency_files = set(dependency_files)
    pending_group: str | None = None
    violations = []

    for file, line in added_lines(diff):
        if file not in dependency_files:
            continue
        group_match = _GROUP_ID.search(line)
        if group_match:
            pending_group = group_match.group(1)
            continue
        artifact_match = _ARTIFACT_ID.search(line)
        if artifact_match and pending_group:
            coordinate = f"{pending_group}:{artifact_match.group(1)}"
            pending_group = None
            if coordinate not in allowlist:
                violations.append(
                    Violation(
                        rule="dependency",
                        detail=f"{coordinate} not on allowlist",
                        file=file,
                        line=line.strip(),
                    )
                )
    return violations


def scan_all(
    diff: str,
    *,
    forbidden_patterns: Iterable[str],
    dependency_files: Iterable[str],
    dependency_allowlist: frozenset[str] | set[str],
) -> list[Violation]:
    """Every scanner over one diff; the policy stage calls just this."""
    return [
        *scan_secrets(diff),
        *scan_forbidden(diff, forbidden_patterns),
        *scan_dependencies(diff, dependency_files, dependency_allowlist),
    ]
