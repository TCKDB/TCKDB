"""Tests for ``backend/scripts/extract_calculation_parameters.py``.

Drives the script's ``run_backfill`` entry point against the test
session so transactional state stays scoped to each test. The script's
own ``main`` wrapper around argparse + create_engine is exercised by a
single arg-validation test; everything else uses ``run_backfill``.

Storage round-trips are stubbed via ``load_artifact_bytes`` so the
suite does not require MinIO. Each candidate calc has at least one
input artifact whose bytes are served from an in-memory map keyed by
SHA-256.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationParameter,
)
from app.db.models.common import (
    ArtifactKind,
    CalculationType,
    ParameterSource,
)
from app.db.models.species import Species, SpeciesEntry
from app.db.models.software import Software, SoftwareRelease
from scripts import extract_calculation_parameters as script_mod


# A minimal Gaussian input file — Link0 + route line — that the
# Gaussian parser can extract parameters from.
GAUSSIAN_GJF_TEXT = (
    "%mem=1GB\n"
    "%nprocshared=4\n"
    "# B3LYP/6-31G(d) opt freq\n"
    "\n"
    "Hydrogen atom test input\n"
    "\n"
    "0 2\n"
    "H 0.0 0.0 0.0\n"
    "\n"
).encode()

GAUSSIAN_GJF_SHA = hashlib.sha256(GAUSSIAN_GJF_TEXT).hexdigest()
UNRECOGNISED_TEXT = b"this content has no recognised ESS markers\n"
UNRECOGNISED_SHA = hashlib.sha256(UNRECOGNISED_TEXT).hexdigest()


# ---------------------------------------------------------------------------
# Storage stub
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_load_artifact_bytes(monkeypatch) -> dict[str, bytes]:
    """In-memory replacement for ``load_artifact_bytes``.

    Tests register ``store[sha] = content`` for any artifact they
    expect the backfill to read. An unregistered SHA raises
    ``ArtifactStorageUnavailable`` so the helper's storage-failure
    branch is covered.
    """
    store: dict[str, bytes] = {}

    def _fake_load(sha256: str, *, client=None, bucket=None) -> bytes:
        try:
            return store[sha256]
        except KeyError:
            from app.services.artifact_storage import ArtifactStorageUnavailable

            raise ArtifactStorageUnavailable(
                f"test stub: no content registered for sha={sha256}"
            )

    # Patch the symbol the bridge module imported, not just the source
    # module — the bridge took a function reference at import time.
    monkeypatch.setattr(
        "app.services.calculation_parameter_extraction.load_artifact_bytes",
        _fake_load,
    )
    return store


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


_INCHI_COUNTER = {"n": 0}


def _next_inchi_key() -> str:
    _INCHI_COUNTER["n"] += 1
    n = _INCHI_COUNTER["n"]
    body = f"BACK{n:0>10}"[:14] + "-" + f"FILL{n:0>6}"[:10] + "-N"
    return body


def _seed_calc(
    db_session: Session,
    *,
    software_name: str = "Gaussian",
    with_input_artifact: bool = True,
    artifact_content: bytes = GAUSSIAN_GJF_TEXT,
    artifact_sha: str = GAUSSIAN_GJF_SHA,
    artifact_filename: str = "input.gjf",
) -> tuple[int, int | None]:
    """Create a Calculation row + optional input artifact. Returns (calc_id, artifact_id)."""

    species = Species(
        kind="molecule",
        smiles="[H]",
        inchi_key=_next_inchi_key(),
        charge=0,
        multiplicity=2,
        stereo_kind="unspecified",
    )
    db_session.add(species)
    db_session.flush()

    entry = SpeciesEntry(species_id=species.id)
    db_session.add(entry)
    db_session.flush()

    # Resolve / create software_release so the bridge can dispatch by
    # DB-linked software name without falling back to text sniffing.
    software = db_session.scalar(
        select(Software).where(Software.name == software_name.lower())
    )
    if software is None:
        software = Software(name=software_name.lower())
        db_session.add(software)
        db_session.flush()
    # Use a unique revision per call so multiple seeds in one test do
    # not collide on uq_software_release_software_id.
    release = SoftwareRelease(
        software_id=software.id,
        version="16",
        revision=f"C.{_INCHI_COUNTER['n']:03d}",
    )
    db_session.add(release)
    db_session.flush()

    calc = Calculation(
        type=CalculationType.opt,
        species_entry_id=entry.id,
        software_release_id=release.id,
    )
    db_session.add(calc)
    db_session.flush()

    artifact_id: int | None = None
    if with_input_artifact:
        artifact = CalculationArtifact(
            calculation_id=calc.id,
            kind=ArtifactKind.input,
            uri=f"s3://test/{artifact_sha[:2]}/{artifact_sha}",
            sha256=artifact_sha,
            bytes=len(artifact_content),
            filename=artifact_filename,
        )
        db_session.add(artifact)
        db_session.flush()
        artifact_id = artifact.id

    return calc.id, artifact_id


def _parser_count(db_session: Session, calc_id: int) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(CalculationParameter)
        .where(
            CalculationParameter.calculation_id == calc_id,
            CalculationParameter.source == ParameterSource.parser,
        )
    ) or 0


# ---------------------------------------------------------------------------
# CLI argument validation
# ---------------------------------------------------------------------------


class TestArgs:
    def test_artifact_id_requires_calculation_id(self):
        with pytest.raises(SystemExit):
            script_mod.main(["--all-missing", "--artifact-id", "1"])

    def test_must_choose_target_mode(self):
        # Mutually-exclusive group is required; argparse raises SystemExit(2).
        with pytest.raises(SystemExit):
            script_mod.main([])


# ---------------------------------------------------------------------------
# Per-calc selection + processing
# ---------------------------------------------------------------------------


class TestRunBackfill:
    def test_calculation_id_parses_one(
        self, db_session, stub_load_artifact_bytes
    ):
        calc_id, _ = _seed_calc(db_session)
        stub_load_artifact_bytes[GAUSSIAN_GJF_SHA] = GAUSSIAN_GJF_TEXT

        stats = script_mod.run_backfill(
            db_session, calculation_id=calc_id
        )

        assert stats.candidates == 1
        assert stats.succeeded == 1
        assert _parser_count(db_session, calc_id) > 0

    def test_all_missing_skips_calcs_with_existing_parser_rows(
        self, db_session, stub_load_artifact_bytes
    ):
        # Calc A: has parser rows already → must be skipped.
        calc_a, _ = _seed_calc(db_session)
        from app.services.calculation_resolution import (
            persist_calculation_parameters,
        )
        from app.schemas.fragments.calculation import (
            CalculationParameterObservation,
        )

        persist_calculation_parameters(
            db_session,
            db_session.get(Calculation, calc_a),
            [
                CalculationParameterObservation(
                    raw_key="seed", raw_value="v", section="custom"
                )
            ],
            source=ParameterSource.parser,
            parser_version="seed_v0",
        )
        db_session.flush()

        # Calc B: no parser rows → must be processed.
        calc_b, _ = _seed_calc(db_session)
        stub_load_artifact_bytes[GAUSSIAN_GJF_SHA] = GAUSSIAN_GJF_TEXT

        stats = script_mod.run_backfill(db_session, all_missing=True)

        # Only B is in the candidate set.
        assert stats.candidates == 1
        assert stats.succeeded == 1
        # A's seed row is intact (not replaced).
        seed_rows = db_session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc_a,
                CalculationParameter.parser_version == "seed_v0",
            )
        ).all()
        assert len(seed_rows) == 1

    def test_force_reparses_existing_parser_rows(
        self, db_session, stub_load_artifact_bytes
    ):
        calc_id, _ = _seed_calc(db_session)
        from app.services.calculation_resolution import (
            persist_calculation_parameters,
        )
        from app.schemas.fragments.calculation import (
            CalculationParameterObservation,
        )

        persist_calculation_parameters(
            db_session,
            db_session.get(Calculation, calc_id),
            [
                CalculationParameterObservation(
                    raw_key="stale", raw_value="v", section="custom"
                )
            ],
            source=ParameterSource.parser,
            parser_version="stale_v0",
        )
        db_session.flush()
        assert _parser_count(db_session, calc_id) == 1

        stub_load_artifact_bytes[GAUSSIAN_GJF_SHA] = GAUSSIAN_GJF_TEXT
        stats = script_mod.run_backfill(
            db_session, all_missing=True, force=True
        )

        assert stats.candidates == 1
        assert stats.succeeded == 1
        # Stale row is gone; new rows have current parser version.
        stale = db_session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc_id,
                CalculationParameter.parser_version == "stale_v0",
            )
        ).all()
        assert stale == []
        new_rows = db_session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc_id,
                CalculationParameter.source == ParameterSource.parser,
            )
        ).all()
        assert all(r.parser_version != "stale_v0" for r in new_rows)
        assert new_rows

    def test_dry_run_writes_nothing(
        self, db_session, stub_load_artifact_bytes
    ):
        calc_id, _ = _seed_calc(db_session)
        stub_load_artifact_bytes[GAUSSIAN_GJF_SHA] = GAUSSIAN_GJF_TEXT

        stats = script_mod.run_backfill(
            db_session, calculation_id=calc_id, dry_run=True
        )

        assert stats.candidates == 1
        assert stats.succeeded == 0
        assert _parser_count(db_session, calc_id) == 0
        # Mirrored fields untouched.
        calc = db_session.get(Calculation, calc_id)
        assert calc.parameters_parser_version is None
        assert calc.parameters_extracted_at is None

    def test_unrecognised_software_logs_skip_and_continues(
        self, db_session, stub_load_artifact_bytes
    ):
        # First calc: bytes are unrecognised AND no software_release;
        # extraction will skip with a warning but the run keeps going.
        species = Species(
            kind="molecule",
            smiles="[H]",
            inchi_key=_next_inchi_key(),
            charge=0,
            multiplicity=2,
            stereo_kind="unspecified",
        )
        db_session.add(species)
        db_session.flush()
        entry = SpeciesEntry(species_id=species.id)
        db_session.add(entry)
        db_session.flush()
        nosw_calc = Calculation(
            type=CalculationType.opt, species_entry_id=entry.id
        )
        db_session.add(nosw_calc)
        db_session.flush()
        artifact = CalculationArtifact(
            calculation_id=nosw_calc.id,
            kind=ArtifactKind.input,
            uri=f"s3://test/{UNRECOGNISED_SHA[:2]}/{UNRECOGNISED_SHA}",
            sha256=UNRECOGNISED_SHA,
            bytes=len(UNRECOGNISED_TEXT),
            filename="mystery.in",
        )
        db_session.add(artifact)
        db_session.flush()
        stub_load_artifact_bytes[UNRECOGNISED_SHA] = UNRECOGNISED_TEXT

        # Second calc: succeeds normally.
        good_calc, _ = _seed_calc(db_session)
        stub_load_artifact_bytes[GAUSSIAN_GJF_SHA] = GAUSSIAN_GJF_TEXT

        stats = script_mod.run_backfill(db_session, all_missing=True)

        assert stats.candidates == 2
        assert stats.succeeded == 1
        # The unrecognised-software case is folded into skipped_no_artifact
        # by the bridge (helper returned None) — what matters is the run
        # didn't fail or stop.
        assert stats.failed == 0
        assert _parser_count(db_session, good_calc) > 0

    def test_per_calc_failure_does_not_roll_back_previous_successes(
        self, db_session, stub_load_artifact_bytes, monkeypatch
    ):
        # Calc 1 succeeds first.
        calc_ok, _ = _seed_calc(db_session)
        stub_load_artifact_bytes[GAUSSIAN_GJF_SHA] = GAUSSIAN_GJF_TEXT

        # Calc 2 will hit a forced exception inside the helper.
        calc_bad, _ = _seed_calc(db_session)

        from app.services import calculation_parameter_extraction as cpe

        original = cpe.try_extract_parameters_from_input_artifact_row
        seen: list[int] = []

        def _selective(session, calculation, artifact):
            seen.append(calculation.id)
            if calculation.id == calc_bad:
                raise RuntimeError("simulated mid-extraction failure")
            return original(session, calculation, artifact)

        monkeypatch.setattr(
            script_mod,
            "try_extract_parameters_from_input_artifact_row",
            _selective,
        )

        stats = script_mod.run_backfill(db_session, all_missing=True)

        assert stats.candidates == 2
        assert stats.succeeded == 1
        assert stats.failed == 1
        # Calc 1's parser rows survived; calc 2 has none.
        assert _parser_count(db_session, calc_ok) > 0
        assert _parser_count(db_session, calc_bad) == 0

    def test_fail_fast_stops_after_first_failure(
        self, db_session, stub_load_artifact_bytes, monkeypatch
    ):
        calc_bad, _ = _seed_calc(db_session)
        calc_after, _ = _seed_calc(db_session)
        stub_load_artifact_bytes[GAUSSIAN_GJF_SHA] = GAUSSIAN_GJF_TEXT

        seen: list[int] = []

        def _always_boom(session, calculation, artifact):
            seen.append(calculation.id)
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(
            script_mod,
            "try_extract_parameters_from_input_artifact_row",
            _always_boom,
        )

        stats = script_mod.run_backfill(
            db_session, all_missing=True, fail_fast=True
        )

        # Should have stopped after the first calc, not visited the second.
        assert len(seen) == 1
        assert stats.failed == 1
        assert _parser_count(db_session, calc_after) == 0

    def test_artifact_id_override_targets_specific_artifact(
        self, db_session, stub_load_artifact_bytes
    ):
        calc_id, first_id = _seed_calc(db_session)
        # Add a second input artifact to the same calc with different bytes
        # whose SHA the stub WILL serve. The default selection rule would
        # pick `first_id` (lowest id); the override forces the second.
        second_text = (
            "%mem=2GB\n"
            "# HF/STO-3G sp\n"
            "\n"
            "alt input\n"
            "\n"
            "0 1\n"
            "H 0.0 0.0 0.0\n"
        ).encode()
        second_sha = hashlib.sha256(second_text).hexdigest()
        second_artifact = CalculationArtifact(
            calculation_id=calc_id,
            kind=ArtifactKind.input,
            uri=f"s3://test/{second_sha[:2]}/{second_sha}",
            sha256=second_sha,
            bytes=len(second_text),
            filename="alt.gjf",
        )
        db_session.add(second_artifact)
        db_session.flush()

        stub_load_artifact_bytes[second_sha] = second_text
        # Note: do NOT register first_id's SHA — if the default rule
        # were used the run would fail to load.

        stats = script_mod.run_backfill(
            db_session,
            calculation_id=calc_id,
            artifact_id=second_artifact.id,
        )

        assert stats.succeeded == 1
        # Parser found a parameter from the override file's route line.
        rows = db_session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc_id,
                CalculationParameter.source == ParameterSource.parser,
            )
        ).all()
        assert rows
