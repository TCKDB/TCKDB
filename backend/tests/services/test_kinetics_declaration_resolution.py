"""The service half of the kinetics declarations: every rule it owns, with the request schema out of the way.

Each rule is enforced in three layers (request schema, ``resolve_kinetics_declarations``, and the
last stop in ``persist_kinetics``), and none may be removed because another exists. A payload
built with ``model_construct`` skips every validator, so these tests are what prove the second
and third layers stand on their own. Each is paired with its accepted neighbour.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload
from tckdb_schemas.kinetics_declarations import (
    KineticsApplicabilityDeclaration,
    KineticsCollider,
    KineticsDeterminationDeclaration,
    KineticsProtocolDeclaration,
)

from app.api.error_contract import CodedValueError
from app.db.models.common import (
    KineticsDirection,
    KineticsRepresentationRole,
    ScientificOriginKind,
)
from app.db.models.kinetics import Kinetics, KineticsDetermination
from app.schemas.entities.kinetics import KineticsCreate
from app.services.kinetics_declaration_resolution import (
    assert_kinetics_declaration_columns,
    determination_identity_hash,
    resolve_kinetics_declarations,
)
from app.services.kinetics_resolution import persist_kinetics
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_literature,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    make_workflow_tool_release,
    next_inchi_key,
)

CLAIM = {"claim_origin": "source_publication"}
N2 = {"smiles": "N#N", "charge": 0, "multiplicity": 1}


@pytest.fixture
def world(db_session):
    h = make_species_entry(db_session, make_species(db_session, smiles="[H]", multiplicity=2, inchi_key=next_inchi_key("SRH")))
    h2 = make_species_entry(db_session, make_species(db_session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("SRI")))
    stranger = make_species_entry(db_session, make_species(db_session, smiles="CC", multiplicity=1, inchi_key=next_inchi_key("SRS")))
    reaction = make_chem_reaction(db_session, reactants=[h.species, h.species], products=[h2.species], reversible=False)
    entry = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[h, h], product_entries=[h2])
    other = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[h, h], product_entries=[h2])
    ts_entry = make_transition_state_entry(
        db_session, transition_state=make_transition_state(db_session, reaction_entry=entry), multiplicity=2
    )
    foreign_reaction = make_chem_reaction(db_session, reactants=[h2.species], products=[h.species, h.species], reversible=False)
    foreign_entry = make_reaction_entry(
        db_session, reaction=foreign_reaction, reactant_entries=[h2], product_entries=[h, h]
    )
    foreign_ts = make_transition_state_entry(
        db_session, transition_state=make_transition_state(db_session, reaction_entry=foreign_entry), multiplicity=2
    )
    return SimpleNamespace(
        entry=entry,
        other=other,
        ts_entry=ts_entry,
        foreign_ts=foreign_ts,
        literature=make_literature(db_session),
        other_literature=make_literature(db_session),
        tool=make_workflow_tool_release(db_session),
        h_calc=make_calculation(db_session, species_entry_id=h.id),
        stranger_calc=make_calculation(db_session, species_entry_id=stranger.id),
    )


def _payload(**fields):
    base = {
        "direction": "forward",
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "is_third_body": False,
        "reaction": SimpleNamespace(reactants=[1, 2], products=[1]),
        "determination": None,
        "applicability": None,
        "protocol": None,
        "network_kinetics_ref": None,
        "pressure_context": None,
        "pressure_bar": None,
        "falloff": None,
        "third_body_efficiencies": [],
    }
    base.update(fields)
    return SimpleNamespace(**base)


def _determination(**fields):
    return KineticsDeterminationDeclaration.model_construct(
        determination_ref=fields.get("determination_ref"),
        key=fields.get("key", "run-1"),
        target_kind=fields.get("target_kind", "whole_reaction"),
        transition_state_entry_ref=fields.get("transition_state_entry_ref"),
        network_ref=fields.get("network_ref"),
        channel_key=fields.get("channel_key"),
        representation_role=fields.get("representation_role", "complete"),
    )


def _resolve(db_session, world, payload, **kw):
    kw.setdefault("literature_id", world.literature.id)
    kw.setdefault("workflow_tool_release_id", None)
    return resolve_kinetics_declarations(db_session, payload, reaction_entry=world.entry, **kw)


def _refusal(exc_info) -> tuple[str, dict]:
    return exc_info.value.code, exc_info.value.context


# ---------------------------------------------------------------------------
# resolve_kinetics_declarations on payloads that never saw a validator
# ---------------------------------------------------------------------------


def test_nothing_declared_resolves_to_nothing(db_session, world):
    resolved = _resolve(db_session, world, _payload())
    assert (resolved.determination_id, resolved.representation_role) == (None, None)
    assert (resolved.applicability_declaration, resolved.protocol_declaration) == (None, None)


def test_a_determination_by_content_is_created_once_and_reused(db_session, world):
    first = _resolve(db_session, world, _payload(determination=_determination()))
    again = _resolve(db_session, world, _payload(determination=_determination()))
    assert first.determination_id == again.determination_id is not None
    assert first.representation_role is KineticsRepresentationRole.complete
    other_key = _resolve(db_session, world, _payload(determination=_determination(key="run-2")))
    assert other_key.determination_id != first.determination_id


def test_the_identity_of_a_determination_includes_every_field_of_its_content(db_session, world):
    base = {
        "reaction_entry_id": world.entry.id,
        "direction": "forward",
        "target_kind": "whole_reaction",
        "target_transition_state_entry_id": None,
        "target_network_channel_id": None,
        "literature_id": world.literature.id,
        "workflow_tool_release_id": None,
        "determination_key": "k",
    }
    reference = determination_identity_hash(**base)
    changed = {
        "reaction_entry_id": world.other.id,
        "direction": "reverse",
        "target_kind": "resolved_channel",
        "target_transition_state_entry_id": world.ts_entry.id,
        "target_network_channel_id": 7,
        "literature_id": world.other_literature.id,
        "workflow_tool_release_id": world.tool.id,
        "determination_key": "k2",
    }
    for name, value in changed.items():
        assert determination_identity_hash(**{**base, name: value}) != reference, name
    assert determination_identity_hash(**base) == reference
    assert len(reference) == 64


def test_a_concurrent_creation_of_the_same_content_resolves_to_the_one_row(db_session, world, monkeypatch):
    first = _resolve(db_session, world, _payload(determination=_determination()))
    real_scalar = db_session.scalar
    calls = {"n": 0}

    def blind_first_lookup(statement, *args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", blind_first_lookup)
    again = _resolve(db_session, world, _payload(determination=_determination()))
    assert again.determination_id == first.determination_id
    assert len(db_session.scalars(select(KineticsDetermination)).all()) == 1


@pytest.mark.parametrize(
    "build,code",
    [
        (lambda: _determination(key=None), "kinetics_determination_invalid"),
        (lambda: _determination(transition_state_entry_ref="tse_x"), "kinetics_determination_invalid"),
        (lambda: _determination(target_kind="resolved_channel"), "kinetics_determination_invalid"),
        (lambda: _determination(target_kind=None), "kinetics_determination_invalid"),
        (lambda: _determination(representation_role="partial"), "kinetics_determination_invalid"),
    ],
    ids=["no_locator", "whole_names_ts", "channel_names_nothing", "no_target_kind", "unknown_role"],
)
def test_a_self_contradicting_determination_is_refused_without_the_schema(db_session, world, build, code):
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(determination=build()))
    assert _refusal(exc)[0] == code
    assert db_session.scalars(select(KineticsDetermination)).all() == []


def test_a_determination_without_a_direction_or_a_source_is_refused_without_the_schema(db_session, world):
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(direction=None, determination=_determination()))
    assert _refusal(exc)[0] == "kinetics_determination_invalid"
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(determination=_determination()), literature_id=None)
    assert _refusal(exc)[0] == "kinetics_determination_invalid"
    assert db_session.scalars(select(KineticsDetermination)).all() == []
    # Either half of the source is enough.
    assert _resolve(
        db_session,
        world,
        _payload(determination=_determination()),
        literature_id=None,
        workflow_tool_release_id=world.tool.id,
    ).determination_id


@pytest.mark.parametrize(
    "patch,reason",
    [
        ({"reaction_entry": "other"}, "reaction"),
        ({"direction": "reverse"}, "direction"),
        ({"literature_id": "other"}, "source"),
        ({"workflow_tool_release_id": "tool"}, "source"),
    ],
)
def test_a_cited_determination_must_state_the_records_reaction_direction_and_source(db_session, world, patch, reason):
    created = _resolve(db_session, world, _payload(determination=_determination()))
    row = db_session.get(KineticsDetermination, created.determination_id)
    cite = _determination(determination_ref=row.public_ref, key=None, target_kind=None, representation_role="additive_component")
    kwargs = {"reaction_entry": world.entry, "literature_id": world.literature.id, "workflow_tool_release_id": None}
    payload = _payload(determination=cite)
    if "reaction_entry" in patch:
        kwargs["reaction_entry"] = world.other
    if "direction" in patch:
        payload = _payload(determination=cite, direction=patch["direction"])
    if patch.get("literature_id") == "other":
        kwargs["literature_id"] = world.other_literature.id
    if "workflow_tool_release_id" in patch:
        kwargs["workflow_tool_release_id"] = world.tool.id
    with pytest.raises(CodedValueError) as exc:
        resolve_kinetics_declarations(db_session, payload, **kwargs)
    code, context = _refusal(exc)
    assert (code, context["reason"]) == ("kinetics_determination_mismatch", reason)
    # The accepted neighbour: the same statement joins it.
    joined = resolve_kinetics_declarations(
        db_session,
        _payload(determination=cite),
        reaction_entry=world.entry,
        literature_id=world.literature.id,
        workflow_tool_release_id=None,
    )
    assert joined.determination_id == row.id
    assert joined.representation_role is KineticsRepresentationRole.additive_component


def test_applicability_is_revalidated_and_checked_against_the_record_without_the_schema(db_session, world):
    bad_version = KineticsApplicabilityDeclaration.model_construct(
        version=2, phase="gas", claim_origin="source_publication"
    )
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(applicability=bad_version))
    assert _refusal(exc)[0] == "kinetics_declaration_version_unsupported"

    contradiction = {"version": 1, "pressure_dependence": "fixed_pressure", **CLAIM}
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(applicability=contradiction))
    assert _refusal(exc)[0] == "kinetics_declaration_contradicts_record"

    ok = _resolve(
        db_session,
        world,
        _payload(applicability=contradiction, pressure_context="apparent_at_pressure", pressure_bar=1.0),
    )
    assert ok.applicability_declaration["pressure_dependence"] == "fixed_pressure"


def test_a_cited_determinations_target_is_compared_with_the_declared_scope_in_the_service(db_session, world):
    created = _resolve(db_session, world, _payload(determination=_determination()))
    row = db_session.get(KineticsDetermination, created.determination_id)
    cite = _determination(determination_ref=row.public_ref, key=None, target_kind=None, representation_role="complete")
    scope = {"version": 1, "scope": "resolved_channel", **CLAIM}
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(determination=cite, applicability=scope))
    assert _refusal(exc)[0] == "kinetics_declaration_contradicts_record"
    assert _resolve(
        db_session, world, _payload(determination=cite, applicability={**scope, "scope": "whole_reaction"})
    ).determination_id == row.id


def test_a_mixture_naming_one_species_twice_is_refused_in_the_service(db_session, world):
    species_ref = make_species(db_session, smiles="N#N", multiplicity=1, inchi_key=next_inchi_key("SRN")).public_ref
    applicability = KineticsApplicabilityDeclaration.model_construct(
        version=1,
        phase=None,
        observable=None,
        coefficient_basis=None,
        scope=None,
        reaction_order=None,
        rate_progress_convention=None,
        pressure_dependence=None,
        pressure_domain_min_bar=None,
        pressure_domain_max_bar=None,
        collider_kind="fixed_mixture",
        colliders=[
            KineticsCollider.model_construct(species=SpeciesEntryIdentityPayload(**N2), mole_fraction=0.5),
            KineticsCollider.model_construct(species=SpeciesEntryIdentityPayload(**N2), mole_fraction=0.5),
        ],
        default_third_body_efficiency=None,
        claim_origin="source_publication",
    )
    # The shared rule re-validates the declaration from its data and refuses the repeat first.
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(applicability=applicability))
    assert exc.value.code == "kinetics_declaration_invalid"
    # Two spellings of one species are caught after resolution, by the service.
    spelled_twice = {
        "version": 1,
        "collider_kind": "fixed_mixture",
        "colliders": [
            {"species": {"smiles": "N#N", "charge": 0, "multiplicity": 1}, "mole_fraction": 0.5},
            {
                "species": {"smiles": "N#N", "charge": 0, "multiplicity": 1, "electronic_state_label": "X"},
                "mole_fraction": 0.5,
            },
        ],
        **CLAIM,
    }
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(applicability=spelled_twice))
    assert exc.value.code == "kinetics_declaration_invalid"
    assert "colliders[1]" in exc.value.context["field"]
    # The accepted neighbour: two different species.
    distinct = {
        "version": 1,
        "collider_kind": "fixed_mixture",
        "colliders": [
            {"species": {"smiles": "N#N", "charge": 0, "multiplicity": 1}, "mole_fraction": 0.5},
            {"species": {"smiles": "[He]", "charge": 0, "multiplicity": 1}, "mole_fraction": 0.5},
        ],
        **CLAIM,
    }
    stored = _resolve(db_session, world, _payload(applicability=distinct)).applicability_declaration["colliders"]
    assert [c["mole_fraction"] for c in stored] == [0.5, 0.5]
    assert stored[0]["species_ref"] != stored[1]["species_ref"] and species_ref == stored[0]["species_ref"]


def test_protocol_is_revalidated_and_its_calculations_must_belong_to_the_reaction(db_session, world):
    bad_version = KineticsProtocolDeclaration.model_construct(version=2, method_kind=None, supporting_calculations=[])
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(protocol=bad_version))
    assert _refusal(exc)[0] == "kinetics_declaration_version_unsupported"

    origin = {"version": 1, "method_kind": "experimental"}
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(protocol=origin))
    assert _refusal(exc)[0] == "kinetics_declaration_contradicts_record"

    foreign = {
        "version": 1,
        "supporting_calculations": [{"calculation_ref": world.stranger_calc.public_ref, "purpose": "geometry"}],
    }
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(protocol=foreign))
    assert _refusal(exc)[0] == "kinetics_protocol_calculation_owner_mismatch"

    own = {
        "version": 1,
        "supporting_calculations": [
            {"calculation_ref": world.h_calc.public_ref, "purpose": "geometry"},
            {"calculation_ref": world.h_calc.public_ref, "purpose": "frequency"},
        ],
    }
    stored = _resolve(db_session, world, _payload(protocol=own)).protocol_declaration
    assert stored["supporting_calculations"] == [
        {"calculation_ref": world.h_calc.public_ref, "purpose": "geometry"},
        {"calculation_ref": world.h_calc.public_ref, "purpose": "frequency"},
    ]


def test_a_protocol_calculation_key_resolves_through_the_request_namespace(db_session, world):
    protocol = {
        "version": 1,
        "supporting_calculations": [{"calculation_key": "k1", "purpose": "geometry"}],
    }
    stored = _resolve(
        db_session, world, _payload(protocol=protocol), calculations_by_key={"k1": world.h_calc.id}
    ).protocol_declaration
    assert stored["supporting_calculations"] == [{"calculation_ref": world.h_calc.public_ref, "purpose": "geometry"}]
    with pytest.raises(CodedValueError) as exc:
        _resolve(
            db_session, world, _payload(protocol=protocol), calculations_by_key={"k1": world.stranger_calc.id}
        )
    assert _refusal(exc)[0] == "kinetics_protocol_calculation_owner_mismatch"
    assert "k1" in exc.value.context["field"]
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(protocol=protocol), calculations_by_key={})
    assert _refusal(exc)[0] == "calculation_key_undeclared"


def test_a_transition_state_calculation_belongs_to_the_records_reaction(db_session, world):
    ts_calc = make_calculation(db_session, transition_state_entry_id=world.ts_entry.id)
    protocol = {"version": 1, "supporting_calculations": [{"calculation_ref": ts_calc.public_ref, "purpose": "irc"}]}
    assert _resolve(db_session, world, _payload(protocol=protocol)).protocol_declaration
    # Under another reaction entry the same transition state is not "entry"-scoped.
    with pytest.raises(CodedValueError) as exc:
        resolve_kinetics_declarations(
            db_session,
            _payload(protocol=protocol),
            reaction_entry=world.other,
            literature_id=world.literature.id,
            workflow_tool_release_id=None,
            ts_scope="entry",
        )
    assert _refusal(exc)[0] == "kinetics_protocol_calculation_owner_mismatch"
    # The reaction scope (a bundle) accepts a transition state of the same reaction and structures.
    assert resolve_kinetics_declarations(
        db_session,
        _payload(protocol=protocol),
        reaction_entry=world.other,
        literature_id=world.literature.id,
        workflow_tool_release_id=None,
        ts_scope="reaction",
    ).protocol_declaration


# ---------------------------------------------------------------------------
# the last stop: resolved columns judged on their own
# ---------------------------------------------------------------------------


def _columns(db_session, world, **overrides) -> dict:
    created = _resolve(db_session, world, _payload(determination=_determination()))
    base = {
        "reaction_entry_id": world.entry.id,
        "direction": KineticsDirection.forward,
        "scientific_origin": ScientificOriginKind.computed,
        "model_kind": "modified_arrhenius",
        "is_third_body": False,
        "pressure_context": None,
        "pressure_bar": None,
        "literature_id": world.literature.id,
        "workflow_tool_release_id": None,
        "network_kinetics_id": None,
        "determination_id": created.determination_id,
        "representation_role": KineticsRepresentationRole.complete,
        "applicability_declaration": None,
        "protocol_declaration": None,
    }
    base.update(overrides)
    return base


def test_the_last_stop_accepts_what_a_workflow_produces(db_session, world):
    applicability, protocol = assert_kinetics_declaration_columns(db_session, **_columns(db_session, world))
    assert (applicability, protocol) == (None, None)


@pytest.mark.parametrize(
    "override,code",
    [
        ({"representation_role": None}, "kinetics_declaration_invalid"),
        ({"direction": None}, "kinetics_determination_invalid"),
        ({"literature_id": None}, "kinetics_determination_invalid"),
        ({"direction": KineticsDirection.reverse}, "kinetics_determination_mismatch"),
        ({"literature_id": "other"}, "kinetics_determination_mismatch"),
    ],
    ids=["role_missing", "no_direction", "no_source", "other_direction", "other_source"],
)
def test_the_last_stop_refuses_a_bad_determination_link(db_session, world, override, code):
    if override.get("literature_id") == "other":
        override = {"literature_id": world.other_literature.id}
    with pytest.raises(CodedValueError) as exc:
        assert_kinetics_declaration_columns(db_session, **_columns(db_session, world, **override))
    assert exc.value.code == code


def test_the_last_stop_refuses_a_determination_of_another_reaction_entry(db_session, world):
    columns = _columns(db_session, world)
    columns["reaction_entry_id"] = world.other.id
    with pytest.raises(CodedValueError) as exc:
        assert_kinetics_declaration_columns(db_session, **columns)
    assert (exc.value.code, exc.value.context["reason"]) == ("kinetics_determination_mismatch", "reaction")


def test_the_last_stop_refuses_a_role_without_a_determination_and_an_unknown_determination(db_session, world):
    with pytest.raises(CodedValueError) as exc:
        assert_kinetics_declaration_columns(
            db_session, **_columns(db_session, world, determination_id=None)
        )
    assert exc.value.code == "kinetics_declaration_invalid"
    with pytest.raises(Exception) as exc:
        assert_kinetics_declaration_columns(db_session, **_columns(db_session, world, determination_id=10**9))
    assert getattr(exc.value, "code", None) == "unknown_kinetics_determination_ref"
    # No id leaks into the refusal body.
    assert str(10**9) not in str(exc.value)


def test_the_last_stop_judges_loose_declarations_and_stores_them_validated(db_session, world):
    loose_applicability = {
        "version": 1,
        "phase": "gas",
        "pressure_dependence": "independent",
        "pressure_domain_min_bar": None,
        "claim_origin": "source_publication",
    }
    loose_protocol = {"version": 1, "method_kind": "saddle_point_tst", "method_other_name": None, "supporting_calculations": []}
    applicability, protocol = assert_kinetics_declaration_columns(
        db_session,
        **_columns(
            db_session, world, determination_id=None, representation_role=None,
            applicability_declaration=loose_applicability, protocol_declaration=loose_protocol,
        ),
    )
    # Canonical stored form: nothing the depositor did not state, nothing defaulted in.
    assert applicability == {
        "version": 1,
        "phase": "gas",
        "pressure_dependence": "independent",
        "claim_origin": "source_publication",
    }
    assert protocol == {"version": 1, "method_kind": "saddle_point_tst"}


@pytest.mark.parametrize(
    "declaration,code",
    [
        ({"version": 2, "phase": "gas", **CLAIM}, "kinetics_declaration_version_unsupported"),
        ({"version": True, "phase": "gas", **CLAIM}, "kinetics_declaration_version_unsupported"),
        ({"version": 1, "phase": "gas"}, "kinetics_declaration_invalid"),
        ({"version": 1, "phase": "gas", **CLAIM, "extra": 1}, "kinetics_declaration_invalid"),
        (
            {"version": 1, "pressure_dependence": "fixed_pressure", **CLAIM},
            "kinetics_declaration_contradicts_record",
        ),
    ],
)
def test_the_last_stop_refuses_an_applicability_declaration_that_is_not_valid_or_contradicts_the_row(
    db_session, world, declaration, code
):
    columns = _columns(db_session, world, determination_id=None, representation_role=None)
    with pytest.raises(CodedValueError) as exc:
        assert_kinetics_declaration_columns(db_session, **{**columns, "applicability_declaration": declaration})
    assert exc.value.code == code


@pytest.mark.parametrize(
    "declaration,code",
    [
        ({"version": 2, "method_kind": "experimental"}, "kinetics_declaration_version_unsupported"),
        ({"version": 1}, "kinetics_declaration_invalid"),
        ({"version": 1, "method_kind": "experimental"}, "kinetics_declaration_contradicts_record"),
    ],
)
def test_the_last_stop_refuses_a_protocol_declaration_that_is_not_valid_or_contradicts_the_row(
    db_session, world, declaration, code
):
    columns = _columns(db_session, world, determination_id=None, representation_role=None)
    with pytest.raises(CodedValueError) as exc:
        assert_kinetics_declaration_columns(db_session, **{**columns, "protocol_declaration": declaration})
    assert exc.value.code == code


def test_the_last_stop_refuses_a_stored_supporting_calculation_of_another_reaction(db_session, world):
    columns = _columns(db_session, world, determination_id=None, representation_role=None)
    declaration = {
        "version": 1,
        "supporting_calculations": [{"calculation_ref": world.stranger_calc.public_ref, "purpose": "geometry"}],
    }
    with pytest.raises(CodedValueError) as exc:
        assert_kinetics_declaration_columns(db_session, **{**columns, "protocol_declaration": declaration})
    assert exc.value.code == "kinetics_protocol_calculation_owner_mismatch"
    ok = {"version": 1, "supporting_calculations": [{"calculation_ref": world.h_calc.public_ref, "purpose": "geometry"}]}
    assert assert_kinetics_declaration_columns(db_session, **{**columns, "protocol_declaration": ok})[1] == ok


def test_persist_kinetics_is_the_last_stop_for_a_payload_that_never_saw_a_workflow(db_session, world):
    columns = _columns(db_session, world)
    refused = KineticsCreate.model_construct(
        reaction_entry_id=world.other.id,  # a determination of another entry
        scientific_origin=ScientificOriginKind.computed,
        model_kind="modified_arrhenius",
        direction=KineticsDirection.forward,
        is_third_body=False,
        determination_id=columns["determination_id"],
        representation_role=KineticsRepresentationRole.complete,
        applicability_declaration=None,
        protocol_declaration=None,
        literature_id=world.literature.id,
        workflow_tool_release_id=None,
        software_release_id=None,
        network_kinetics_id=None,
        pressure_context=None,
        pressure_bar=None,
        a=1.0, a_units=None, n=None, t0_k=1.0, ea_kj_mol=None,
        a_uncertainty=None, a_uncertainty_kind=None, n_uncertainty=None, ea_uncertainty_kj_mol=None,
        tmin_k=None, tmax_k=None, degeneracy=None, degeneracy_convention="unknown",
        tunneling_model=None, note=None, source_calculations=[],
    )
    with pytest.raises(CodedValueError) as exc:
        persist_kinetics(db_session, refused)
    assert exc.value.code == "kinetics_determination_mismatch"
    assert db_session.scalars(select(Kinetics)).all() == []

    accepted = KineticsCreate.model_construct(**{**refused.__dict__, "reaction_entry_id": world.entry.id})
    row = persist_kinetics(db_session, accepted)
    assert row.determination_id == columns["determination_id"]


# ---------------------------------------------------------------------------
# currency: legacy digests are untouched, a declared meaning is part of them
# ---------------------------------------------------------------------------


def _legacy_row(db_session, world) -> Kinetics:
    row = Kinetics(reaction_entry_id=world.entry.id, scientific_origin=ScientificOriginKind.computed, a=1.0)
    db_session.add(row)
    db_session.flush()
    return row


def test_a_record_with_no_declaration_gains_no_key_in_either_digest(db_session, world):
    from app.services.consistency.core import snapshot
    from app.services.reproducibility_rubric import _mapped_columns, _target_snapshot

    row = _legacy_row(db_session, world)
    names = {"determination_id", "representation_role", "applicability_declaration", "protocol_declaration"}
    assert names.isdisjoint(snapshot(row)) and names.isdisjoint(_mapped_columns(row))
    from app.db.models.common import SubmissionRecordType

    target = _target_snapshot(row, SubmissionRecordType.kinetics)
    assert "determination" not in target["relationships"]


@pytest.mark.parametrize(
    "assignment",
    [
        {"protocol_declaration": {"version": 1, "method_kind": "saddle_point_tst"}},
        {"applicability_declaration": {"version": 1, "phase": "gas", **CLAIM}},
    ],
    ids=["protocol", "applicability"],
)
def test_a_populated_declaration_changes_both_digests(db_session, world, assignment):
    from app.services.consistency.core import encoded, snapshot
    from app.services.reproducibility_rubric import _mapped_columns

    row = _legacy_row(db_session, world)
    before = (encoded(snapshot(row)), encoded(_mapped_columns(row)))
    for name, value in assignment.items():
        setattr(row, name, value)
    assert (encoded(snapshot(row)), encoded(_mapped_columns(row))) != before


def test_the_linked_determinations_content_is_part_of_both_snapshots_when_there_is_one(db_session, world):
    from app.db.models.common import SubmissionRecordType
    from app.services.consistency.core import snapshot
    from app.services.consistency.kinetics import _with_determination
    from app.services.reproducibility_rubric import _target_snapshot

    created = _resolve(db_session, world, _payload(determination=_determination()))
    row = _legacy_row(db_session, world)
    row.direction = KineticsDirection.forward
    row.determination_id = created.determination_id
    row.representation_role = KineticsRepresentationRole.complete
    db_session.flush()
    db_session.refresh(row)

    captured = _with_determination(row, snapshot(row))
    assert captured["determination"]["determination_key"] == "run-1"
    assert "identity_hash" in captured["determination"]
    target = _target_snapshot(row, SubmissionRecordType.kinetics)
    assert target["relationships"]["determination"]["determination_key"] == "run-1"
    assert _with_determination(_legacy_row(db_session, world), {"x": 1}) == {"x": 1}


# ---------------------------------------------------------------------------
# replacement of an accepted record: same declared target
# ---------------------------------------------------------------------------


def _approved_pair(db_session, world, *, old_determination, new_determination):
    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole, SubmissionRecordType
    from tests.db.test_accepted_science_immutability import _approve

    actor = AppUser(username="supersession-curator", role=AppUserRole.curator)
    db_session.add(actor)
    db_session.flush()
    rows = []
    for determination in (old_determination, new_determination):
        row = Kinetics(
            reaction_entry_id=world.entry.id,
            scientific_origin=ScientificOriginKind.computed,
            a=1.0,
            direction=KineticsDirection.forward,
            literature_id=world.literature.id,
        )
        if determination is not None:
            row.determination_id = determination.id
            row.representation_role = KineticsRepresentationRole.complete
        db_session.add(row)
        db_session.flush()
        _approve(db_session, SubmissionRecordType.kinetics, row.id, actor)
        rows.append(row)
    return actor, rows


def _make_determination(db_session, world, key, **target):
    det = KineticsDetermination(
        reaction_entry_id=world.entry.id,
        direction=KineticsDirection.forward,
        target_kind=target.get("kind", "whole_reaction"),
        target_transition_state_entry_id=target.get("ts"),
        literature_id=world.literature.id,
        determination_key=key,
        identity_hash=hashlib.sha256(key.encode()).hexdigest(),
    )
    db_session.add(det)
    db_session.flush()
    return det


def test_a_replacement_must_be_a_determination_of_the_same_target(db_session, world):
    from app.api.errors import DomainError
    from app.db.models.common import SubmissionRecordType
    from app.services.scientific_record_supersession import supersede_scientific_record

    whole = _make_determination(db_session, world, "whole")
    channel = _make_determination(db_session, world, "channel", kind="resolved_channel", ts=world.ts_entry.id)
    actor, (old, new) = _approved_pair(db_session, world, old_determination=whole, new_determination=channel)
    with pytest.raises(DomainError, match="same determination target"), db_session.begin_nested():
        supersede_scientific_record(
            db_session, record_type=SubmissionRecordType.kinetics, superseded_record_id=old.id,
            superseding_record_id=new.id, actor=actor, reason="re-measured",
        )


def test_a_replacement_with_a_different_key_but_the_same_target_is_allowed(db_session, world):
    from app.db.models.common import SubmissionRecordType
    from app.services.scientific_record_supersession import supersede_scientific_record

    a = _make_determination(db_session, world, "first-measurement")
    b = _make_determination(db_session, world, "re-measurement")
    actor, (old, new) = _approved_pair(db_session, world, old_determination=a, new_determination=b)
    edge = supersede_scientific_record(
        db_session, record_type=SubmissionRecordType.kinetics, superseded_record_id=old.id,
        superseding_record_id=new.id, actor=actor, reason="re-measured",
    )
    assert edge.superseded_record_id == old.id


@pytest.mark.parametrize("which", ["old_undeclared", "new_undeclared", "both_undeclared"])
def test_legacy_absence_does_not_block_a_replacement_and_is_not_papered_over(db_session, world, which):
    from app.db.models.common import SubmissionRecordType
    from app.services.scientific_record_supersession import supersede_scientific_record

    det = _make_determination(db_session, world, "declared")
    old_det = None if which in {"old_undeclared", "both_undeclared"} else det
    new_det = None if which in {"new_undeclared", "both_undeclared"} else det
    actor, (old, new) = _approved_pair(db_session, world, old_determination=old_det, new_determination=new_det)
    supersede_scientific_record(
        db_session, record_type=SubmissionRecordType.kinetics, superseded_record_id=old.id,
        superseding_record_id=new.id, actor=actor, reason="legacy replacement",
    )
    # The absence stays visible on the record that has none.
    assert (old.determination_id is None) == (old_det is None)
    assert (new.determination_id is None) == (new_det is None)


# ---------------------------------------------------------------------------
# release: the determination ships with the record that belongs to it
# ---------------------------------------------------------------------------


def test_a_released_kinetics_record_carries_its_determination_without_ids_or_the_identity_hash(db_session, world):
    from app.db.models.common import SubmissionRecordType
    from app.services.release.records import serialize_records
    from app.services.scientific_read.internal_ids import is_internal_id_key

    det = _make_determination(db_session, world, "ship-it")
    row = Kinetics(
        reaction_entry_id=world.entry.id, scientific_origin=ScientificOriginKind.computed, a=1.0,
        direction=KineticsDirection.forward, literature_id=world.literature.id,
        determination_id=det.id, representation_role=KineticsRepresentationRole.complete,
        protocol_declaration={"version": 1, "method_kind": "saddle_point_tst"},
    )
    db_session.add(row)
    db_session.flush()
    legacy = _legacy_row(db_session, world)

    out = serialize_records(db_session, record_type=SubmissionRecordType.kinetics, record_ids=[row.id, legacy.id])
    shipped, unshipped = out[row.id], out[legacy.id]
    assert shipped["determination_ref"] == det.public_ref
    assert shipped["representation_role"] == "complete"
    assert shipped["protocol_declaration"] == {"version": 1, "method_kind": "saddle_point_tst"}
    block = shipped["determination"]
    assert block["determination_key"] == "ship-it" and block["direction"] == "forward"
    assert block["target_kind"] == "whole_reaction" and block["literature_ref"] == world.literature.public_ref
    assert "identity_hash" not in block
    assert not [key for key in block if is_internal_id_key(key)]
    assert block["reaction_entry_ref"] == world.entry.public_ref
    # A record with no determination ships none, and says so by absence.
    assert "determination" not in unshipped and unshipped.get("determination_ref") is None


def test_the_archive_and_release_registries_account_for_the_determination_table():
    from app.db.base import Base
    from app.services.archive.registry import INCLUDED_TABLES
    from app.services.release.records import RECORD_CHILD_EXCLUSIONS

    assert "kinetics_determination" in Base.metadata.tables
    assert "kinetics_determination" in INCLUDED_TABLES
    assert ("transition_state_entry", "kinetics_determination") in RECORD_CHILD_EXCLUSIONS


def test_a_channel_target_must_be_a_transition_state_of_the_records_reaction_in_the_service(db_session, world):
    """The route anchors the entry first, so only a bundle or a direct caller reaches this check."""
    own = _determination(target_kind="resolved_channel", transition_state_entry_ref=world.ts_entry.public_ref)
    resolved = _resolve(db_session, world, _payload(determination=own))
    assert db_session.get(KineticsDetermination, resolved.determination_id).target_transition_state_entry_id == (
        world.ts_entry.id
    )
    foreign = _determination(target_kind="resolved_channel", transition_state_entry_ref=world.foreign_ts.public_ref)
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(determination=foreign))
    assert (exc.value.code, exc.value.context["reason"]) == ("kinetics_determination_mismatch", "target")
    assert exc.value.context["field"] == "determination.transition_state_entry_ref"
    # Under the reaction scope a bundle uses, the same transition state of the same reaction is accepted
    # for another entry of that reaction, and the foreign one is still refused.
    assert resolve_kinetics_declarations(
        db_session, _payload(determination=own), reaction_entry=world.other,
        literature_id=world.literature.id, workflow_tool_release_id=None, ts_scope="reaction",
    ).determination_id
    with pytest.raises(CodedValueError):
        resolve_kinetics_declarations(
            db_session, _payload(determination=foreign), reaction_entry=world.other,
            literature_id=world.literature.id, workflow_tool_release_id=None, ts_scope="reaction",
        )


# ---------------------------------------------------------------------------
# #694 review: the target is part of identity, and a determination holds one role
# ---------------------------------------------------------------------------


def test_the_same_key_on_one_entry_with_a_different_target_is_a_different_determination(db_session, world):
    whole = _resolve(db_session, world, _payload(determination=_determination(key="same")))
    channel = _resolve(
        db_session,
        world,
        _payload(
            determination=_determination(
                key="same", target_kind="resolved_channel", transition_state_entry_ref=world.ts_entry.public_ref
            )
        ),
    )
    assert whole.determination_id != channel.determination_id
    rows = db_session.scalars(select(KineticsDetermination).where(KineticsDetermination.determination_key == "same")).all()
    assert len(rows) == 2 and {r.target_kind.value for r in rows} == {"whole_reaction", "resolved_channel"}
    # Restating either one joins it rather than minting a third.
    again = _resolve(db_session, world, _payload(determination=_determination(key="same")))
    assert again.determination_id == whole.determination_id


def test_the_same_key_on_one_entry_with_two_different_transition_states_is_two_determinations(db_session, world):
    other_ts = make_transition_state_entry(db_session, transition_state=world.ts_entry.transition_state, multiplicity=2)

    def channel(ts):
        return _resolve(
            db_session,
            world,
            _payload(
                determination=_determination(
                    key="same", target_kind="resolved_channel", transition_state_entry_ref=ts.public_ref
                )
            ),
        )

    first, second = channel(world.ts_entry), channel(other_ts)
    assert first.determination_id != second.determination_id
    assert channel(world.ts_entry).determination_id == first.determination_id, "restating one joins it"


def _record_of(db_session, world, resolved, role):
    row = Kinetics(
        reaction_entry_id=world.entry.id,
        scientific_origin=ScientificOriginKind.computed,
        a=1.0,
        direction=KineticsDirection.forward,
        literature_id=world.literature.id,
        determination_id=resolved.determination_id,
        representation_role=role,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_a_determination_holds_one_role_and_a_record_of_the_other_role_is_refused(db_session, world):
    first = _resolve(db_session, world, _payload(determination=_determination()))
    _record_of(db_session, world, first, KineticsRepresentationRole.complete)
    same = _resolve(db_session, world, _payload(determination=_determination()))
    assert same.determination_id == first.determination_id
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(determination=_determination(representation_role="additive_component")))
    assert exc.value.code == "kinetics_determination_mismatch" and exc.value.context["reason"] == "role"
    # The other way round: additive components first, a complete record second.
    other = _resolve(db_session, world, _payload(determination=_determination(key="parts", representation_role="additive_component")))
    _record_of(db_session, world, other, KineticsRepresentationRole.additive_component)
    with pytest.raises(CodedValueError) as exc:
        _resolve(db_session, world, _payload(determination=_determination(key="parts")))
    assert exc.value.context["reason"] == "role"


def test_the_last_stop_refuses_a_second_role_in_a_determination_too(db_session, world):
    columns = _columns(db_session, world)
    _record_of(db_session, world, SimpleNamespace(determination_id=columns["determination_id"]), KineticsRepresentationRole.complete)
    assert assert_kinetics_declaration_columns(db_session, **columns) == (None, None)
    columns["representation_role"] = KineticsRepresentationRole.additive_component
    with pytest.raises(CodedValueError) as exc:
        assert_kinetics_declaration_columns(db_session, **columns)
    assert exc.value.code == "kinetics_determination_mismatch" and exc.value.context["reason"] == "role"


def test_the_last_stop_reads_the_products_for_a_reverse_order(db_session, world):
    # H + H -> H2: the reverse coefficient is of the one product, first order; the forward one is second order.
    from tckdb_schemas.kinetics_declarations import StoredKineticsApplicabilityDeclaration

    def declared(order):
        return StoredKineticsApplicabilityDeclaration(version=1, claim_origin="source_publication", reaction_order=order)

    columns = _columns(db_session, world, determination_id=None, representation_role=None)
    def at(direction, order):
        return {**columns, "direction": direction, "applicability_declaration": declared(order)}

    assert assert_kinetics_declaration_columns(db_session, **at(KineticsDirection.reverse, 1))
    with pytest.raises(CodedValueError) as exc:
        assert_kinetics_declaration_columns(db_session, **at(KineticsDirection.reverse, 2))
    assert exc.value.code == "kinetics_declaration_contradicts_record"
    assert assert_kinetics_declaration_columns(db_session, **at(KineticsDirection.forward, 2))
