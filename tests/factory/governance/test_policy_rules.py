import shutil

import pytest

from factory.governance import policy_rules
from factory.governance.policy_rules import (
    scan_all,
    scan_dependencies,
    scan_external,
    scan_forbidden,
    scan_secrets,
)
from factory.profiles import get_profile

PROFILE = get_profile("java-springboot")


def as_diff(path: str, *added: str) -> str:
    """Minimal unified diff adding the given lines to one file."""
    lines = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        "@@ -0,0 +1 @@",
        *[f"+{line}" for line in added],
    ]
    return "\n".join(lines) + "\n"


class TestScanSecrets:
    def test_aws_key_flagged(self):
        diff = as_diff("src/Config.java", 'String key = "AKIAIOSFODNN7EXAMPLE";')
        violations = scan_secrets(diff)
        assert len(violations) == 1
        assert violations[0].rule == "secret"
        assert violations[0].file == "src/Config.java"

    def test_pem_header_and_password_literal_flagged(self):
        diff = as_diff(
            "src/App.java",
            "-----BEGIN RSA PRIVATE KEY-----",
            'props.put("password", "hunter22");',
        )
        assert {v.detail for v in scan_secrets(diff)} == {
            "private key material",
            "hardcoded password",
        }

    def test_clean_code_passes(self):
        diff = as_diff(
            "src/App.java",
            'String password = System.getenv("DB_PASSWORD");',
            "// Bearer tokens are validated upstream",
        )
        assert scan_secrets(diff) == []

    def test_removed_lines_ignored(self):
        diff = (
            "+++ b/src/App.java\n"
            '-String key = "AKIAIOSFODNN7EXAMPLE";\n'
        )
        assert scan_secrets(diff) == []


class TestScanForbidden:
    def test_process_spawn_flagged_via_profile_patterns(self):
        diff = as_diff(
            "src/Shell.java", 'Runtime.getRuntime().exec("rm -rf /");'
        )
        violations = scan_forbidden(diff, PROFILE.forbidden_patterns)
        assert len(violations) == 1
        assert violations[0].rule == "forbidden"

    def test_system_exit_and_processbuilder_flagged(self):
        diff = as_diff(
            "src/App.java",
            "System.exit(1);",
            'new ProcessBuilder("ls").start();',
        )
        assert len(scan_forbidden(diff, PROFILE.forbidden_patterns)) == 2

    def test_ordinary_spring_code_passes(self):
        diff = as_diff(
            "src/UrlController.java",
            "@RestController",
            "public class UrlController {",
            "    return ResponseEntity.ok(stats);",
        )
        assert scan_forbidden(diff, PROFILE.forbidden_patterns) == []


class TestScanDependencies:
    def test_offlist_coordinate_flagged(self):
        diff = as_diff(
            "pom.xml",
            "<dependency>",
            "    <groupId>com.google.guava</groupId>",
            "    <artifactId>guava</artifactId>",
            "</dependency>",
        )
        violations = scan_dependencies(
            diff, PROFILE.dependency_files, PROFILE.dependency_allowlist
        )
        assert len(violations) == 1
        assert "com.google.guava:guava" in violations[0].detail

    def test_allowlisted_coordinate_passes(self):
        diff = as_diff(
            "pom.xml",
            "<dependency>",
            "    <groupId>com.h2database</groupId>",
            "    <artifactId>h2</artifactId>",
            "</dependency>",
        )
        assert (
            scan_dependencies(
                diff, PROFILE.dependency_files, PROFILE.dependency_allowlist
            )
            == []
        )

    def test_xml_in_non_dependency_files_ignored(self):
        diff = as_diff(
            "src/main/resources/config.xml",
            "<groupId>anything.at.all</groupId>",
            "<artifactId>whatever</artifactId>",
        )
        assert (
            scan_dependencies(
                diff, PROFILE.dependency_files, PROFILE.dependency_allowlist
            )
            == []
        )


class TestScanAll:
    def test_aggregates_across_scanners(self):
        diff = as_diff(
            "pom.xml",
            "<groupId>org.apache.commons</groupId>",
            "<artifactId>commons-exec</artifactId>",
        ) + as_diff(
            "src/Bad.java",
            'String key = "AKIAIOSFODNN7EXAMPLE";',
            "System.exit(0);",
        )
        violations = scan_all(
            diff,
            forbidden_patterns=PROFILE.forbidden_patterns,
            dependency_files=PROFILE.dependency_files,
            dependency_allowlist=PROFILE.dependency_allowlist,
        )
        assert {v.rule for v in violations} == {"secret", "forbidden", "dependency"}

    def test_clean_diff_produces_nothing(self):
        diff = as_diff("src/Main.java", "public class Main {}")
        assert (
            scan_all(
                diff,
                forbidden_patterns=PROFILE.forbidden_patterns,
                dependency_files=PROFILE.dependency_files,
                dependency_allowlist=PROFILE.dependency_allowlist,
            )
            == []
        )


# Realistic-shaped fake credential, assembled at runtime so this repo never
# contains a scannable secret literal. NOTE: gitleaks' default config
# allowlists well-known documentation keys (anything with "EXAMPLE"), so
# test fixtures must not reuse those.
FAKE_GITHUB_PAT = "ghp_" + "wWPw5k4aXcaT4fNP0UcnZwJUVFk6LO" + "0pINUx"

gitleaks_installed = pytest.mark.skipif(
    shutil.which("gitleaks") is None, reason="gitleaks binary not installed"
)


class TestScanExternal:
    def test_profile_declares_gitleaks(self):
        assert "gitleaks" in PROFILE.external_scanners

    def test_unknown_scanner_name_fails_fast(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown external scanner"):
            scan_external(tmp_path, ["nonexistent-scanner"])

    def test_missing_binary_reported_as_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(policy_rules.shutil, "which", lambda name: None)
        violations, skipped = scan_external(tmp_path, ["gitleaks"])
        assert violations == []
        assert skipped == ["gitleaks"]

    @gitleaks_installed
    def test_planted_secret_found_and_redacted(self, tmp_path):
        (tmp_path / "Config.java").write_text(
            f'String token = "{FAKE_GITHUB_PAT}";\n'
        )

        violations, skipped = scan_external(tmp_path, PROFILE.external_scanners)

        assert skipped == []
        assert len(violations) == 1
        violation = violations[0]
        assert violation.rule == "secret"
        assert violation.detail == "gitleaks:github-pat"
        assert violation.file == "Config.java"  # relative to the sandbox
        # --redact must keep the credential out of every violation field
        assert FAKE_GITHUB_PAT not in repr(violation)

    @gitleaks_installed
    def test_clean_tree_passes(self, tmp_path):
        (tmp_path / "Main.java").write_text("public class Main {}\n")
        assert scan_external(tmp_path, PROFILE.external_scanners) == ([], [])
