"""The method-family classifier shipped in the package: its answers for a fixed list of method names."""

from __future__ import annotations

import pytest

from tckdb_schemas.method_families import (
    CLASSIFICATION,
    CLASSIFICATION_NAME,
    CLASSIFICATION_REF,
    CLASSIFICATION_VERSION,
    MethodClassFilter,
    MethodFamily,
    classify_method,
    method_stem_key,
)
from tckdb_schemas.method_names import method_identity_key

F = MethodFamily

# (name, family, dft_rung). Expected values were fixed for the classifier.
PARITY = [
    ("dlpno-ccsd(t)-f12", F.wavefunction, None),
    ("ccsd(t)-f12/ri", F.unclassified, None),
    ("b3lyp", F.dft, 4),
    ("B3LYP", F.dft, 4),
    ("g4", F.composite, None),
    ("caspt2", F.multireference, None),
    ("mrci+q", F.multireference, None),
    ("hf", F.wavefunction, None),
    ("mp2-f12", F.wavefunction, None),
    ("ccsd(t)-f12", F.wavefunction, None),
    ("wb97x-d", F.dft, 4),
    ("b97-d3", F.dft, 2),
    ("b97", F.dft, 4),
    ("m06-2x-d3zero", F.dft, 4),
    ("b3lyp-d3(bj)", F.dft, 4),
    ("uccsd", F.wavefunction, None),
    ("b2plyp", F.dft, 5),
    ("pbe", F.dft, 2),
    ("tpss", F.dft, 3),
    ("cbsqb3", F.composite, None),
    ("gfn2-xtb", F.semiempirical, None),
    ("uff", F.force_field, None),
    ("ci", F.unclassified, None),
    ("xyg3", F.unclassified, None),
    ("", F.unclassified, None),
    (None, F.unclassified, None),
]


@pytest.mark.parametrize(("name", "family", "rung"), PARITY, ids=lambda v: repr(v))
def test_classification_is_unchanged(name, family, rung):
    result = classify_method(name)
    assert (result.family, result.dft_rung) == (family, rung)


def test_the_classifier_reads_only_the_package():
    assert method_stem_key("B97-D3") == "b97-d3"
    assert method_identity_key(" M06-2X ") == "m062x"
    assert (CLASSIFICATION_REF.name, CLASSIFICATION_REF.version) == (CLASSIFICATION_NAME, CLASSIFICATION_VERSION)
    assert classify_method("b3lyp") is CLASSIFICATION["b3lyp"]


def test_the_filter_matches_through_the_same_table():
    dft = MethodClassFilter.of([F.dft], 4, 4)
    assert dft.matches("b3lyp")
    assert not dft.matches("pbe")
    assert not dft.matches("hf")
