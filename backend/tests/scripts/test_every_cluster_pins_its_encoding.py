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

import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

#: The one Postgres image this repo starts, matched by repository basename so
#: the guard sees it under ANY registry. Today every caller uses
#: ``informaticsmatters/rdkit-cartridge-debian`` from Docker Hub; the follow-up
#: that moves CI to the GHCR copy (``ghcr.io/tckdb/rdkit-cartridge-debian@sha256:...``)
#: must keep matching, or this guard goes blind and passes having checked
#: nothing. Pinned by tag in every caller because the RDKit cartridge is
#: required to migrate: the first revision issues
#: ``CREATE EXTENSION IF NOT EXISTS rdkit``.
POSTGRES_IMAGE = "rdkit-cartridge-debian"

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


#: The ways this repo's files actually start a container: a ``services:`` or
#: compose ``image:`` key, or a ``docker run`` / ``docker create`` command.
#: Merely NAMING the image is not starting it: ``mirror-ci-images.yml`` lists
#: the image in a table to copy it between registries and never runs Postgres,
#: so demanding an initdb pin there would be a meaningless setting added to
#: satisfy a scan. Anything that does start the image, in any file, still
#: matches one of these shapes and is checked.
_IMAGE_KEY = re.compile(r"^\s*(?:-\s*)?image:")
_RUN_COMMAND = re.compile(r"\bdocker\s+(?:compose\s+)?(?:run|create)\b")


def _logical_lines(lines: list[str]) -> list[str]:
    """Join shell ``\\`` continuations, so a ``docker run`` whose image sits on a
    later line (as in ``build-api-image.yml``) is one line to inspect."""
    out: list[str] = []
    pending = ""
    for line in lines:
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        out.append(pending + line)
        pending = ""
    if pending:
        out.append(pending)
    return out


def _cluster_starts(text: str) -> list[str]:
    """Uncommented logical lines that start the Postgres image."""
    return [
        line
        for line in _logical_lines(_uncommented(text))
        if POSTGRES_IMAGE in line and (_IMAGE_KEY.match(line) or _RUN_COMMAND.search(line))
    ]


def _files_that_start_a_cluster() -> list[Path]:
    seen: dict[Path, None] = {}
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        candidates = sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml"))
        for path in candidates:
            body = path.read_text(encoding="utf-8")
            if _cluster_starts(body):
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


def test_a_file_that_only_names_the_image_is_not_a_cluster_start() -> None:
    """The mirror workflow copies the image; it must not be demanded a pin.

    Both halves matter. The real file must be exempt (it names the image but
    starts nothing), and the exemption must not be a blind spot: the same file
    with a ``docker run`` of the image added must be flagged.
    """
    mirror = REPO_ROOT / ".github" / "workflows" / "mirror-ci-images.yml"
    text = mirror.read_text(encoding="utf-8")
    assert any(POSTGRES_IMAGE in line for line in _uncommented(text)), (
        "mirror-ci-images.yml no longer names the image; this test would pass vacuously."
    )
    assert _cluster_starts(text) == []

    started = text + "\n          docker run -d ghcr.io/tckdb/rdkit-cartridge-debian@sha256:abc\n"
    assert len(_cluster_starts(started)) == 1
    continued = text + "\n          docker run -d --name x \\\n            ghcr.io/tckdb/rdkit-cartridge-debian:t\n"
    assert len(_cluster_starts(continued)) == 1
    assert len(_cluster_starts("    services:\n      pg:\n        image: ghcr.io/tckdb/rdkit-cartridge-debian@sha256:abc\n")) == 1


@pytest.mark.parametrize(
    "path", _files_that_start_a_cluster(), ids=lambda p: p.name
)
def test_a_started_cluster_pins_utf8(path: Path) -> None:
    """A file that starts the Postgres image must pin the cluster encoding."""
    lines = _uncommented(path.read_text(encoding="utf-8"))
    body = "\n".join(lines)

    image_starts = len(_cluster_starts(body))
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
