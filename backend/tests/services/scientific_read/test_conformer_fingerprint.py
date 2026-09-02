"""Unit tests for ``_build_group_fingerprint`` (scientific_read/conformers.py).

This is the one place that zips ``conformer_group.representative_fingerprint_json``'s
four parallel arrays (``canonical_rotor_keys`` / ``quantized_bins`` /
``raw_torsions_deg`` / ``folded_torsions_deg``) into paired
``ConformerRotorTorsion`` rows. A mistake here — a reversed array, an
off-by-one, a silently dropped rotor — attributes an angle to the wrong
bond, which the ticket calls out as worse than surfacing nothing. These
tests exercise the zip directly with a plain stand-in object (no DB
session needed: the function only reads one attribute), rather than only
through the API, so the pairing behaviour is pinned at its exact source.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scientific_read.conformers import _build_group_fingerprint

# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------


def _cg(representative_fingerprint_json):
    """Bare stand-in: `_build_group_fingerprint` only reads this attribute."""
    return SimpleNamespace(
        representative_fingerprint_json=representative_fingerprint_json
    )


def test_parses_the_measured_three_group_species_shape():
    """Shape and values lifted verbatim from the live-archive measurement
    (the 3-group species' first group), including the trailing
    ``fingerprint_hash`` the row carries and this function must drop."""
    cg = _cg({
        "rotor_count": 2,
        "bin_width_deg": 15,
        "quantized_bins": [23, 3],
        "raw_torsions_deg": [359.9994, 59.8254],
        "folded_torsions_deg": [359.9994, 59.8254],
        "canonical_rotor_keys": ["R_8_10", "R_9_10"],
        "fingerprint_hash": "5453bcc5",
    })
    fp = _build_group_fingerprint(cg)
    assert fp is not None
    assert fp.rotor_count == 2
    assert fp.bin_width_deg == 15
    assert len(fp.torsions) == 2
    assert fp.torsions[0].rotor_key == "R_8_10"
    assert fp.torsions[0].quantized_bin == 23
    assert fp.torsions[0].raw_torsion_deg == 359.9994
    assert fp.torsions[0].folded_torsion_deg == 359.9994
    assert fp.torsions[1].rotor_key == "R_9_10"
    assert fp.torsions[1].quantized_bin == 3
    assert fp.torsions[1].raw_torsion_deg == 59.8254
    assert fp.torsions[1].folded_torsion_deg == 59.8254
    # fingerprint_hash never reaches the returned shape at all -- not as a
    # field, not stashed anywhere reachable from the result.
    assert not hasattr(fp, "fingerprint_hash")
    assert "fingerprint_hash" not in fp.model_dump()


def test_sibling_group_with_different_bins_produces_different_values():
    """The prompt's sibling group: same two rotor keys, different bins and
    angles. Proves the function does not hardcode or memoize a single
    group's numbers."""
    sibling = _cg({
        "rotor_count": 2,
        "bin_width_deg": 15,
        "quantized_bins": [14, 4],
        "raw_torsions_deg": [224.1937, 60.4643],
        "folded_torsions_deg": [224.1937, 60.4643],
        "canonical_rotor_keys": ["R_8_10", "R_9_10"],
        "fingerprint_hash": "different-hash",
    })
    fp = _build_group_fingerprint(sibling)
    assert fp is not None
    assert fp.torsions[0].quantized_bin == 14
    assert fp.torsions[0].raw_torsion_deg == 224.1937
    assert fp.torsions[1].quantized_bin == 4
    assert fp.torsions[1].raw_torsion_deg == 60.4643


def test_pairing_survives_non_sorted_rotor_key_order():
    """Rotor keys need not be sorted; positional pairing across all four
    arrays is what matters, not any ordering of the keys themselves."""
    cg = _cg({
        "rotor_count": 3,
        "bin_width_deg": 10,
        "quantized_bins": [7, 1, 30],
        "raw_torsions_deg": [70.5, 12.2, 305.9],
        "folded_torsions_deg": [70.5, 12.2, 305.9],
        "canonical_rotor_keys": ["R_9_10", "R_1_2", "R_20_21"],
    })
    fp = _build_group_fingerprint(cg)
    assert fp is not None
    by_key = {t.rotor_key: t for t in fp.torsions}
    assert by_key["R_9_10"].quantized_bin == 7
    assert by_key["R_9_10"].raw_torsion_deg == 70.5
    assert by_key["R_1_2"].quantized_bin == 1
    assert by_key["R_1_2"].raw_torsion_deg == 12.2
    assert by_key["R_20_21"].quantized_bin == 30
    assert by_key["R_20_21"].raw_torsion_deg == 305.9


def test_rotor_count_falls_back_to_key_count_when_missing_or_wrong_type():
    cg = _cg({
        "bin_width_deg": 15,
        "quantized_bins": [1, 2],
        "raw_torsions_deg": [1.0, 2.0],
        "folded_torsions_deg": [1.0, 2.0],
        "canonical_rotor_keys": ["R_1_2", "R_3_4"],
        # rotor_count omitted entirely
    })
    fp = _build_group_fingerprint(cg)
    assert fp is not None
    assert fp.rotor_count == 2


# ---------------------------------------------------------------------------
# Defensive / negative cases -- absence over a specious pairing
# ---------------------------------------------------------------------------


def test_returns_none_when_blob_is_none():
    assert _build_group_fingerprint(_cg(None)) is None


def test_returns_none_when_blob_is_empty_dict():
    assert _build_group_fingerprint(_cg({})) is None


def test_returns_none_when_arrays_have_mismatched_lengths():
    """A row whose arrays disagree on length is malformed -- never zip a
    partial pairing and call it an answer."""
    cg = _cg({
        "rotor_count": 2,
        "bin_width_deg": 15,
        "quantized_bins": [23, 3],
        "raw_torsions_deg": [359.9994],  # one short
        "folded_torsions_deg": [359.9994, 59.8254],
        "canonical_rotor_keys": ["R_8_10", "R_9_10"],
    })
    assert _build_group_fingerprint(cg) is None


def test_returns_none_when_bin_width_deg_missing():
    cg = _cg({
        "rotor_count": 1,
        "quantized_bins": [3],
        "raw_torsions_deg": [59.8],
        "folded_torsions_deg": [59.8],
        "canonical_rotor_keys": ["R_9_10"],
    })
    assert _build_group_fingerprint(cg) is None


def test_returns_none_when_rotor_keys_array_is_empty():
    cg = _cg({
        "rotor_count": 0,
        "bin_width_deg": 15,
        "quantized_bins": [],
        "raw_torsions_deg": [],
        "folded_torsions_deg": [],
        "canonical_rotor_keys": [],
    })
    assert _build_group_fingerprint(cg) is None


def test_returns_none_when_an_array_field_is_not_a_list():
    cg = _cg({
        "rotor_count": 1,
        "bin_width_deg": 15,
        "quantized_bins": "not-a-list",
        "raw_torsions_deg": [59.8],
        "folded_torsions_deg": [59.8],
        "canonical_rotor_keys": ["R_9_10"],
    })
    assert _build_group_fingerprint(cg) is None
