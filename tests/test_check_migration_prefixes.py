"""
Migration Prefix Collision Check Tests
=========================================

Component: ``scripts.check_migration_prefixes``. ``find_collisions`` is pure and hermetic. ``main``
needs a real git repository to exercise its plumbing; those tests build one in ``tmp_path`` rather
than depending on this repository's own history, so they run anywhere, including CI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.check_migration_prefixes import find_collisions, main


def test_find_collisions_flags_an_added_file_whose_prefix_already_exists_on_the_base() -> None:
    added = ("0004_case_write_constraints.sql",)
    base_files = ("0001_serving_store.sql", "0004_analytics_schema.sql")

    assert find_collisions(added, base_files) == ("0004_case_write_constraints.sql",)


def test_find_collisions_ignores_an_added_file_with_a_prefix_free_on_the_base() -> None:
    """The mirror-image case: a genuinely new prefix must not be flagged."""
    added = ("0009_new_thing.sql",)
    base_files = ("0001_serving_store.sql", "0004_analytics_schema.sql")

    assert find_collisions(added, base_files) == ()


def test_find_collisions_flags_only_the_colliding_files_among_several_added() -> None:
    added = ("0004_collides.sql", "0009_free.sql")
    base_files = ("0004_analytics_schema.sql",)

    assert find_collisions(added, base_files) == ("0004_collides.sql",)


def test_find_collisions_ignores_a_filename_with_no_numeric_prefix() -> None:
    added = ("readme.sql",)
    base_files = ("readme.sql",)

    assert find_collisions(added, base_files) == ()


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],  # noqa: S607 - resolved through PATH by design
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "checkout", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "selftest@example.invalid")
    _run_git(repo, "config", "user.name", "selftest")
    _run_git(repo, "config", "commit.gpgsign", "false")


def _add_migration(repo: Path, filename: str) -> None:
    migrations = repo / "app" / "persistence" / "migrations"
    migrations.mkdir(parents=True, exist_ok=True)
    (migrations / filename).write_text("select 1;\n", encoding="utf-8")
    _run_git(repo, "add", str((migrations / filename).relative_to(repo)))
    _run_git(repo, "commit", "-q", "-m", f"add {filename}")


def test_main_returns_1_when_a_new_migration_collides_with_one_on_the_base_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _add_migration(repo, "0004_analytics_schema.sql")
    _run_git(repo, "checkout", "-q", "-b", "feature")
    _add_migration(repo, "0004_case_write_constraints.sql")

    monkeypatch.chdir(repo)
    exit_code = main(["--base", "main"])

    assert exit_code == 1


def test_main_returns_0_when_a_new_migration_has_a_free_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revert-check pairing for the collision test above: the same setup, a free prefix instead,
    proves the check does not simply fail every branch that adds a migration."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _add_migration(repo, "0004_analytics_schema.sql")
    _run_git(repo, "checkout", "-q", "-b", "feature")
    _add_migration(repo, "0005_new_thing.sql")

    monkeypatch.chdir(repo)
    exit_code = main(["--base", "main"])

    assert exit_code == 0


def test_main_returns_0_when_the_base_ref_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unresolvable base yields no comparable files, so nothing is flagged (see Limitations)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _add_migration(repo, "0001_serving_store.sql")

    monkeypatch.chdir(repo)
    exit_code = main(["--base", "does-not-exist"])

    assert exit_code == 0
