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


#: Files that NAME the image without ever starting it, and why. The exemption
#: is by file name, never by line shape: a shape-based test can only exempt the
#: spellings its author thought of, and every spelling it did not think of
#: (a matrix value, a variable, a folded ``run: >-`` block) would then be
#: silently ignored. Here an unrecognised mention in any OTHER file is still
#: treated as a possible start (and must carry the pin), and
#: ``test_every_mention_is_a_recognised_start_or_in_an_exempt_file`` fails
#: loudly, telling the author to add the shape or exempt the file.
#:
#: Starts are still counted inside an exempt file, so a ``docker run`` of the
#: image added to one of these is caught and held to the pin like any other.
NAMES_ONLY = {
    "mirror-ci-images.yml": (
        "copies the image between registries by digest (imagetools create) and "
        "never runs Postgres, so an initdb pin there would be meaningless"
    ),
}

#: Shapes that start a container from the image. Known gap, deliberately not
#: handled: YAML anchors and aliases (``&pg image: ...`` / ``image: *pg``) are
#: resolved by the YAML loader and are invisible to a text scan; the first line
#: still names the image and is caught by the mention rule above, but a use
#: site that carries only the alias is not.
_IMAGE_KEY = re.compile(r"^\s*(?:-\s*)?image:")
_JOB_CONTAINER = re.compile(r"^\s*container:\s*\S")
_USES_DOCKER = re.compile(r"\buses:\s*docker://")
#: ``docker run``, ``docker container run``, ``docker create``, global flags
#: before the verb (``docker --context x run``), ``docker compose run``, and
#: the drop-in engines. ``\s`` after the engine name keeps ``docker.io/...``
#: and ``docker buildx imagetools create`` from matching.
_RUN_COMMAND = re.compile(
    r"\b(?:docker|podman|nerdctl)\s+(?:-{1,2}[\w-]+(?:[= ]\S+)?\s+)*(?:container\s+|compose\s+)?(?:run|create)\b"
)


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


def _mentions(text: str) -> list[str]:
    """Uncommented logical lines that name the image at all."""
    return [line for line in _logical_lines(_uncommented(text)) if POSTGRES_IMAGE in line]


def _is_recognised_start(line: str) -> bool:
    return bool(
        _IMAGE_KEY.match(line) or _JOB_CONTAINER.match(line) or _USES_DOCKER.search(line) or _RUN_COMMAND.search(line)
    )


def _cluster_starts(text: str) -> list[str]:
    """Mentions that match a known way of starting a container."""
    return [line for line in _mentions(text) if _is_recognised_start(line)]


def _claimed_starts(name: str, text: str) -> list[str]:
    """What the pin test must hold this file to.

    An exempt file is held only to its recognised starts. Every other file is
    held to ALL of its mentions: over-inclusive on purpose, because the failure
    to avoid is a started cluster the scan did not see.
    """
    return _cluster_starts(text) if name in NAMES_ONLY else _mentions(text)


def _files_naming_the_image() -> list[Path]:
    seen: dict[Path, None] = {}
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        candidates = sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml"))
        for path in candidates:
            if _mentions(path.read_text(encoding="utf-8")):
                seen[path] = None
    return list(seen)


def _files_that_start_a_cluster() -> list[Path]:
    return [p for p in _files_naming_the_image() if _claimed_starts(p.name, p.read_text(encoding="utf-8"))]


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


@pytest.mark.parametrize("path", _files_naming_the_image(), ids=lambda p: p.name)
def test_every_mention_is_a_recognised_start_or_in_an_exempt_file(path: Path) -> None:
    """A mention no shape recognises must fail loudly, not be assumed harmless."""
    text = path.read_text(encoding="utf-8")
    unrecognised = [line.strip() for line in _mentions(text) if not _is_recognised_start(line)]
    if path.name in NAMES_ONLY:
        return
    assert not unrecognised, (
        f"{path.relative_to(REPO_ROOT)} names {POSTGRES_IMAGE} in a form this scan does not "
        f"recognise as starting a container: {unrecognised}. Add the shape to the scan, or, "
        f"if the file only names the image (copies it, documents it), add it to NAMES_ONLY "
        f"with the reason."
    )


def test_a_file_that_only_names_the_image_is_not_a_cluster_start() -> None:
    """The mirror workflow copies the image; it must not be demanded a pin.

    Both halves matter. The real file must be exempt (it names the image but
    starts nothing), and the exemption must not be a blind spot: the same file
    with a start of the image added must still be flagged.
    """
    mirror = REPO_ROOT / ".github" / "workflows" / "mirror-ci-images.yml"
    assert mirror.name in NAMES_ONLY
    text = mirror.read_text(encoding="utf-8")
    assert _mentions(text), "mirror-ci-images.yml no longer names the image; this test would pass vacuously."
    assert _claimed_starts(mirror.name, text) == []

    for added in (
        "          docker run -d ghcr.io/tckdb/rdkit-cartridge-debian@sha256:abc\n",
        "          docker run -d --name x \\\n            ghcr.io/tckdb/rdkit-cartridge-debian:t\n",
    ):
        assert len(_claimed_starts(mirror.name, text + "\n" + added)) == 1


@pytest.mark.parametrize(
    "snippet",
    [
        "docker run -d ghcr.io/tckdb/rdkit-cartridge-debian@sha256:abc",
        "docker container run -d ghcr.io/tckdb/rdkit-cartridge-debian:t",
        "docker create ghcr.io/tckdb/rdkit-cartridge-debian:t",
        "docker --context remote run -d ghcr.io/tckdb/rdkit-cartridge-debian:t",
        "podman run -d ghcr.io/tckdb/rdkit-cartridge-debian:t",
        "nerdctl run -d ghcr.io/tckdb/rdkit-cartridge-debian:t",
        "    image: ghcr.io/tckdb/rdkit-cartridge-debian@sha256:abc",
        "    - image: ghcr.io/tckdb/rdkit-cartridge-debian:t",
        "    container: ghcr.io/tckdb/rdkit-cartridge-debian:t",
        "      - uses: docker://ghcr.io/tckdb/rdkit-cartridge-debian:t",
    ],
)
def test_the_start_shapes_are_recognised(snippet: str) -> None:
    assert len(_cluster_starts(snippet + "\n")) == 1


def test_an_unanticipated_shape_fails_loudly_in_a_non_exempt_file() -> None:
    """A matrix value is a real start no shape recognises; the net must catch it."""
    text = "matrix:\n  pg: [ghcr.io/tckdb/rdkit-cartridge-debian:t]\nimage: ${{ matrix.pg }}\n"
    assert _cluster_starts(text) == []  # not recognised as a start ...
    assert len(_claimed_starts("some-new-workflow.yml", text)) == 1  # ... but still held to the pin
    assert [line for line in _mentions(text) if not _is_recognised_start(line)]  # ... and flagged


def test_the_mirror_table_and_copy_commands_are_not_starts() -> None:
    assert _cluster_starts(
        "ghcr.io/tckdb/rdkit-cartridge-debian:t docker.io/informaticsmatters/rdkit-cartridge-debian sha256:abc\n"
        "docker buildx imagetools create --tag ghcr.io/tckdb/rdkit-cartridge-debian:t x\n"
    ) == []


@pytest.mark.parametrize("path", _files_that_start_a_cluster(), ids=lambda p: p.name)
def test_a_started_cluster_pins_utf8(path: Path) -> None:
    """A file that starts the Postgres image must pin the cluster encoding."""
    lines = _uncommented(path.read_text(encoding="utf-8"))
    body = "\n".join(lines)

    image_starts = len(_claimed_starts(path.name, body))
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
