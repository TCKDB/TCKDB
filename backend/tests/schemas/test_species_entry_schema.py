import pytest
from pydantic import ValidationError

from app.schemas.entities.species_entry import SpeciesEntryCreate, SpeciesEntryUpdate


def test_species_entry_create_normalizes_identity_text_fields() -> None:
    schema = SpeciesEntryCreate(
        species_id=1,
        unmapped_smiles="  C=C  ",
        stereo_label="   ",
        electronic_state_label="  X  ",
        term_symbol_raw="  X^2Pi  ",
        term_symbol="  X2Pi  ",
    )

    assert schema.unmapped_smiles == "C=C"
    assert schema.stereo_label is None
    assert schema.electronic_state_label == "X"
    assert schema.term_symbol_raw == "X^2Pi"
    assert schema.term_symbol == "X2Pi"


@pytest.mark.parametrize("schema_cls", [SpeciesEntryCreate, SpeciesEntryUpdate])
@pytest.mark.parametrize("field", ["isotopologue_label", "isotope_key"])
def test_species_entry_write_schemas_reject_isotope_identity_fields(
    schema_cls, field
) -> None:
    """Neither the retired free-text label nor the derived key is writable.

    ``isotopologue_label`` used to fork species identity from an arbitrary
    string; ``isotope_key`` is derived server-side from the SMILES. Neither
    may be supplied by a client.
    """

    with pytest.raises(ValidationError):
        schema_cls(species_id=1, **{field: "13C"})


def test_species_entry_update_normalizes_identity_text_fields() -> None:
    schema = SpeciesEntryUpdate(
        stereo_label="  R  ",
        electronic_state_label="   ",
        term_symbol="  A2Sigma+  ",
    )

    assert schema.stereo_label == "R"
    assert schema.electronic_state_label is None
    assert schema.term_symbol == "A2Sigma+"


def test_species_entry_schema_allows_stereo_label_without_stereo_kind() -> None:
    """stereo_kind is now on Species, so SpeciesEntry can have a label independently."""
    schema = SpeciesEntryCreate(
        species_id=1,
        stereo_label="R",
    )

    assert schema.stereo_label == "R"
