"""The ``LevelOfTheory`` builder sends the typed method qualifiers and has no free-text ``keywords``."""

from __future__ import annotations

import pytest
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from tckdb_client.builders import LevelOfTheory


def test_the_qualifiers_are_sent_only_when_set_and_validate_against_the_wire_schema():
    full = LevelOfTheory(
        method="DLPNO-CCSD(T)",
        basis="cc-pVTZ",
        core_treatment="window",
        relativistic_hamiltonian="dkh2",
        pno_truncation="tight",
        ri_approximation="rijcosx",
    ).to_payload()
    assert full["relativistic_hamiltonian"] == "dkh2" and full["core_treatment"] == "window"
    ref = LevelOfTheoryRef(**full)
    assert ref.pno_truncation is not None and ref.ri_approximation is not None
    bare = LevelOfTheory(method="b3lyp", basis="def2-tzvp").to_payload()
    assert set(bare) == {"method", "basis"}


def test_there_is_no_keywords_argument():
    with pytest.raises(TypeError):
        LevelOfTheory(method="b3lyp", keywords="Opt")  # type: ignore[call-arg]


def test_an_empty_qualifier_is_refused_by_the_builder():
    with pytest.raises(Exception):
        LevelOfTheory(method="b3lyp", ri_approximation="  ")
