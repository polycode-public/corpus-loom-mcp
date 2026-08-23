"""SourceAdapter protocol and the DocProbe/Doc exchange types.

The indexer drives every source the same way:

    for probe in adapter.discover():          # cheap: no file reads
        if unchanged_in_db(probe): continue   # stat_sig / git sha fast path
        doc = adapter.load(probe)             # full read + decode

Waves 2+ implement adapters (filetree, eml_tree, gitrepo) against exactly
these types; keep them minimal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DocProbe:
    """Cheap identity of one current document in a source.

    Emitted by discover() for every document the source currently holds,
    without reading content. Exactly one of the change-detection signatures
    is set:

    - stat-based sources (filetree, eml_tree): stat_sig = 'mtime:size'
      (st_mtime_ns and st_size, colon-joined); git_sha is None.
    - git sources: git_sha = blob sha for tracked files, commit sha for
      'commit/<sha>' documents; stat_sig is None.
    """

    source: str          # config source name
    path: str            # relative to the source root, POSIX separators; 'commit/<sha>' for commits
    stat_sig: str | None = None
    git_sha: str | None = None


@dataclass(slots=True)
class Doc:
    """One fully loaded document, ready for the indexer.

    text is the decoded body when the document is content-indexed, or None
    for a metadata-only record (attachments, spreadsheets, binaries, failed
    conversions). documents.content_indexed is derived from text is not None.
    """

    probe: DocProbe
    content_hash: str            # sha256 hex of raw bytes; git blob/commit sha for git docs
    title: str | None = None     # mail Subject / file basename / commit summary line
    doc_date: str | None = None  # ISO-8601 UTC
    bytes: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)  # stored as JSON in documents.meta
    text: str | None = None

    @property
    def content_indexed(self) -> bool:
        return self.text is not None


@runtime_checkable
class SourceAdapter(Protocol):
    """One configured corpus source (a filetree, an .eml tree, a git repo)."""

    name: str

    def discover(self) -> Iterator[DocProbe]:
        """Yield a probe for every document currently present, without reading content."""
        ...

    def load(self, probe: DocProbe) -> Doc:
        """Read and decode the document a probe points at."""
        ...
