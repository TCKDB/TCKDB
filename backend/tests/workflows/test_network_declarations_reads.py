"""Reads, release and currency of network declarations (chunk 1).

Each test reads the stored or served row back and asserts the specific claim, so a projection that
silently dropped the declaration -- or a digest that silently ignored it -- fails here.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.network_pdep import NetworkKinetics, NetworkSolve
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.workflows.test_network_declarations import _declared_payload, _persist
from tests.workflows.test_network_pdep_upload import _full_payload


def _approve(session, solve: NetworkSolve, username: str) -> None:
    from app.db.models.app_user import AppUser

    actor = AppUser(username=username, role=AppUserRole.curator)
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


def test_the_solve_and_fit_reads_serve_the_declarations_as_stored(db_conn) -> None:
    from app.services.scientific_read.network_kinetics import get_network_kinetics
    from app.services.scientific_read.networks import get_network_solve

    session, network = _persist(db_conn, _declared_payload())
    try:
        solve = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == network.id)).one()
        core = get_network_solve(session, network_solve_handle=solve.public_ref).record.network_solve
        assert core.declarations_unreadable is False
        assert core.target.claim_origin.value == "source_publication"
        assert core.target.product_sets[0].members[0].determination_ref.startswith("nkdet_")
        assert core.protocol.tunneling_treatment.value == "eckart"
        assert core.validation.entries[0].kind.value == "convergence"
        assert [(d.determination_key, d.channel_key) for d in core.determinations] == [
            ("d_assoc", "association_path"),
            ("d_diss", "dissociation_path"),
        ]
        assert core.determinations[0].observable.reaction_order == 2

        fit = session.scalars(
            select(NetworkKinetics).where(NetworkKinetics.solve_id == solve.id, NetworkKinetics.model_kind == "chebyshev")
        ).one()
        served = get_network_kinetics(session, network_kinetics_handle=fit.public_ref).record.network_kinetics
        assert served.determination.determination_key == "d_assoc"
        assert served.determination.determination_ref == core.determinations[0].determination_ref
        assert served.representation_role.value == "complete"
        assert served.representation.key == "cheb1"
        assert served.declaration_unreadable is False
    finally:
        session.rollback()


def test_an_undeclared_solve_reads_as_not_stated_and_a_corrupt_claim_as_unreadable(db_conn) -> None:
    from app.services.scientific_read.networks import get_network_solve

    session, network = _persist(db_conn, _full_payload(include_solve=True))
    try:
        solve = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == network.id)).one()
        core = get_network_solve(session, network_solve_handle=solve.public_ref).record.network_solve
        assert (core.target, core.protocol, core.validation) == (None, None, None)
        assert core.declarations_unreadable is False and core.determinations == []

        solve.protocol_declaration = {"version": 1, "reduction_method": "a_method_this_server_does_not_know"}
        session.flush()
        core = get_network_solve(session, network_solve_handle=solve.public_ref).record.network_solve
        assert core.protocol is None  # not served, not guessed at
        assert core.declarations_unreadable is True  # and not mistaken for "not stated"
    finally:
        session.rollback()


def test_a_release_ships_the_determinations_without_their_identity_digest(db_conn) -> None:
    from app.services.release.records import serialize_records

    session, network = _persist(db_conn, _declared_payload())
    try:
        solve = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == network.id)).one()
        _approve(session, solve, "rel-curator")
        payload = serialize_records(
            session, record_type=SubmissionRecordType.network_solve, record_ids=[solve.id]
        )[solve.id]

        shipped = {d["determination_key"]: d for d in payload["network_kinetics_determination"]}
        assert set(shipped) == {"d_assoc", "d_diss"}
        for row in shipped.values():
            assert "identity_hash" not in row  # a digest of this database's row ids
            assert row["public_ref"].startswith("nkdet_")
            assert row["observable_declaration"]["version"] == 1
            assert not any(key in ("id", "solve_id", "channel_id") for key in row), row
        fits = payload["network_kinetics"]
        assert {f["determination_ref"] for f in fits} == {d["public_ref"] for d in shipped.values()}
        assert {f["representation_declaration"]["key"] for f in fits} == {"plog1", "cheb1"}
        member = payload["target_declaration"]["product_sets"][0]["members"][0]
        assert member["determination_ref"] == shipped["d_assoc"]["public_ref"]
    finally:
        session.rollback()


def test_a_legacy_solve_keeps_its_snapshot_and_a_declared_one_changes_it(db_conn) -> None:
    from app.services.reproducibility_rubric import _target_snapshot

    legacy_session, legacy_network = _persist(db_conn, _full_payload(include_solve=True))
    try:
        legacy = legacy_session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == legacy_network.id)
        ).one()
        snapshot = _target_snapshot(legacy, SubmissionRecordType.network_solve)
        # Null additions must not restale a stored assessment of a row that predates them.
        assert not {"target_declaration", "protocol_declaration", "validation_declaration"} & set(snapshot["columns"])
        assert "determinations" not in snapshot["relationships"]
    finally:
        legacy_session.rollback()

    session, network = _persist(db_conn, _declared_payload())
    try:
        solve = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == network.id)).one()
        snapshot = _target_snapshot(solve, SubmissionRecordType.network_solve)
        assert snapshot["columns"]["target_declaration"]["product_sets"][0]["product_set_key"] == "assoc"
        assert "protocol_declaration" in snapshot["columns"] and "validation_declaration" in snapshot["columns"]
        determinations = snapshot["relationships"]["determinations"]
        assert [d["columns"]["determination_key"] for d in determinations] == ["d_assoc", "d_diss"]
        grouped = {d["columns"]["determination_key"]: d["fits"] for d in determinations}
        assert sorted(f["representation_declaration"]["key"] for f in grouped["d_assoc"]) == ["cheb1", "plog1"]

        # Regrouping a fit changes the digest: the grouping is part of what the solve claims.
        before = _target_snapshot(solve, SubmissionRecordType.network_solve)
        fit = next(f for f in solve.kinetics_records if f.representation_declaration["key"] == "cheb1")
        fit.representation_declaration = {"version": 1, "key": "cheb_renamed", "fit_origin": "solver_output"}
        session.flush()
        assert _target_snapshot(solve, SubmissionRecordType.network_solve) != before
    finally:
        session.rollback()
