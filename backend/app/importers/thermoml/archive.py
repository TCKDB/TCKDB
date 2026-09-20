"""Fetch, select and snapshot ThermoML archive content.

Three responsibilities, kept in one module because they share the
same trust boundary (bytes from a third party, verified before use):

* :func:`fetch_archive` -- download the single pinned archive URL,
  verifying its digest before any member is read.
* :func:`select_article` -- pull one article's XML and JSON twin out
  of the ``.tgz`` **without extracting the whole archive to disk**,
  cross-checking the JSON twin's embedded MD5 against the XML bytes.
* :func:`write_snapshot` -- lay the selected article down on disk by
  content hash, with a deterministic ``manifest.json``, mirroring the
  shape (not the HTML-specific classifier) of
  ``app.importers.cccbdb.snapshot``.

Never writes to the database. Never fetches any URL other than the one
pinned in ``app.importers.thermoml.ARCHIVE_URL``.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.importers.thermoml import (
    ARCHIVE_SHA256,
    ARCHIVE_URL,
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)

_CHUNK_SIZE = 1024 * 1024


class UnverifiedUrlError(RuntimeError):
    """Raised when asked to fetch anything but the single pinned URL.

    Pattern of ``app.importers.cccbdb.crawl_plan.assert_all_validated``:
    the importer never follows a discovered link or an operator-supplied
    URL, only the one constant this package pins.
    """


class ArchiveDigestMismatchError(RuntimeError):
    """The downloaded (or already-on-disk) archive does not hash to
    ``ARCHIVE_SHA256``. Raised before any tar member is read."""


class ArticleNotFoundError(RuntimeError):
    """No unique XML/JSON member pair for a DOI was found in the archive."""


class ArticleIntegrityError(RuntimeError):
    """The JSON twin's ``THERMOML_MD5_CHECKSUM`` does not match the
    fetched XML bytes' MD5."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_archive(url: str, dest: Path) -> Path:
    """Download ``url`` to ``dest``, refusing anything but the pinned URL.

    :param url: Must equal :data:`app.importers.thermoml.ARCHIVE_URL`
        exactly, or :class:`UnverifiedUrlError` is raised before any
        network call is made.
    :param dest: Destination file path. If a file already exists there
        and hashes to :data:`app.importers.thermoml.ARCHIVE_SHA256`,
        the existing file is reused and nothing is downloaded (the
        archive is 189 MB; re-fetching on every call would be hostile
        to NIST's server and to the caller).
    :returns: ``dest``.
    :raises UnverifiedUrlError: ``url`` is not the pinned archive URL.
    :raises ArchiveDigestMismatchError: The downloaded bytes do not
        hash to ``ARCHIVE_SHA256``. The partial/mismatched file is
        removed before raising -- a caller must never read a
        mismatched ``dest``.
    """

    if url != ARCHIVE_URL:
        raise UnverifiedUrlError(
            f"Refusing to fetch unverified ThermoML URL {url!r}. The only "
            f"URL this importer will fetch is {ARCHIVE_URL!r}."
        )

    if dest.exists() and _sha256_file(dest) == ARCHIVE_SHA256:
        return dest

    import requests  # local import: keeps this module importable dep-free

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                digest.update(chunk)
                fh.write(chunk)

    if digest.hexdigest() != ARCHIVE_SHA256:
        tmp.unlink(missing_ok=True)
        raise ArchiveDigestMismatchError(
            f"downloaded archive sha256={digest.hexdigest()!r}, expected "
            f"{ARCHIVE_SHA256!r}"
        )
    tmp.replace(dest)
    return dest


@dataclass(frozen=True)
class ArticleBytes:
    """One article's XML and JSON twin, read from the archive.

    :param xml: Raw ThermoML XML bytes.
    :param json_bytes: Raw JSON twin bytes.
    :param xml_sha256: SHA-256 of ``xml``.
    :param json_sha256: SHA-256 of ``json_bytes``.
    :param member_paths: The two tar member names the bytes were read
        from, ``(xml_member, json_member)``.
    """

    xml: bytes
    json_bytes: bytes
    xml_sha256: str
    json_sha256: str
    member_paths: tuple[str, str]


#: Placeholder JSON twin for a document that has none -- the MD5
#: cross-check :func:`select_article` performs is an archive-specific
#: integrity guard against the tarball's own embedded checksum; a
#: standalone document (CLI ``--file`` or the upload route) has no JSON
#: twin to cross-check against, and this constant documents that at the
#: one place it is manufactured rather than at every call site.
_NO_JSON_TWIN = b"{}"


def build_standalone_article(xml_bytes: bytes, *, label: str) -> ArticleBytes:
    """Wrap raw XML bytes with no JSON twin as an :class:`ArticleBytes`,
    for a document that did not come from the pinned bulk archive.

    Shared by the CLI's ``--file`` mode and the ``POST /uploads/thermoml``
    route (Phase C-E6 review round 2, F8) -- previously each built its own
    copy of this four-line construction, which could silently drift (e.g.
    one computing ``json_sha256`` over different placeholder bytes than
    the other).

    :param xml_bytes: The document's raw bytes, exactly as read from disk
        or decoded from the upload request.
    :param label: A **display-only** string recorded as
        ``member_paths[0]`` -- e.g. the local file path for the CLI, or
        ``f"upload:{filename}"`` for the route. This is never a
        retrievable location (unlike an archive member path, which names
        a real position inside the pinned, digest-verified tarball) --
        callers must not treat it as one. See
        ``app.services.thermoml_cp_import._raw_uri_for``'s
        ``allow_member_path_fallback`` parameter, which is ``False`` for
        every :class:`ArticleBytes` this function builds.
    """
    return ArticleBytes(
        xml=xml_bytes,
        json_bytes=_NO_JSON_TWIN,
        xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
        json_sha256=hashlib.sha256(_NO_JSON_TWIN).hexdigest(),
        member_paths=(label, ""),
    )


def _doi_prefix_suffix(doi: str) -> tuple[str, str]:
    prefix, sep, suffix = doi.partition("/")
    if not sep or not prefix or not suffix:
        raise ValueError(
            f"DOI does not look like '<prefix>/<suffix>' (e.g. "
            f"'10.1016/j.fluid.2016.07.034'): {doi!r}"
        )
    return prefix, suffix


def _match_member(names: list[str], *, prefix: str, suffix: str, ext: str) -> str:
    """Find the one archive member for ``doi`` with extension ``ext``.

    The archive's documented layout is one directory per DOI prefix
    (``10.1016/``, ``10.1021/``, ...) containing ``<suffix>.xml`` /
    ``<suffix>.json`` for every article under that prefix. We check
    the exact expected path first, then fall back to a suffix+prefix
    match so a naming variant (e.g. a different separator) still
    resolves deterministically -- as long as exactly one member
    matches; more or less than one is an error, never a guess.
    """

    exact = f"{prefix}/{suffix}{ext}"
    if exact in names:
        return exact

    candidates = [
        n
        for n in names
        if n.startswith(f"{prefix}/") and n.endswith(ext) and suffix in n
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ArticleNotFoundError(
            f"no {ext} member found for DOI prefix={prefix!r} suffix={suffix!r}"
        )
    raise ArticleNotFoundError(
        f"ambiguous {ext} member for DOI prefix={prefix!r} suffix={suffix!r}: "
        f"{candidates!r}"
    )


def _find_md5_checksum(obj: Any) -> str | None:
    """Recursively search a decoded JSON twin for ``THERMOML_MD5_CHECKSUM``.

    The exact nesting of the JSON twin is not part of any published
    contract this importer pins to, so the search is structural rather
    than a fixed key path.
    """

    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "THERMOML_MD5_CHECKSUM" and isinstance(value, str):
                return value
            found = _find_md5_checksum(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_md5_checksum(item)
            if found is not None:
                return found
    return None


def select_article(tgz_path: Path, doi: str) -> ArticleBytes:
    """Read one article's XML and JSON twin out of the archive tarball.

    Reads the two members directly from the tar stream -- the archive
    is never extracted to disk. Cross-checks the JSON twin's embedded
    ``THERMOML_MD5_CHECKSUM`` against the actual XML bytes before
    returning.

    :param tgz_path: Path to a ``.tgz`` already verified by
        :func:`fetch_archive` (this function does not re-verify the
        archive digest).
    :param doi: The article's own DOI, e.g. ``"10.1016/j.fluid.2016.07.034"``.
    :raises ArticleNotFoundError: No unique XML/JSON pair for ``doi``.
    :raises ArticleIntegrityError: The JSON twin's embedded MD5 does
        not match the fetched XML bytes.
    """

    prefix, suffix = _doi_prefix_suffix(doi)
    with tarfile.open(tgz_path, mode="r:gz") as tar:
        names = tar.getnames()
        xml_name = _match_member(names, prefix=prefix, suffix=suffix, ext=".xml")
        json_name = _match_member(names, prefix=prefix, suffix=suffix, ext=".json")

        xml_member = tar.getmember(xml_name)
        json_member = tar.getmember(json_name)
        xml_fh = tar.extractfile(xml_member)
        json_fh = tar.extractfile(json_member)
        if xml_fh is None or json_fh is None:
            raise ArticleNotFoundError(
                f"member is not a regular file: {xml_name!r} / {json_name!r}"
            )
        xml_bytes = xml_fh.read()
        json_bytes = json_fh.read()

    expected_md5 = _find_md5_checksum(json.loads(json_bytes))
    actual_md5 = hashlib.md5(xml_bytes).hexdigest()
    if expected_md5 is None:
        raise ArticleIntegrityError(
            f"JSON twin {json_name!r} carries no THERMOML_MD5_CHECKSUM"
        )
    if expected_md5.lower() != actual_md5.lower():
        raise ArticleIntegrityError(
            f"XML member {xml_name!r} md5={actual_md5!r} does not match "
            f"JSON twin's THERMOML_MD5_CHECKSUM={expected_md5!r}"
        )

    return ArticleBytes(
        xml=xml_bytes,
        json_bytes=json_bytes,
        xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
        json_sha256=hashlib.sha256(json_bytes).hexdigest(),
        member_paths=(xml_name, json_name),
    )


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _atomic_write_json(path: Path, data: Any) -> None:
    _atomic_write_bytes(
        path,
        (json.dumps(data, indent=2, sort_keys=True, default=str) + "\n").encode(
            "utf-8"
        ),
    )


def write_snapshot(
    article: ArticleBytes,
    *,
    output_dir: Path,
    doi: str,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Lay ``article`` down on disk by content hash, with a manifest.

    Layout: ``<output_dir>/raw/<sha256[:2]>/<sha256>.xml`` (and
    ``.json``), plus a deterministic ``<output_dir>/manifest.json``
    recording the source, DOI, digests, tar member paths and versions
    -- the shape of ``app.importers.cccbdb.snapshot``'s manifest, not
    its HTML-page classifier (there is nothing to classify here: the
    archive already tells us exactly which two members are the
    article).

    :returns: The manifest dict that was written.
    """

    xml_path = output_dir / "raw" / article.xml_sha256[:2] / f"{article.xml_sha256}.xml"
    json_path = (
        output_dir / "raw" / article.json_sha256[:2] / f"{article.json_sha256}.json"
    )
    _atomic_write_bytes(xml_path, article.xml)
    _atomic_write_bytes(json_path, article.json_bytes)

    manifest = {
        "source": SOURCE_NAME,
        "source_release": SOURCE_RELEASE,
        "source_database_doi": SOURCE_DATABASE_DOI,
        "archive_url": ARCHIVE_URL,
        "archive_sha256": ARCHIVE_SHA256,
        "doi": doi,
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "parser_version": PARSER_VERSION,
        "xml_sha256": article.xml_sha256,
        "json_sha256": article.json_sha256,
        "xml_path": str(xml_path.relative_to(output_dir)),
        "json_path": str(json_path.relative_to(output_dir)),
        "member_paths": list(article.member_paths),
    }
    _atomic_write_json(output_dir / "manifest.json", manifest)
    return manifest


__all__ = [
    "ArchiveDigestMismatchError",
    "ArticleBytes",
    "ArticleIntegrityError",
    "ArticleNotFoundError",
    "UnverifiedUrlError",
    "build_standalone_article",
    "fetch_archive",
    "select_article",
    "write_snapshot",
]
