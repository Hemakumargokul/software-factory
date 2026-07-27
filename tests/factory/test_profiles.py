import pytest

from factory.profiles import JAVA_SPRINGBOOT, get_profile


def test_get_profile_returns_java_springboot():
    profile = get_profile("java-springboot")
    assert profile is JAVA_SPRINGBOOT
    assert profile.language == "java"


def test_scaffold_template_exists_with_wrapper_and_pom():
    template = JAVA_SPRINGBOOT.scaffold_template
    assert template.is_dir()
    assert (template / "pom.xml").is_file()
    assert (template / "mvnw").is_file()


def test_unknown_profile_raises_with_available_names():
    with pytest.raises(ValueError, match="java-springboot"):
        get_profile("cobol")
