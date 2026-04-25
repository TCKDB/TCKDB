from app.schemas.entities.species_entry import SpeciesEntryCreate, SpeciesEntryUpdate


def test_species_entry_create_normalizes_identity_text_fields() -> None:
    schema = SpeciesEntryCreate(
        species_id=1,
        unmapped_smiles="  C=C  ",
        stereo_label="   ",
        electronic_state_label="  X  ",
        term_symbol_raw="  X^2Pi  ",
        term_symbol="  X2Pi  ",
        isotopologue_label="  13C  ",
    )

    assert schema.unmapped_smiles == "C=C"
    assert schema.stereo_label is None
    assert schema.electronic_state_label == "X"
    assert schema.term_symbol_raw == "X^2Pi"
    assert schema.term_symbol == "X2Pi"
    assert schema.isotopologue_label == "13C"


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
