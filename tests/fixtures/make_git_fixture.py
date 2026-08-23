"""Build a tiny throwaway git repo for adapter tests.

Usage in a test:

    from tests.fixtures.make_git_fixture import make_git_fixture
    repo = make_git_fixture(tmp_path / "repo")

Produces 3 deterministic commits (fixed authors/dates, so shas are stable
across runs on the same git version's default branch name).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_COMMITS = [
    (
        "Add pricing notes",
        "Priya Raman",
        "priya.raman@bluegable.example.com",
        "2024-01-10T09:00:00 +0000",
        {
            "docs/pricing.md": "# Pricing\n\nStarter: 120/yr. Professional: 480/yr.\n",
        },
    ),
    (
        "Add invoice helper script",
        "Devon Aylward",
        "devon.aylward@hollowbeck.example.com",
        "2024-02-03T14:30:00 +0000",
        {
            "scripts/invoice.py": (
                "def next_invoice_number(last: int) -> str:\n"
                '    return f"INV-2024-{last + 1:04d}"\n'
            ),
        },
    ),
    (
        "Correct Professional price after INV-2024-0312 credit",
        "Priya Raman",
        "priya.raman@bluegable.example.com",
        "2024-03-08T10:15:00 +0000",
        {
            "docs/pricing.md": (
                "# Pricing\n\nStarter: 120/yr. Professional: 420/yr.\n\n"
                "Reduced March 2024 after the INV-2024-0312 packaging credit.\n"
            ),
        },
    ),
]


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def make_git_fixture(repo: Path) -> Path:
    """Create a git repo at `repo` with 3 commits; returns `repo`."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
    )
    _git(repo, "config", "user.name", "Fixture Bot")
    _git(repo, "config", "user.email", "fixturebox@example.com")

    for message, author, email, date, files in _COMMITS:
        for rel, content in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            _git(repo, "add", rel)
        env = os.environ | {
            "GIT_AUTHOR_NAME": author,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_NAME": author,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_COMMITTER_DATE": date,
        }
        _git(repo, "commit", "-q", "-m", message, env=env)
    return repo
