"""A declared ``energy_level_of_theory`` is stored and read back (#619).

It used to be resolved, checked by ``assert_role_consistency`` and thrown
away. It is now persisted on ``thermo`` and ``statmech`` as
``energy_level_of_theory_id`` and reported as ``levels.declared_energy``.

``levels.energy`` is still re-derived from the linked calculations on every
read; these tests keep the two apart. The declaration is a stored claim, and
it is never back-filled when the depositor did not make one.

Every write path is exercised, because each builds the row itself:
standalone statmech, standalone thermo, the computed-species bundle (thermo
and statmech) and the computed-reaction bundle (thermo and statmech).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from tests.api.test_api_statmech_thermo_levels import (
    _LOT_A,
    _LOT_B,
    _SOFTWARE,
    _bundle_conformer,
    _computed_species_bundle_payload,
    _methyl_xyz,
    _opt_calc_at,
    _sp_calc_at,
    _standalone_statmech_payload,
    _thermo_payload,
)
from tests.services.scientific_read._factories import (
    make_lot,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
)


def _statmech_levels(client, statmech_handle) -> dict:
    detail = client.get(f"/api/v1/scientific/statmech/{statmech_handle}")
    assert detail.status_code == 200, detail.text
    return detail.json()["record"]["levels"]


def _thermo_levels(client, species_entry_id: int) -> dict:
    read = client.get(f"/api/v1/scientific/species-entries/{species_entry_id}/thermo")
    assert read.status_code == 200, read.text
    (record,) = read.json()["records"]
    return record["levels"]


# ---------------------------------------------------------------------------
# Standalone statmech
# ---------------------------------------------------------------------------


def _opt_sp_statmech(smiles: str, **overrides) -> dict:
    return _standalone_statmech_payload(
        species_entry={"smiles": smiles, "charge": 0, "multiplicity": 1},
        calculations=[
            {"key": "opt0", "calculation": _opt_calc_at(_LOT_A)},
            {"key": "sp0", "calculation": _sp_calc_at(_LOT_B)},
        ],
        source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
        ],
        **overrides,
    )


def test_statmech_declared_energy_level_is_stored_and_read(client, db_session):
    resp = client.post(
        "/api/v1/uploads/statmech",
        json=_opt_sp_statmech("CCCCCCCCCCCCCCCCC", energy_level_of_theory=_LOT_B),
    )
    assert resp.status_code == 201, resp.text

    row = db_session.get(Statmech, resp.json()["id"])
    assert row.energy_level_of_theory_id is not None
    declared = db_session.get(LevelOfTheory, row.energy_level_of_theory_id)
    assert (declared.method, declared.basis) == ("wB97X-D", "def2-TZVP")

    levels = _statmech_levels(client, resp.json()["id"])
    assert levels["declared_energy"]["method"] == "wB97X-D"
    assert levels["declared_energy"]["level_of_theory_ref"] == declared.public_ref
    # Derived and declared are separate answers that happen to agree here.
    assert levels["energy_source"] == "sp"
    assert levels["energy"]["level_of_theory_ref"] == declared.public_ref


def test_statmech_without_a_declaration_reports_none_and_is_not_back_filled(
    client, db_session
):
    """The derived level is wB97X-D, yet nothing was declared: stays null."""
    resp = client.post(
        "/api/v1/uploads/statmech", json=_opt_sp_statmech("CCCCCCCCCCCCCCCCCC")
    )
    assert resp.status_code == 201, resp.text
    assert db_session.get(Statmech, resp.json()["id"]).energy_level_of_theory_id is None

    levels = _statmech_levels(client, resp.json()["id"])
    assert levels["declared_energy"] is None
    assert levels["energy"]["method"] == "wB97X-D"


def test_a_refused_declaration_stores_nothing(client, db_session):
    """A declaration that contradicts the linked sp is refused, row and all."""
    resp = client.post(
        "/api/v1/uploads/statmech",
        json=_opt_sp_statmech("CCCCCCCCCCCCCCCCCCC", energy_level_of_theory=_LOT_A),
    )
    assert resp.status_code == 422, resp.text
    assert db_session.scalars(select(Statmech)).all() == []


# ---------------------------------------------------------------------------
# Standalone thermo
# ---------------------------------------------------------------------------


def test_thermo_declared_energy_level_is_stored_and_read(client, db_session):
    resp = client.post(
        "/api/v1/uploads/thermo",
        json=_thermo_payload(
            "CCCCCCCCCCCCCCCCCCCC",
            calculations=[
                {"key": "opt0", "calculation": _opt_calc_at(_LOT_A)},
                {"key": "sp0", "calculation": _sp_calc_at(_LOT_B)},
            ],
            source_calculations=[
                {"calculation_key": "opt0", "role": "opt"},
                {"calculation_key": "sp0", "role": "sp"},
            ],
            energy_level_of_theory=_LOT_B,
        ),
    )
    assert resp.status_code == 201, resp.text
    row = db_session.scalars(select(Thermo)).one()
    assert row.energy_level_of_theory_id is not None

    levels = _thermo_levels(client, resp.json()["species_entry_id"])
    assert levels["declared_energy"]["method"] == "wB97X-D"


def test_thermo_without_a_declaration_reports_none(client, db_session):
    resp = client.post(
        "/api/v1/uploads/thermo",
        json=_thermo_payload(
            "CCCCCCCCCCCCCCCCCCCCC",
            calculations=[{"key": "opt0", "calculation": _opt_calc_at(_LOT_A)}],
            source_calculations=[{"calculation_key": "opt0", "role": "opt"}],
        ),
    )
    assert resp.status_code == 201, resp.text
    assert db_session.scalars(select(Thermo)).one().energy_level_of_theory_id is None
    assert _thermo_levels(client, resp.json()["species_entry_id"])["declared_energy"] is None


def test_thermo_reports_the_declaration_of_its_statmech_basis(client, db_session):
    """A thermo that derives from a statmech inherits that record's declaration,
    the way it inherits its derived levels; it stores none of its own."""
    smiles = "CCCCCCCCCCCCCCCCCCCCCC"
    sm = client.post(
        "/api/v1/uploads/statmech",
        json=_opt_sp_statmech(smiles, energy_level_of_theory=_LOT_B),
    )
    assert sm.status_code == 201, sm.text
    thermo = client.post(
        "/api/v1/uploads/thermo",
        json=_thermo_payload(smiles, existing_statmech_id=sm.json()["id"]),
    )
    assert thermo.status_code == 201, thermo.text

    assert db_session.scalars(select(Thermo)).one().energy_level_of_theory_id is None
    levels = _thermo_levels(client, thermo.json()["species_entry_id"])
    assert levels["declared_energy"]["method"] == "wB97X-D"


# ---------------------------------------------------------------------------
# Computed-species bundle
# ---------------------------------------------------------------------------


def test_computed_species_bundle_stores_both_declarations(client, db_session):
    payload = _computed_species_bundle_payload(
        conformers=[_bundle_conformer(0, opt_lot=_LOT_A, xyz_z=0.0, sp_lot=_LOT_B)],
        statmech_source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
        ],
    )
    payload["statmech"]["energy_level_of_theory"] = _LOT_B
    payload["thermo"] = {
        "enthalpy_reference_kind": "formation_298k",
        "h298_kj_mol": 146.7,
        "source_calculations": [
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
        ],
        "energy_level_of_theory": _LOT_B,
    }
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text

    assert db_session.scalars(select(Statmech)).one().energy_level_of_theory_id is not None
    assert db_session.scalars(select(Thermo)).one().energy_level_of_theory_id is not None
    levels = _statmech_levels(client, resp.json()["statmech"]["statmech_id"])
    assert levels["declared_energy"]["method"] == "wB97X-D"


# ---------------------------------------------------------------------------
# Computed-reaction bundle
# ---------------------------------------------------------------------------


def test_computed_reaction_bundle_stores_both_declarations(client, db_session):
    def _species(key: str, smiles: str, multiplicity: int, xyz: str, **extra) -> dict:
        return {
            "key": key,
            "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
            "conformers": [
                {
                    "key": f"{key}c",
                    "geometry": {"key": f"{key}g", "xyz_text": xyz},
                    "calculation": {
                        "key": f"{key}opt",
                        "type": "opt",
                        "software_release": _SOFTWARE,
                        "level_of_theory": _LOT_A,
                        "opt_converged": True,
                    },
                }
            ],
            "calculations": [],
            **extra,
        }

    payload = {
        "species": [
            _species(
                "ch3",
                "[CH3]",
                2,
                _methyl_xyz(0.0),
                statmech={
                    "statmech_treatment": "rrho",
                    "external_symmetry": 1,
                    "source_calculations": [{"calculation_key": "ch3opt", "role": "opt"}],
                    "energy_level_of_theory": _LOT_A,
                },
                thermo={
                    "enthalpy_reference_kind": "formation_298k",
                    "h298_kj_mol": 146.7,
                    "source_calculations": [{"calculation_key": "ch3opt", "role": "opt"}],
                    "energy_level_of_theory": _LOT_A,
                },
            ),
            _species("h", "[H]", 2, "1\nH\nH 0.0 0.0 0.0"),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch3", "h"],
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert resp.status_code == 201, resp.text

    statmech = db_session.scalars(select(Statmech)).one()
    thermo = db_session.scalars(select(Thermo)).one()
    assert statmech.energy_level_of_theory_id is not None
    assert thermo.energy_level_of_theory_id == statmech.energy_level_of_theory_id


# ---------------------------------------------------------------------------
# A declaration that points at a merged level of theory
# ---------------------------------------------------------------------------


def test_a_declared_level_later_merged_is_reported_as_the_row_it_merged_into(
    client, db_session
):
    species = make_species(db_session, smiles="[CH3]", charge=0, multiplicity=2)
    entry = make_species_entry(db_session, species)
    statmech = make_statmech(db_session, species_entry=entry)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    duplicate = make_lot(db_session, method="b3lyp", basis="def2svp")
    holder = make_lot(db_session, method="b3lyp", basis="def2-svp")
    statmech.energy_level_of_theory_id = duplicate.id
    thermo.energy_level_of_theory_id = duplicate.id
    db_session.flush()
    db_session.add(LevelOfTheoryMerge(merged_lot_id=duplicate.id, into_lot_id=holder.id))
    db_session.flush()

    assert duplicate.public_ref != holder.public_ref
    levels = _statmech_levels(client, statmech.public_ref)
    assert levels["declared_energy"]["level_of_theory_ref"] == holder.public_ref
    levels = _thermo_levels(client, entry.id)
    assert levels["declared_energy"]["level_of_theory_ref"] == holder.public_ref


def test_an_unlinked_thermo_borrows_no_declaration_from_a_sibling_statmech(
    client, db_session
):
    """A declared statmech and an experimental thermo with no statmech link sit on
    one entry. The entry-wide statmech pick must not lend its declaration."""
    from app.db.models.common import ScientificOriginKind

    species = make_species(db_session, smiles="[CH3]", charge=0, multiplicity=2)
    entry = make_species_entry(db_session, species)
    lot = make_lot(db_session, method="b3lyp", basis="def2svp")
    statmech = make_statmech(db_session, species_entry=entry)
    statmech.energy_level_of_theory_id = lot.id
    experimental = make_thermo_scalar(
        db_session, species_entry=entry, scientific_origin=ScientificOriginKind.experimental
    )
    linked = make_thermo_scalar(db_session, species_entry=entry, statmech_id=statmech.id)
    db_session.flush()

    read = client.get(f"/api/v1/scientific/species-entries/{entry.id}/thermo")
    assert read.status_code == 200, read.text
    by_ref = {r["thermo_ref"]: r["levels"] for r in read.json()["records"]}
    assert by_ref[experimental.public_ref]["declared_energy"] is None
    assert by_ref[linked.public_ref]["declared_energy"]["level_of_theory_ref"] == lot.public_ref
