"""Tests for the CCCBDB quadrupole property table (quadlistx.asp).

Quadrupole is the project's first ``workflow_ready=False`` target:
the parser pulls every row, but the builder emits no
``MolecularPropertyObservationCreate`` payload because the page
publishes only the diagonal traceless tensor (xx/yy/zz) — there is
no scientifically safe scalar to ship.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.importers.cccbdb.crawl_plan import EXPERIMENTAL_PROPERTIES_PILOT
from app.importers.cccbdb.parsers import parse_experimental_property_table_page
from app.importers.cccbdb.parsers.experimental_property_table import (
    PROPERTY_CONFIGS,
)
from app.importers.cccbdb.property_payload_dryrun import run_payload_dryrun

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)


def _populate_quadrupole_cache(archive_dir: Path) -> None:
    raw_html = archive_dir / "raw_html"
    raw_html.mkdir(parents=True, exist_ok=True)
    src = FIXTURES_DIR / "property_quadrupole.html"
    content = src.read_text(encoding="utf-8")
    sha12 = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    # Filename must match the CrawlTarget's species_key
    # ("quadrupole_moment"), not the fixture file name.
    (raw_html / f"property_quadrupole_moment_{sha12}.html").write_text(content)


# ---------------------------------------------------------------------------
# Config + CrawlTarget shape
# ---------------------------------------------------------------------------


def test_quadrupole_config_is_tensor_only():
    cfg = PROPERTY_CONFIGS["quadrupole_moment"]
    # The tensor-only contract: no scalar value column, and an
    # explicit list of component columns the parser preserves.
    assert cfg.value_column is None
    assert cfg.tensor_component_columns == ("xx", "yy", "zz")
    assert cfg.default_raw_unit == "Debye*Angstrom"


def test_quadrupole_crawltarget_is_workflow_not_ready():
    target = next(
        t
        for t in EXPERIMENTAL_PROPERTIES_PILOT
        if t.property_kind == "quadrupole_moment"
    )
    assert target.workflow_ready is False
    assert "quadlistx.asp" in target.source_url


# ---------------------------------------------------------------------------
# Parser shape
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parsed_table():
    html = (FIXTURES_DIR / "property_quadrupole.html").read_text(
        encoding="utf-8"
    )
    return parse_experimental_property_table_page(
        html,
        property_kind="quadrupole_moment",
        source_url="https://cccbdb.nist.gov/quadlistx.asp",
    )


def test_quadrupole_parses_nonzero_rows(parsed_table):
    assert len(parsed_table.rows) > 0
    assert parsed_table.title == "Experimental Quadrupoles"
    assert parsed_table.raw_units == "Bohr^3" or \
        parsed_table.raw_units == "Debye*Angstrom" or \
        "Debye" in parsed_table.raw_units
    # No "value column not found" warning when value_column is None.
    assert not any(
        "value column" in w for w in parsed_table.warnings
    )


def test_quadrupole_headers_are_meaningful(parsed_table):
    assert "Molecule" in parsed_table.column_names
    assert "name" in parsed_table.column_names
    assert "xx" in parsed_table.column_names
    assert "yy" in parsed_table.column_names
    assert "zz" in parsed_table.column_names


def test_quadrupole_first_row_identity_and_components(parsed_table):
    first = parsed_table.rows[0]
    assert first.formula  # H2CO normalized
    assert first.name  # "Formaldehyde"
    # No scalar value — tensor-only.
    assert first.value is None
    assert first.normalized_value is None
    # The xx/yy/zz components are preserved in raw_row.
    assert first.raw_row["xx"] == "-0.270"
    assert first.raw_row["yy"] == "0.330"
    assert first.raw_row["zz"] == "-0.060"
    # Reference squib survives.
    assert first.reference is not None
    assert first.reference.reference_label  # "1974Hel/Hel(II/6)"


def test_quadrupole_raw_row_preserves_all_columns(parsed_table):
    for row in parsed_table.rows:
        for col in ("Molecule", "name", "xx", "yy", "zz", "squib"):
            assert col in row.raw_row, (
                f"row {row.row_index} dropped column {col!r}"
            )


def test_quadrupole_comment_column_preserved(parsed_table):
    h2co = next(r for r in parsed_table.rows if r.raw_row["Molecule"] == "H2CO")
    # The "commment" (sic) column is mapped to reference_comment.
    assert h2co.reference is not None
    assert h2co.reference.reference_comment is not None
    assert "aa=" in h2co.reference.reference_comment


# ---------------------------------------------------------------------------
# Dry-run health gate
# ---------------------------------------------------------------------------


def test_quadrupole_dryrun_emits_no_payloads_and_is_quarantined(tmp_path):
    archive = tmp_path / "archive"
    out = tmp_path / "out"
    _populate_quadrupole_cache(archive)

    summary = run_payload_dryrun(
        archive_dir=archive,
        output_dir=out,
        property_kinds=("quadrupole_moment",),
        use_cache_only=True,
    )

    data = json.loads((out / "quadrupole_moment.json").read_text())
    assert data["parsed_row_count"] > 0
    assert data["payload_count"] == 0
    assert data["workflow_ready"] is False
    assert data["health"] == "quarantined"
    assert "workflow_ready=False" in (data["health_reason"] or "")

    # Aggregate gate: parsed-but-no-payloads is NOT counted as
    # unhealthy when workflow_ready=False.
    assert summary.unhealthy_count == 0
    assert summary.quarantined_count == 1


def test_default_pilot_unhealthy_count_remains_zero(tmp_path):
    """Sanity: introducing the quadrupole target must NOT push the
    aggregate dry-run into the unhealthy bucket. Tensor targets
    are quarantined by design."""

    archive = tmp_path / "archive"
    out = tmp_path / "out"
    # Populate the full pilot from fixtures.
    from importers.cccbdb.test_property_payload_dryrun import (
        _populate_cache,
    )

    _populate_cache(archive)
    summary = run_payload_dryrun(
        archive_dir=archive, output_dir=out, use_cache_only=True
    )
    assert summary.unhealthy_count == 0
    assert summary.quarantined_count == 1
    assert "quadrupole_moment" in summary.health_summary
    assert summary.health_summary["quadrupole_moment"] == "quarantined"
