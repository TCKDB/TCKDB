"""Every Postgres cluster this repo starts must be created as UTF8.

``POSTGRES_INITDB_ARGS`` applies **only at initdb**. It cannot be retrofitted
onto a cluster that already exists, so the pin has to be present at the moment
the container is first started or the cluster is SQL_ASCII forever. That makes
this an easy invariant to add and an easy one to forget, because forgetting it
produces no error -- SQL_ASCII accepts every byte and only mis-counts them
afterwards.

The forgetting has now happened twice:

* the deployed Pi's ``template1`` (task #109), so every database created
  there inherited SQL_ASCII while the real database was UTF8;
* ``backend-nightly.yml`` (this file's reason for existing). The PR gates were
  pinned and the nightly was not, so the two jobs disagreed about what the
  database would accept. Every ``\\uXXXX`` escape above U+007F that the gates
  stored happily raised ``22P05 untranslatable_character`` in the nightly. On
  2026-08-13 that surfaced as an idempotency receipt failing to write, which
  turned a 409 replay into a 201 and failed a test that was correct about the
  code and wrong about nothing.

The check is deliberately crude -- a text scan rather than a YAML parse --
because the clusters are not all declared the same way. ``backend-ci.yml`` and
``backend-nightly.yml`` use a ``services:`` block, ``build-api-image.yml``
issues ``docker run`` inside a shell step, and ``docker-compose.yml`` is a
compose service. A parser tuned to one of those would silently skip the others,
which is the failure mode this file exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

#: The one Postgres image this repo starts. Pinned by tag in every caller
#: because the RDKit cartridge is required to migrate: the first revision
#: issues ``CREATE EXTENSION IF NOT EXISTS rdkit``.
POSTGRES_IMAGE = "informaticsmatters/rdkit-cartridge-debian"

#: What a correctly pinned cluster carries. ``LANG`` is what initdb reads for
#: its default locale; ``--encoding=UTF8`` is the explicit override. Both are
#: checked because the image sets no locale of its own.
INITDB_PIN = "POSTGRES_INITDB_ARGS"
ENCODING_VALUE = "--encoding=UTF8"

SEARCH_ROOTS = (
    REPO_ROOT / ".github" / "workflows",
    REPO_ROOT,
)


def _uncommented(text: str) -> list[str]:
    """Lines with comment-only lines removed.

    Both YAML and the shell inside a ``run:`` block comment with ``#``, and
    this file's own prose mentions the image and the pin by name. Counting raw
    occurrences would let a comment satisfy the assertion -- a check passing on
    the strength of a sentence about the check.
    """
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _files_that_start_a_cluster() -> list[Path]:
    seen: dict[Path, None] = {}
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        candidates = sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml"))
        for path in candidates:
            body = path.read_text(encoding="utf-8")
            if any(POSTGRES_IMAGE in line for line in _uncommented(body)):
                seen[path] = None
    return list(seen)


def test_the_scan_finds_the_clusters_we_know_about() -> None:
    """Guard the guard: a glob that matches nothing would pass vacuously.

    Every assertion below is parametrised over whatever this scan returns, so
    a scan that silently returned an empty list would report success while
    checking nothing -- the exact defect shape being guarded against.
    """
    found = {path.name for path in _files_that_start_a_cluster()}
    expected = {"backend-ci.yml", "backend-nightly.yml", "build-api-image.yml", "docker-compose.yml"}

    missing = expected - found
    assert not missing, (
        f"These files are known to start a Postgres cluster but the scan did not "
        f"find them: {sorted(missing)}. Either the image reference moved or the "
        f"scan is broken; do not delete them from the expected set to get green."
    )


@pytest.mark.parametrize(
    "path", _files_that_start_a_cluster(), ids=lambda p: p.name
)
def test_a_started_cluster_pins_utf8(path: Path) -> None:
    """A file that starts the Postgres image must pin the cluster encoding."""
    lines = _uncommented(path.read_text(encoding="utf-8"))
    body = "\n".join(lines)

    image_starts = sum(1 for line in lines if POSTGRES_IMAGE in line)
    pins = sum(1 for line in lines if INITDB_PIN in line)

    assert INITDB_PIN in body, (
        f"{path.relative_to(REPO_ROOT)} starts {POSTGRES_IMAGE} without "
        f"{INITDB_PIN}. This image sets no locale, so initdb falls back to "
        f"SQL_ASCII and the cluster will accept bytes the deployed UTF8 "
        f"database rejects. The pin applies only at initdb and cannot be added "
        f"to a cluster that already exists."
    )
    assert ENCODING_VALUE in body, (
        f"{path.relative_to(REPO_ROOT)} sets {INITDB_PIN} without "
        f"{ENCODING_VALUE!r}, so it pins something other than the encoding."
    )
    assert pins >= image_starts, (
        f"{path.relative_to(REPO_ROOT)} starts {image_starts} cluster(s) but "
        f"carries only {pins} {INITDB_PIN} setting(s). Every started cluster "
        f"needs its own pin; one pin does not cover two containers."
    )
