"""Service-level tests for parsed calculation parameter persistence.

Covers the wiring that turns parsed execution-control parameters into
``calculation_parameter`` rows during normal calculation ingestion via the
shared ``resolve_and_persist_calculation_with_results`` path.

The key invariants under test:

- raw observations are always persisted, even when the canonical key is
  unknown to the vocab table
- canonical keys link through the FK when the vocab row exists
- the parser snapshot on ``Calculation`` (``parameters_json`` and friends)
  coexists with the relational parameter rows
- ordering metadata (``parameter_index``) round-trips deterministically
- multi-call workflows (primary + additional calculations) wire each
  observation to the correct calculation row
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationParameter,
    CalculationParameterVocab,
)
from app.db.models.common import CalculationType
from app.schemas.fragments.calculation import (
    CalculationParameterObservation,
    CalculationWithResultsPayload,
    OptResultPayload,
    SPResultPayload,
)
from app.schemas.fragments.geometry import GeometryPayload
from app.services.calculation_resolution import (
    persist_additional_calculations,
    resolve_and_persist_calculation_with_results,
)
from app.services.geometry_resolution import resolve_geometry_payload

_SOFTWARE = {"name": "gaussian", "version": "16", "revision": "C.02"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


_INCHI_COUNTER = 0


def _next_inchi_key(prefix: str) -> str:
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


def _create_species_entry(session: Session, *, inchi_key: str) -> int:
    species_id = session.connection().execute(
        text(
            """
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """
        ),
        {"smiles": inchi_key, "inchi_key": inchi_key},
    ).scalar_one()
    return session.connection().execute(
        text(
            """
            INSERT INTO species_entry (species_id)
            VALUES (:species_id)
            RETURNING id
            """
        ),
        {"species_id": species_id},
    ).scalar_one()


def _seed_vocab(session: Session, canonical_keys: list[str]) -> None:
    """Insert vocab rows skipping any that already exist (test-scoped)."""

    if not canonical_keys:
        return
    existing = set(
        session.scalars(
            select(CalculationParameterVocab.canonical_key).where(
                CalculationParameterVocab.canonical_key.in_(canonical_keys)
            )
        ).all()
    )
    for key in canonical_keys:
        if key in existing:
            continue
        session.add(CalculationParameterVocab(canonical_key=key))
    session.flush()


def _opt_upload(
    *,
    parameters: list[CalculationParameterObservation] | None = None,
    parameters_json: dict | None = None,
    parameters_parser_version: str | None = None,
    parameters_extracted_at: datetime | None = None,
) -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload(
        type=CalculationType.opt,
        software_release=_SOFTWARE,
        level_of_theory=_LOT,
        opt_result=OptResultPayload(converged=True),
        parameters=parameters,
        parameters_json=parameters_json,
        parameters_parser_version=parameters_parser_version,
        parameters_extracted_at=parameters_extracted_at,
    )


# ---------------------------------------------------------------------------
# 1. End-to-end parameter persistence through the shared workflow path.
# ---------------------------------------------------------------------------


def test_parameters_persist_through_resolve_and_persist(db_engine) -> None:
    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("PARAMS")
        )
        _seed_vocab(session, ["opt.convergence"])

        observations = [
            CalculationParameterObservation(
                raw_key="tight",
                raw_value="tight",
                canonical_key="opt.convergence",
                canonical_value="tight",
                section="opt",
                value_type="enum",
            ),
            CalculationParameterObservation(
                raw_key="%mem",
                raw_value="8GB",
                section="resource",
                value_type="string",
                unit="GB",
            ),
        ]

        upload = _opt_upload(parameters=observations)

        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        rows = session.scalars(
            select(CalculationParameter)
            .where(CalculationParameter.calculation_id == calc.id)
            .order_by(CalculationParameter.id)
        ).all()
        assert len(rows) == 2

        first, second = rows
        assert first.raw_key == "tight"
        assert first.raw_value == "tight"
        assert first.canonical_key == "opt.convergence"
        assert first.canonical_value == "tight"
        assert first.section == "opt"
        assert first.value_type == "enum"

        assert second.raw_key == "%mem"
        assert second.raw_value == "8GB"
        assert second.canonical_key is None
        assert second.section == "resource"
        assert second.unit == "GB"


# ---------------------------------------------------------------------------
# 2. Unknown canonical key still persists with canonical_key = NULL.
# ---------------------------------------------------------------------------


def test_unknown_canonical_key_does_not_block_persistence(db_engine) -> None:
    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("PARAMSUNK")
        )

        # No vocab seeding — the canonical_key should be silently demoted.
        observations = [
            CalculationParameterObservation(
                raw_key="some_unmapped_keyword",
                raw_value="42",
                canonical_key="not_in_vocab_yet",
                canonical_value="42",
                section="custom",
                value_type="int",
            ),
        ]

        upload = _opt_upload(parameters=observations)

        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        row = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc.id
            )
        ).one()
        assert row.raw_key == "some_unmapped_keyword"
        assert row.raw_value == "42"
        assert row.canonical_key is None
        # canonical_value is dropped when canonical_key is not linkable
        assert row.canonical_value is None
        assert row.section == "custom"
        assert row.value_type == "int"


# ---------------------------------------------------------------------------
# 3. Known canonical keys link through the FK when the vocab row exists.
# ---------------------------------------------------------------------------


def test_known_canonical_key_links_when_vocab_exists(db_engine) -> None:
    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("PARAMSKNOWN")
        )
        _seed_vocab(session, ["scf.convergence"])

        observations = [
            CalculationParameterObservation(
                raw_key="tight",
                raw_value="tight",
                canonical_key="scf.convergence",
                canonical_value="tight",
                section="scf",
                value_type="enum",
            ),
        ]
        upload = _opt_upload(parameters=observations)

        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        row = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc.id
            )
        ).one()
        assert row.canonical_key == "scf.convergence"
        assert row.canonical_value == "tight"
        assert row.vocab is not None
        assert row.vocab.canonical_key == "scf.convergence"


# ---------------------------------------------------------------------------
# 4. parameters_json snapshot coexists with relational rows.
# ---------------------------------------------------------------------------


def test_snapshot_and_relational_persistence_coexist(db_engine) -> None:
    extracted_at = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc).replace(
        tzinfo=None
    )
    snapshot = {
        "route_line": "#p opt b3lyp/6-31g(d)",
        "sections": {"opt": {"tight": "tight"}},
    }

    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("PARAMSSNAP")
        )

        observations = [
            CalculationParameterObservation(
                raw_key="tight",
                raw_value="tight",
                section="opt",
            ),
        ]

        upload = _opt_upload(
            parameters=observations,
            parameters_json=snapshot,
            parameters_parser_version="gaussian_v1",
            parameters_extracted_at=extracted_at,
        )

        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        stored = session.get(Calculation, calc.id)
        assert stored.parameters_json == snapshot
        assert stored.parameters_parser_version == "gaussian_v1"
        assert stored.parameters_extracted_at == extracted_at

        rows = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc.id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].raw_key == "tight"


# ---------------------------------------------------------------------------
# 5. Repeated/indexed observations preserve parameter_index deterministically.
# ---------------------------------------------------------------------------


def test_indexed_repeated_observations_persist_in_order(db_engine) -> None:
    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("PARAMSIDX")
        )

        # Three observations of the same raw_key, distinguished only by
        # parameter_index.  Persistence must keep all three rows and preserve
        # the index so callers can reconstruct ordering.
        observations = [
            CalculationParameterObservation(
                raw_key="iop",
                raw_value=str(value),
                section="internal_option",
                parameter_index=index,
            )
            for index, value in enumerate([2000, 33, 42])
        ]

        upload = _opt_upload(parameters=observations)

        calc = resolve_and_persist_calculation_with_results(
            session, upload, species_entry_id=species_entry_id
        )

        rows = session.scalars(
            select(CalculationParameter)
            .where(CalculationParameter.calculation_id == calc.id)
            .order_by(CalculationParameter.parameter_index.asc())
        ).all()
        assert [r.parameter_index for r in rows] == [0, 1, 2]
        assert [r.raw_value for r in rows] == ["2000", "33", "42"]
        assert all(r.raw_key == "iop" for r in rows)


# ---------------------------------------------------------------------------
# 6. Additional-calculation path also writes parameters via the shared helper.
# ---------------------------------------------------------------------------


def test_parameters_persist_for_additional_calculations(db_engine) -> None:
    """``persist_additional_calculations`` delegates to the shared helper, so
    parameters on per-child uploads must end up on the right calculation."""

    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("PARAMSADD")
        )

        primary_upload = _opt_upload(
            parameters=[
                CalculationParameterObservation(
                    raw_key="tight", raw_value="tight", section="opt"
                ),
            ]
        )
        primary = resolve_and_persist_calculation_with_results(
            session, primary_upload, species_entry_id=species_entry_id
        )

        # Build a child SP calculation carrying its own parameters.
        sp_upload = CalculationWithResultsPayload(
            type=CalculationType.sp,
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
            sp_result=SPResultPayload(electronic_energy_hartree=-1.0),
            parameters=[
                CalculationParameterObservation(
                    raw_key="tightscf", raw_value="tightscf", section="scf"
                ),
            ],
        )

        # Insert a geometry the additional calc can attach to.
        geometry = resolve_geometry_payload(
            session,
            GeometryPayload(xyz_text="1\nH\nH 0.0 0.0 0.0\n"),
        )
        geometry_id = geometry.id

        children = persist_additional_calculations(
            session,
            primary_calc=primary,
            additional_uploads=[sp_upload],
            geometry_id=geometry_id,
            species_entry_id=species_entry_id,
        )
        assert len(children) == 1
        child = children[0]

        primary_rows = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == primary.id
            )
        ).all()
        child_rows = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == child.id
            )
        ).all()

        assert {r.raw_key for r in primary_rows} == {"tight"}
        assert {r.raw_key for r in child_rows} == {"tightscf"}
