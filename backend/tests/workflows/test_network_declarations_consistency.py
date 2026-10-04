"""A declaration is a claim, and the stored columns stay authoritative: a contradiction is refused, never reconciled.

Each refusal names its code and field, and each has a positive twin that differs by one fact, so a check that
silently stopped firing cannot pass as "accepted".
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.network_pdep import NetworkKinetics, NetworkKineticsDetermination, NetworkSolve
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.workflows.network_pdep import persist_network_pdep_upload
from tests.workflows.test_network_declarations import (
    _chebyshev,
    _declared_payload,
    _persist,
    _plog,
    _refusal,
    _target,
)
from tests.workflows.test_network_pdep_upload import _full_payload


def accepted(payload: dict) -> NetworkPDepUploadRequest:
    return NetworkPDepUploadRequest(**payload)


# -- alternates must all declare their keys ---------------------------------------------------------


def test_a_declared_alternate_beside_an_undeclared_one_is_refused_with_the_legacy_refusal() -> None:
    payload = _declared_payload()
    payload["solve"]["target"] = None
    payload["solve"]["channel_kinetics"] = [
        _plog("association_path", det=None, rep=None, order=2),
        _plog("association_path", det="d_assoc", rep="plog_b", order=2),
    ]
    with pytest.raises(ValidationError, match="unique by"):
        accepted(payload)
    payload["solve"]["channel_kinetics"].reverse()  # the order of the entries changes nothing
    with pytest.raises(ValidationError, match="unique by"):
        accepted(payload)


def test_every_fit_of_a_repeated_group_declaring_its_keys_is_accepted_and_one_undeclared_fit_alone_is_too() -> None:
    both = _declared_payload()
    both["solve"]["target"] = None
    both["solve"]["channel_kinetics"] = [
        _plog("association_path", det="d_assoc", rep="plog_a", order=2),
        _plog("association_path", det="d_assoc", rep="plog_b", order=2),
    ]
    assert len(accepted(both).solve.channel_kinetics) == 2
    alone = _declared_payload()
    alone["solve"]["target"] = None
    alone["solve"]["channel_kinetics"] = [_plog("association_path", det=None, rep=None, order=2)]
    assert len(accepted(alone).solve.channel_kinetics) == 1  # legacy behaviour is unchanged


def test_a_plog_and_a_chebyshev_on_one_channel_need_no_declaration() -> None:
    payload = _declared_payload()
    payload["solve"]["target"] = None
    payload["solve"]["channel_kinetics"] = [
        _plog("association_path", det=None, rep=None, order=2),
        {**_chebyshev("association_path", det="x", rep="y", order=2)},
    ]
    del payload["solve"]["channel_kinetics"][1]["determination"]
    del payload["solve"]["channel_kinetics"][1]["representation"]
    assert len(accepted(payload).solve.channel_kinetics) == 2


# -- the stored columns stay authoritative -----------------------------------------------------------


def _with_conventions(payload: dict, convention: str) -> dict:
    for row in (*payload["solve"]["state_energies"], *payload["solve"]["channel_barriers"]):
        row["correction_convention"] = convention
    return payload


@pytest.mark.parametrize(
    "stored, basis, refused",
    [
        ("electronic_plus_zpe", "classical_electronic", True),
        ("atom_and_bond_corrected", "classical_electronic", True),
        ("thermal_enthalpy_298k", "classical_electronic", True),
        ("electronic_only", "zpe_corrected", True),
        ("electronic_plus_zpe", "zpe_corrected", False),
        ("electronic_only", "classical_electronic", False),
        ("atom_and_bond_corrected", "zpe_corrected", False),
    ],
)
def test_a_barrier_basis_that_contradicts_the_stored_correction_convention_is_refused(stored, basis, refused) -> None:
    payload = _with_conventions(_declared_payload(), stored)
    payload["solve"]["protocol"] = {"version": 1, "barrier_basis": basis}
    if refused:
        code, field, message = _refusal(payload)
        assert (code, field) == ("network_declaration_invalid", "solve.protocol")
        assert basis in message and stored in message
    else:
        assert accepted(payload).solve.protocol.barrier_basis.value == basis


def test_a_barrier_basis_is_accepted_when_the_stored_rows_state_no_convention_to_contradict() -> None:
    payload = _declared_payload()
    for row in (*payload["solve"]["state_energies"], *payload["solve"]["channel_barriers"]):
        row["correction_convention"] = "other"
        row["convention_note"] = "a convention outside the vocabulary"
    for basis in ("classical_electronic", "zpe_corrected"):
        payload["solve"]["protocol"] = {"version": 1, "barrier_basis": basis}
        assert accepted(payload).solve.protocol.barrier_basis.value == basis
    # A reported solve holds no energies at all: nothing to contradict either.
    reported = _declared_payload()
    reported["solve"]["kind"] = "reported"
    reported["solve"]["state_energies"] = []
    reported["solve"]["channel_barriers"] = []
    reported["solve"]["source_calculations"] = []
    reported["solve"]["energy_transfer"] = []
    reported["solve"]["literature"] = {"kind": "article", "title": "A paper", "year": 2020}
    reported["solve"]["protocol"] = {"version": 1, "barrier_basis": "classical_electronic"}
    assert accepted(reported).solve.protocol is not None


@pytest.mark.parametrize("availability", ["unavailable", "declared_zero"])
def test_an_output_declared_unavailable_or_zero_cannot_have_a_determination_in_the_same_upload(availability) -> None:
    payload = _declared_payload()
    outputs = payload["solve"]["target"]["outputs"]
    extra = {"zero_basis": "source_statement"} if availability == "declared_zero" else {}
    outputs[0] = {"channel_key": "association_path", "availability": availability, **extra}
    code, field, message = _refusal(payload)
    assert (code, field) == ("network_declaration_invalid", "solve.target.outputs[0]")
    assert availability in message and "association_path" in message


def test_the_catalogs_unavailable_and_zero_claims_stand_for_channels_without_a_determination() -> None:
    payload = _declared_payload()
    outputs = payload["solve"]["target"]["outputs"]
    assert {o["availability"] for o in outputs} == {"supplied", "declared_zero"}
    assert accepted(payload).solve.target.outputs[2].availability.value == "declared_zero"


@pytest.mark.parametrize(
    "validity, bad_axis",
    [
        ({"temperature_min_k": 50.0}, "temperature"),
        ({"temperature_max_k": 5000.0}, "temperature"),
        ({"pressure_min_bar": 0.001}, "pressure"),
        ({"pressure_max_bar": 500.0}, "pressure"),
        ({"temperature_min_k": 50.0, "pressure_max_bar": 500.0}, "temperature and pressure"),
    ],
)
def test_a_declared_validity_cannot_exceed_the_solves_own_range(validity, bad_axis) -> None:
    payload = _declared_payload()
    payload["solve"]["target"]["validity"] = {**payload["solve"]["target"]["validity"], **validity}
    code, field, message = _refusal(payload)
    assert (code, field) == ("network_declaration_invalid", "solve.target.validity")
    assert bad_axis in message


def test_a_declared_validity_equal_to_or_inside_the_solves_range_is_accepted() -> None:
    payload = _declared_payload()
    for validity in (
        payload["solve"]["target"]["validity"],  # exactly the solve's range
        {"temperature_min_k": 400.0, "temperature_max_k": 1000.0, "pressure_min_bar": 0.1, "pressure_max_bar": 10.0},
    ):
        payload["solve"]["target"]["validity"] = validity
        payload["solve"]["target"]["outputs"] = [o for o in _target()["outputs"]]
        assert accepted(payload).solve.target.validity is not None


def test_a_declared_order_that_contradicts_the_fits_own_units_is_refused() -> None:
    payload = _declared_payload()
    payload["solve"]["target"] = None
    diss = payload["solve"]["channel_kinetics"][2]
    assert diss["determination"]["observable"]["reaction_order"] == 1  # unimolecular, per_s
    for entry in diss["plog"]["entries"]:
        entry["a_units"] = "cm3_mol_s"
    code, field, message = _refusal(payload)
    assert (code, field) == ("network_declaration_invalid", "solve.channel_kinetics[2].determination")
    assert "cm3_mol_s" in message and "reaction_order 1" in message
    # rate_units on the parent are held to the same order.
    parent = _declared_payload()
    parent["solve"]["target"] = None
    parent["solve"]["channel_kinetics"][2]["rate_units"] = "cm6_mol2_s"
    _, field, _ = _refusal(parent)
    assert field == "solve.channel_kinetics[2].determination"
    # The twin: matching units are accepted, and so is a fit that states no units.
    ok = _declared_payload()
    ok["solve"]["target"] = None
    ok["solve"]["channel_kinetics"][2]["rate_units"] = "per_s"
    assert accepted(ok)


def test_a_fit_addressed_by_endpoints_cannot_declare_a_determination_and_the_message_says_so() -> None:
    payload = _declared_payload()
    payload["solve"]["target"] = None
    fit = payload["solve"]["channel_kinetics"][0]
    fit["source_state_key"], fit["sink_state_key"] = "entrance", "well_RO2"
    fit.pop("channel_key")
    with pytest.raises(ValidationError, match="addressed by channel_key"):
        accepted(payload)
    fit["channel_key"] = "association_path"  # both addresses stated: the key is what a determination is addressed by
    assert accepted(payload)


def test_the_service_gives_the_same_message_for_a_payload_built_without_validation(db_conn) -> None:
    request = accepted(_declared_payload())
    request.solve.channel_kinetics[0].channel_key = None  # as a model_construct payload could arrive
    session = Session(db_conn)
    session.begin()
    try:
        with pytest.raises(CodedValueError) as caught:
            persist_network_pdep_upload(session, request)
        assert caught.value.code == "network_declaration_invalid"
        assert "addressed by channel_key" in str(caught.value) and "not one of this network's" not in str(caught.value)
    finally:
        session.rollback()


# -- determinations belong to their own solve and channel --------------------------------------------


def test_two_uploads_with_the_same_keys_keep_separate_determinations_and_links(db_conn) -> None:
    first_session, first = _persist(db_conn, _declared_payload())
    try:
        second = persist_network_pdep_upload(first_session, accepted(_declared_payload()))
        first_session.flush()
        solves = {n.id: first_session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == n.id)).one() for n in (first, second)}
        determinations = first_session.scalars(select(NetworkKineticsDetermination)).all()
        assert len(determinations) == 4 and len({d.id for d in determinations}) == 4  # d_assoc and d_diss, twice
        for fit in first_session.scalars(select(NetworkKinetics).where(NetworkKinetics.determination_id.is_not(None))):
            assert fit.determination.solve_id == fit.solve_id and fit.determination.channel_id == fit.channel_id
        keys_per_solve = {
            solve.id: sorted(d.determination_key for d in determinations if d.solve_id == solve.id)
            for solve in solves.values()
        }
        assert all(keys == ["d_assoc", "d_diss"] for keys in keys_per_solve.values())
    finally:
        first_session.rollback()


def test_the_database_refuses_a_fit_pointing_at_another_solves_or_channels_determination(db_conn) -> None:
    session, first = _persist(db_conn, _declared_payload())
    try:
        second = persist_network_pdep_upload(session, accepted(_declared_payload()))
        session.flush()
        solve_a = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == first.id)).one()
        solve_b = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == second.id)).one()
        det_a = session.scalars(
            select(NetworkKineticsDetermination).where(
                NetworkKineticsDetermination.solve_id == solve_a.id, NetworkKineticsDetermination.determination_key == "d_assoc"
            )
        ).one()
        det_b_diss = session.scalars(
            select(NetworkKineticsDetermination).where(
                NetworkKineticsDetermination.solve_id == solve_b.id, NetworkKineticsDetermination.determination_key == "d_diss"
            )
        ).one()
        fit_b = session.scalars(
            select(NetworkKinetics).where(NetworkKinetics.solve_id == solve_b.id, NetworkKinetics.channel_id == det_b_diss.channel_id)
        ).first()
        # A key no other fit of the target determination uses, so only the scope constraint can object.
        fit_b.representation_declaration = {"version": 1, "key": "unused_key", "fit_origin": "refit"}
        session.flush()
        with pytest.raises(DBAPIError, match="fk_network_kinetics_determination_scope"), session.begin_nested():
            fit_b.determination_id = det_a.id  # another solve's determination
            session.flush()
        det_b_assoc = session.scalars(
            select(NetworkKineticsDetermination).where(
                NetworkKineticsDetermination.solve_id == solve_b.id, NetworkKineticsDetermination.determination_key == "d_assoc"
            )
        ).one()
        with pytest.raises(DBAPIError, match="fk_network_kinetics_determination_scope"), session.begin_nested():
            fit_b.determination_id = det_b_assoc.id  # the right solve, another channel
            session.flush()
        fit_b.determination_id = det_b_diss.id  # its own: unchanged, accepted
        session.flush()
    finally:
        session.rollback()


# -- a cited reference solve must not leak what it is -----------------------------------------------


def _model_fidelity(ref: str) -> dict:
    from tests.workflows.test_network_declarations import _target as target

    return {
        "version": 1,
        "entries": [{"kind": "model_fidelity", "domain": target()["validity"], "reference_solve_ref": ref}],
    }


def test_a_hidden_reference_solve_and_a_nonexistent_one_get_the_identical_response(db_conn) -> None:
    from app.db.models.common import RecordReviewStatus
    from app.services.scientific_read.profile import (
        ProfileRecommendation,
        ReadProfile,
        ResolvedReadProfile,
        reset_current_read_profile,
        set_current_read_profile,
    )

    session, network = _persist(db_conn, _declared_payload())
    try:
        existing = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == network.id)).one()
        assert existing.public_ref.startswith("nsolve_")
        # Under the exploratory profile the not-yet-reviewed solve is visible and may be cited.
        payload = deepcopy(_declared_payload())
        payload["solve"]["validation"] = _model_fidelity(existing.public_ref)
        persist_network_pdep_upload(session, accepted(payload))
        # Under the curated profile (floor: approved) the same solve is hidden, and reads as absent.
        token = set_current_read_profile(
            ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
        )
        try:
            outcomes = []
            for ref in (existing.public_ref, "nsolve_doesnotexist"):
                payload = deepcopy(_declared_payload())
                payload["solve"]["validation"] = _model_fidelity(ref)
                with pytest.raises(NotFoundError) as caught:
                    persist_network_pdep_upload(session, accepted(payload))
                outcomes.append(caught.value)
        finally:
            reset_current_read_profile(token)
        hidden, absent = outcomes
        assert hidden.code == absent.code == "unknown_network_solve_ref"
        strip = lambda e: (  # noqa: E731
            str(e).replace(e.context["ref"], "<ref>"),
            {k: v for k, v in e.context.items() if k != "ref"},
        )
        assert strip(hidden) == strip(absent)  # the same words, the same context: nothing says which it was
        assert RecordReviewStatus.not_reviewed
    finally:
        session.rollback()
