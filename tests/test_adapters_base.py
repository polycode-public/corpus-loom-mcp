from corpusindex.adapters import Doc, DocProbe, SourceAdapter


class DummyAdapter:
    name = "dummy"

    def discover(self):
        yield DocProbe(source="dummy", path="a.txt", stat_sig="1700000000000000000:12")

    def load(self, probe):
        return Doc(probe=probe, content_hash="ab" * 32, text="hello world", bytes=12)


def test_protocol_conformance():
    assert isinstance(DummyAdapter(), SourceAdapter)


def test_doc_content_indexed():
    adapter = DummyAdapter()
    probe = next(adapter.discover())
    assert probe.git_sha is None
    doc = adapter.load(probe)
    assert doc.content_indexed
    assert not Doc(probe=probe, content_hash="0" * 64).content_indexed
