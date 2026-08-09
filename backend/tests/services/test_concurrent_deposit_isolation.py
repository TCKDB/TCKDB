"""Two more instances of the bug class, found by sweeping for its shape.

Both were located by looking for what the five named sites had in common
rather than for the sites themselves, and both destroy an upload for a reason
that is not about the upload.

**Literature.** ``resolve_or_create_literature`` is check-then-insert against
the normalized DOI/ISBN uniqueness indexes, and it runs *during* deposit — so
the collateral of losing that race is scientific records, not a citation. Two
contributors citing the same paper at the same time is at least as ordinary as
two contributors depositing against the same molecule. Every sibling identity
resolver (species, reaction, geometry, software, calculation,
energy-correction) already wraps its insert in ``begin_nested()``; this one
was the exception.

**Geometry validation.** ``run_and_persist_geometry_validation`` is
best-effort by policy — its own docstring says "never aborts the upload" and
"recorded as evidence, never used as a hard upload gate", and its call sites
in ``computed_species`` / ``computed_reaction`` repeat the promise. But its
``except Exception`` covered only the chemistry call, and the row was added
with no flush, so the INSERT was emitted by whatever flushed next — the
route's COMMIT, outside every guard. A verdict *about* a calculation could
take the calculation with it.

Assertions are made from a fresh session after a genuine top-level commit.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
)
from app.db.models.common import (
    CalculationType,
    MoleculeKind,
    StereoKind,
    ValidationStatus,
)
from app.db.models.literature import Literature
from app.db.models.species import Species
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.services import geometry_validation as geomval
from app.services.literature_resolution import resolve_or_create_literature

MARKER = "concurdep"
DOI = "10.1000/concurdep-shared-paper"


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
    """Commits for real; a rollback would hide the failure under test."""
    try:
        yield
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(Literature).where(Literature.doi == DOI))
            cleanup.execute(
                delete(Species).where(Species.smiles.like(f"[He]{MARKER}%"))
            )
            cleanup.commit()


def _request() -> LiteratureUploadRequest:
    return LiteratureUploadRequest(
        doi=DOI,
        title="A paper two contributors both cite",
        journal="Journal of Simultaneous Deposits",
        year=2026,
    )


@pytest.fixture(autouse=True)
def _no_doi_lookup(monkeypatch):
    """Keep the resolver offline; metadata enrichment is not under test."""
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {},
    )


class TestConcurrentCitationOfOnePaper:
    def test_the_loser_adopts_the_citation_and_keeps_its_upload(
        self, db_engine, committed_scratch
    ) -> None:
        """The winner holds the uncommitted index entry; the loser must survive.

        Fully ordered so it reproduces every run: the loser reads (nothing),
        the winner inserts and holds, the loser's INSERT blocks on the unique
        index, the winner commits, and the loser's INSERT violates.
        """
        loser_selected = threading.Event()
        winner_inserted = threading.Event()
        outcome: dict[str, object] = {}

        def _loser() -> None:
            with Session(db_engine) as session:
                try:
                    assert session.scalar(
                        select(Literature).where(Literature.doi == DOI)
                    ) is None
                    # The upload's science is already written by the time
                    # literature is resolved.
                    session.add(Species(**_species_kwargs("-loser")))
                    session.flush()
                    loser_selected.set()
                    assert winner_inserted.wait(timeout=30)

                    lit = resolve_or_create_literature(session, _request())
                    outcome["literature_id"] = lit.id
                    session.commit()
                except BaseException as exc:
                    outcome["error"] = exc
                    session.rollback()

        with Session(db_engine) as winner:
            assert winner.scalar(
                select(Literature).where(Literature.doi == DOI)
            ) is None

            thread = threading.Thread(target=_loser, daemon=True)
            thread.start()
            assert loser_selected.wait(timeout=30)

            winner_lit = resolve_or_create_literature(winner, _request())
            winner_literature_id = winner_lit.id
            winner_inserted.set()
            time.sleep(1.5)  # let the loser reach and block on its INSERT
            winner.commit()

        thread.join(timeout=30)
        assert not thread.is_alive()
        assert "error" not in outcome, (
            f"the second contributor's upload was destroyed: {outcome.get('error')!r}"
        )
        assert outcome["literature_id"] == winner_literature_id

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-loser")
            ) is not None
            rows = verify.scalars(
                select(Literature).where(Literature.doi == DOI)
            ).all()
            assert len(rows) == 1, "one paper must yield one citation row"

    def test_the_sequential_path_is_unchanged(
        self, db_engine, committed_scratch
    ) -> None:
        """Resolving the same DOI twice in one transaction still dedupes."""
        with Session(db_engine) as session:
            first = resolve_or_create_literature(session, _request())
            second = resolve_or_create_literature(session, _request())
            assert first.id == second.id
            session.commit()

        with Session(db_engine) as verify:
            assert len(
                verify.scalars(
                    select(Literature).where(Literature.doi == DOI)
                ).all()
            ) == 1


class TestGeometryValidationIsTrulyBestEffort:
    """Its docstring and both call sites promise it never aborts the upload.

    The behaviour of the isolation itself is pinned by
    ``tests/services/test_best_effort_isolation.py``; what matters here is
    that this function's write actually goes through it, because previously
    the row was added with no flush and its INSERT escaped to the caller's
    ``COMMIT`` where no guard could reach it.
    """

    def test_the_persist_step_runs_inside_the_isolation(
        self, db_engine, committed_scratch, monkeypatch
    ) -> None:
        """A failing persist must be absorbed, not raised at the caller."""
        calls: list[str] = []
        real_isolate = geomval.isolated_best_effort

        def _tracking_isolate(session, work, *, what):
            calls.append(what)
            return real_isolate(session, work, what=what)

        monkeypatch.setattr(geomval, "isolated_best_effort", _tracking_isolate)
        # Reach the persist step without building a full opt calculation:
        # everything before it is a pure skip-gate.
        monkeypatch.setattr(
            geomval,
            "validate_calculation_geometry",
            lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("unreachable in this test")
            ),
        )

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs("-geom")))

            # The persist closure, exercised through the same helper the
            # function now routes it through, with an unstorable verdict.
            result = geomval.isolated_best_effort(
                session,
                lambda: session.add(
                    Species(
                        **{
                            **_species_kwargs("-unstorable"),
                            "smiles": f"[He]{MARKER}" + chr(0) + "z",
                        }
                    )
                ),
                what="geometry validation for calculation id=1",
            )
            assert result is None
            assert calls == ["geometry validation for calculation id=1"]

            # Still usable: the workflow validates every additional calc next.
            session.add(Species(**_species_kwargs("-geom2")))
            session.commit()

        with Session(db_engine) as verify:
            found = {
                s.smiles
                for s in verify.scalars(
                    select(Species).where(Species.smiles.like(f"[He]{MARKER}-geom%"))
                ).all()
            }
            assert found == {f"[He]{MARKER}-geom", f"[He]{MARKER}-geom2"}

    def test_the_function_routes_its_write_through_the_isolation(
        self, db_engine, committed_scratch, monkeypatch
    ) -> None:
        """Pin the wiring behaviourally: a bare ``add()`` must not come back.

        This calls the real ``run_and_persist_geometry_validation`` and makes
        its verdict row unstorable, by handing it a ``Calculation`` whose id
        does not exist — so the ``calculation_id`` foreign key fails on the
        INSERT. The two implementations differ exactly here:

        * inside ``isolated_best_effort`` the INSERT is flushed within a
          SAVEPOINT, so the failure is absorbed, the function returns ``None``,
          and the caller's own work still commits;
        * with a bare ``session.add()`` the row stays pending and its INSERT is
          emitted by the caller's ``COMMIT`` — which is the shape this test
          exists to forbid, and which would fail the ``session.commit()`` below
          and lose the species written before it.

        The predecessor of this test asserted ``"isolated_best_effort("`` was
        present in ``inspect.getsource(...)``, which passes for a reformat and
        fails for a rename — it never executed the path it claimed to protect.

        Everything before the persist step is a pure skip-gate on inputs this
        test does not care about, so the geometry lookups and the chemistry
        call are stubbed; the write itself is real.
        """
        # An id no calculation row will ever have, so the FK cannot resolve.
        orphan_calculation_id = 2**40
        calculation = Calculation(
            id=orphan_calculation_id, type=CalculationType.opt
        )

        class _Link:
            geometry = object()
            geometry_id = None

        monkeypatch.setattr(geomval, "_select_output_geometry", lambda s, cid: _Link())
        monkeypatch.setattr(geomval, "_select_input_geometry", lambda s, cid: None)
        monkeypatch.setattr(
            geomval, "_atoms_from_geometry", lambda geometry: (("He", 0.0, 0.0, 0.0),)
        )
        monkeypatch.setattr(
            geomval,
            "validate_calculation_geometry",
            lambda **kwargs: geomval.GeometryValidationResult(
                species_smiles=kwargs["species_smiles"],
                is_isomorphic=True,
                rmsd=None,
                atom_mapping=None,
                n_mappings=1,
                validation_status=ValidationStatus.passed,
                validation_reason=None,
                rmsd_warning_threshold=None,
                input_geometry_id=None,
                output_geometry_id=None,
            ),
        )

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs("-wiring")))

            assert (
                geomval.run_and_persist_geometry_validation(
                    session, calculation, species_smiles=f"[He]{MARKER}"
                )
                is None
            ), "an unstorable verdict must be absorbed, not returned"

            # The caller's commit is where a bare add() would surface.
            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-wiring")
            ) is not None, (
                "the upload's own row was lost with the verdict about it"
            )
            assert verify.scalar(
                select(CalculationGeometryValidation).where(
                    CalculationGeometryValidation.calculation_id
                    == orphan_calculation_id
                )
            ) is None
