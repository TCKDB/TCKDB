"""``archive.py``: digest verification (fetch) and article selection.

Mutation checks (verified manually; see the PR body's mutation table):

* ``test_fetch_archive_rejects_digest_mismatch``: removing the
  ``digest.hexdigest() != ARCHIVE_SHA256`` check in ``fetch_archive``
  makes this test go red (no exception raised, and the corrupt bytes
  would have been accepted as the archive).
* ``test_select_article_rejects_md5_mismatch``: removing the MD5
  cross-check in ``select_article`` makes this test go red.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from contextlib import contextmanager

import pytest

from app.importers.thermoml.archive import (
    ArchiveDigestMismatchError,
    ArticleIntegrityError,
    ArticleNotFoundError,
    fetch_archive,
    select_article,
)


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@contextmanager
def _patched_requests_get(monkeypatch, payload: bytes):
    import requests

    def _fake_get(url, stream=True, timeout=300):
        return _FakeResponse(payload)

    monkeypatch.setattr(requests, "get", _fake_get)
    yield


def test_fetch_archive_rejects_digest_mismatch(tmp_path, monkeypatch):
    """A downloaded archive whose bytes do not hash to ``ARCHIVE_SHA256``
    is refused, and the corrupt file is never left at ``dest``."""

    from app.importers.thermoml import ARCHIVE_URL

    dest = tmp_path / "ThermoML.v2020-09-30.tgz"
    with _patched_requests_get(monkeypatch, b"not the real archive"):
        with pytest.raises(ArchiveDigestMismatchError):
            fetch_archive(ARCHIVE_URL, dest)

    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_fetch_archive_does_not_trust_a_stale_cache_blindly(tmp_path, monkeypatch):
    """A pre-existing ``dest`` is only reused when it already hashes to
    ``ARCHIVE_SHA256`` -- a stale/wrong file at that path must NOT be
    accepted just because a path exists there. We prove this by
    blocking the network: if ``fetch_archive`` reused the stale file
    it would return silently; instead it must attempt a fetch, which
    fails here because we deliberately blocked it."""

    from app.importers.thermoml import ARCHIVE_SHA256, ARCHIVE_URL

    dest = tmp_path / "ThermoML.v2020-09-30.tgz"
    dest.write_bytes(b"stale, wrong content")

    def _unreachable_get(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("fetch_archive must not hit the network here")

    import requests

    monkeypatch.setattr(requests, "get", _unreachable_get)

    # The stale file does not match ARCHIVE_SHA256, so fetch_archive
    # must NOT silently accept it -- it should attempt to fetch, which
    # here raises because we blocked the network. This proves the
    # cache short-circuit is digest-gated, not path-existence-gated.
    with pytest.raises(AssertionError):
        fetch_archive(ARCHIVE_URL, dest)
    assert ARCHIVE_SHA256 != hashlib.sha256(b"stale, wrong content").hexdigest()


def _build_tiny_archive(*, doi: str, xml_bytes: bytes, embed_correct_md5: bool) -> bytes:
    prefix, suffix = doi.split("/", 1)
    actual_md5 = hashlib.md5(xml_bytes).hexdigest()
    embedded_md5 = actual_md5 if embed_correct_md5 else "0" * 32
    json_obj = {"THERMOML_MD5_CHECKSUM": embedded_md5, "doi": doi}
    json_bytes = json.dumps(json_obj).encode("utf-8")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (
            (f"{prefix}/{suffix}.xml", xml_bytes),
            (f"{prefix}/{suffix}.json", json_bytes),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_select_article_reads_matching_pair_without_extracting(tmp_path):
    doi = "10.1000/test.article"
    xml_bytes = b"<DataReport/>"
    archive_bytes = _build_tiny_archive(
        doi=doi, xml_bytes=xml_bytes, embed_correct_md5=True
    )
    tgz_path = tmp_path / "tiny.tgz"
    tgz_path.write_bytes(archive_bytes)

    article = select_article(tgz_path, doi)
    assert article.xml == xml_bytes
    assert article.xml_sha256 == hashlib.sha256(xml_bytes).hexdigest()
    assert article.member_paths == (
        "10.1000/test.article.xml",
        "10.1000/test.article.json",
    )


def test_select_article_rejects_md5_mismatch(tmp_path):
    doi = "10.1000/test.article"
    xml_bytes = b"<DataReport/>"
    archive_bytes = _build_tiny_archive(
        doi=doi, xml_bytes=xml_bytes, embed_correct_md5=False
    )
    tgz_path = tmp_path / "tiny.tgz"
    tgz_path.write_bytes(archive_bytes)

    with pytest.raises(ArticleIntegrityError):
        select_article(tgz_path, doi)


def test_select_article_raises_when_doi_not_present(tmp_path):
    archive_bytes = _build_tiny_archive(
        doi="10.1000/test.article", xml_bytes=b"<DataReport/>", embed_correct_md5=True
    )
    tgz_path = tmp_path / "tiny.tgz"
    tgz_path.write_bytes(archive_bytes)

    with pytest.raises(ArticleNotFoundError):
        select_article(tgz_path, "10.9999/does.not.exist")
