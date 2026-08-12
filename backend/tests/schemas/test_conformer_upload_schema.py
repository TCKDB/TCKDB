import pytest
from pydantic import ValidationError

from app.schemas.workflows.conformer_upload import ConformerUploadRequest


def test_conformer_upload_request_normalizes_nested_identity_fields() -> None:
    request = ConformerUploadRequest(
        species_entry={
            "smiles": " [H] ",
            "charge": 0,
            "multiplicity": 2,
            "stereo_label": "   ",
            "electronic_state_label": "  X  ",
            "term_symbol": "  X2S  ",
        },
        geometry={"xyz_text": " 1\ncomment\nH 0.0 0.0 0.0\n "},
        calculation={
            "type": "sp",
            "software_release": {"name": " Gaussian ", "version": " 16 "},
            "level_of_theory": {"method": " B3LYP ", "basis": " 6-31G(d) "},
        },
        note="  imported  ",
        label="  conf-a  ",
    )

    assert request.species_entry.smiles == "[H]"
    assert request.species_entry.stereo_label is None
    assert request.species_entry.electronic_state_label == "X"
    assert request.species_entry.term_symbol == "X2S"
    assert request.geometry.xyz_text == "1\ncomment\nH 0.0 0.0 0.0"
    assert request.note == "imported"
    assert request.label == "conf-a"


def test_conformer_upload_request_requires_calculation_provenance() -> None:
    with pytest.raises(ValidationError):
        ConformerUploadRequest(
            species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
            geometry={"xyz_text": "1\ncomment\nH 0.0 0.0 0.0"},
            calculation={
                "type": "sp",
                "level_of_theory": {"method": "B3LYP"},
            },
        )


def _minimal_statmech_payload(**overrides) -> dict:
    """Minimal valid statmech payload kwargs, plus overrides for drift tests."""
    base = {"statmech_treatment": "rrho"}
    base.update(overrides)
    return base


def _minimal_conformer_request(**statmech_overrides) -> dict:
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\ncomment\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "statmech": _minimal_statmech_payload(**statmech_overrides),
    }


def test_conformer_upload_statmech_rejects_raw_literature_id() -> None:
    """Regression: the conformer statmech payload must not accept a raw DB FK.

    If ``literature_id`` is reintroduced on ``ConformerUploadStatmechPayload``
    this test will silently pass — so we assert the strict Pydantic
    ``extra='forbid'`` rejection path that SchemaBase enforces.
    """
    payload = _minimal_conformer_request(literature_id=42)
    with pytest.raises(ValidationError) as exc_info:
        ConformerUploadRequest(**payload)
    assert "literature_id" in str(exc_info.value)


def test_conformer_upload_statmech_accepts_literature_submission_payload() -> None:
    """The canonical replacement is a nested ``LiteratureUploadRequest``."""
    payload = _minimal_conformer_request(
        literature={
            "kind": "article",
            "title": "Hydrogen atom energetics",
            "doi": "10.1234/h-atom",
        }
    )
    request = ConformerUploadRequest(**payload)
    assert request.statmech is not None
    assert request.statmech.literature is not None
    assert request.statmech.literature.doi == "10.1234/h-atom"


# ---------------------------------------------------------------------------
# Statmech source calculations: local keys, never row ids (#118, DR-0029 Req 1)
# ---------------------------------------------------------------------------


def _keyed_conformer_request(**statmech_overrides) -> dict:
    """A conformer upload that names its own calculations.

    The primary sp is ``h_sp`` and one additional freq is ``h_freq``, so
    statmech cross-references have something real to point at.
    """
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\ncomment\nH 0.0 0.0 0.0"},
        "calculation": {
            "key": "h_sp",
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "additional_calculations": [
            {
                "key": "h_freq",
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "freq_result": {"n_imag": 0},
            }
        ],
        "statmech": {"statmech_treatment": "rrho", **statmech_overrides},
    }


def _assert_field_forbidden(exc_info, field: str) -> None:
    """Assert the failure is specifically "this field may not be sent".

    Matching on ``str(exc_info.value)`` is not enough: Pydantic echoes the
    whole offending input into the message, so the field name appears in
    the text even when the model happily accepted it and failed for some
    unrelated reason. Only ``extra_forbidden`` on that exact location says
    the contract refuses the field.
    """
    offending = [
        error
        for error in exc_info.value.errors()
        if error["type"] == "extra_forbidden" and error["loc"][-1] == field
    ]
    assert offending, (
        f"expected an extra_forbidden error on '{field}', got "
        f"{[(e['type'], e['loc']) for e in exc_info.value.errors()]}"
    )


def test_statmech_source_calculation_rejects_raw_calculation_id() -> None:
    """The published contract must not accept a calculation primary key.

    A depositor cannot know one. Before #118 this field was
    ``calculation_id: int`` and was the only way to declare the link.
    """
    payload = _keyed_conformer_request(
        source_calculations=[{"calculation_id": 7, "role": "freq"}]
    )
    with pytest.raises(ValidationError) as exc_info:
        ConformerUploadRequest(**payload)
    _assert_field_forbidden(exc_info, "calculation_id")


def test_statmech_torsion_rejects_raw_source_scan_calculation_id() -> None:
    payload = _keyed_conformer_request(
        statmech_treatment="rrho_1d",
        torsions=[
            {
                "torsion_index": 1,
                "dimension": 1,
                "source_scan_calculation_id": 7,
                "coordinates": [
                    {
                        "coordinate_index": 1,
                        "atom1_index": 1,
                        "atom2_index": 2,
                        "atom3_index": 3,
                        "atom4_index": 4,
                    }
                ],
            }
        ],
    )
    with pytest.raises(ValidationError) as exc_info:
        ConformerUploadRequest(**payload)
    _assert_field_forbidden(exc_info, "source_scan_calculation_id")


def test_statmech_source_calculation_accepts_a_declared_key() -> None:
    request = ConformerUploadRequest(
        **_keyed_conformer_request(
            source_calculations=[{"calculation_key": "h_freq", "role": "freq"}]
        )
    )
    assert request.statmech is not None
    assert request.statmech.source_calculations[0].calculation_key == "h_freq"
    assert request.declared_calculation_keys() == ["h_sp", "h_freq"]


def test_statmech_source_calculation_key_must_be_declared() -> None:
    """An unresolvable key is a 422, not a silently dropped provenance link."""
    payload = _keyed_conformer_request(
        source_calculations=[{"calculation_key": "nowhere", "role": "freq"}]
    )
    with pytest.raises(ValidationError) as exc_info:
        ConformerUploadRequest(**payload)
    assert "does not name a calculation declared in this upload" in str(
        exc_info.value
    )


def test_torsion_source_scan_calculation_key_must_be_declared() -> None:
    payload = _keyed_conformer_request(
        statmech_treatment="rrho_1d",
        torsions=[
            {
                "torsion_index": 1,
                "dimension": 1,
                "source_scan_calculation_key": "nowhere",
                "coordinates": [
                    {
                        "coordinate_index": 1,
                        "atom1_index": 1,
                        "atom2_index": 2,
                        "atom3_index": 3,
                        "atom4_index": 4,
                    }
                ],
            }
        ],
    )
    with pytest.raises(ValidationError) as exc_info:
        ConformerUploadRequest(**payload)
    assert "source_scan_calculation_key" in str(exc_info.value)


def test_duplicate_calculation_keys_rejected() -> None:
    payload = _keyed_conformer_request()
    payload["additional_calculations"][0]["key"] = "h_sp"
    with pytest.raises(ValidationError) as exc_info:
        ConformerUploadRequest(**payload)
    assert "unique" in str(exc_info.value)


def test_calculation_keys_are_optional() -> None:
    """A payload with no statmech cross-references needs no keys at all."""
    request = ConformerUploadRequest(**_minimal_conformer_request())
    assert request.calculation.key is None
    assert request.declared_calculation_keys() == []


def test_duplicate_source_calculation_pairs_rejected() -> None:
    """(calculation_key, role) is the source link's primary key in the DB."""
    payload = _keyed_conformer_request(
        source_calculations=[
            {"calculation_key": "h_freq", "role": "freq"},
            {"calculation_key": "h_freq", "role": "freq"},
        ]
    )
    with pytest.raises(ValidationError) as exc_info:
        ConformerUploadRequest(**payload)
    assert "unique by (calculation_key, role)" in str(exc_info.value)


def test_primary_calculation_cannot_be_linked_twice() -> None:
    """``uploaded_calculation_role`` plus the same key/role is one PK, twice."""
    payload = _keyed_conformer_request(
        uploaded_calculation_role="sp",
        source_calculations=[{"calculation_key": "h_sp", "role": "sp"}],
    )
    with pytest.raises(ValidationError) as exc_info:
        ConformerUploadRequest(**payload)
    assert "uploaded_calculation_role already links" in str(exc_info.value)


def test_primary_calculation_may_be_linked_under_a_different_role() -> None:
    """A distinct role is a distinct row, so it is allowed."""
    request = ConformerUploadRequest(
        **_keyed_conformer_request(
            uploaded_calculation_role="sp",
            source_calculations=[{"calculation_key": "h_sp", "role": "composite"}],
        )
    )
    assert request.statmech is not None
    assert len(request.statmech.source_calculations) == 1


def test_duplicate_torsion_indices_rejected() -> None:
    coordinates = [
        {
            "coordinate_index": 1,
            "atom1_index": 1,
            "atom2_index": 2,
            "atom3_index": 3,
            "atom4_index": 4,
        }
    ]
    payload = _keyed_conformer_request(
        statmech_treatment="rrho_1d",
        torsions=[
            {"torsion_index": 1, "dimension": 1, "coordinates": coordinates},
            {"torsion_index": 1, "dimension": 1, "coordinates": coordinates},
        ],
    )
    with pytest.raises(ValidationError) as exc_info:
        ConformerUploadRequest(**payload)
    assert "torsion_index values must be unique" in str(exc_info.value)
