"""State labels and term symbols are read with the write-time grammar, never raising."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.scientific_read.species_identity import (
    entry_facets_of,
    public_electronic_state_label,
    public_term_symbol,
)


@pytest.mark.parametrize(("stored", "served"), [("T1", "T1"), ("S1", "S1"), ("X", "X"), ("A~", "\u00c3"), ("E", "E")])
def test_a_valid_label_is_served_in_its_canonical_spelling(stored: str, served: str) -> None:
    assert public_electronic_state_label(stored) == served


@pytest.mark.parametrize(
    "stored", ["R", "S", "Z", "M", "ground", "DEPOSITOR_SENTINEL", "[2H]C", "S100", "", "   ", None]
)
def test_a_label_outside_the_grammar_reads_none(stored) -> None:
    assert public_electronic_state_label(stored) is None


@pytest.mark.parametrize(
    ("stored", "served"),
    [("2Pi", "²Π"), ("²Π", "²Π"), ("X2Pi", "X ²Π"), ("3Sigma_g-", "³Σg⁻"), ("1A1", "¹A₁")],
)
def test_a_valid_term_symbol_is_served_canonical(stored: str, served: str) -> None:
    assert public_term_symbol(stored) == served


@pytest.mark.parametrize("stored", ["DEPOSITOR_SENTINEL", "T1", "hello world", "R 2Pi", "", None])
def test_a_term_symbol_outside_the_grammar_reads_none(stored) -> None:
    assert public_term_symbol(stored) is None


def test_entry_facets_carry_only_grammar_valid_state_text() -> None:
    def row(label, term):
        return SimpleNamespace(
            kind="minimum",
            electronic_state_kind="excited",
            stereo_descriptor=None,
            stereo_resolution=None,
            isotope_key=None,
            electronic_state_label=label,
            term_symbol=term,
        )

    served = entry_facets_of(row("T1", "3B1"))
    assert (served["electronic_state_label"], served["term_symbol"]) == ("T1", "³B₁")
    legacy = entry_facets_of(row("R", "DEPOSITOR_SENTINEL"))
    assert (legacy["electronic_state_label"], legacy["term_symbol"]) == (None, None)
