"""Project profiles: everything product-language-specific lives here.

The orchestration graph, gates, state, git operations, tracing and metrics
never mention a product language. Bootstrap copies the profile's scaffold,
the verification stages run its commands, and the policy rules apply its
patterns. Supporting a new product language means adding a profile here.
"""

from dataclasses import dataclass
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


@dataclass(frozen=True)
class ProjectProfile:
    language: str
    scaffold_template: Path
    build_cmd: tuple[str, ...]
    test_cmd: tuple[str, ...]
    package_cmd: tuple[str, ...]
    run_cmd: tuple[str, ...]
    health_url: str
    service_port: int
    dependency_files: tuple[str, ...]
    dependency_allowlist: frozenset[str]
    forbidden_patterns: tuple[str, ...]
    protected_globs: tuple[str, ...]
    # Optional external scanners (names from policy_rules.EXTERNAL_SCANNERS).
    # Run on top of the built-in regex scanners, never instead of them;
    # a missing binary means the scanner is skipped, not a failed run.
    external_scanners: tuple[str, ...] = ()
    first_build_timeout_s: int = 600
    build_timeout_s: int = 180


JAVA_SPRINGBOOT = ProjectProfile(
    language="java",
    scaffold_template=TEMPLATES_DIR / "java-springboot",
    build_cmd=("./mvnw", "-q", "-B", "compile"),
    test_cmd=("./mvnw", "-q", "-B", "test"),
    package_cmd=("./mvnw", "-q", "-B", "package", "-DskipTests"),
    run_cmd=("java", "-jar", "target/app.jar"),
    health_url="http://127.0.0.1:8080/actuator/health",
    service_port=8080,
    dependency_files=("pom.xml",),
    dependency_allowlist=frozenset(
        {
            "org.springframework.boot:spring-boot-starter-web",
            "org.springframework.boot:spring-boot-starter-data-jpa",
            "org.springframework.boot:spring-boot-starter-actuator",
            "org.springframework.boot:spring-boot-starter-validation",
            "org.springframework.boot:spring-boot-starter-test",
            "com.h2database:h2",
        }
    ),
    forbidden_patterns=(
        r"Runtime\.getRuntime\(\)\.exec",
        r"\bProcessBuilder\b",
        r"System\.exit\s*\(",
        r"Class\.forName\s*\(",
        r"\bURLClassLoader\b",
    ),
    protected_globs=(),
    external_scanners=("gitleaks",),
)

PROFILES: dict[str, ProjectProfile] = {
    "java-springboot": JAVA_SPRINGBOOT,
}


def get_profile(name: str) -> ProjectProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(
            f"Unknown profile {name!r}. Available: {sorted(PROFILES)}"
        ) from None
