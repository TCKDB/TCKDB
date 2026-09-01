"""Tests for the converged-opt-energy ingest guard (issue #292).

52 converged optimisations on the deployed archive carry
``converged = true`` with no ``final_energy_hartree``. Convergence is
itself a claim about the energy -- an optimiser declares it when the
energy change between steps falls below a threshold -- so claiming
convergence while reporting no usable energy is claiming a result was
reached without reporting it.

The settled rule, exercised here directly against
:func:`collect_converged_opt_energy_warnings`:

  (a) a single-point calculation exists for the same owner (species_entry
      or transition_state_entry), at *any* level of theory -> no warning,
      the energy is obtainable from that ``sp``.
  (b) no such single point -> the ``opt`` must carry both its own
      ``final_energy_hartree`` *and* an artifact, or it warns.

``converged`` false or NULL never warns -- the rule is about a claim of
convergence, not about optimisations in general.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationArtifact
from app.db.models.common import ArtifactKind, CalculationRecordKind, CalculationType
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    OptResultPayload,
)
from app.services import calculation_resolution
from app.services.calculation_resolution import (
    W_CONVERGED_OPT_NO_USABLE_ENERGY,
    collect_converged_opt_energy_warnings,
    resolve_and_persist_calculation_with_results,
)

_SOFTWARE = {"name": "gaussian", "version": "16"}
_LOT_A = {"method": "wb97xd", "basis": "def2tzvp"}
_LOT_B = {"method": "MRCI+Davidson", "basis": "aug-cc-pV(T+d)Z"}

_COUNTER = 0


def _next_tag(prefix: str) -> str:
    global _COUNTER
    _COUNTER += 1
    stem = f"{prefix}{_COUNTER:0>21}"
    return stem[:27]


def _create_species_entry(session: Session) -> int:
    tag = _next_tag("CVGOPT")
    species_id = session.connection().execute(
        text(
            """
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """
        ),
        {"smiles": tag, "inchi_key": tag},
    ).scalar_one()
    return session.connection().execute(
        text(
            "INSERT INTO species_entry (species_id) VALUES (:species_id) RETURNING id"
        ),
        {"species_id": species_id},
    ).scalar_one()


def _create_ts_entry(session: Session) -> int:
    reaction_id = session.connection().execute(
        text("INSERT INTO chem_reaction (reversible) VALUES (true) RETURNING id")
    ).scalar_one()
    reaction_entry_id = session.connection().execute(
        text(
            "INSERT INTO reaction_entry (reaction_id) VALUES (:r) RETURNING id"
        ),
        {"r": reaction_id},
    ).scalar_one()
    ts_id = session.connection().execute(
        text(
            "INSERT INTO transition_state (reaction_entry_id) VALUES (:r) RETURNING id"
        ),
        {"r": reaction_entry_id},
    ).scalar_one()
    return session.connection().execute(
        text(
            "INSERT INTO transition_state_entry "
            "(transition_state_id, charge, multiplicity) "
            "VALUES (:ts, 0, 2) RETURNING id"
        ),
        {"ts": ts_id},
    ).scalar_one()


def _opt_upload(
    *,
    converged: bool | None,
    final_energy_hartree: float | None = None,
    lot: dict = _LOT_A,
) -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload(
        type=CalculationType.opt,
        software_release=_SOFTWARE,
        level_of_theory=lot,
        opt_result=OptResultPayload(
            converged=converged, final_energy_hartree=final_energy_hartree
        ),
    )


def _sp_upload(*, lot: dict = _LOT_A) -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload(
        type=CalculationType.sp,
        software_release=_SOFTWARE,
        level_of_theory=lot,
    )


def _attach_artifact(session: Session, calculation_id: int) -> None:
    session.add(
        CalculationArtifact(
            calculation_id=calculation_id,
            kind=ArtifactKind.output_log,
            uri=f"s3://tckdb-test/{calculation_id}/job.log",
            sha256="0" * 64,
            bytes=1024,
            filename="job.log",
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# 1. Base case: converged, no energy, no sp anywhere -> warns.
# ---------------------------------------------------------------------------


def test_converged_no_energy_no_sp_warns(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(session)
        opt = resolve_and_persist_calculation_with_results(
            session, _opt_upload(converged=True), species_entry_id=species_entry_id
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [opt.id])

        assert len(warnings) == 1
        assert warnings[0].code == W_CONVERGED_OPT_NO_USABLE_ENERGY
        assert warnings[0].field == "opt_result"


# ---------------------------------------------------------------------------
# 2. sp on the same owner at a DIFFERENT level of theory -> silence.
#    This is the 39-record historical case: an over-strict same-level
#    predicate would get this wrong.
# ---------------------------------------------------------------------------


def test_sp_same_owner_different_lot_silences(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(session)
        opt = resolve_and_persist_calculation_with_results(
            session,
            _opt_upload(converged=True, lot=_LOT_A),
            species_entry_id=species_entry_id,
        )
        sp = resolve_and_persist_calculation_with_results(
            session,
            _sp_upload(lot=_LOT_B),
            species_entry_id=species_entry_id,
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [opt.id, sp.id])

        assert warnings == []


# ---------------------------------------------------------------------------
# 3. sp on a DIFFERENT owner (species direction) -> warns. Cross-owner
#    leakage: a foreign sp must never satisfy the rule.
# ---------------------------------------------------------------------------


def test_an_sp_under_the_OTHER_owner_kind_does_not_silence(db_conn, monkeypatch) -> None:
    """The sp lookup must be scoped BY OWNER KIND, not by id alone.

    ``species_entry`` and ``transition_state_entry`` are separate tables
    with separate id sequences, so the same integer routinely names a row
    in both. If the lookup ignored the kind, a species entry's single
    point would silence the warning for an unrelated transition-state
    entry sharing that number, and vice versa -- no error, no symptom, a
    genuinely unusable deposit passing quietly.

    The two "different owner" tests below vary the id WITHIN one kind, so
    neither can catch a dropped kind: that only bites when ids collide
    ACROSS kinds. Rather than depend on two independent sequences
    happening to align, this stubs the loader to report the owner's id
    under the WRONG kind and asserts the warning still fires. Verified:
    replacing the kind-scoped lookup with an any-kind one passes every
    other test in this file.
    """
    with Session(db_conn) as session, session.begin():
        owner_id = _create_ts_entry(session)
        opt = resolve_and_persist_calculation_with_results(
            session,
            _opt_upload(converged=True),
            transition_state_entry_id=owner_id,
        )
        session.flush()

        # The sp exists, but under `species` -- a different owner kind.
        monkeypatch.setattr(
            calculation_resolution,
            "_load_sp_owner_ids",
            lambda _session, _wanted: {
                CalculationRecordKind.species: {owner_id},
                CalculationRecordKind.transition_state: set(),
            },
        )

        warnings = collect_converged_opt_energy_warnings(session, [opt.id])

    assert len(warnings) == 1, (
        "a single point owned by a SPECIES entry must not silence the "
        "warning for a TRANSITION STATE entry that shares its id"
    )
    assert warnings[0].code == W_CONVERGED_OPT_NO_USABLE_ENERGY


def test_sp_on_different_species_owner_still_warns(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        owner_id = _create_species_entry(session)
        other_owner_id = _create_species_entry(session)
        assert owner_id != other_owner_id

        opt = resolve_and_persist_calculation_with_results(
            session, _opt_upload(converged=True), species_entry_id=owner_id
        )
        resolve_and_persist_calculation_with_results(
            session, _sp_upload(), species_entry_id=other_owner_id
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [opt.id])

        assert len(warnings) == 1
        assert warnings[0].code == W_CONVERGED_OPT_NO_USABLE_ENERGY


# ---------------------------------------------------------------------------
# 3b. Same leakage assertion, transition-state direction.
# ---------------------------------------------------------------------------


def test_sp_on_different_ts_owner_still_warns(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        owner_id = _create_ts_entry(session)
        other_owner_id = _create_ts_entry(session)
        assert owner_id != other_owner_id

        opt = resolve_and_persist_calculation_with_results(
            session,
            _opt_upload(converged=True),
            transition_state_entry_id=owner_id,
        )
        resolve_and_persist_calculation_with_results(
            session, _sp_upload(), transition_state_entry_id=other_owner_id
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [opt.id])

        assert len(warnings) == 1
        assert warnings[0].code == W_CONVERGED_OPT_NO_USABLE_ENERGY


# ---------------------------------------------------------------------------
# 3c. sp on the SAME transition-state owner, any level of theory -> silence.
#     "Assert both directions" -- this is the TS-side mirror of test 2.
# ---------------------------------------------------------------------------


def test_sp_same_ts_owner_different_lot_silences(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        ts_entry_id = _create_ts_entry(session)
        opt = resolve_and_persist_calculation_with_results(
            session,
            _opt_upload(converged=True, lot=_LOT_A),
            transition_state_entry_id=ts_entry_id,
        )
        sp = resolve_and_persist_calculation_with_results(
            session,
            _sp_upload(lot=_LOT_B),
            transition_state_entry_id=ts_entry_id,
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [opt.id, sp.id])

        assert warnings == []


# ---------------------------------------------------------------------------
# 4. Own energy + own artifact, no sp -> silence (branch (b) satisfied).
# ---------------------------------------------------------------------------


def test_own_energy_and_artifact_no_sp_silences(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(session)
        opt = resolve_and_persist_calculation_with_results(
            session,
            _opt_upload(converged=True, final_energy_hartree=-100.123456),
            species_entry_id=species_entry_id,
        )
        _attach_artifact(session, opt.id)

        warnings = collect_converged_opt_energy_warnings(session, [opt.id])

        assert warnings == []


# ---------------------------------------------------------------------------
# 5. Own energy, NO artifact, no sp -> warns. This is the branch-(b)
#    decision this guard makes: an unevidenced number is exactly the
#    "bare metadata" shape that produced the 52 historical rows, so an
#    energy alone (without an artifact backing it) does not silence the
#    warning when there is no independent sp either.
# ---------------------------------------------------------------------------


def test_own_energy_no_artifact_no_sp_warns(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(session)
        opt = resolve_and_persist_calculation_with_results(
            session,
            _opt_upload(converged=True, final_energy_hartree=-100.123456),
            species_entry_id=species_entry_id,
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [opt.id])

        assert len(warnings) == 1
        assert warnings[0].code == W_CONVERGED_OPT_NO_USABLE_ENERGY


# ---------------------------------------------------------------------------
# 6. converged = false -> never warns, regardless of energy/sp/artifact.
# ---------------------------------------------------------------------------


def test_not_converged_never_warns(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(session)
        opt = resolve_and_persist_calculation_with_results(
            session,
            _opt_upload(converged=False),
            species_entry_id=species_entry_id,
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [opt.id])

        assert warnings == []


# ---------------------------------------------------------------------------
# 6b. converged = NULL (never assessed / not recorded) -> never warns.
# ---------------------------------------------------------------------------


def test_converged_null_never_warns(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(session)
        opt = resolve_and_persist_calculation_with_results(
            session,
            _opt_upload(converged=None),
            species_entry_id=species_entry_id,
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [opt.id])

        assert warnings == []


# ---------------------------------------------------------------------------
# 7. Historical shape: converged opt with no energy, sp present at a
#    different level of theory on the same owner (the exact wb97xd/def2tzvp
#    opt + MRCI+Davidson/aug-cc-pV(T+d)Z sp pairing from the issue).
#    All 52 archived rows have this shape -- must stay silent.
# ---------------------------------------------------------------------------


def test_historical_deposit_shape_silences(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(session)
        opt = resolve_and_persist_calculation_with_results(
            session,
            _opt_upload(converged=True, final_energy_hartree=None, lot=_LOT_A),
            species_entry_id=species_entry_id,
        )
        sp = resolve_and_persist_calculation_with_results(
            session,
            _sp_upload(lot=_LOT_B),
            species_entry_id=species_entry_id,
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [opt.id, sp.id])

        assert warnings == []


# ---------------------------------------------------------------------------
# 8. Non-opt and unrelated ids in the candidate list are simply ignored.
# ---------------------------------------------------------------------------


def test_non_opt_calculation_ids_ignored(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_entry_id = _create_species_entry(session)
        sp = resolve_and_persist_calculation_with_results(
            session, _sp_upload(), species_entry_id=species_entry_id
        )
        session.flush()

        warnings = collect_converged_opt_energy_warnings(session, [sp.id])

        assert warnings == []


def test_empty_calculation_ids_returns_empty(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        assert collect_converged_opt_energy_warnings(session, []) == []
