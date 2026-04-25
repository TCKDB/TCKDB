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
