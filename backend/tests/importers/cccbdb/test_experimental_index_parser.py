"""Tests for the CCCBDB Experimental index/discovery parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers.cccbdb.parsers.experimental_index import (
    ExperimentalIndex,
    ExperimentalIndexLink,
    parse_experimental_index_page,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)


@pytest.fixture(scope="module")
def index() -> ExperimentalIndex:
    html = (FIXTURES_DIR / "experimental_index_exp2x.html").read_text(
        encoding="utf-8"
    )
    return parse_experimental_index_page(
        html, source_url="https://cccbdb.nist.gov/exp2x.asp"
    )


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_at_least_one_link_discovered(index: ExperimentalIndex):
    assert len(index.links) > 0
    assert not index.warnings


def test_every_link_has_experimental_root_section(index: ExperimentalIndex):
    """All discovered links must sit under the ``Experimental``
    breadcrumb root — the parser should stop at the
    ``Calculated`` sibling."""

    for link in index.links:
        assert link.section_path[0] == "Experimental", (
            f"link spilled into a non-Experimental section: "
            f"{link.section_path} {link.label}"
        )


def test_no_calculated_section_leakage(index: ExperimentalIndex):
    """The walker must terminate at the end of the Experimental
    sub-tree. None of the well-known Calculated-only pages
    (``energy1x.asp``, ``vibs1x.asp``, …) should appear unless
    they are also linked from the Experimental side."""

    hrefs = [link.href for link in index.links]
    # ``energy1x.asp`` is Experimental in one place (Energy section)
    # so we don't blanket-exclude it. Instead spot-check pages that
    # only exist on the Calculated side.
    forbidden = {"rotbar1x.asp", "rbc1x.asp", "stgap1x.asp"}
    assert not forbidden.intersection(hrefs)


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


def test_relative_hrefs_resolved_to_absolute(index: ExperimentalIndex):
    for link in index.links:
        assert link.absolute_url.startswith("https://cccbdb.nist.gov/"), (
            f"href {link.href!r} did not resolve absolutely: "
            f"{link.absolute_url!r}"
        )


# ---------------------------------------------------------------------------
# Target guess
# ---------------------------------------------------------------------------


def test_known_property_targets_are_guessed(index: ExperimentalIndex):
    """The well-known data-page links (``hf0kx.asp``, ``goodlistx.asp``,
    ``diplistx.asp``, ``pollistx.asp``, ``expdiatomicsx.asp``) must
    each be tagged with the expected property_kind token."""

    by_href = {link.href: link for link in index.links}
    expected = {
        "hf0kx.asp": "hf_0",
        "goodlistx.asp": "hf_0_with_uncertainty",
        "diplistx.asp": "dipole",
        "pollistx.asp": "polarizability_iso",
        "expdiatomicsx.asp": "diatomic_spectroscopic",
    }
    for href, want in expected.items():
        assert href in by_href, f"missing experimental link: {href}"
        assert by_href[href].target_guess == want


# ---------------------------------------------------------------------------
# Section path
# ---------------------------------------------------------------------------


def test_polarizability_under_electrostatics(index: ExperimentalIndex):
    poll = next(link for link in index.links if link.href == "pollistx.asp")
    assert "Electrostatics" in poll.section_path
    assert poll.label == "Polarizability"


def test_goodlist_under_reference_data(index: ExperimentalIndex):
    goodlist = next(
        link for link in index.links if link.href == "goodlistx.asp"
    )
    assert "Reference Data" in goodlist.section_path
    assert goodlist.label == "Molecules with good enthalpy"


# ---------------------------------------------------------------------------
# JSON shape
# ---------------------------------------------------------------------------


def test_link_to_json_shape(index: ExperimentalIndex):
    sample: ExperimentalIndexLink = index.links[0]
    payload = sample.to_json()
    assert set(payload.keys()) == {
        "section_path",
        "label",
        "href",
        "absolute_url",
        "target_guess",
    }


def test_empty_html_emits_warning():
    idx = parse_experimental_index_page(
        "<html><body>nothing here</body></html>",
        source_url="https://cccbdb.nist.gov/exp2x.asp",
    )
    assert idx.links == []
    assert idx.warnings
