"""Opportunistic enrichment hooks must not be able to abort the upload.

``POST /calculations/{id}/artifacts`` persists the artifact rows and then runs
three enrichment hooks over each artifact, described in the route as
"best-effort (never abort the upload)": parameter extraction from input files,
single-point-energy reconciliation from output logs, and Hessian extraction.

Each hook had a guard, and none of the guards covered its writes:

* ``_extract_safe`` caught ``ParameterExtractionError`` — a *parse*-layer
  exception. ``persist_calculation_parameters`` then issues a ``DELETE`` and a
  batch of ``INSERT``s, and a database error there is not a
  ``ParameterExtractionError``. It escaped uncaught.

* The SP-energy and Hessian hooks had ``except Exception`` catch-alls, which
  is worse rather than better. PostgreSQL marks a transaction aborted at the
  point of error; catching the exception in Python does not undo that. With no
  ``ROLLBACK TO SAVEPOINT``, the swallowed error left the session poisoned and
  the failure resurfaced at ``COMMIT`` — after the response was determined,
  where it destroys the artifact upload rather than merely the enrichment.

Every assertion is made from a **fresh session after a genuine top-level
commit**. A NUL character is the encoding-independent stand-in for a value
PostgreSQL will not store.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db.models.common import (
    ArtifactKind,
    CalculationType,
    MoleculeKind,
    StereoKind,
)
from app.db.models.species import Species
from app.schemas.fragments.artifact import ArtifactIn
from app.services import calculation_parameter_extraction as cpe
from app.services import hessian_extraction as hess
from app.services import sp_energy_extraction as spx
from app.services.best_effort import isolated_best_effort

MARKER = "besteffort"

GAUSSIAN_LOG = (
    Path(__file__).parent.parent / "fixtures" / "gaussian" / "opt_g09.log"
)


def _species_kwargs(suffix: str) -> dict:
    return {
        "kind": MoleculeKind.molecule,
        "smiles": f"[He]{MARKER}{suffix}",
        "inchi_key": "SWQJXJOGLNCZEY-UHFFFAOYSA-N",
        "charge": 0,
        "multiplicity": 1,
        "stereo_kind": StereoKind.unspecified,
    }


@pytest.fixture
def committed_scratch(db_engine):
    """Commits for real; a rollback would hide the very failure under test."""
    try:
        yield
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(
                delete(Species).where(Species.smiles.like(f"[He]{MARKER}%"))
            )
            cleanup.commit()


def _explode(session: Session) -> None:
    """Provoke a real, transaction-aborting database error."""
    session.execute(text("SELECT 1 FROM table_that_does_not_exist"))


class TestIsolatedBestEffort:
    def test_payload_survives_a_failing_enrichment(
        self, db_engine, committed_scratch
    ) -> None:
        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs("-a")))

            assert isolated_best_effort(
                session, lambda: _explode(session), what="test enrichment"
            ) is None

            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-a")
            ) is not None

    def test_session_stays_usable_for_the_rest_of_the_request(
        self, db_engine, committed_scratch
    ) -> None:
        """The route loops over every artifact; one bad file must not end it."""
        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs("-b")))
            isolated_best_effort(
                session, lambda: _explode(session), what="test enrichment"
            )

            # A poisoned transaction would fail here, not above.
            session.add(Species(**_species_kwargs("-c")))
            session.commit()

        with Session(db_engine) as verify:
            found = {
                s.smiles
                for s in verify.scalars(
                    select(Species).where(Species.smiles.like(f"[He]{MARKER}-%"))
                ).all()
            }
            assert {f"[He]{MARKER}-b", f"[He]{MARKER}-c"} <= found

    def test_an_unflushed_mutation_fails_inside_the_savepoint(
        self, db_engine, committed_scratch
    ) -> None:
        """A hook that only assigns must not defer its failure to COMMIT.

        This is the 2026-08-05 shape: no SQL is issued by the hook itself, so
        without a flush inside the savepoint the ``UPDATE``/``INSERT`` is
        emitted by the commit — outside every guard the hook wrote.
        """
        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs("-d")))

            def _assign_only() -> None:
                # Never flushed by the hook; storable only at flush time.
                session.add(
                    Species(
                        **{
                            **_species_kwargs("-unstorable"),
                            "smiles": f"[He]{MARKER}" + chr(0) + "x",
                        }
                    )
                )

            assert isolated_best_effort(
                session, _assign_only, what="test enrichment"
            ) is None
            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-d")
            ) is not None

    def test_success_returns_the_value_and_keeps_the_write(
        self, db_engine, committed_scratch
    ) -> None:
        with Session(db_engine) as session:
            def _work() -> str:
                session.add(Species(**_species_kwargs("-e")))
                return "done"

            assert isolated_best_effort(session, _work, what="test") == "done"
            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-e")
            ) is not None

    def test_failure_is_logged(self, db_engine, committed_scratch, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="app.services.best_effort"):
            with Session(db_engine) as session:
                isolated_best_effort(
                    session, lambda: _explode(session), what="widget extraction"
                )
                session.rollback()

        assert "widget extraction" in " ".join(
            r.getMessage() for r in caplog.records
        )


class TestParameterExtractionWriteHalf:
    """Site 5 proper: the guard existed, it just did not cover the writes."""

    def test_a_database_error_in_the_write_half_no_longer_escapes(
        self, db_engine, committed_scratch, monkeypatch
    ) -> None:
        """``_extract_safe`` must survive a failure past the parse layer.

        A real Gaussian log is parsed all the way through — so the parse half
        genuinely succeeds and control genuinely reaches the writes — and the
        write is then made to fail the way a database failure does.
        """
        calls: list[str] = []

        def _failing_persist(session_, calculation, observations, **kwargs):
            calls.append("write half reached")
            _explode(session_)

        monkeypatch.setattr(cpe, "persist_calculation_parameters", _failing_persist)

        class _Calc:
            """Minimal stand-in: text-sniff dispatch, no software_release."""

            id = 1
            software_release = None

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs("-params")))

            assert cpe._extract_safe(
                session, _Calc(), GAUSSIAN_LOG.read_text(), source="job.inp"
            ) is None
            assert calls, "the parse half short-circuited; the writes were never run"

            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-params")
            ) is not None


class TestCatchAllsThatLeftTheTransactionAborted:
    """A swallowed DB error without a savepoint rollback is a delayed bomb."""

    def _artifact(self, kind: ArtifactKind) -> ArtifactIn:
        return ArtifactIn(
            kind=kind.value, filename="run.log", content_base64="aGVsbG8="
        )

    def test_hessian_hook_leaves_the_session_usable(
        self, db_engine, committed_scratch, monkeypatch
    ) -> None:
        class _Calc:
            type = CalculationType.freq
            id = 1

        with Session(db_engine) as session:
            monkeypatch.setattr(
                hess,
                "_extract_and_store",
                lambda s, c, a: _explode(session),
            )
            session.add(Species(**_species_kwargs("-hess")))

            hess.try_extract_hessian_from_artifact_upload(
                session, _Calc(), self._artifact(ArtifactKind.output_log)
            )

            # Pre-fix this raised: the transaction was already aborted.
            session.add(Species(**_species_kwargs("-hess2")))
            session.commit()

        with Session(db_engine) as verify:
            found = {
                s.smiles
                for s in verify.scalars(
                    select(Species).where(Species.smiles.like(f"[He]{MARKER}-hess%"))
                ).all()
            }
            assert found == {f"[He]{MARKER}-hess", f"[He]{MARKER}-hess2"}

    def test_sp_energy_hook_leaves_the_session_usable(
        self, db_engine, committed_scratch, monkeypatch
    ) -> None:
        class _Calc:
            type = CalculationType.sp
            id = 1

        with Session(db_engine) as session:
            monkeypatch.setattr(
                spx,
                "_reconcile_and_fill",
                lambda s, c, a: _explode(session),
            )
            session.add(Species(**_species_kwargs("-sp")))

            assert spx.try_reconcile_sp_energy_from_output_upload(
                session, _Calc(), self._artifact(ArtifactKind.output_log)
            ) is None

            session.add(Species(**_species_kwargs("-sp2")))
            session.commit()

        with Session(db_engine) as verify:
            found = {
                s.smiles
                for s in verify.scalars(
                    select(Species).where(Species.smiles.like(f"[He]{MARKER}-sp%"))
                ).all()
            }
            assert found == {f"[He]{MARKER}-sp", f"[He]{MARKER}-sp2"}
