"""Git repo adapter: tracked files at a branch HEAD, plus its commit log.

subprocess-only, no git library. File probes carry the blob sha as
git_sha; commit probes use path 'commit/<sha>' with the commit sha as
git_sha, per DocProbe.
"""

from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from corpusindex.adapters.base import Doc, DocProbe
from corpusindex.adapters.filetree import matches_globs
from corpusindex.config import SourceConfig
from corpusindex.extract.convert import convert
from corpusindex.extract.decode import classify, decode_text, is_binary
from corpusindex.extract.html_text import html_to_text

_FIELD_SEP = "\x1f"
_DEFAULT_BRANCH = "main"
_HTML_EXTENSIONS = frozenset({".html", ".htm"})


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=True,
    )
    return result.stdout


def _list_tree(root: Path, branch: str) -> Iterator[tuple[str, str]]:
    out = _git(root, "ls-tree", "-r", "--full-tree", branch).decode("utf-8", errors="replace")
    for line in out.splitlines():
        if not line:
            continue
        meta, _, path = line.partition("\t")
        mode, obj_type, sha = meta.split(" ", 2)
        if obj_type != "blob":
            continue
        yield sha, path


def _commit_shas(root: Path, branch: str) -> list[str]:
    out = _git(root, "log", branch, "--format=%H").decode("utf-8", errors="replace")
    return [line for line in out.splitlines() if line]


def _commit_fields(root: Path, sha: str) -> tuple[str, str, str, str, str, str]:
    fmt = _FIELD_SEP.join(("%H", "%an", "%ae", "%aI", "%s", "%B"))
    out = _git(root, "log", "-1", f"--format={fmt}", sha).decode("utf-8", errors="replace")
    commit_sha, author_name, author_email, author_date, subject, body = out.split(_FIELD_SEP, 5)
    return commit_sha, author_name, author_email, author_date, subject, body.rstrip("\n")


def _changed_files(root: Path, sha: str) -> list[str]:
    out = _git(
        root, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha
    ).decode("utf-8", errors="replace")
    return [line for line in out.splitlines() if line]


class GitRepoAdapter:
    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.name = config.name
        self.branch = config.branch or _DEFAULT_BRANCH

    def discover(self) -> Iterator[DocProbe]:
        root = self.config.root
        for sha, path in _list_tree(root, self.branch):
            if not matches_globs(path, self.config.include, self.config.exclude):
                continue
            yield DocProbe(source=self.name, path=path, git_sha=sha)
        for sha in _commit_shas(root, self.branch):
            yield DocProbe(source=self.name, path=f"commit/{sha}", git_sha=sha)

    def load(self, probe: DocProbe) -> Doc:
        if probe.path.startswith("commit/"):
            return self._load_commit(probe)
        return self._load_file(probe)

    def _load_file(self, probe: DocProbe) -> Doc:
        root = self.config.root
        raw = _git(root, "cat-file", "blob", probe.git_sha)

        text: str | None = None
        classification = classify(probe.path)
        if classification == "decode" and not is_binary(raw):
            text = decode_text(raw)
            if Path(probe.path).suffix.lower() in _HTML_EXTENSIONS:
                text = html_to_text(text)
        elif classification == "convert":
            ext = Path(probe.path).suffix.lower().lstrip(".")
            if ext in self.config.convert:
                with tempfile.NamedTemporaryFile(suffix=Path(probe.path).suffix) as tmp:
                    tmp.write(raw)
                    tmp.flush()
                    text = convert(tmp.name)

        return Doc(
            probe=probe,
            content_hash=probe.git_sha,
            title=Path(probe.path).name,
            doc_date=None,
            bytes=len(raw),
            meta={},
            text=text,
        )

    def _load_commit(self, probe: DocProbe) -> Doc:
        root = self.config.root
        sha, author_name, author_email, author_date, subject, body = _commit_fields(
            root, probe.git_sha
        )
        dt = datetime.fromisoformat(author_date)
        doc_date = dt.astimezone(timezone.utc).isoformat()
        files = _changed_files(root, probe.git_sha)
        text = body if body else subject

        return Doc(
            probe=probe,
            content_hash=sha,
            title=subject,
            doc_date=doc_date,
            bytes=len(text.encode("utf-8")),
            meta={"author_name": author_name, "author_email": author_email, "files": files},
            text=text,
        )
