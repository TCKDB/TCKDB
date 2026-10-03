"""A thermo record's target and protocol declarations, through the workflow and service layers.

The declarations are attributed claims: stored as made, resolved to rows of the
record's own species entry, never inferred and never defaulted. Each refusal is
coded, and the checks are re-derived from the declaration itself, so a payload
built with ``model_construct`` (which skips every validator) is judged the same
as a parsed one. The route-level round trips are in
``tests/api/test_api_thermo_declarations.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload
from tckdb_schemas.thermo_declarations import (
    ThermoProtocolDeclaration,
    ThermoTargetDeclaration,
)

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.calculation import Calculation
from app.db.models.common import ThermoTargetKind
from app.db.models.species import ConformerGroup
from app.db.models.thermo import Thermo
from app.schemas.entities.thermo import ThermoCreate
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.species_resolution import resolve_species_entry
from app.services.thermo_declaration_resolution import (
    assert_thermo_declaration_columns,
    resolve_thermo_declarations,
)
from app.services.thermo_resolution import persist_thermo
from app.workflows.thermo import persist_thermo_upload
from tests.services.scientific_read._factories import make_conformer_group

SOFTWARE = {"name": "Gaussian", "version": "16"}
LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
METHANE = {"smiles": "C", "charge": 0, "multiplicity": 1}
ETHANE = {"smiles": "CC", "charge": 0, "multiplicity": 1}
G4_PROTOCOL = {
    "version": 1,
    "recipe": {"name": "g4"},
    "formation_reference": {"derivation": "atomization", "reference_data_source": "atct"},
    "thermal_approximation": {"ensemble_representation": "lowest_conformer"},
    "departures": [],
}


def _entry(session, identity=METHANE):
    return resolve_species_entry(session, SpeciesEntryIdentityPayload(**identity))


def _request(identity=METHANE, **extra) -> ThermoUploadRequest:
    return ThermoUploadRequest.model_validate(
        {
            "species_entry": identity,
            "scientific_origin": "computed",
            "h298_kj_mol": -74.6,
            "enthalpy_reference_kind": "formation_298k",
            **extra,
        }
    )


def _thermo_count(session) -> int:
    return session.scalar(select(func.count()).select_from(Thermo)) or 0


def _opt_calc() -> dict:
    return {"type": "opt", "software_release": SOFTWARE, "level_of_theory": LOT, "opt_result": {"converged": True}}


# ---------------------------------------------------------------------------
# What is stored
# ---------------------------------------------------------------------------


def test_a_deposit_without_declarations_stores_null_for_all_three(db_session):
    thermo = persist_thermo_upload(db_session, _request())
    assert (thermo.thermodynamic_target_kind, thermo.target_conformer_group_id, thermo.protocol_declaration) == (
        None,
        None,
        None,
    )


def test_a_statmech_link_does_not_imply_a_target(db_session):
    """No inference: the target is what the depositor said, not what is attached."""
    from tests.services.scientific_read._factories import make_statmech

    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    thermo = persist_thermo_upload(db_session, _request(existing_statmech_id=statmech.id))
    assert thermo.statmech_id == statmech.id
    assert thermo.thermodynamic_target_kind is None
    assert thermo.target_conformer_group_id is None


def test_an_equilibrium_target_and_a_full_protocol_are_stored_as_made(db_session):
    thermo = persist_thermo_upload(
        db_session,
        _request(thermodynamic_target={"kind": "equilibrium_ensemble"}, protocol=G4_PROTOCOL),
    )
    assert thermo.thermodynamic_target_kind is ThermoTargetKind.equilibrium_ensemble
    assert thermo.target_conformer_group_id is None
    # Stored exactly as stated, including the explicit "no departures".
    assert thermo.protocol_declaration == G4_PROTOCOL


def test_protocol_departures_omitted_stays_absent_in_storage(db_session):
    protocol = {key: value for key, value in G4_PROTOCOL.items() if key != "departures"}
    thermo = persist_thermo_upload(db_session, _request(protocol=protocol))
    assert "departures" not in thermo.protocol_declaration
    assert thermo.protocol_declaration == protocol


def test_a_single_conformer_target_stores_its_group(db_session):
    group = make_conformer_group(db_session, _entry(db_session))
    thermo = persist_thermo_upload(
        db_session,
        _request(thermodynamic_target={"kind": "single_conformer", "conformer_group_ref": group.public_ref}),
    )
    assert thermo.thermodynamic_target_kind is ThermoTargetKind.single_conformer
    assert thermo.target_conformer_group_id == group.id


# ---------------------------------------------------------------------------
# Refusals at the service
# ---------------------------------------------------------------------------


def test_a_group_of_another_species_entry_is_refused_with_a_code(db_session):
    foreign = make_conformer_group(db_session, _entry(db_session, ETHANE))
    before = _thermo_count(db_session)
    with pytest.raises(CodedValueError) as caught:
        persist_thermo_upload(
            db_session,
            _request(thermodynamic_target={"kind": "single_conformer", "conformer_group_ref": foreign.public_ref}),
        )
    assert caught.value.code == "thermo_target_group_owner_mismatch"
    assert caught.value.context["field"] == "thermodynamic_target.conformer_group_ref"
    assert _thermo_count(db_session) == before


def test_an_unknown_group_ref_is_a_404_with_a_code(db_session):
    with pytest.raises(NotFoundError) as caught:
        persist_thermo_upload(
            db_session,
            _request(thermodynamic_target={"kind": "single_conformer", "conformer_group_ref": "cg_" + "a" * 26}),
        )
    assert caught.value.code == "unknown_conformer_group_ref"


def test_a_supporting_calculation_by_key_is_stored_as_its_public_ref(db_session):
    thermo = persist_thermo_upload(
        db_session,
        _request(
            calculations=[{"key": "opt0", "calculation": _opt_calc()}],
            protocol={"version": 1, "supporting_calculations": [{"calculation_key": "opt0"}]},
        ),
    )
    calc = db_session.scalars(select(Calculation).where(Calculation.species_entry_id == thermo.species_entry_id)).one()
    assert thermo.protocol_declaration == {
        "version": 1,
        "supporting_calculations": [{"calculation_ref": calc.public_ref}],
    }
    # Never a key and never an id.
    assert "opt0" not in str(thermo.protocol_declaration)
    assert str(calc.id) not in str(thermo.protocol_declaration["supporting_calculations"])


def test_a_supporting_calculation_by_public_ref_resolves_and_deduplicates(db_session):
    first = persist_thermo_upload(db_session, _request(calculations=[{"key": "opt0", "calculation": _opt_calc()}]))
    calc = db_session.scalars(select(Calculation).where(Calculation.species_entry_id == first.species_entry_id)).one()
    second = persist_thermo_upload(
        db_session,
        _request(
            calculations=[],
            protocol={
                "version": 1,
                "supporting_calculations": [{"calculation_ref": calc.public_ref}],
            },
        ),
    )
    assert second.protocol_declaration["supporting_calculations"] == [{"calculation_ref": calc.public_ref}]


def test_a_supporting_calculation_of_another_species_entry_is_refused(db_session):
    other = persist_thermo_upload(
        db_session, _request(ETHANE, calculations=[{"key": "opt0", "calculation": _opt_calc()}])
    )
    foreign = db_session.scalars(select(Calculation).where(Calculation.species_entry_id == other.species_entry_id)).one()
    with pytest.raises(CodedValueError) as caught:
        persist_thermo_upload(
            db_session,
            _request(protocol={"version": 1, "supporting_calculations": [{"calculation_ref": foreign.public_ref}]}),
        )
    assert caught.value.code == "thermo_protocol_calculation_owner_mismatch"
    assert caught.value.context["field"] == "protocol.supporting_calculations[0].calculation_ref"


def test_an_unknown_calculation_ref_is_a_404_with_a_code(db_session):
    with pytest.raises(NotFoundError) as caught:
        persist_thermo_upload(
            db_session,
            _request(protocol={"version": 1, "supporting_calculations": [{"calculation_ref": "calc_" + "a" * 26}]}),
        )
    assert caught.value.code == "unknown_calculation_ref"


# ---------------------------------------------------------------------------
# Payloads that skipped validation (``model_construct``): the service re-checks
# ---------------------------------------------------------------------------


def _unvalidated(**declarations) -> ThermoUploadRequest:
    """A request whose declarations never went through a validator."""
    return _request().model_copy(update=declarations)


@pytest.mark.parametrize(
    "target,code",
    [
        (ThermoTargetDeclaration.model_construct(kind=ThermoTargetKind.single_conformer), "thermo_target_group_required"),
        (
            ThermoTargetDeclaration.model_construct(
                kind=ThermoTargetKind.equilibrium_ensemble, conformer_group_ref="cg_" + "a" * 26
            ),
            "thermo_target_group_not_allowed",
        ),
        (
            ThermoTargetDeclaration.model_construct(
                kind=ThermoTargetKind.single_conformer, conformer_group_ref="cg_" + "a" * 26, conformer_key="c0"
            ),
            "thermo_target_group_required",
        ),
    ],
    ids=["single_without_group", "equilibrium_with_group", "group_named_twice"],
)
def test_a_target_that_skipped_validation_is_refused_by_the_workflow(db_session, target, code):
    with pytest.raises(CodedValueError) as caught:
        persist_thermo_upload(db_session, _unvalidated(thermodynamic_target=target))
    assert caught.value.code == code


def test_an_unvalidated_group_of_another_species_entry_is_refused_by_the_service(db_session):
    foreign = make_conformer_group(db_session, _entry(db_session, ETHANE))
    target = ThermoTargetDeclaration.model_construct(
        kind=ThermoTargetKind.single_conformer, conformer_group_ref=foreign.public_ref, conformer_key=None
    )
    with pytest.raises(CodedValueError) as caught:
        persist_thermo_upload(db_session, _unvalidated(thermodynamic_target=target))
    assert caught.value.code == "thermo_target_group_owner_mismatch"


@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings")
@pytest.mark.parametrize(
    "protocol,code",
    [
        (ThermoProtocolDeclaration.model_construct(version=7, recipe=None), "thermo_protocol_version_unsupported"),
        (
            ThermoProtocolDeclaration.model_construct(version=1, recipe={"name": "g5"}),
            "thermo_declaration_invalid",
        ),
    ],
    ids=["unsupported_version", "invalid_recipe"],
)
def test_a_protocol_that_skipped_validation_is_refused_by_the_workflow(db_session, protocol, code):
    with pytest.raises(CodedValueError) as caught:
        persist_thermo_upload(db_session, _unvalidated(protocol=protocol))
    assert caught.value.code == code


def test_the_last_stop_in_persist_thermo_checks_the_resolved_columns(db_session):
    """A resolved payload built by hand, without any workflow, is judged at ``persist_thermo``."""
    entry = _entry(db_session)
    foreign = make_conformer_group(db_session, _entry(db_session, ETHANE))
    base = {"species_entry_id": entry.id, "scientific_origin": "computed"}

    cases = [
        # group present, kind absent
        (ThermoCreate.model_construct(**base, target_conformer_group_id=foreign.id), "thermo_target_group_not_allowed"),
        # single_conformer, no group
        (
            ThermoCreate.model_construct(**base, thermodynamic_target_kind=ThermoTargetKind.single_conformer),
            "thermo_target_group_required",
        ),
        # a group that is another species entry's
        (
            ThermoCreate.model_construct(
                **base, thermodynamic_target_kind=ThermoTargetKind.single_conformer, target_conformer_group_id=foreign.id
            ),
            "thermo_target_group_owner_mismatch",
        ),
        # a protocol the schema would never have produced
        (ThermoCreate.model_construct(**base, protocol_declaration={"version": 9}), "thermo_protocol_version_unsupported"),
        (
            ThermoCreate.model_construct(
                **base, protocol_declaration={"version": 1, "supporting_calculations": [{"calculation_key": "opt0"}]}
            ),
            "thermo_declaration_invalid",
        ),
    ]
    for create, code in cases:
        with pytest.raises(CodedValueError) as caught:
            persist_thermo(db_session, create)
        assert caught.value.code == code, (code, caught.value.code)


def test_the_last_stop_stores_the_validated_form_not_what_it_was_handed(db_session):
    entry = _entry(db_session)
    create = ThermoCreate.model_construct(
        species_entry_id=entry.id,
        scientific_origin="computed",
        protocol_declaration={"version": 1, "recipe": {"name": "g4"}, "supporting_calculations": []},
    )
    thermo = persist_thermo(db_session, create)
    assert thermo.protocol_declaration == {"version": 1, "recipe": {"name": "g4"}}


def test_resolve_refuses_a_conformer_key_nothing_declared(db_session):
    entry = _entry(db_session)
    group = make_conformer_group(db_session, entry)
    payload = ThermoUploadRequest.model_construct(
        thermodynamic_target=ThermoTargetDeclaration.model_construct(
            kind=ThermoTargetKind.single_conformer, conformer_group_ref=None, conformer_key="c9"
        ),
        protocol=None,
    )
    with pytest.raises(CodedValueError) as caught:
        resolve_thermo_declarations(
            db_session, payload, species_entry_id=entry.id, conformer_group_ids_by_key={"c0": group.id}
        )
    assert caught.value.code == "conformer_key_undeclared"
    assert caught.value.context["declared_keys"] == ["c0"]
    # And the declared one resolves.
    ok = ThermoUploadRequest.model_construct(
        thermodynamic_target=ThermoTargetDeclaration.model_construct(
            kind=ThermoTargetKind.single_conformer, conformer_group_ref=None, conformer_key="c0"
        ),
        protocol=None,
    )
    resolved = resolve_thermo_declarations(
        db_session, ok, species_entry_id=entry.id, conformer_group_ids_by_key={"c0": group.id}
    )
    assert resolved.target_conformer_group_id == group.id


def test_a_key_map_pointing_at_another_entrys_group_is_still_refused(db_session):
    """Bundles resolve keys to this species' own groups by construction; the service does not rely on that."""
    entry = _entry(db_session)
    foreign = make_conformer_group(db_session, _entry(db_session, ETHANE))
    payload = ThermoUploadRequest.model_construct(
        thermodynamic_target=ThermoTargetDeclaration.model_construct(
            kind=ThermoTargetKind.single_conformer, conformer_group_ref=None, conformer_key="c0"
        ),
        protocol=None,
    )
    with pytest.raises(CodedValueError) as caught:
        resolve_thermo_declarations(
            db_session, payload, species_entry_id=entry.id, conformer_group_ids_by_key={"c0": foreign.id}
        )
    assert caught.value.code == "thermo_target_group_owner_mismatch"


def test_the_group_check_runs_on_the_group_row_not_on_the_ref_text(db_session):
    entry = _entry(db_session)
    group = make_conformer_group(db_session, entry)
    assert (
        assert_thermo_declaration_columns(
            db_session,
            species_entry_id=entry.id,
            thermodynamic_target_kind=ThermoTargetKind.single_conformer,
            target_conformer_group_id=group.id,
            protocol_declaration=None,
        )
        is None
    )
    assert db_session.get(ConformerGroup, group.id).species_entry_id == entry.id
