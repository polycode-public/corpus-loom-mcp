import subprocess
from pathlib import Path

from corpusindex.adapters.gitrepo import GitRepoAdapter
from corpusindex.config import SourceConfig
from tests.fixtures.make_git_fixture import make_git_fixture


def _config(root: Path, **overrides) -> SourceConfig:
    fields = dict(name="repo", type="gitrepo", root=root)
    fields.update(overrides)
    return SourceConfig(**fields)


def _file_probes(adapter):
    return {p.path: p for p in adapter.discover() if not p.path.startswith("commit/")}


def _commit_probes(adapter):
    return {p.path: p for p in adapter.discover() if p.path.startswith("commit/")}


def _load(adapter, path):
    probe = next(p for p in adapter.discover() if p.path == path)
    return adapter.load(probe)


def test_discover_file_probes(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    adapter = GitRepoAdapter(_config(repo))
    files = _file_probes(adapter)
    assert set(files) == {"docs/pricing.md", "scripts/invoice.py"}
    for probe in files.values():
        assert probe.stat_sig is None
        assert probe.git_sha is not None
        assert len(probe.git_sha) == 40


def test_discover_commit_probes(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    adapter = GitRepoAdapter(_config(repo))
    commits = _commit_probes(adapter)
    assert len(commits) == 3
    log_shas = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    assert set(commits) == {f"commit/{sha}" for sha in log_shas}
    for path, probe in commits.items():
        assert probe.git_sha == path.removeprefix("commit/")
        assert probe.stat_sig is None


def test_discover_defaults_to_main_branch(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    adapter = GitRepoAdapter(_config(repo, branch=None))
    assert adapter.branch == "main"
    assert len(_file_probes(adapter)) == 2


def test_discover_exclude_glob(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    adapter = GitRepoAdapter(_config(repo, exclude=("scripts/*",)))
    assert set(_file_probes(adapter)) == {"docs/pricing.md"}


def test_discover_include_glob(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    adapter = GitRepoAdapter(_config(repo, include=("scripts/*",)))
    assert set(_file_probes(adapter)) == {"scripts/invoice.py"}


def test_load_file_markdown_decode(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    adapter = GitRepoAdapter(_config(repo))
    doc = _load(adapter, "docs/pricing.md")
    assert doc.content_indexed
    assert "Professional: 420/yr" in doc.text
    assert doc.title == "pricing.md"
    probe = next(p for p in adapter.discover() if p.path == "docs/pricing.md")
    assert doc.content_hash == probe.git_sha


def test_load_file_code_decode(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    adapter = GitRepoAdapter(_config(repo))
    doc = _load(adapter, "scripts/invoice.py")
    assert doc.content_indexed
    assert "next_invoice_number" in doc.text
    assert doc.title == "invoice.py"


def test_load_commit_latest(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    adapter = GitRepoAdapter(_config(repo))
    commits = _commit_probes(adapter)
    latest_sha = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%H"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    doc = adapter.load(commits[f"commit/{latest_sha}"])
    assert doc.content_hash == latest_sha
    assert doc.title == "Correct Professional price after INV-2024-0312 credit"
    assert "INV-2024-0312" in doc.text
    assert doc.doc_date == "2024-03-08T10:15:00+00:00"
    assert doc.meta["author_name"] == "Priya Raman"
    assert doc.meta["author_email"] == "priya.raman@bluegable.example.com"
    assert doc.meta["files"] == ["docs/pricing.md"]


def test_load_commit_root_commit_reports_files(tmp_path):
    repo = make_git_fixture(tmp_path / "repo")
    adapter = GitRepoAdapter(_config(repo))
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H|%s"],
        check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()
    root_sha = next(l.split("|", 1)[0] for l in log if l.endswith("Add pricing notes"))
    commits = _commit_probes(adapter)
    doc = adapter.load(commits[f"commit/{root_sha}"])
    assert doc.meta["files"] == ["docs/pricing.md"]
    assert doc.title == "Add pricing notes"


def _init_repo_with_pdf(tmp_path: Path) -> Path:
    repo = tmp_path / "pdfrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture Bot"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixturebox@example.com"], check=True)
    (repo / "report.pdf").write_bytes(
        b"%PDF-1.4\n% fake fixture pdf: bytes only, not a parseable document\n"
        b"1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "report.pdf"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "Add report"],
        check=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "Fixture Bot",
            "GIT_AUTHOR_EMAIL": "fixturebox@example.com",
            "GIT_AUTHOR_DATE": "2024-05-01T00:00:00 +0000",
            "GIT_COMMITTER_NAME": "Fixture Bot",
            "GIT_COMMITTER_EMAIL": "fixturebox@example.com",
            "GIT_COMMITTER_DATE": "2024-05-01T00:00:00 +0000",
        },
    )
    return repo


def test_load_file_convert_not_configured_is_metadata_only(tmp_path):
    repo = _init_repo_with_pdf(tmp_path)
    adapter = GitRepoAdapter(_config(repo))
    doc = _load(adapter, "report.pdf")
    assert not doc.content_indexed


def test_load_file_convert_configured_graceful_metadata_only(tmp_path):
    repo = _init_repo_with_pdf(tmp_path)
    adapter = GitRepoAdapter(_config(repo, convert=("pdf",)))
    doc = _load(adapter, "report.pdf")
    assert not doc.content_indexed
    assert doc.text is None
