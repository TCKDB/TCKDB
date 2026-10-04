"""Network solve declarations, fit determinations and their persistence (chunk 1).

Every refusal test names the exact code and field it expects, and every positive test reads the stored
row back, so a check that silently stopped firing -- or a stored form that silently stopped carrying
the claim -- fails here rather than passing on an absence.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tckdb_schemas.network_declarations import (
    NETWORK_PRODUCT_SET_MEMBERSHIP_VERSION,
    NetworkTargetDeclaration,
    StoredNetworkTargetDeclaration,
    network_product_set_content_hash,
    validation_objective,
)

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsDetermination,
    NetworkSolve,
    NetworkState,
)
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.services.record_review import ensure_record_review, set_record_review_status
from app.workflows.network_pdep import persist_network_pdep_upload
from tests.workflows.test_network_pdep_upload import _full_payload

_BOUNDS = {"tmin_k": 300.0, "tmax_k": 2000.0, "pmin_bar": 0.01, "pmax_bar": 100.0}


def _units(order: int) -> str:
    """The rate units of a coefficient of this order (the unimolecular ones have no concentration)."""
    return "per_s" if order == 1 else "cm3_mol_s"


def _plog(channel: str, *, det: str | None, rep: str | None, order: int, role: str = "complete") -> dict:
    fit = {
        "channel_key": channel,
        "model_kind": "plog",
        "plog": {
            "entries": [
                {"pressure_bar": 1.0, "a": 1.0e13, "a_units": _units(order), "n": 0.0, "ea_kj_mol": 40.0},
                {"pressure_bar": 10.0, "a": 2.0e13, "a_units": _units(order), "n": 0.1, "ea_kj_mol": 42.0},
            ]
        },
    }
    if det is not None:
        fit["determination"] = {
            "key": det,
            "representation_role": role,
            "observable": {
                "version": 1,
                "observable": "product_resolved_coefficient",
                "coefficient_basis": "kernel",
                "reaction_order": order,
                "degeneracy_applied": True,
            },
        }
        fit["representation"] = {"version": 1, "key": rep, "fit_origin": "solver_output"}
    return fit


def _chebyshev(channel: str, *, det: str, rep: str, order: int) -> dict:
    fit = _plog(channel, det=det, rep=rep, order=order)
    del fit["plog"]
    fit.update(
        model_kind="chebyshev",
        chebyshev={"n_temperature": 2, "n_pressure": 2, "coefficients": [[1.0, 2.0], [3.0, 4.0]]},
        **_BOUNDS,
    )
    return fit


def _target() -> dict:
    return {
        "version": 1,
        "claim_origin": "source_publication",
        "partition": {"retained": ["entrance", "well_RO2", "exit"], "eliminated": [], "lumps": []},
        "boundaries": [{"state_key": "exit", "kind": "absorbing"}],
        "regime": {"kind": "time_independent"},
        "validity": {
            "temperature_min_k": 300.0,
            "temperature_max_k": 2000.0,
            "pressure_min_bar": 0.01,
            "pressure_max_bar": 100.0,
        },
        "bath_scope": "specified_collider",
        "outputs": [
            {"channel_key": "association_path", "availability": "supplied"},
            {"channel_key": "dissociation_path", "availability": "supplied"},
            {"channel_key": "elimination_path", "availability": "declared_zero", "zero_basis": "source_statement"},
        ],
        "product_sets": [
            {
                "product_set_key": "assoc",
                "members": [{"determination_key": "d_assoc", "representation_keys": ["plog1", "cheb1"]}],
            }
        ],
    }


def _declared_payload() -> dict:
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        _plog("association_path", det="d_assoc", rep="plog1", order=2),
        _chebyshev("association_path", det="d_assoc", rep="cheb1", order=2),
        _plog("dissociation_path", det="d_diss", rep="plog1", order=1),
    ]
    payload["solve"]["target"] = _target()
    payload["solve"]["protocol"] = {
        "version": 1,
        "reduction_method": "chemically_significant_eigenvalues",
        "rotor_treatment": "hindered_rotor",
        "tunneling_treatment": "eckart",
    }
    payload["solve"]["validation"] = {
        "version": 1,
        "entries": [
            {
                "kind": "convergence",
                "metric": "max_relative_error",
                "value": 0.02,
                "domain": _target()["validity"],
                "channel_keys": ["association_path"],
                "initialization": "thermal",
            }
        ],
    }
    return payload


def _refusal(payload: dict) -> tuple[str, str, str]:
    """``(code, field, message)`` of the one coded refusal a payload must raise."""
    with pytest.raises(ValidationError) as caught:
        NetworkPDepUploadRequest(**payload)
    error = caught.value.errors()[0]["ctx"]["error"]
    return error.code, error.context["field"], str(error)


# ---------------------------------------------------------------------------
# Wire contract (no database)
# ---------------------------------------------------------------------------


def test_a_fully_declared_payload_validates_and_round_trips() -> None:
    request = NetworkPDepUploadRequest(**_declared_payload())
    assert request.solve.target.product_sets[0].members[0].representation_keys == ["plog1", "cheb1"]
    assert [f.determination.key for f in request.solve.channel_kinetics] == ["d_assoc", "d_assoc", "d_diss"]
    assert NetworkPDepUploadRequest(**request.model_dump(mode="json")) == request


def test_an_undeclared_payload_is_unchanged() -> None:
    payload = _full_payload(include_solve=True)
    request = NetworkPDepUploadRequest(**payload)
    assert request.solve.target is None and request.solve.protocol is None and request.solve.validation is None


def test_determination_and_representation_travel_together() -> None:
    payload = _declared_payload()
    del payload["solve"]["channel_kinetics"][0]["representation"]
    with pytest.raises(ValidationError, match="stated together"):
        NetworkPDepUploadRequest(**payload)


def test_same_kind_alternates_need_declared_keys_and_undeclared_duplicates_are_still_refused() -> None:
    declared = _declared_payload()
    declared["solve"]["channel_kinetics"] = [
        _plog("association_path", det="d_assoc", rep="plog_a", order=2),
        _plog("association_path", det="d_assoc", rep="plog_b", order=2),
    ]
    declared["solve"]["target"] = None
    assert len(NetworkPDepUploadRequest(**declared).solve.channel_kinetics) == 2

    legacy = _full_payload(include_solve=True)
    legacy["solve"]["channel_kinetics"] = [
        _plog("association_path", det=None, rep=None, order=2),
        _plog("association_path", det=None, rep=None, order=2),
    ]
    with pytest.raises(ValidationError, match="unique"):
        NetworkPDepUploadRequest(**legacy)


def test_two_fits_of_one_determination_cannot_share_a_representation_key() -> None:
    payload = _declared_payload()
    payload["solve"]["channel_kinetics"] = [
        _plog("association_path", det="d_assoc", rep="same", order=2),
        _chebyshev("association_path", det="d_assoc", rep="same", order=2),
    ]
    payload["solve"]["target"] = None
    code, field, message = _refusal(payload)
    assert (code, field) == ("network_declaration_invalid", "solve.channel_kinetics[1].representation")
    assert "same" in message


def test_fits_sharing_a_determination_must_agree_on_channel_and_observable() -> None:
    other_channel = _declared_payload()
    other_channel["solve"]["channel_kinetics"][1]["channel_key"] = "dissociation_path"
    other_channel["solve"]["target"] = None
    code, field, _ = _refusal(other_channel)
    assert (code, field) == ("network_declaration_invalid", "solve.channel_kinetics[1].determination")

    other_basis = _declared_payload()
    other_basis["solve"]["channel_kinetics"][1]["determination"]["observable"]["coefficient_basis"] = (
        "composition_effective"
    )
    other_basis["solve"]["target"] = None
    code, field, _ = _refusal(other_basis)
    assert (code, field) == ("network_declaration_invalid", "solve.channel_kinetics[1].determination")


def test_observable_order_must_match_the_channels_source_state() -> None:
    payload = _declared_payload()
    payload["solve"]["channel_kinetics"][0]["determination"]["observable"]["reaction_order"] = 1
    payload["solve"]["channel_kinetics"][1]["determination"]["observable"]["reaction_order"] = 1
    payload["solve"]["target"] = None
    code, field, message = _refusal(payload)
    assert (code, field) == ("network_declaration_invalid", "solve.channel_kinetics[0].determination")
    assert "order 2" in message


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda t: t["partition"].update(retained=["entrance", "well_RO2"]), "not placed: ['exit']"),
        (lambda t: t["partition"].update(retained=["entrance", "well_RO2", "exit", "ghost"]), "ghost"),
        (lambda t: t["boundaries"].append({"state_key": "ghost", "kind": "open"}), "ghost"),
        (lambda t: t["regime"].update(kind="initial_population_restricted", initial_state_keys=["ghost"]), "ghost"),
        (lambda t: t["outputs"].append({"channel_key": "ghost_path", "availability": "unavailable"}), "ghost_path"),
        (lambda t: t.update(bath_scope="fixed_mixture"), "fixed_mixture contradicts a solve with 1 bath species"),
    ],
)
def test_a_target_that_contradicts_the_network_is_refused(mutate, fragment) -> None:
    payload = _declared_payload()
    mutate(payload["solve"]["target"])
    code, field, message = _refusal(payload)
    assert (code, field) == ("network_declaration_invalid", "solve.target")
    assert fragment in message


def test_a_supplied_output_needs_a_declared_determination_of_that_channel() -> None:
    payload = _declared_payload()
    payload["solve"]["target"]["outputs"] = [{"channel_key": "elimination_path", "availability": "supplied"}]
    code, field, message = _refusal(payload)
    assert (code, field) == ("network_declaration_invalid", "solve.target.outputs[0]")
    assert "elimination_path" in message


def test_product_sets_name_declared_determinations_and_representations_by_key() -> None:
    unknown_determination = _declared_payload()
    unknown_determination["solve"]["target"]["product_sets"][0]["members"][0]["determination_key"] = "d_nope"
    code, field, message = _refusal(unknown_determination)
    assert (code, field) == ("network_declaration_invalid", "solve.target.product_sets[0]")
    assert "d_nope" in message

    unknown_representation = _declared_payload()
    unknown_representation["solve"]["target"]["product_sets"][0]["members"][0]["representation_keys"] = ["zzz"]
    code, field, message = _refusal(unknown_representation)
    assert (code, field) == ("network_declaration_invalid", "solve.target.product_sets[0]")
    assert "zzz" in message

    by_ref = _declared_payload()
    member = by_ref["solve"]["target"]["product_sets"][0]["members"][0]
    member["determination_ref"] = "nkdet_x"
    del member["determination_key"]
    _, _, message = _refusal(by_ref)
    assert "determination_ref is the stored form" in message

    twice = _declared_payload()
    members = twice["solve"]["target"]["product_sets"][0]["members"]
    members.append(deepcopy(members[0]))
    _, _, message = _refusal(twice)
    assert "more than once" in message


def test_validation_evidence_must_name_the_networks_channels() -> None:
    payload = _declared_payload()
    payload["solve"]["validation"]["entries"][0]["channel_keys"] = ["ghost_path"]
    code, field, message = _refusal(payload)
    assert (code, field) == ("network_declaration_invalid", "solve.validation.entries[0]")
    assert "ghost_path" in message


@pytest.mark.parametrize("block", ["target", "protocol", "validation"])
@pytest.mark.parametrize("version", [2, 0, -1])
def test_an_unsupported_version_is_refused_with_its_own_code(block, version) -> None:
    payload = _declared_payload()
    payload["solve"][block]["version"] = version
    code, field, message = _refusal(payload)
    assert (code, field) == ("network_declaration_version_unsupported", f"{block}.version")
    assert "supported versions: [1]" in message


@pytest.mark.parametrize("block", ["target", "protocol", "validation"])
@pytest.mark.parametrize("version", ["1", True, 1.0])
def test_a_version_that_is_not_the_integer_one_is_refused(block, version) -> None:
    """``"1"``, ``True`` and ``1.0`` are not version 1: strict typing refuses them (no coded refusal)."""
    payload = _declared_payload()
    payload["solve"][block]["version"] = version
    with pytest.raises(ValidationError) as caught:
        NetworkPDepUploadRequest(**payload)
    first = caught.value.errors()[0]
    assert first["loc"][-1] == "version" and first["type"] == "int_type"


def test_the_vocabulary_refuses_what_it_does_not_define() -> None:
    for patch in (
        {"zero_basis": "guess", "availability": "declared_zero", "channel_key": "a"},
        {"channel_key": "a", "availability": "supplied", "zero_basis": "source_statement"},
        {"channel_key": "a", "availability": "declared_zero"},
    ):
        target = {"version": 1, "claim_origin": "source_publication", "outputs": [patch]}
        with pytest.raises(ValidationError):
            NetworkTargetDeclaration.model_validate(target)
    with pytest.raises(ValidationError):
        NetworkTargetDeclaration.model_validate({"version": 1, "claim_origin": "source_publication"})
    with pytest.raises(ValidationError):  # unknown keys are refused, not dropped
        NetworkTargetDeclaration.model_validate(
            {"version": 1, "claim_origin": "source_publication", "regime": {"kind": "time_independent"}, "x": 1}
        )


def test_a_partition_places_each_state_once() -> None:
    base = {"version": 1, "claim_origin": "source_publication"}
    with pytest.raises(ValidationError, match="once"):
        NetworkTargetDeclaration.model_validate(
            {**base, "partition": {"retained": ["a"], "eliminated": ["a"]}}
        )
    with pytest.raises(ValidationError, match="once"):
        NetworkTargetDeclaration.model_validate(
            {**base, "partition": {"retained": ["a"], "lumps": [{"members": ["a", "b"]}]}}
        )
    with pytest.raises(ValidationError, match="retains at least one"):
        NetworkTargetDeclaration.model_validate({**base, "partition": {"eliminated": ["a"]}})


def test_validation_evidence_supports_exactly_the_objective_its_kind_names() -> None:
    assert validation_objective("convergence").value == "model_fidelity"
    assert validation_objective("model_fidelity").value == "model_fidelity"
    assert validation_objective("physical_validation").value == "physical_accuracy"
    assert validation_objective("representation_validation").value == "representation_fidelity"
    assert validation_objective("uncertainty") is None
    assert validation_objective("dependence") is None


def test_product_set_content_hash_is_order_free_and_key_independent() -> None:
    a = network_product_set_content_hash([("nkdet_a", ["x", "y"]), ("nkdet_b", [])])
    b = network_product_set_content_hash([("nkdet_b", []), ("nkdet_a", ["y", "x"])])
    assert a == b
    assert a != network_product_set_content_hash([("nkdet_a", ["x"]), ("nkdet_b", [])])
    assert a != network_product_set_content_hash([("nkdet_a", ["x", "y"])])
    assert NETWORK_PRODUCT_SET_MEMBERSHIP_VERSION == 1


def test_a_stored_target_refuses_local_keys() -> None:
    body = {
        "version": 1,
        "claim_origin": "source_publication",
        "partition": {"retained": ["entrance"]},
    }
    with pytest.raises(ValidationError, match="composition hash"):
        StoredNetworkTargetDeclaration.model_validate(body)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _persist(db_conn, payload: dict):
    session = Session(db_conn)
    session.begin()
    network = persist_network_pdep_upload(session, NetworkPDepUploadRequest(**payload))
    session.flush()
    return session, network


def test_declarations_are_stored_in_their_resolved_form(db_conn) -> None:
    session, network = _persist(db_conn, _declared_payload())
    try:
        solve = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == network.id)).one()
        hashes = {s.composition_hash for s in session.scalars(select(NetworkState).where(NetworkState.network_id == network.id))}
        determinations = {
            d.determination_key: d
            for d in session.scalars(select(NetworkKineticsDetermination).where(NetworkKineticsDetermination.solve_id == solve.id))
        }
        assert set(determinations) == {"d_assoc", "d_diss"}

        target = solve.target_declaration
        assert set(target["partition"]["retained"]) == hashes  # states by content locator, never local keys
        (boundary,) = target["boundaries"]
        assert boundary["state_key"] in hashes and boundary["kind"] == "absorbing"
        (product_set,) = target["product_sets"]
        (member,) = product_set["members"]
        assert member["determination_ref"] == determinations["d_assoc"].public_ref
        assert "determination_key" not in member
        assert product_set["content_hash"] == network_product_set_content_hash(
            [(determinations["d_assoc"].public_ref, ["plog1", "cheb1"])]
        )
        assert product_set["membership_version"] == 1
        assert {o["channel_key"]: o["availability"] for o in target["outputs"]} == {
            "association_path": "supplied",
            "dissociation_path": "supplied",
            "elimination_path": "declared_zero",
        }
        assert solve.protocol_declaration["tunneling_treatment"] == "eckart"
        assert solve.validation_declaration["entries"][0]["kind"] == "convergence"

        fits = {
            (f.determination.determination_key, f.representation_declaration["key"]): f
            for f in session.scalars(select(NetworkKinetics).where(NetworkKinetics.solve_id == solve.id))
        }
        assert set(fits) == {("d_assoc", "plog1"), ("d_assoc", "cheb1"), ("d_diss", "plog1")}
        assert {f.representation_role.value for f in fits.values()} == {"complete"}
        channel = session.get(NetworkChannel, determinations["d_assoc"].channel_id)
        assert channel.channel_key == "association_path"
        assert determinations["d_assoc"].observable_declaration["reaction_order"] == 2
        # Both fits of one determination share the one row: not two supports for the channel.
        assert fits[("d_assoc", "plog1")].determination_id == fits[("d_assoc", "cheb1")].determination_id
        assert len(determinations["d_assoc"].identity_hash) == 64
    finally:
        session.rollback()


def test_a_payload_that_declares_nothing_stores_nothing(db_conn) -> None:
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [_plog("association_path", det=None, rep=None, order=2)]
    session, network = _persist(db_conn, payload)
    try:
        solve = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == network.id)).one()
        assert (solve.target_declaration, solve.protocol_declaration, solve.validation_declaration) == (None, None, None)
        (fit,) = session.scalars(select(NetworkKinetics).where(NetworkKinetics.solve_id == solve.id)).all()
        assert (fit.determination_id, fit.representation_role, fit.representation_declaration) == (None, None, None)
        assert session.scalars(select(NetworkKineticsDetermination)).all() == []
    finally:
        session.rollback()


def test_a_stored_determination_cannot_be_changed(db_conn) -> None:
    session, network = _persist(db_conn, _declared_payload())
    try:
        determination = session.scalars(select(NetworkKineticsDetermination)).first()
        with pytest.raises(DBAPIError, match="network_kinetics_determination_is_immutable"), session.begin_nested():
            determination.determination_key = "renamed"
            session.flush()
    finally:
        session.rollback()


def test_an_accepted_solve_refuses_a_new_determination_and_regrouping(db_conn) -> None:
    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole

    session, network = _persist(db_conn, _declared_payload())
    try:
        solve = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == network.id)).one()
        actor = AppUser(username="decl-curator", role=AppUserRole.curator)
        session.add(actor)
        session.flush()
        ensure_record_review(session, record_type=SubmissionRecordType.network_solve, record_id=solve.id)
        set_record_review_status(
            session,
            record_type=SubmissionRecordType.network_solve,
            record_id=solve.id,
            status=RecordReviewStatus.approved,
            actor=actor,
        )
        channel = session.scalars(select(NetworkChannel).where(NetworkChannel.channel_key == "elimination_path")).one()
        with pytest.raises(DBAPIError, match="is immutable"), session.begin_nested():
            session.add(
                NetworkKineticsDetermination(
                    solve_id=solve.id,
                    channel_id=channel.id,
                    determination_key="late",
                    observable_declaration={"version": 1},
                    identity_hash="f" * 64,
                )
            )
            session.flush()
        fit = session.scalars(select(NetworkKinetics).where(NetworkKinetics.solve_id == solve.id)).first()
        with pytest.raises(DBAPIError, match="is immutable"), session.begin_nested():
            fit.representation_declaration = {"version": 1, "key": "swapped", "fit_origin": "refit"}
            session.flush()
        with pytest.raises(DBAPIError, match="is immutable"), session.begin_nested():
            solve.target_declaration = {"version": 1, "claim_origin": "depositor_interpretation"}
            session.flush()
    finally:
        session.rollback()


def test_the_database_refuses_two_fits_of_one_determination_with_one_key(db_conn) -> None:
    session, network = _persist(db_conn, _declared_payload())
    try:
        fits = {
            f.representation_declaration["key"]: f
            for f in session.scalars(select(NetworkKinetics).where(NetworkKinetics.determination_id.is_not(None)))
            if f.determination.determination_key == "d_assoc"
        }
        with pytest.raises(DBAPIError, match="uq_network_kinetics_representation_key"), session.begin_nested():
            fits["cheb1"].representation_declaration = {"version": 1, "key": "plog1", "fit_origin": "refit"}
            session.flush()
    finally:
        session.rollback()


def test_a_payload_built_without_validation_meets_the_same_refusal(db_conn) -> None:
    """The service re-checks what the request validator checks (``model_construct`` skips validators)."""
    payload = _declared_payload()
    request = NetworkPDepUploadRequest(**payload)
    request.solve.channel_kinetics[1].determination.observable.observable = (
        request.solve.channel_kinetics[1].determination.observable.observable.__class__.total_loss_coefficient
    )
    session = Session(db_conn)
    session.begin()
    try:
        from app.api.error_contract import CodedValueError

        with pytest.raises(CodedValueError) as caught:
            persist_network_pdep_upload(session, request)
        assert caught.value.code == "network_declaration_invalid"
        assert "d_assoc" in str(caught.value)
    finally:
        session.rollback()
