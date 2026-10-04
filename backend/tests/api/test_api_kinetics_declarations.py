"""Kinetics determination, applicability and protocol declarations over HTTP.

``/uploads/kinetics`` and ``/uploads/computed-reaction`` each build the row (the bundle builds
``Kinetics`` itself), so each is asserted against the database and read back through the
scientific kinetics endpoint. A record that declares nothing must read ``null`` for all three,
a deposit that omits the fields must keep working exactly as before, and every refusal is paired
with an accepted neighbour built from the same fixtures so that a route that refuses everything
cannot pass.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload

from app.db.models.common import (
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkStateKind,
    SubmissionRecordType,
)
from app.db.models.kinetics import Kinetics, KineticsDetermination
from app.db.models.network_pdep import NetworkChannelMicroReaction
from app.services.species_resolution import resolve_species
from tests.services.scientific_read._factories import (
    attach_network_state_participant,
    make_calculation,
    make_chem_reaction,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

KINETICS = "/api/v1/uploads/kinetics"
BUNDLE = "/api/v1/uploads/computed-reaction"
TOOL = {"name": "Arkane", "version": "3.2"}
OTHER_TOOL = {"name": "Arkane", "version": "3.3"}
SOFTWARE = {"name": "Gaussian", "version": "16"}
LOT = {"method": "wb97xd", "basis": "def2tzvp"}
XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"
CLAIM = {"claim_origin": "source_publication"}
N2 = {"smiles": "N#N", "charge": 0, "multiplicity": 1}
HE = {"smiles": "[He]", "charge": 0, "multiplicity": 1}
WHOLE = {"kind": "whole_reaction"}

REACTION = {
    "reversible": False,
    "reactants": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
    ],
    "products": [{"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}}],
}


def _determination(key: str = "run-1", role: str = "complete", target: dict | None = None) -> dict:
    target = target or WHOLE
    return {
        "key": key,
        "target_kind": target["kind"],
        **{name: value for name, value in target.items() if name != "kind"},
        "representation_role": role,
    }


def _standalone(**extra) -> dict:
    body = {
        "reaction": REACTION,
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "n": 0.5,
        "reported_ea": 10.0,
        "reported_ea_units": "kj_mol",
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
        "direction": "forward",
        "workflow_tool_release": TOOL,
    }
    body.update(extra)
    return body


def _species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": SOFTWARE,
                    "level_of_theory": LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }


def _bundle(*, root: dict | None = None, **kinetics) -> dict:
    fit = {
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "n": 0.5,
        "reported_ea": 10.0,
        "reported_ea_units": "kj_mol",
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
    }
    fit.update(kinetics)
    return {
        "species": [_species("h", "[H]", 2, XYZ_H), _species("h2", "[H][H]", 1, XYZ_H2)],
        "reversible": True,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
        "workflow_tool_release": TOOL,
        "kinetics": [fit],
        **(root or {}),
    }


def _code(response, status: int = 422) -> str:
    assert response.status_code == status, response.text[:800]
    return response.json()["code"]


def _latest(db_session) -> Kinetics:
    return db_session.scalars(select(Kinetics).order_by(Kinetics.id.desc()).limit(1)).one()


def _count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model))


def _read(client, kinetics: Kinetics) -> dict:
    response = client.get(f"/api/v1/scientific/reaction-entries/{kinetics.reaction_entry_id}/kinetics")
    assert response.status_code == 200, response.text[:800]
    (record,) = [r for r in response.json()["records"] if r["kinetics_ref"] == kinetics.public_ref]
    return record


def _stated(value):
    """What a read states: nulls are "not said", so drop them (and empty lists defaults)."""
    if isinstance(value, dict):
        return {
            k: _stated(v)
            for k, v in value.items()
            if v is not None and not (k in {"supporting_calculations", "colliders"} and v == [])
        }
    if isinstance(value, list):
        return [_stated(v) for v in value]
    return value


@pytest.fixture
def corpus(db_session):
    """``H + H -> H2`` stored, with a transition state and one calculation per subject."""
    h = make_species_entry(
        db_session, make_species(db_session, smiles="[H]", multiplicity=2, inchi_key=next_inchi_key("KDH"))
    )
    h2 = make_species_entry(
        db_session, make_species(db_session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("KDI"))
    )
    reaction = make_chem_reaction(db_session, reactants=[h.species, h.species], products=[h2.species], reversible=False)
    entry = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[h, h], product_entries=[h2])
    ts_entry = make_transition_state_entry(
        db_session, transition_state=make_transition_state(db_session, reaction_entry=entry), multiplicity=2
    )
    stranger = make_species_entry(
        db_session, make_species(db_session, smiles="CC", multiplicity=1, inchi_key=next_inchi_key("KDS"))
    )
    return SimpleNamespace(
        h=h,
        h2=h2,
        entry=entry,
        ts_entry=ts_entry,
        h_calc=make_calculation(db_session, species_entry_id=h.id),
        ts_calc=make_calculation(db_session, transition_state_entry_id=ts_entry.id),
        stranger_calc=make_calculation(db_session, species_entry_id=stranger.id),
    )


# ---------------------------------------------------------------------------
# A deposit that declares nothing is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["standalone", "bundle"])
def test_a_deposit_that_declares_nothing_is_accepted_and_reads_null(client, db_session, route):
    response = client.post(KINETICS, json=_standalone()) if route == "standalone" else client.post(BUNDLE, json=_bundle())
    assert response.status_code == 201, response.text[:800]

    row = _latest(db_session)
    assert (row.determination_id, row.representation_role, row.applicability_declaration, row.protocol_declaration) == (
        None,
        None,
        None,
        None,
    )
    assert _count(db_session, KineticsDetermination) == 0
    record = _read(client, row)
    # Present and null: "not stated", never "standalone", never "universally valid".
    for field in ("determination", "applicability", "protocol"):
        assert field in record and record[field] is None
    assert record["declaration_unreadable"] is False


# ---------------------------------------------------------------------------
# /uploads/kinetics: determinations
# ---------------------------------------------------------------------------


def test_a_determination_by_content_is_created_stored_and_read_back(client, db_session):
    response = client.post(KINETICS, json=_standalone(determination=_determination("burke-set-A")))
    assert response.status_code == 201, response.text[:800]

    row = _latest(db_session)
    det = db_session.get(KineticsDetermination, row.determination_id)
    assert (det.determination_key, det.direction.value, det.target_kind.value) == ("burke-set-A", "forward", "whole_reaction")
    assert (det.reaction_entry_id, det.literature_id is None, det.workflow_tool_release_id is not None) == (
        row.reaction_entry_id,
        True,
        True,
    )
    assert row.representation_role.value == "complete"
    assert len(det.identity_hash) == 64

    record = _read(client, row)
    assert record["determination"] == {
        "determination_ref": det.public_ref,
        "key": "burke-set-A",
        "direction": "forward",
        "target": {
            "kind": "whole_reaction",
            "transition_state_entry_ref": None,
            "network_ref": None,
            "channel_key": None,
        },
        "representation_role": "complete",
    }
    # Pinned to exact paths: refs and strings only, no id of any kind inside the block.
    flat = []

    def walk(value, path):
        if isinstance(value, dict):
            for key, inner in value.items():
                walk(inner, f"{path}.{key}")
        else:
            flat.append((path, value))

    walk(record["determination"], "determination")
    assert all(value is None or isinstance(value, str) for _, value in flat), flat
    assert not any("id" == path.rsplit(".", 1)[-1] or path.endswith("_id") for path, _ in flat)


CHEBYSHEV = {
    "model_kind": "chebyshev",
    "a": None,
    "a_units": None,
    "n": None,
    "reported_ea": None,
    "reported_ea_units": None,
    "tmin_k": None,
    "tmax_k": None,
    "chebyshev": {
        "n_temperature": 1,
        "n_pressure": 1,
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
        "pmin_bar": 0.1,
        "pmax_bar": 10.0,
        "coefficients": [[1.0]],
    },
    "pressure_context": "pressure_dependent",
}


def test_a_determination_stated_twice_by_content_is_two_determinations_because_each_upload_has_its_own_entry(
    client, db_session
):
    """Every standalone upload mints its own reaction entry, and a determination is of one entry."""
    for _ in range(2):
        assert client.post(KINETICS, json=_standalone(determination=_determination("set-A"))).status_code == 201
    a, b = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    assert a.reaction_entry_id != b.reaction_entry_id
    assert a.determination_id != b.determination_id


def test_alternate_representations_join_a_determination_by_citing_its_ref(client, db_session):
    first = client.post(KINETICS, json=_standalone(determination=_determination("set-A")))
    assert first.status_code == 201, first.text[:800]
    det = db_session.scalars(select(KineticsDetermination)).one()
    second = client.post(
        KINETICS,
        json=_standalone(
            **CHEBYSHEV, determination={"determination_ref": det.public_ref, "representation_role": "complete"}
        ),
    )
    assert second.status_code == 201, second.text[:800]
    a, b = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    # Anchored to the determination's own reaction entry, as a transition-state ref anchors a rate.
    assert a.reaction_entry_id == b.reaction_entry_id
    assert a.determination_id == b.determination_id == det.id
    assert _count(db_session, KineticsDetermination) == 1, "alternate fits are not independent determinations"
    assert _read(client, b)["determination"]["representation_role"] == "complete"
    assert _read(client, a)["determination"]["determination_ref"] == _read(client, b)["determination"]["determination_ref"]


def _post_anchored(client, corpus, **extra):
    return _post_stored_entry_rate(client, corpus, **extra)


@pytest.mark.parametrize(
    "second_body",
    [
        {"determination": _determination("set-B")},
        {"direction": "reverse", "determination": _determination("set-A")},
        {"workflow_tool_release": OTHER_TOOL, "determination": _determination("set-A")},
    ],
    ids=["different_key", "different_direction", "different_source"],
)
def test_a_determination_differing_in_any_identity_field_is_a_different_determination(
    client, db_session, corpus, second_body
):
    assert _post_anchored(client, corpus, determination=_determination("set-A")).status_code == 201
    assert _post_anchored(client, corpus, **second_body).status_code == 201
    a, b = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    assert a.reaction_entry_id == b.reaction_entry_id == corpus.entry.id
    assert a.determination_id != b.determination_id
    assert _count(db_session, KineticsDetermination) == 2


def test_identical_content_on_one_reaction_entry_resolves_to_the_one_determination(client, db_session, corpus):
    for _ in range(2):
        assert _post_anchored(client, corpus, determination=_determination()).status_code == 201
    assert _count(db_session, KineticsDetermination) == 1
    assert _count(db_session, Kinetics) == 2


@pytest.mark.parametrize(
    "patch,reason",
    [
        ({"direction": "reverse"}, "direction"),
        ({"workflow_tool_release": OTHER_TOOL}, "source"),
        (
            {
                "reaction": {
                    "reversible": False,
                    "reactants": [{"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}}],
                    "products": [
                        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                    ],
                },
                "a_units": "per_s",
            },
            "reaction",
        ),
    ],
    ids=["direction", "source", "reaction"],
)
def test_joining_a_determination_that_states_something_else_is_refused_and_nothing_is_stored(
    client, db_session, patch, reason
):
    assert client.post(KINETICS, json=_standalone(determination=_determination("set-A"))).status_code == 201
    det = db_session.scalars(select(KineticsDetermination)).one()
    rows_before, dets_before = _count(db_session, Kinetics), _count(db_session, KineticsDetermination)

    response = client.post(
        KINETICS,
        json=_standalone(
            determination={"determination_ref": det.public_ref, "representation_role": "complete"}, **patch
        ),
    )
    assert _code(response) == "kinetics_determination_mismatch"
    assert response.json()["context"]["reason"] == reason
    assert (_count(db_session, Kinetics), _count(db_session, KineticsDetermination)) == (rows_before, dets_before)
    assert str(det.id) not in response.text.replace(det.public_ref, "")
    # The accepted neighbour: stating what the determination states joins it.
    joined = client.post(
        KINETICS,
        json=_standalone(determination={"determination_ref": det.public_ref, "representation_role": "complete"}),
    )
    assert joined.status_code == 201, joined.text[:800]


def test_an_unknown_determination_ref_is_a_404_with_a_code(client, db_session):
    response = client.post(
        KINETICS,
        json=_standalone(determination={"determination_ref": "kdet_" + "a" * 26, "representation_role": "complete"}),
    )
    assert _code(response, 404) == "unknown_kinetics_determination_ref"
    assert response.json()["context"]["field"] == "determination.determination_ref"
    assert _count(db_session, Kinetics) == 0


def test_a_determination_needs_a_direction_and_a_source(client, db_session):
    body = _standalone(determination=_determination())
    no_direction = client.post(KINETICS, json={**body, "direction": None})
    assert _code(no_direction) == "kinetics_determination_invalid"
    assert no_direction.json()["context"]["missing"] == "direction"
    no_source = client.post(KINETICS, json={**body, "workflow_tool_release": None})
    assert _code(no_source) == "kinetics_determination_invalid"
    assert no_source.json()["context"]["missing"] == "source"
    assert _count(db_session, Kinetics) == 0
    assert client.post(KINETICS, json=body).status_code == 201


@pytest.mark.parametrize(
    "determination,code",
    [
        ({"representation_role": "complete"}, "kinetics_determination_invalid"),
        ({"key": "k", "representation_role": "complete"}, "kinetics_determination_invalid"),
        (
            {"key": "k", "target_kind": "whole_reaction", "transition_state_entry_ref": "tse_" + "a" * 26,
             "representation_role": "complete"},
            "kinetics_determination_invalid",
        ),
        (
            {"key": "k", "target_kind": "resolved_channel", "representation_role": "complete"},
            "kinetics_determination_invalid",
        ),
    ],
)
def test_determination_shape_refusals_carry_codes(client, db_session, determination, code):
    assert _code(client.post(KINETICS, json=_standalone(determination=determination))) == code
    assert _count(db_session, Kinetics) == 0


def test_unknown_declaration_fields_and_versions_are_refused(client, db_session):
    assert client.post(KINETICS, json=_standalone(determination={**_determination(), "note": "x"})).status_code == 422
    bad_applicability = client.post(
        KINETICS, json=_standalone(applicability={"version": 2, "phase": "gas", **CLAIM})
    )
    assert _code(bad_applicability) == "kinetics_declaration_version_unsupported"
    bad_protocol = client.post(
        KINETICS, json=_standalone(protocol={"version": 2, "method_kind": "saddle_point_tst"})
    )
    assert _code(bad_protocol) == "kinetics_declaration_version_unsupported"
    assert _count(db_session, Kinetics) == 0


# ---------------------------------------------------------------------------
# resolved-channel targets
# ---------------------------------------------------------------------------


def _post_stored_entry_rate(client, corpus, **extra):
    """A standalone rate on the stored corpus reaction entry, anchored by its transition state."""
    return client.post(
        KINETICS,
        json=_standalone(
            tunneling_application={
                "model": "wigner",
                "imaginary_frequency_cm1": -1500.0,
                "transition_state_entry_ref": corpus.ts_entry.public_ref,
            },
            tunneling_model="wigner",
            **extra,
        ),
    )


def test_a_resolved_channel_target_names_a_transition_state_of_the_records_reaction(client, db_session, corpus):
    target = {"kind": "resolved_channel", "transition_state_entry_ref": corpus.ts_entry.public_ref}
    response = _post_stored_entry_rate(client, corpus, determination=_determination("ts-run", target=target))
    assert response.status_code == 201, response.text[:800]
    det = db_session.scalars(select(KineticsDetermination)).one()
    assert (det.target_kind.value, det.target_transition_state_entry_id) == ("resolved_channel", corpus.ts_entry.id)
    assert det.reaction_entry_id == corpus.entry.id
    record = _read(client, _latest(db_session))
    assert record["determination"]["target"] == {
        "kind": "resolved_channel",
        "transition_state_entry_ref": corpus.ts_entry.public_ref,
        "network_ref": None,
        "channel_key": None,
    }


def test_a_transition_state_of_another_reaction_is_refused_with_its_own_code(client, db_session, corpus):
    other_entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(
            db_session, reactants=[corpus.h2.species], products=[corpus.h.species, corpus.h.species], reversible=False
        ),
        reactant_entries=[corpus.h2],
        product_entries=[corpus.h, corpus.h],
    )
    foreign = make_transition_state_entry(
        db_session, transition_state=make_transition_state(db_session, reaction_entry=other_entry), multiplicity=2
    )
    target = {"kind": "resolved_channel", "transition_state_entry_ref": foreign.public_ref}
    response = client.post(KINETICS, json=_standalone(determination=_determination(target=target)))
    assert _code(response) == "kinetics_determination_mismatch"
    assert response.json()["context"]["reason"] == "target"
    assert response.json()["context"]["field"] == "determination.transition_state_entry_ref"
    assert _count(db_session, Kinetics) == 0 and _count(db_session, KineticsDetermination) == 0
    # The neighbour: the record's own transition state is accepted, and anchors the entry.
    own = {"kind": "resolved_channel", "transition_state_entry_ref": corpus.ts_entry.public_ref}
    assert client.post(KINETICS, json=_standalone(determination=_determination(target=own))).status_code == 201
    assert _latest(db_session).reaction_entry_id == corpus.entry.id


def test_an_unknown_transition_state_ref_in_a_target_is_a_404(client, db_session, corpus):
    target = {"kind": "resolved_channel", "transition_state_entry_ref": "tse_" + "a" * 26}
    response = client.post(KINETICS, json=_standalone(determination=_determination(target=target)))
    assert _code(response, 404) == "unknown_transition_state_entry_ref"
    assert response.json()["context"]["field"] == "determination.transition_state_entry_ref"


def _network_with_channel(db_session, corpus, *, micro: bool):
    network = make_network(db_session, name=f"net-{next_inchi_key('KDN')}")
    states = []
    for label in ("a", "b"):
        state = make_network_state(
            db_session, network=network, kind=NetworkStateKind.well, composition_hash=f"{label}{'0' * 63}"
        )
        attach_network_state_participant(db_session, state=state, species_entry=corpus.h)
        states.append(state)
    channel = make_network_channel(
        db_session,
        network=network,
        source_state=states[0],
        sink_state=states[1],
        kind=NetworkChannelKind.isomerization,
        channel_key="W1->W2",
    )
    if micro:
        db_session.add(NetworkChannelMicroReaction(channel_id=channel.id, reaction_entry_id=corpus.entry.id))
        db_session.flush()
    return network, channel


def test_a_network_channel_target_must_belong_to_the_records_reaction(client, db_session, corpus):
    network, channel = _network_with_channel(db_session, corpus, micro=False)
    target = {"kind": "resolved_channel", "network_ref": network.public_ref, "channel_key": "W1->W2"}
    refused = _post_stored_entry_rate(client, corpus, determination=_determination(target=target))
    assert _code(refused) == "kinetics_determination_mismatch"
    assert refused.json()["context"]["reason"] == "target"
    assert response_field(refused) == "determination.network_ref"

    db_session.add(NetworkChannelMicroReaction(channel_id=channel.id, reaction_entry_id=corpus.entry.id))
    db_session.flush()
    accepted = _post_stored_entry_rate(client, corpus, determination=_determination(target=target))
    assert accepted.status_code == 201, accepted.text[:800]
    det = db_session.scalars(select(KineticsDetermination)).one()
    assert det.target_network_channel_id == channel.id
    record = _read(client, _latest(db_session))
    assert (
        record["determination"]["target"]["network_ref"],
        record["determination"]["target"]["channel_key"],
    ) == (network.public_ref, "W1->W2")


def response_field(response) -> str:
    return response.json()["context"]["field"]


def test_a_network_channel_that_is_not_the_linked_solves_channel_is_refused(client, db_session, corpus):
    network, channel = _network_with_channel(db_session, corpus, micro=True)
    other = make_network_channel(
        db_session,
        network=network,
        source_state=db_session.scalars(select(channel.__class__)).first().source_state,
        sink_state=channel.sink_state,
        kind=NetworkChannelKind.isomerization,
        channel_key="W2->W1",
    )
    nk = make_network_kinetics(
        db_session, channel=channel, solve=make_network_solve(db_session, network=network),
        model_kind=NetworkKineticsModelKind.plog,
    )
    own = {"kind": "resolved_channel", "network_ref": network.public_ref, "channel_key": "W1->W2"}
    foreign = {"kind": "resolved_channel", "network_ref": network.public_ref, "channel_key": "W2->W1"}
    assert other.channel_key == "W2->W1"
    common = {"network_kinetics_ref": nk.public_ref, "pressure_context": "pressure_dependent"}
    refused = _post_stored_entry_rate(client, corpus, determination=_determination(target=foreign), **common)
    assert _code(refused) == "kinetics_determination_mismatch"
    assert refused.json()["context"]["reason"] == "target"
    accepted = _post_stored_entry_rate(client, corpus, determination=_determination(target=own), **common)
    assert accepted.status_code == 201, accepted.text[:800]


def test_an_unknown_network_channel_is_a_404_naming_both_halves(client, db_session, corpus):
    network, _ = _network_with_channel(db_session, corpus, micro=True)
    for ref, key in ((network.public_ref, "nope"), ("net_" + "a" * 26, "W1->W2")):
        target = {"kind": "resolved_channel", "network_ref": ref, "channel_key": key}
        response = _post_stored_entry_rate(client, corpus, determination=_determination(target=target))
        assert _code(response, 404) == "unknown_network_channel"
        assert response.json()["context"]["channel_key"] == key


# ---------------------------------------------------------------------------
# applicability
# ---------------------------------------------------------------------------


def _applicability(**extra) -> dict:
    return {"version": 1, **CLAIM, **extra}


def test_an_applicability_declaration_round_trips_with_colliders_stored_as_refs(client, db_session):
    declared = _applicability(
        phase="gas",
        observable="rate_coefficient",
        coefficient_basis="elementary_coefficient",
        scope="whole_reaction",
        reaction_order=2,
        rate_progress_convention="reaction_progress",
        pressure_dependence="fixed_pressure",
        collider_kind="specified_collider", colliders=[{"species": N2}],
    )
    response = client.post(
        KINETICS,
        json=_standalone(
            applicability=declared,
            determination=_determination(),
            pressure_context="apparent_at_pressure",
            pressure_bar=1.0,
        ),
    )
    assert response.status_code == 201, response.text[:800]
    row = _latest(db_session)
    stored = row.applicability_declaration
    n2 = resolve_species(db_session, SpeciesEntryIdentityPayload(**N2))
    assert (stored["collider_kind"], stored["colliders"]) == ("specified_collider", [{"species_ref": n2.public_ref}])
    assert set(stored["colliders"][0]) == {"species_ref"}, "a collider is stored as a ref, not as content"
    record = _read(client, row)
    assert _stated(record["applicability"])["colliders"] == [{"species_ref": n2.public_ref}]
    assert record["applicability"]["collider_kind"] == "specified_collider"
    assert record["applicability"]["pressure_dependence"] == "fixed_pressure"
    assert record["applicability"]["claim_origin"] == "source_publication"


def test_a_declared_mixture_is_stored_with_species_refs_and_its_fractions(client, db_session):
    mixture = {
        "collider_kind": "fixed_mixture",
        "colliders": [{"species": N2, "mole_fraction": 0.79}, {"species": HE, "mole_fraction": 0.21}],
    }
    response = client.post(
        KINETICS,
        json=_standalone(
            applicability=_applicability(coefficient_basis="composition_effective_coefficient", **mixture)
        ),
    )
    assert response.status_code == 201, response.text[:800]
    colliders = _latest(db_session).applicability_declaration["colliders"]
    assert [c["mole_fraction"] for c in colliders] == [0.79, 0.21]
    assert all(c["species_ref"].startswith("spc_") for c in colliders)


def test_a_mixture_that_names_one_species_twice_is_refused(client, db_session):
    """Two spellings of one species: the schema sees different content, the service one species."""
    n2_spelled_again = {**N2, "electronic_state_label": "X"}
    mixture = {
        "collider_kind": "fixed_mixture",
        "colliders": [
            {"species": N2, "mole_fraction": 0.5},
            {"species": n2_spelled_again, "mole_fraction": 0.5},
        ],
    }
    response = client.post(
        KINETICS,
        json=_standalone(
            applicability=_applicability(coefficient_basis="composition_effective_coefficient", **mixture)
        ),
    )
    assert _code(response) == "kinetics_declaration_invalid"
    assert response.json()["context"]["field"] == "applicability.colliders[1]"
    assert _count(db_session, Kinetics) == 0
    mixture["colliders"][1] = {"species": HE, "mole_fraction": 0.5}
    accepted = client.post(
        KINETICS,
        json=_standalone(
            applicability=_applicability(coefficient_basis="composition_effective_coefficient", **mixture)
        ),
    )
    assert accepted.status_code == 201, accepted.text[:800]


@pytest.mark.parametrize(
    "body,field",
    [
        ({"applicability": _applicability(pressure_dependence="fixed_pressure")}, "pressure_dependence"),
        ({"applicability": _applicability(pressure_dependence="independent"), "pressure_context": "high_p_limit"}, "pressure_dependence"),
        ({"applicability": _applicability(reaction_order=3)}, "reaction_order"),
        ({"applicability": _applicability(coefficient_basis="third_body_kernel")}, "coefficient_basis"),
        ({"applicability": _applicability(scope="resolved_channel"), "determination": _determination()}, "scope"),
    ],
    ids=["fixed_without_bar", "independent_vs_hpl", "order", "kernel_without_third_body", "scope_vs_target"],
)
def test_an_applicability_declaration_that_contradicts_the_record_is_refused(client, db_session, body, field):
    response = client.post(KINETICS, json=_standalone(**body))
    assert _code(response) == "kinetics_declaration_contradicts_record"
    assert any(f"applicability.{field}" in error["msg"] for error in response.json()["detail"])
    assert _count(db_session, Kinetics) == 0


DISSOCIATION = {
    "reversible": False,
    "reactants": [{"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}}],
    "products": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
    ],
}


@pytest.mark.parametrize(
    "direction,order,accepted",
    [
        ("forward", 1, True),  # H2 -> 2 H: the forward coefficient is first order
        ("forward", 2, False),
        ("reverse", 2, True),  # 2 H -> H2 read the other way: second order
        ("reverse", 1, False),
        ("net", 1, True),  # a net rate has no order this layer can state
        ("net", 2, True),
    ],
)
def test_the_declared_order_is_the_order_of_the_side_the_direction_names(client, db_session, direction, order, accepted):
    # per_s whatever the direction: the A-units molecularity check reads the reactant count only (a
    # separate, pre-existing blindness this change does not touch).
    body = _standalone(direction=direction, a_units="per_s", applicability=_applicability(reaction_order=order))
    body["reaction"] = DISSOCIATION
    response = client.post(KINETICS, json=body)
    if accepted:
        assert response.status_code == 201, response.text[:800]
    else:
        assert _code(response) == "kinetics_declaration_contradicts_record"
        assert any("applicability.reaction_order" in e["msg"] and direction in e["msg"] for e in response.json()["detail"])
        assert _count(db_session, Kinetics) == 0


def test_a_cited_determinations_target_is_compared_with_the_declared_scope(client, db_session):
    assert client.post(KINETICS, json=_standalone(determination=_determination("set-A"))).status_code == 201
    det = db_session.scalars(select(KineticsDetermination)).one()
    body = _standalone(
        determination={"determination_ref": det.public_ref, "representation_role": "complete"},
        applicability=_applicability(scope="resolved_channel"),
    )
    assert _code(client.post(KINETICS, json=body)) == "kinetics_declaration_contradicts_record"
    body["applicability"] = _applicability(scope="whole_reaction")
    assert client.post(KINETICS, json=body).status_code == 201


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------

PROTOCOL = {
    "version": 1,
    "method_kind": "saddle_point_tst",
    "barrier_basis": "zpe_corrected",
    "zero_point_treatment": "harmonic_scaled",
    "rotor_treatment": "hindered_rotor",
    "conformer_treatment": "single_conformer",
    "departures": [],
}


def test_a_protocol_round_trips_with_its_supporting_calculations_stored_as_refs(client, db_session, corpus):
    protocol = {
        **PROTOCOL,
        "supporting_calculations": [
            {"calculation_ref": corpus.ts_calc.public_ref, "purpose": "electronic_energy"},
            {"calculation_ref": corpus.h_calc.public_ref, "purpose": "geometry"},
        ],
    }
    response = _post_stored_entry_rate(client, corpus, protocol=protocol)
    assert response.status_code == 201, response.text[:800]
    row = _latest(db_session)
    assert row.protocol_declaration == protocol
    record = _read(client, row)
    assert _stated(record["protocol"]) == protocol
    # "No departures" is a statement and survives the read; omitted would read null.
    assert record["protocol"]["departures"] == []


def test_a_supporting_calculation_of_another_reaction_is_refused(client, db_session, corpus):
    protocol = {
        "version": 1,
        "supporting_calculations": [{"calculation_ref": corpus.stranger_calc.public_ref, "purpose": "geometry"}],
    }
    response = _post_stored_entry_rate(client, corpus, protocol=protocol)
    assert _code(response) == "kinetics_protocol_calculation_owner_mismatch"
    assert response.json()["context"]["field"] == "protocol.supporting_calculations[0].calculation_ref"
    assert str(corpus.stranger_calc.id) not in response.text.replace(corpus.stranger_calc.public_ref, "")
    assert _count(db_session, Kinetics) == 0
    own = {"version": 1, "supporting_calculations": [{"calculation_ref": corpus.h_calc.public_ref, "purpose": "geometry"}]}
    assert _post_stored_entry_rate(client, corpus, protocol=own).status_code == 201


def test_an_unknown_supporting_calculation_ref_is_a_404(client, db_session):
    protocol = {"version": 1, "supporting_calculations": [{"calculation_ref": "calc_" + "a" * 26, "purpose": "geometry"}]}
    assert _code(client.post(KINETICS, json=_standalone(protocol=protocol)), 404) == "unknown_calculation_ref"


def test_a_methods_kind_must_agree_with_the_records_origin(client, db_session):
    experimental = {"version": 1, "method_kind": "experimental"}
    response = client.post(KINETICS, json=_standalone(protocol=experimental))
    assert _code(response) == "kinetics_declaration_contradicts_record"
    ok = client.post(KINETICS, json=_standalone(scientific_origin="experimental", protocol=experimental))
    assert ok.status_code == 201, ok.text[:800]


# ---------------------------------------------------------------------------
# /uploads/computed-reaction
# ---------------------------------------------------------------------------


def test_a_bundle_fit_declares_a_determination_applicability_and_protocol(client, db_session):
    response = client.post(
        BUNDLE,
        json=_bundle(
            direction="forward",
            determination=_determination("arkane-run"),
            applicability=_applicability(phase="gas", pressure_dependence="independent", reaction_order=2),
            protocol={
                "version": 1,
                "method_kind": "saddle_point_tst",
                "supporting_calculations": [{"calculation_key": "h-opt", "purpose": "geometry"}],
            },
        ),
    )
    assert response.status_code == 201, response.text[:800]
    row = _latest(db_session)
    assert row.direction.value == "forward"
    det = db_session.get(KineticsDetermination, row.determination_id)
    assert (det.determination_key, det.workflow_tool_release_id is not None) == ("arkane-run", True)
    (supporting,) = row.protocol_declaration["supporting_calculations"]
    assert supporting["calculation_ref"].startswith("calc_") and supporting["purpose"] == "geometry"
    assert "calculation_key" not in str(row.protocol_declaration)
    record = _read(client, row)
    assert record["determination"]["determination_ref"] == det.public_ref
    assert record["applicability"]["reaction_order"] == 2


def test_a_bundle_cannot_state_a_reverse_direction_but_a_swapped_fit_is_its_own_determination(client, db_session):
    refused = client.post(BUNDLE, json=_bundle(direction="reverse"))
    assert refused.status_code == 422, refused.text[:800]
    forward = _bundle(direction="forward", determination=_determination("run"))
    swapped = {
        **forward["kinetics"][0],
        "reactant_keys": ["h2"],
        "product_keys": ["h", "h"],
        "a_units": "per_s",
    }
    forward["kinetics"].append(swapped)
    assert client.post(BUNDLE, json=forward).status_code == 201
    rows = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    assert len(rows) == 2 and rows[0].reaction_entry_id != rows[1].reaction_entry_id
    assert rows[0].determination_id != rows[1].determination_id


def test_a_bundle_determination_needs_a_bundle_source(client, db_session):
    body = _bundle(direction="forward", determination=_determination(), root={"workflow_tool_release": None})
    refused = client.post(BUNDLE, json=body)
    assert _code(refused) == "kinetics_determination_invalid"
    assert refused.json()["context"]["missing"] == "source"
    assert _count(db_session, Kinetics) == 0
    assert client.post(BUNDLE, json=_bundle(direction="forward", determination=_determination())).status_code == 201


def test_a_bundle_protocol_naming_an_undeclared_calculation_key_is_refused(client, db_session):
    protocol = {"version": 1, "supporting_calculations": [{"calculation_key": "nope", "purpose": "geometry"}]}
    response = client.post(BUNDLE, json=_bundle(protocol=protocol))
    assert _code(response) == "calculation_key_undeclared"
    assert _count(db_session, Kinetics) == 0


def test_a_bundle_applicability_that_contradicts_the_fit_is_refused(client, db_session):
    body = _bundle(applicability=_applicability(pressure_dependence="fixed_pressure"))
    assert _code(client.post(BUNDLE, json=body)) == "kinetics_declaration_contradicts_record"
    assert _count(db_session, Kinetics) == 0


def test_standalone_and_bundle_store_the_same_declaration_content(client, db_session):
    applicability = _applicability(phase="gas", pressure_dependence="independent", reaction_order=2)
    protocol = {"version": 1, "method_kind": "saddle_point_tst", "departures": []}
    assert client.post(
        KINETICS, json=_standalone(applicability=applicability, protocol=protocol)
    ).status_code == 201
    standalone = _latest(db_session)
    assert client.post(BUNDLE, json=_bundle(applicability=applicability, protocol=protocol)).status_code == 201
    bundled = _latest(db_session)
    assert standalone.id != bundled.id
    assert standalone.applicability_declaration == bundled.applicability_declaration
    assert standalone.protocol_declaration == bundled.protocol_declaration


# ---------------------------------------------------------------------------
# export and re-import
# ---------------------------------------------------------------------------


def test_an_exported_bundle_carries_the_portable_declarations_and_reimports_into_the_same_grouping(
    client, db_session
):
    from uuid import uuid4

    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole
    from app.schemas.workflows.contribution_bundle import ContributionBundleV0
    from app.services.contribution_bundle_export import BundleExportOmission, export_kinetics_bundle
    from app.workflows.contribution_bundle_submit import submit_contribution_bundle

    n2_content = {"collider_kind": "specified_collider", "colliders": [{"species": N2}]}
    declared = {
        "applicability": _applicability(phase="gas", pressure_dependence="independent", **n2_content),
        "protocol": {**PROTOCOL},
    }
    assert client.post(
        KINETICS, json=_standalone(determination=_determination("set-A", role="additive_component"), **declared)
    ).status_code == 201
    det = db_session.scalars(select(KineticsDetermination)).one()
    # A second component of the same determination: it cites the ref, which anchors it.
    assert client.post(
        KINETICS,
        json=_standalone(
            determination={"determination_ref": det.public_ref, "representation_role": "additive_component"},
            **declared,
        ),
    ).status_code == 201
    rows = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    assert rows[0].determination_id == rows[1].determination_id == det.id
    assert [_read(client, row)["determination"]["representation_role"] for row in rows] == ["additive_component"] * 2

    omissions: list[BundleExportOmission] = []
    bundle = export_kinetics_bundle(
        db_session,
        kinetics_ids=[row.id for row in rows],
        title="Declarations",
        summary="Declaration round trip.",
        exporter_label="tester",
        omissions=omissions,
    )
    assert omissions == []
    uploads = bundle.records.kinetics_uploads
    assert [u.determination.key for u in uploads] == ["set-A", "set-A"]
    assert [u.determination.representation_role.value for u in uploads] == ["additive_component"] * 2
    assert all(u.direction.value == "forward" for u in uploads)
    # The collider travels as species content, never as the exporting database's ref.
    assert uploads[0].applicability.colliders[0].species.smiles == "N#N"
    assert "spc_" not in bundle.model_dump_json()

    reloaded = ContributionBundleV0.model_validate_json(bundle.model_dump_json())
    actor = AppUser(username=f"decl-{uuid4().hex[:10]}", role=AppUserRole.user)
    db_session.add(actor)
    db_session.flush()
    submitted = submit_contribution_bundle(db_session, reloaded, actor=actor)
    imported_ids = [r.record_id for r in submitted.records if r.record_type.value == SubmissionRecordType.kinetics.value]
    db_session.flush()
    imported = [db_session.get(Kinetics, kinetics_id) for kinetics_id in imported_ids]
    assert len(imported) == 2

    # Grouping is preserved: both re-imported records share one determination (and one entry),
    # which is a new determination of the importing instance, not the exporter's row.
    assert imported[0].determination_id == imported[1].determination_id is not None
    assert imported[0].determination_id != det.id
    assert imported[0].reaction_entry_id == imported[1].reaction_entry_id != rows[0].reaction_entry_id
    assert [k.representation_role.value for k in imported] == ["additive_component"] * 2
    assert [k.applicability_declaration for k in imported] == [rows[0].applicability_declaration] * 2
    assert [k.protocol_declaration for k in imported] == [rows[0].protocol_declaration] * 2
    assert _count(db_session, KineticsDetermination) == 2


def test_two_unrelated_uploads_with_the_same_key_in_one_bundle_do_not_share_a_determination(client, db_session):
    """Sharing needs the same reaction content and source as well as the same key."""
    from uuid import uuid4

    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole
    from app.schemas.workflows.contribution_bundle import ContributionBundleV0
    from app.services.contribution_bundle_export import export_kinetics_bundle
    from app.workflows.contribution_bundle_submit import submit_contribution_bundle

    assert client.post(KINETICS, json=_standalone(determination=_determination("set-A"))).status_code == 201
    assert client.post(
        KINETICS,
        json=_standalone(determination=_determination("set-A"), workflow_tool_release=OTHER_TOOL),
    ).status_code == 201
    rows = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    bundle = export_kinetics_bundle(
        db_session, kinetics_ids=[r.id for r in rows], title="Two", summary="Two sources.", exporter_label="t"
    )
    actor = AppUser(username=f"decl-{uuid4().hex[:10]}", role=AppUserRole.user)
    db_session.add(actor)
    db_session.flush()
    submitted = submit_contribution_bundle(
        db_session, ContributionBundleV0.model_validate_json(bundle.model_dump_json()), actor=actor
    )
    ids = [r.record_id for r in submitted.records if r.record_type.value == SubmissionRecordType.kinetics.value]
    db_session.flush()
    imported = [db_session.get(Kinetics, i) for i in ids]
    assert imported[0].determination_id != imported[1].determination_id
    assert imported[0].reaction_entry_id != imported[1].reaction_entry_id


def _round_trip(db_session, kinetics_ids, omissions=None):
    from uuid import uuid4

    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole
    from app.schemas.workflows.contribution_bundle import ContributionBundleV0
    from app.services.contribution_bundle_export import export_kinetics_bundle
    from app.workflows.contribution_bundle_submit import submit_contribution_bundle

    bundle = export_kinetics_bundle(
        db_session, kinetics_ids=kinetics_ids, title="Round trip", summary="Round trip.",
        exporter_label="t", omissions=omissions,
    )
    actor = AppUser(username=f"decl-{uuid4().hex[:10]}", role=AppUserRole.user)
    db_session.add(actor)
    db_session.flush()
    submitted = submit_contribution_bundle(
        db_session, ContributionBundleV0.model_validate_json(bundle.model_dump_json()), actor=actor
    )
    db_session.flush()
    ids = [r.record_id for r in submitted.records if r.record_type.value == SubmissionRecordType.kinetics.value]
    return bundle, [db_session.get(Kinetics, i) for i in ids]


def _stored_keys(db_session) -> list[str]:
    return list(db_session.scalars(select(KineticsDetermination.determination_key).order_by(KineticsDetermination.id)))


def test_two_determinations_with_one_key_source_and_reaction_stay_two_after_an_export_and_re_import(client, db_session):
    # The same key deposited twice, from the same source, on two reaction entries that read identically.
    for _ in range(2):
        assert client.post(KINETICS, json=_standalone(determination=_determination("set-A"))).status_code == 201
    rows = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    assert rows[0].determination_id != rows[1].determination_id and rows[0].reaction_entry_id != rows[1].reaction_entry_id
    before = _count(db_session, KineticsDetermination)

    omissions: list = []
    bundle, imported = _round_trip(db_session, [r.id for r in rows], omissions)

    # The key is exactly what the depositor stated; a bundle-local handle separates the two.
    uploads = bundle.records.kinetics_uploads
    assert [u.determination.key for u in uploads] == ["set-A", "set-A"]
    assert [u.determination.group for u in uploads] == ["d1", "d2"]
    assert omissions == [], "nothing was left out and nothing was renamed, so there is nothing to report"
    assert imported[0].determination_id != imported[1].determination_id
    assert imported[0].reaction_entry_id != imported[1].reaction_entry_id
    assert _count(db_session, KineticsDetermination) == before + 2, "the same number of determinations as the source"
    # The handle is never stored: both re-imported determinations carry the key as stated.
    assert _stored_keys(db_session)[-2:] == ["set-A", "set-A"]
    assert "group" not in {c.name for c in KineticsDetermination.__table__.columns}


def test_a_mixed_export_and_its_re_export_keep_every_key_as_stated_and_every_determination_apart(client, db_session):
    # Three determinations all called "set-A" and a fourth literally called "set-A~2".
    for key in ("set-A", "set-A", "set-A", "set-A~2"):
        assert client.post(KINETICS, json=_standalone(determination=_determination(key))).status_code == 201
    rows = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    first, imported = _round_trip(db_session, [r.id for r in rows])
    assert [u.determination.key for u in first.records.kinetics_uploads] == ["set-A", "set-A", "set-A", "set-A~2"]
    assert [u.determination.group for u in first.records.kinetics_uploads] == ["d1", "d2", "d3", "d4"]
    assert len({k.determination_id for k in imported}) == 4
    # Exporting what was imported again changes nothing: still four determinations, keys still as stated.
    second, reimported = _round_trip(db_session, [k.id for k in imported])
    assert [u.determination.key for u in second.records.kinetics_uploads] == ["set-A", "set-A", "set-A", "set-A~2"]
    assert len({k.determination_id for k in reimported}) == 4
    assert _stored_keys(db_session)[-4:] == ["set-A", "set-A", "set-A", "set-A~2"]


def test_records_of_one_determination_share_one_handle_and_stay_one_determination(client, db_session):
    assert client.post(KINETICS, json=_standalone(determination=_determination("set-A"))).status_code == 201
    det = db_session.scalars(select(KineticsDetermination)).one()
    cite = {"determination_ref": det.public_ref, "representation_role": "complete"}
    assert client.post(KINETICS, json=_standalone(determination=cite, a=2.0e13)).status_code == 201
    rows = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    omissions: list = []
    bundle, imported = _round_trip(db_session, [r.id for r in rows], omissions)
    assert [u.determination.group for u in bundle.records.kinetics_uploads] == ["d1", "d1"]
    assert omissions == []
    assert imported[0].determination_id == imported[1].determination_id


def test_a_group_handle_is_for_a_bundle_import_only(client, db_session):
    grouped = _determination("set-A")
    grouped["group"] = "d1"
    standalone = client.post(KINETICS, json=_standalone(determination=grouped))
    assert _code(standalone) == "kinetics_determination_invalid"
    assert standalone.json()["context"]["field"] == "determination.group"
    assert _count(db_session, Kinetics) == 0
    # The fits of one reaction upload already share a determination by key.
    refused = client.post(BUNDLE, json=_bundle(determination=grouped, direction="forward"))
    assert _code(refused) == "kinetics_determination_invalid"
    # A handle goes with a key, never with a determination_ref.
    cited = {"determination_ref": "kdet_" + "a" * 26, "representation_role": "complete", "group": "d1"}
    assert _code(client.post(KINETICS, json=_standalone(determination=cited))) == "kinetics_determination_invalid"


def test_a_determination_cannot_mix_complete_and_additive_records(client, db_session):
    assert client.post(KINETICS, json=_standalone(determination=_determination("set-A"))).status_code == 201
    det = db_session.scalars(select(KineticsDetermination)).one()
    mixed = {"determination_ref": det.public_ref, "representation_role": "additive_component"}
    response = client.post(KINETICS, json=_standalone(determination=mixed))
    assert _code(response) == "kinetics_determination_mismatch"
    assert response.json()["context"]["reason"] == "role"
    assert _count(db_session, Kinetics) == 1
    # The same record in the determination's own role is accepted.
    same = {"determination_ref": det.public_ref, "representation_role": "complete"}
    assert client.post(KINETICS, json=_standalone(determination=same)).status_code == 201


def test_each_record_reads_its_own_determinations_role(client, db_session):
    assert client.post(KINETICS, json=_standalone(determination=_determination("whole"))).status_code == 201
    parts = _determination("parts", role="additive_component")
    assert client.post(KINETICS, json=_standalone(determination=parts)).status_code == 201
    rows = db_session.scalars(select(Kinetics).order_by(Kinetics.id)).all()
    assert [_read(client, row)["determination"]["representation_role"] for row in rows] == [
        "complete",
        "additive_component",
    ]


def test_an_export_reports_what_a_portable_bundle_cannot_carry(client, db_session, corpus):
    from app.services.contribution_bundle_export import export_kinetics_bundle

    target = {"kind": "resolved_channel", "transition_state_entry_ref": corpus.ts_entry.public_ref}
    protocol = {**PROTOCOL, "supporting_calculations": [{"calculation_ref": corpus.h_calc.public_ref, "purpose": "geometry"}]}
    response = _post_stored_entry_rate(
        client, corpus, determination=_determination(target=target), protocol=protocol
    )
    assert response.status_code == 201, response.text[:800]
    row = _latest(db_session)

    omissions: list = []
    bundle = export_kinetics_bundle(
        db_session, kinetics_ids=[row.id], title="Pruned", summary="Pruned declarations.",
        exporter_label="tester", omissions=omissions,
    )
    (upload,) = bundle.records.kinetics_uploads
    # Absent, never replaced: a channel determination is not turned into a whole-reaction one.
    assert upload.determination is None
    assert [s.model_dump() for s in (upload.protocol.supporting_calculations or [])] == []
    (omission,) = omissions
    assert omission.action == "declaration_pruned" and omission.ref == row.public_ref
    assert "resolved channel" in omission.detail and "supporting calculations" in omission.detail
    assert str(row.id) not in omission.detail


def test_the_record_type_of_a_determination_is_not_a_reviewable_record():
    # A determination is identity, not a record a reviewer approves: it has no review type.
    assert "kinetics_determination" not in {t.value for t in SubmissionRecordType}


def test_the_standalone_route_has_no_calculation_keys_so_a_key_is_always_refused(client, db_session, corpus):
    # /uploads/kinetics carries no calculations, so there is nothing a calculation_key could name. A key is
    # refused with the shared code; a calculation_ref is how the route cites a calculation.
    protocol = {**PROTOCOL, "supporting_calculations": [{"calculation_key": "x", "purpose": "geometry"}]}
    response = client.post(KINETICS, json=_standalone(protocol=protocol))
    assert _code(response) == "calculation_key_undeclared"
    assert response.json()["context"]["declared_keys"] == []
    assert _count(db_session, Kinetics) == 0
    by_ref = {**PROTOCOL, "supporting_calculations": [{"calculation_ref": corpus.h_calc.public_ref, "purpose": "geometry"}]}
    assert _post_stored_entry_rate(client, corpus, protocol=by_ref).status_code == 201
