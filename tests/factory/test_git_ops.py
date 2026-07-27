import pytest

from factory import git_ops


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "sandbox"
    base_sha = git_ops.init_repo(path)
    return path, base_sha


def test_init_repo_creates_main_with_initial_commit(repo):
    path, base_sha = repo
    assert len(base_sha) == 40
    branch = git_ops.git(path, "branch", "--show-current").strip()
    assert branch == "main"
    assert git_ops.current_sha(path) == base_sha


def test_commit_all_returns_new_sha_and_trailer_round_trips(repo):
    path, base_sha = repo
    (path / "hello.txt").write_text("hi\n")

    sha = git_ops.commit_all(
        path, "add hello", trailers={"Factory-Trace-Id": "trace-abc-123"}
    )

    assert sha != base_sha
    assert sha == git_ops.current_sha(path)
    assert git_ops.get_trailer(path, "Factory-Trace-Id") == "trace-abc-123"
    assert git_ops.get_trailer(path, "Nonexistent-Key") is None


def test_commit_with_nothing_to_commit_raises(repo):
    path, _ = repo
    with pytest.raises(git_ops.GitError):
        git_ops.commit_all(path, "empty commit attempt")


def test_diff_and_changed_files_include_untracked_new_files(repo):
    path, base_sha = repo
    (path / "src").mkdir()
    (path / "src" / "App.java").write_text("class App {}\n")

    diff_text = git_ops.diff(path, base_sha)
    assert "class App {}" in diff_text
    assert git_ops.changed_files(path, base_sha) == ["src/App.java"]


def test_reset_hard_restores_deleted_and_removes_untracked(repo):
    path, _ = repo
    (path / "keep.txt").write_text("keep me\n")
    known_good = git_ops.commit_all(path, "add keep.txt")

    (path / "keep.txt").unlink()          # agent deleted a committed file
    (path / "junk.txt").write_text("junk")  # agent created a stray file

    restored_sha = git_ops.reset_hard(path, known_good)

    assert restored_sha == known_good
    assert (path / "keep.txt").read_text() == "keep me\n"
    assert not (path / "junk.txt").exists()


def test_branch_and_merge_to_main_creates_merge_commit(repo):
    path, _ = repo
    git_ops.create_branch(path, "factory/task-1")
    (path / "feature.txt").write_text("feature\n")
    branch_sha = git_ops.commit_all(path, "task 1 work")

    merge_sha = git_ops.merge_to_main(path, "factory/task-1")

    assert git_ops.git(path, "branch", "--show-current").strip() == "main"
    assert (path / "feature.txt").exists()
    # --no-ff: the merge commit is distinct from the branch tip and has two parents
    assert merge_sha != branch_sha
    parents = git_ops.git(path, "log", "-1", "--format=%P").split()
    assert len(parents) == 2


def test_git_error_carries_command_and_stderr(repo):
    path, _ = repo
    with pytest.raises(git_ops.GitError, match="rev-parse"):
        git_ops.git(path, "rev-parse", "not-a-real-ref")
