"""Tests for Gaussian log parameter extraction and DB storage."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationParameter,
    CalculationParameterVocab,
)
from app.db.models.common import CalculationType, StereoKind
from app.db.models.species import Species, SpeciesEntry
from app.services.gaussian_parameter_parser import (
    PARSER_VERSION,
    parse_gaussian_log,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
GAUSSIAN_DIR = FIXTURES_DIR / "gaussian"
LOG_PATH = GAUSSIAN_DIR / "opt_g09.log"
TS_LOG_PATH = GAUSSIAN_DIR / "ts_opt_g09.log"


# ---------------------------------------------------------------------------
# Pure parsing tests (no DB)
# ---------------------------------------------------------------------------


class TestRouteLineParsing:
    """Verify the parser extracts parameters from the Gaussian log file."""

    @pytest.fixture(autouse=True)
    def parsed(self):
        self.result = parse_gaussian_log(LOG_PATH)

    def test_route_line_extracted(self):
        route = self.result["route_line"]
        assert route.startswith("#P")
        assert "opt=" in route
        assert "scf=" in route

    def test_parser_version(self):
        assert self.result["parser_version"] == PARSER_VERSION

    def test_software_version(self):
        sw = self.result["software"]
        assert sw is not None
        assert sw["name"] == "gaussian"
        assert sw["version"] == "09"
        assert "RevD" in sw["build"]

    def test_charge_multiplicity(self):
        cm = self.result["charge_multiplicity"]
        assert cm == (0, 2)

    def test_method_basis(self):
        mb = self.result["method_basis"]
        assert mb is not None
        assert mb["method"].lower() == "uwb97xd"
        assert mb["basis"].lower() == "def2tzvp"

    def test_resource_parameters(self):
        params = self.result["parameters"]
        resource_params = [p for p in params if p["section"] == "resource"]
        keys = {p["raw_key"] for p in resource_params}
        assert "%mem" in keys
        assert "%NProcShared" in keys

        mem = next(p for p in resource_params if p["raw_key"] == "%mem")
        assert mem["raw_value"] == "32768mb"
        assert mem["canonical_key"] == "memory.raw"

        nproc = next(
            p for p in resource_params if p["raw_key"] == "%NProcShared"
        )
        assert nproc["raw_value"] == "8"
        assert nproc["canonical_key"] == "parallel.nproc_shared"

    def test_opt_parameters(self):
        params = self.result["parameters"]
        opt_params = [p for p in params if p["section"] == "opt"]
        opt_keys = {p["raw_key"] for p in opt_params}

        assert "calcfc" in opt_keys
        assert "maxcycle" in opt_keys
        assert "maxstep" in opt_keys
        assert "tight" in opt_keys

        calcfc = next(p for p in opt_params if p["raw_key"] == "calcfc")
        assert calcfc["canonical_key"] == "opt.initial_hessian"
        assert calcfc["canonical_value"] == "calculate_at_first_point"

        maxcycle = next(p for p in opt_params if p["raw_key"] == "maxcycle")
        assert maxcycle["raw_value"] == "100"
        assert maxcycle["canonical_key"] == "opt.max_cycles"

        tight = next(p for p in opt_params if p["raw_key"] == "tight")
        assert tight["canonical_key"] == "opt.convergence"
        assert tight["canonical_value"] == "tight"

    def test_scf_parameters(self):
        params = self.result["parameters"]
        scf_params = [p for p in params if p["section"] == "scf"]
        scf_keys = {p["raw_key"] for p in scf_params}

        assert "direct" in scf_keys
        assert "tight" in scf_keys

        direct = next(p for p in scf_params if p["raw_key"] == "direct")
        assert direct["canonical_key"] == "scf.direct"

        tight = next(p for p in scf_params if p["raw_key"] == "tight")
        assert tight["canonical_key"] == "scf.convergence"
        assert tight["canonical_value"] == "tight"

    def test_integral_parameters(self):
        params = self.result["parameters"]
        integral_params = [p for p in params if p["section"] == "integral"]

        grid = next(
            p for p in integral_params if p["raw_key"] == "grid"
        )
        assert grid["raw_value"] == "ultrafine"
        assert grid["canonical_key"] == "grid.quality"

        acc = next(p for p in integral_params if p["raw_key"] == "Acc2E")
        assert acc["raw_value"] == "12"
        assert acc["canonical_key"] == "integral.accuracy"

    def test_iop_parameter(self):
        params = self.result["parameters"]
        iop_params = [
            p for p in params if p["section"] == "internal_option"
        ]
        assert len(iop_params) >= 1
        iop = iop_params[0]
        assert iop["raw_key"] == "IOp(2/9)"
        assert iop["raw_value"] == "2000"
        # All IOp directives share one canonical key; the overlay/option
        # coordinate stays in raw_key for queryability.
        assert iop["canonical_key"] == "internal_option.iop"

    def test_guess_parameter(self):
        params = self.result["parameters"]
        guess_params = [
            p
            for p in params
            if p["raw_key"] == "guess" and p["section"] == "general"
        ]
        assert len(guess_params) == 1
        assert guess_params[0]["raw_value"] == "read"
        assert guess_params[0]["canonical_key"] == "guess.strategy"

    def test_parameters_json_snapshot(self):
        pj = self.result["parameters_json"]
        assert "route_line" in pj
        assert "sections" in pj
        assert "opt" in pj["sections"]
        assert "scf" in pj["sections"]
        assert "resource" in pj["sections"]

    def test_total_parameter_count(self):
        """Sanity check: we should have at least 12 parameters from this log."""
        params = self.result["parameters"]
        assert len(params) >= 12


class TestTSRouteLineParsing:
    """Verify parameter parsing from a TS optimization log file."""

    @pytest.fixture(autouse=True)
    def parsed(self):
        self.result = parse_gaussian_log(TS_LOG_PATH)

    def test_route_line_extracted(self):
        route = self.result["route_line"]
        assert "ts" in route.lower() or "TS" in route

    def test_ts_flag_parsed(self):
        params = self.result["parameters"]
        opt_params = [p for p in params if p["section"] == "opt"]
        ts = next((p for p in opt_params if p["raw_key"] == "ts"), None)
        assert ts is not None
        assert ts["raw_value"] == "true"
        assert ts["canonical_key"] == "opt.saddle_order"

    def test_noeigentest_parsed(self):
        params = self.result["parameters"]
        opt_params = [p for p in params if p["section"] == "opt"]
        noeigen = next(
            (p for p in opt_params if p["raw_key"] == "noeigentest"), None
        )
        assert noeigen is not None
        assert noeigen["canonical_key"] == "opt.eigen_test"
        assert noeigen["canonical_value"] == "disabled"

    def test_ts_opt_has_calcfc(self):
        params = self.result["parameters"]
        opt_params = [p for p in params if p["section"] == "opt"]
        calcfc = next((p for p in opt_params if p["raw_key"] == "calcfc"), None)
        assert calcfc is not None
        assert calcfc["canonical_key"] == "opt.initial_hessian"

    def test_ts_and_species_share_scf_parameters(self):
        """TS and species logs should parse SCF params identically."""
        params = self.result["parameters"]
        scf_params = [p for p in params if p["section"] == "scf"]
        scf_keys = {p["raw_key"] for p in scf_params}
        assert "direct" in scf_keys
        assert "tight" in scf_keys

    def test_ts_software_version(self):
        sw = self.result["software"]
        assert sw is not None
        assert sw["name"] == "gaussian"
        assert sw["version"] == "09"


# ---------------------------------------------------------------------------
# DB round-trip tests
# ---------------------------------------------------------------------------


class TestParameterDBStorage:
    """Verify that parsed parameters can be stored and queried via the ORM."""

    @pytest.fixture
    def db_session(self, db_engine):
        """Per-test session with rollback."""
        conn = db_engine.connect()
        txn = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        yield session
        session.close()
        txn.rollback()
        conn.close()

    @pytest.fixture
    def parsed(self):
        return parse_gaussian_log(LOG_PATH)

    @pytest.fixture
    def stored_calculation(self, db_session, parsed):
        """Create a minimal Calculation with parameters from the parsed log."""
        # Minimal species + entry to satisfy the one_owner constraint
        species = Species(
            kind="molecule",
            smiles="N=N",
            inchi_key="IJGRMHOSHXDMSA-UHFFFAOYSA-N",
            charge=0,
            multiplicity=2,
            stereo_kind=StereoKind.ez_isomer,
        )
        db_session.add(species)
        db_session.flush()

        entry = SpeciesEntry(species_id=species.id)
        db_session.add(entry)
        db_session.flush()

        # Seed vocab entries for canonical keys that appear in parsed params.
        # The initial migration now seeds the Phase 1 vocabulary, so skip
        # any keys that are already present to keep this test idempotent.
        canonical_keys_used = {
            p["canonical_key"]
            for p in parsed["parameters"]
            if p.get("canonical_key")
        }
        already_seeded = set(
            db_session.scalars(
                select(CalculationParameterVocab.canonical_key).where(
                    CalculationParameterVocab.canonical_key.in_(canonical_keys_used)
                )
            ).all()
        )
        for ck in canonical_keys_used - already_seeded:
            db_session.add(CalculationParameterVocab(canonical_key=ck))
        db_session.flush()

        # Create the calculation
        calc = Calculation(
            type=CalculationType.opt,
            species_entry_id=entry.id,
            parameters_json=parsed["parameters_json"],
            parameters_parser_version=parsed["parser_version"],
            parameters_extracted_at=datetime.now(tz=timezone.utc).replace(
                tzinfo=None
            ),
        )
        db_session.add(calc)
        db_session.flush()

        # Create parameter rows
        for p in parsed["parameters"]:
            param = CalculationParameter(
                calculation_id=calc.id,
                raw_key=p["raw_key"],
                canonical_key=p.get("canonical_key"),
                raw_value=p["raw_value"],
                canonical_value=p.get("canonical_value"),
                section=p.get("section"),
                value_type=p.get("value_type"),
            )
            db_session.add(param)
        db_session.flush()

        return calc

    def test_parameters_stored(self, db_session, stored_calculation):
        """All parsed parameters are persisted."""
        params = (
            db_session.execute(
                select(CalculationParameter).where(
                    CalculationParameter.calculation_id
                    == stored_calculation.id
                )
            )
            .scalars()
            .all()
        )
        assert len(params) >= 12

    def test_parameters_json_stored(self, db_session, stored_calculation):
        """The JSONB snapshot is persisted on the calculation."""
        calc = db_session.get(Calculation, stored_calculation.id)
        assert calc.parameters_json is not None
        assert "opt" in calc.parameters_json["sections"]
        assert calc.parameters_parser_version == PARSER_VERSION

    def test_query_by_canonical_key(self, db_session, stored_calculation):
        """Can query: find calculations with tight SCF convergence."""
        results = (
            db_session.execute(
                select(CalculationParameter).where(
                    CalculationParameter.calculation_id == stored_calculation.id,
                    CalculationParameter.canonical_key == "scf.convergence",
                    CalculationParameter.canonical_value == "tight",
                )
            )
            .scalars()
            .all()
        )
        assert len(results) == 1
        assert results[0].calculation_id == stored_calculation.id

    def test_query_by_raw_key_and_section(self, db_session, stored_calculation):
        """Can query: find all calcfc opt jobs (reproduction query)."""
        results = (
            db_session.execute(
                select(CalculationParameter).where(
                    CalculationParameter.calculation_id == stored_calculation.id,
                    CalculationParameter.raw_key == "calcfc",
                    CalculationParameter.section == "opt",
                )
            )
            .scalars()
            .all()
        )
        assert len(results) == 1
        assert results[0].canonical_key == "opt.initial_hessian"

    def test_query_iop(self, db_session, stored_calculation):
        """IOp parameters land under canonical key 'internal_option.iop'.

        The overlay/option coordinate is preserved in raw_key so each
        IOp row is distinguishable while still grouped under one
        canonical key for queryability.
        """
        results = (
            db_session.execute(
                select(CalculationParameter).where(
                    CalculationParameter.calculation_id == stored_calculation.id,
                    CalculationParameter.section == "internal_option",
                )
            )
            .scalars()
            .all()
        )
        assert len(results) >= 1
        iop = results[0]
        assert iop.canonical_key == "internal_option.iop"
        assert iop.raw_key == "IOp(2/9)"
        assert iop.raw_value == "2000"

    def test_vocab_fk_works(self, db_session, stored_calculation):
        """Canonical keys are FK-linked to the vocab table."""
        param = (
            db_session.execute(
                select(CalculationParameter).where(
                    CalculationParameter.calculation_id == stored_calculation.id,
                    CalculationParameter.canonical_key == "opt.convergence",
                )
            )
            .scalar_one()
        )
        assert param.vocab is not None
        assert param.vocab.canonical_key == "opt.convergence"

    def test_resource_params_segregated(self, db_session, stored_calculation):
        """Resource parameters are stored with section='resource'."""
        results = (
            db_session.execute(
                select(CalculationParameter).where(
                    CalculationParameter.calculation_id == stored_calculation.id,
                    CalculationParameter.section == "resource",
                )
            )
            .scalars()
            .all()
        )
        keys = {r.canonical_key for r in results}
        assert "memory.raw" in keys
        assert "parallel.nproc_shared" in keys
