"""Thermo target and protocol declarations over HTTP: every route that carries thermo.

``/uploads/thermo``, ``/uploads/computed-species`` and ``/uploads/computed-reaction``
each build the row themselves, so each is asserted against the database and
read back through the scientific thermo endpoint. A record that declares
nothing must read ``null`` for both, and a deposit that omits the fields must
keep working exactly as before.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import AppUserRole, SubmissionRecordType, ThermoTargetKind
from app.db.models.species import ConformerGroup, ConformerObservation
from app.db.models.thermo import Thermo
from app.schemas.workflows.contribution_bundle import ContributionBundleV0
from app.services.contribution_bundle_export import export_thermo_bundle
from app.services.species_resolution import resolve_species_entry
from app.workflows.contribution_bundle_submit import submit_contribution_bundle
from tests.api.test_api_bundle_thermo_and_scf_provenance import _reaction_bundle, _species_bundle
from tests.services.scientific_read._factories import make_conformer_group

THERMO = "/api/v1/uploads/thermo"
SPECIES = "/api/v1/uploads/computed-species"
REACTION = "/api/v1/uploads/computed-reaction"
HYDROGEN = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
G4_PROTOCOL = {
    "version": 1,
    "recipe": {"name": "g4", "recipe_version": "Curtiss 2007"},
    "formation_reference": {"derivation": "atomization", "reference_data_source": "atct"},
    "thermal_approximation": {"ensemble_representation": "lowest_conformer", "internal_motion": "harmonic"},
    "departures": [],
}
SOFTWARE = {"name": "Gaussian", "version": "16"}
LOT = {"method": "wb97xd", "basis": "def2tzvp"}


def _standalone(**extra) -> dict:
    return {
        "species_entry": HYDROGEN,
        "scientific_origin": "computed",
        "h298_kj_mol": 217.998,
        "enthalpy_reference_kind": "formation_298k",
        **extra,
    }


def _bundle_thermo(**extra) -> dict:
    return {"h298_kj_mol": 217.998, "enthalpy_reference_kind": "formation_298k", **extra}


def _read(client, species_entry_id: int) -> list[dict]:
    response = client.get(f"/api/v1/scientific/species-entries/{species_entry_id}/thermo")
    assert response.status_code == 200, response.text
    return response.json()["records"]


def _stated(value):
    """What a read states: nulls are "not said", so drop them (and an empty supporting list)."""
    if isinstance(value, dict):
        return {
            k: _stated(v)
            for k, v in value.items()
            if v is not None and not (k == "supporting_calculations" and v == [])
        }
    if isinstance(value, list):
        return [_stated(v) for v in value]
    return value


def _only_thermo(db_session) -> Thermo:
    return db_session.scalars(select(Thermo)).one()


def _code(response, status: int = 422) -> str:
    assert response.status_code == status, response.text
    return response.json()["code"]


# ---------------------------------------------------------------------------
# Legacy-null reads, and existing uploads unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["thermo", "computed-species", "computed-reaction"])
def test_a_deposit_that_omits_both_fields_is_accepted_and_reads_null(client, db_session, route):
    if route == "thermo":
        response = client.post(THERMO, json=_standalone())
    elif route == "computed-species":
        response = client.post(SPECIES, json=_species_bundle(thermo=_bundle_thermo()))
    else:
        bundle = _reaction_bundle()
        bundle["species"][0]["thermo"] = _bundle_thermo()
        response = client.post(REACTION, json=bundle)
    assert response.status_code == 201, response.text

    row = _only_thermo(db_session)
    assert (row.thermodynamic_target_kind, row.target_conformer_group_id, row.protocol_declaration) == (None, None, None)
    (record,) = _read(client, row.species_entry_id)
    # Present and null: "not stated", never "equilibrium" and never "standard".
    assert "thermodynamic_target" in record and record["thermodynamic_target"] is None
    assert "protocol" in record and record["protocol"] is None


# ---------------------------------------------------------------------------
# /uploads/thermo
# ---------------------------------------------------------------------------


def test_standalone_equilibrium_target_and_protocol_round_trip(client, db_session):
    response = client.post(
        THERMO, json=_standalone(thermodynamic_target={"kind": "equilibrium_ensemble"}, protocol=G4_PROTOCOL)
    )
    assert response.status_code == 201, response.text

    row = _only_thermo(db_session)
    assert row.thermodynamic_target_kind is ThermoTargetKind.equilibrium_ensemble
    assert row.protocol_declaration == G4_PROTOCOL
    (record,) = _read(client, row.species_entry_id)
    assert record["thermodynamic_target"] == {"kind": "equilibrium_ensemble", "conformer_group_ref": None}
    assert _stated(record["protocol"]) == G4_PROTOCOL
    # "No departures" is a statement and survives the read; omitted would read null.
    assert record["protocol"]["departures"] == []


def test_standalone_single_conformer_target_reads_back_its_group_ref(client, db_session):
    entry = resolve_species_entry(db_session, SpeciesEntryIdentityPayload(**HYDROGEN))
    group = make_conformer_group(db_session, entry)
    response = client.post(
        THERMO,
        json=_standalone(thermodynamic_target={"kind": "single_conformer", "conformer_group_ref": group.public_ref}),
    )
    assert response.status_code == 201, response.text
    row = _only_thermo(db_session)
    assert row.target_conformer_group_id == group.id
    (record,) = _read(client, row.species_entry_id)
    assert record["thermodynamic_target"] == {"kind": "single_conformer", "conformer_group_ref": group.public_ref}
    # A public ref, never a row id.
    assert str(group.id) != record["thermodynamic_target"]["conformer_group_ref"]


def test_a_group_of_another_species_entry_is_refused_and_nothing_is_stored(client, db_session):
    other = resolve_species_entry(db_session, SpeciesEntryIdentityPayload(smiles="CC", charge=0, multiplicity=1))
    foreign = make_conformer_group(db_session, other)
    response = client.post(
        THERMO,
        json=_standalone(thermodynamic_target={"kind": "single_conformer", "conformer_group_ref": foreign.public_ref}),
    )
    assert _code(response) == "thermo_target_group_owner_mismatch"
    assert response.json()["context"]["field"] == "thermodynamic_target.conformer_group_ref"
    # No ids in a refusal body (DR-0028).
    assert str(foreign.id) not in response.text.replace(foreign.public_ref, "")
    assert db_session.scalars(select(Thermo)).all() == []


@pytest.mark.parametrize(
    "target,code,status",
    [
        ({"kind": "single_conformer"}, "thermo_target_group_required", 422),
        ({"kind": "equilibrium_ensemble", "conformer_group_ref": "cg_" + "a" * 26}, "thermo_target_group_not_allowed", 422),
        ({"kind": "single_conformer", "conformer_group_ref": "cg_" + "a" * 26}, "unknown_conformer_group_ref", 404),
    ],
    ids=["single_without_group", "equilibrium_with_group", "unknown_group"],
)
def test_target_refusals_carry_codes(client, db_session, target, code, status):
    assert _code(client.post(THERMO, json=_standalone(thermodynamic_target=target)), status) == code
    assert db_session.scalars(select(Thermo)).all() == []


def test_a_conformer_key_is_not_accepted_on_the_standalone_route(client):
    response = client.post(
        THERMO, json=_standalone(thermodynamic_target={"kind": "single_conformer", "conformer_key": "c0"})
    )
    assert response.status_code == 422, response.text


def test_unknown_protocol_fields_and_versions_are_refused(client, db_session):
    unknown_field = client.post(THERMO, json=_standalone(protocol={**G4_PROTOCOL, "basis_quality": "high"}))
    assert unknown_field.status_code == 422, unknown_field.text
    unknown_version = client.post(THERMO, json=_standalone(protocol={**G4_PROTOCOL, "version": 2}))
    assert _code(unknown_version) == "thermo_protocol_version_unsupported"
    assert db_session.scalars(select(Thermo)).all() == []


def test_protocol_supporting_calculations_resolve_by_key_and_by_ref(client, db_session):
    first = client.post(
        THERMO,
        json=_standalone(
            calculations=[
                {
                    "key": "opt0",
                    "calculation": {
                        "type": "opt",
                        "software_release": SOFTWARE,
                        "level_of_theory": LOT,
                        "opt_result": {"converged": True},
                    },
                }
            ],
            protocol={"version": 1, "supporting_calculations": [{"calculation_key": "opt0"}]},
        ),
    )
    assert first.status_code == 201, first.text
    calc = db_session.scalars(select(Calculation)).one()
    row = db_session.scalars(select(Thermo)).one()
    assert row.protocol_declaration["supporting_calculations"] == [{"calculation_ref": calc.public_ref}]

    by_ref = client.post(
        THERMO,
        json=_standalone(
            protocol={"version": 1, "supporting_calculations": [{"calculation_ref": calc.public_ref}]}
        ),
    )
    assert by_ref.status_code == 201, by_ref.text

    undeclared = client.post(
        THERMO,
        json=_standalone(protocol={"version": 1, "supporting_calculations": [{"calculation_key": "nope"}]}),
    )
    assert _code(undeclared) == "calculation_key_undeclared"
    missing = client.post(
        THERMO,
        json=_standalone(
            protocol={"version": 1, "supporting_calculations": [{"calculation_ref": "calc_" + "a" * 26}]}
        ),
    )
    assert _code(missing, 404) == "unknown_calculation_ref"


# ---------------------------------------------------------------------------
# /uploads/computed-species
# ---------------------------------------------------------------------------


def test_species_bundle_round_trips_target_by_conformer_key_and_protocol_by_calculation_key(client, db_session):
    thermo = _bundle_thermo(
        thermodynamic_target={"kind": "single_conformer", "conformer_key": "c0"},
        protocol={**G4_PROTOCOL, "supporting_calculations": [{"calculation_key": "opt0"}]},
    )
    response = client.post(SPECIES, json=_species_bundle(thermo=thermo))
    assert response.status_code == 201, response.text

    row = _only_thermo(db_session)
    group = db_session.scalars(select(ConformerGroup).where(ConformerGroup.species_entry_id == row.species_entry_id)).one()
    calc = db_session.scalars(select(Calculation).where(Calculation.species_entry_id == row.species_entry_id)).one()
    assert row.thermodynamic_target_kind is ThermoTargetKind.single_conformer
    assert row.target_conformer_group_id == group.id
    assert row.protocol_declaration == {**G4_PROTOCOL, "supporting_calculations": [{"calculation_ref": calc.public_ref}]}
    (record,) = _read(client, row.species_entry_id)
    assert record["thermodynamic_target"]["conformer_group_ref"] == group.public_ref
    assert record["protocol"]["supporting_calculations"] == [{"calculation_ref": calc.public_ref}]


def test_species_bundle_refuses_a_conformer_or_calculation_nothing_declared(client, db_session):
    bad_conformer = _bundle_thermo(thermodynamic_target={"kind": "single_conformer", "conformer_key": "c9"})
    response = client.post(SPECIES, json=_species_bundle(thermo=bad_conformer))
    assert _code(response) == "conformer_key_undeclared"
    assert response.json()["context"]["declared_keys"] == ["c0"]

    bad_calc = _bundle_thermo(protocol={"version": 1, "supporting_calculations": [{"calculation_key": "nope"}]})
    assert _code(client.post(SPECIES, json=_species_bundle(thermo=bad_calc))) == "calculation_key_undeclared"
    assert db_session.scalars(select(Thermo)).all() == []


def test_a_bundle_refuses_public_refs_where_it_wants_keys(client):
    by_ref = _bundle_thermo(
        thermodynamic_target={"kind": "single_conformer", "conformer_group_ref": "cg_" + "a" * 26}
    )
    assert client.post(SPECIES, json=_species_bundle(thermo=by_ref)).status_code == 422
    calc_ref = _bundle_thermo(
        protocol={"version": 1, "supporting_calculations": [{"calculation_ref": "calc_" + "a" * 26}]}
    )
    assert client.post(SPECIES, json=_species_bundle(thermo=calc_ref)).status_code == 422


def test_species_bundle_equilibrium_with_a_group_is_refused(client):
    bad = _bundle_thermo(thermodynamic_target={"kind": "equilibrium_ensemble", "conformer_key": "c0"})
    assert _code(client.post(SPECIES, json=_species_bundle(thermo=bad))) == "thermo_target_group_not_allowed"


# ---------------------------------------------------------------------------
# /uploads/computed-reaction
# ---------------------------------------------------------------------------


def test_reaction_bundle_round_trips_target_and_protocol(client, db_session):
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = _bundle_thermo(
        thermodynamic_target={"kind": "single_conformer", "conformer_key": "h-conf"},
        protocol={**G4_PROTOCOL, "supporting_calculations": [{"calculation_key": "h-opt"}, {"calculation_key": "h-sp"}]},
    )
    response = client.post(REACTION, json=bundle)
    assert response.status_code == 201, response.text

    row = db_session.get(Thermo, response.json()["thermo_ids"][0])
    group = db_session.scalars(select(ConformerGroup).where(ConformerGroup.species_entry_id == row.species_entry_id)).one()
    keys = response.json()["calculation_keys"]
    refs = [db_session.get(Calculation, keys[key]).public_ref for key in ("h-opt", "h-sp")]
    assert row.thermodynamic_target_kind is ThermoTargetKind.single_conformer
    assert row.target_conformer_group_id == group.id
    assert row.protocol_declaration["supporting_calculations"] == [{"calculation_ref": ref} for ref in refs]
    assert row.protocol_declaration["recipe"] == {"name": "g4", "recipe_version": "Curtiss 2007"}
    (record,) = _read(client, row.species_entry_id)
    assert record["thermodynamic_target"] == {"kind": "single_conformer", "conformer_group_ref": group.public_ref}


def test_reaction_bundle_a_siblings_conformer_is_out_of_scope(client, db_session):
    """Conformer keys are scoped to the species that declared them."""
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = _bundle_thermo(
        thermodynamic_target={"kind": "single_conformer", "conformer_key": "h2-conf"}
    )
    response = client.post(REACTION, json=bundle)
    assert _code(response) == "conformer_key_undeclared"
    assert response.json()["context"]["declared_keys"] == ["h-conf"]
    assert db_session.scalars(select(Thermo)).all() == []


def test_reaction_bundle_a_siblings_calculation_is_refused_on_ownership(client, db_session):
    """Calculation keys span the bundle, so the ownership check is the workflow's."""
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = _bundle_thermo(
        protocol={"version": 1, "supporting_calculations": [{"calculation_key": "h2-opt"}]}
    )
    response = client.post(REACTION, json=bundle)
    assert _code(response) == "thermo_protocol_calculation_owner_mismatch"
    assert db_session.scalars(select(Thermo)).all() == []


def test_reaction_bundle_undeclared_protocol_calculation_is_refused(client):
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = _bundle_thermo(
        protocol={"version": 1, "supporting_calculations": [{"calculation_key": "nope"}]}
    )
    assert _code(client.post(REACTION, json=bundle)) == "calculation_key_undeclared"


# ---------------------------------------------------------------------------
# Contribution bundles: export then import
# ---------------------------------------------------------------------------


def _import(db_session, bundle: ContributionBundleV0) -> Thermo:
    reloaded = ContributionBundleV0.model_validate(bundle.model_dump(mode="json"))
    actor = AppUser(username=f"decl-{uuid4().hex[:10]}", role=AppUserRole.user)
    db_session.add(actor)
    db_session.flush()
    submitted = submit_contribution_bundle(db_session, reloaded, actor=actor)
    [thermo_id] = [r.record_id for r in submitted.records if r.record_type.value == SubmissionRecordType.thermo.value]
    db_session.flush()
    return db_session.get(Thermo, thermo_id)


def test_contribution_bundle_export_then_import_carries_equilibrium_target_and_protocol(client, db_session):
    assert client.post(
        THERMO, json=_standalone(thermodynamic_target={"kind": "equilibrium_ensemble"}, protocol=G4_PROTOCOL)
    ).status_code == 201
    source = _only_thermo(db_session)

    exported = export_thermo_bundle(
        db_session, thermo_ids=[source.id], title="declarations", summary="round trip", exporter_label="tester"
    )
    assert exported.omissions == []
    (upload,) = exported.bundle.records.thermo_uploads
    assert upload.thermodynamic_target.kind.value == ThermoTargetKind.equilibrium_ensemble.value
    assert upload.protocol.model_dump(mode="json", exclude_none=True, exclude_defaults=True) == G4_PROTOCOL

    imported = _import(db_session, exported.bundle)
    assert imported.id != source.id
    assert imported.thermodynamic_target_kind is ThermoTargetKind.equilibrium_ensemble
    assert imported.protocol_declaration == G4_PROTOCOL


def test_contribution_bundle_export_leaves_out_what_a_portable_bundle_cannot_carry(client, db_session):
    entry = resolve_species_entry(db_session, SpeciesEntryIdentityPayload(**HYDROGEN))
    group = make_conformer_group(db_session, entry)
    assert client.post(
        THERMO,
        json=_standalone(
            thermodynamic_target={"kind": "single_conformer", "conformer_group_ref": group.public_ref},
            calculations=[
                {
                    "key": "opt0",
                    "calculation": {
                        "type": "opt",
                        "software_release": SOFTWARE,
                        "level_of_theory": LOT,
                        "opt_result": {"converged": True},
                    },
                }
            ],
            protocol={**G4_PROTOCOL, "supporting_calculations": [{"calculation_key": "opt0"}]},
        ),
    ).status_code == 201
    source = _only_thermo(db_session)

    exported = export_thermo_bundle(
        db_session, thermo_ids=[source.id], title="portable", summary="refs left out", exporter_label="tester"
    )
    (omission,) = exported.omissions
    assert omission.action == "declaration_pruned"
    assert omission.ref == source.public_ref
    assert "single_conformer" in omission.detail and "supporting calculations" in omission.detail
    (upload,) = exported.bundle.records.thermo_uploads
    # Absent, never replaced: a dropped single-conformer target does not become an equilibrium one.
    assert upload.thermodynamic_target is None
    assert upload.protocol.supporting_calculations == []
    assert upload.protocol.recipe.name.value == "g4"

    imported = _import(db_session, exported.bundle)
    assert imported.thermodynamic_target_kind is None
    assert imported.target_conformer_group_id is None
    assert "supporting_calculations" not in imported.protocol_declaration
    assert imported.protocol_declaration["recipe"] == {"name": "g4", "recipe_version": "Curtiss 2007"}


def test_a_legacy_row_exports_with_no_declarations_and_no_omission(client, db_session):
    assert client.post(THERMO, json=_standalone()).status_code == 201
    source = _only_thermo(db_session)
    exported = export_thermo_bundle(
        db_session, thermo_ids=[source.id], title="legacy", summary="no declarations", exporter_label="tester"
    )
    assert exported.omissions == []
    (upload,) = exported.bundle.records.thermo_uploads
    assert upload.thermodynamic_target is None and upload.protocol is None


def test_export_leaves_a_protocol_with_only_calculation_refs_out_entirely(client, db_session):
    assert client.post(
        THERMO,
        json=_standalone(
            calculations=[
                {
                    "key": "opt0",
                    "calculation": {
                        "type": "opt",
                        "software_release": SOFTWARE,
                        "level_of_theory": LOT,
                        "opt_result": {"converged": True},
                    },
                }
            ],
            protocol={"version": 1, "supporting_calculations": [{"calculation_key": "opt0"}]},
        ),
    ).status_code == 201
    source = _only_thermo(db_session)
    exported = export_thermo_bundle(
        db_session, thermo_ids=[source.id], title="refs only", summary="nothing portable", exporter_label="tester"
    )
    (omission,) = exported.omissions
    assert omission.action == "declaration_pruned"
    (upload,) = exported.bundle.records.thermo_uploads
    assert upload.protocol is None


# ---------------------------------------------------------------------------
# Observations the groups come from
# ---------------------------------------------------------------------------


def test_the_bundle_target_resolves_to_the_group_of_that_conformers_observation(client, db_session):
    thermo = _bundle_thermo(thermodynamic_target={"kind": "single_conformer", "conformer_key": "c0"})
    response = client.post(SPECIES, json=_species_bundle(thermo=thermo))
    assert response.status_code == 201, response.text
    row = _only_thermo(db_session)
    observation = db_session.scalars(select(ConformerObservation)).one()
    assert row.target_conformer_group_id == observation.conformer_group_id
