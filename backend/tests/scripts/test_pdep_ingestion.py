"""End-to-end tests for scripts/pdep_ingestion.

Parses a trimmed but REAL excerpt of Michal Keslin's hydrazine
``Final_MRCI_PDep`` Arkane run (committed under
``tests/fixtures/pdep/hydrazine_mrci``) into a ``NetworkPDepUploadRequest``,
persists it via ``persist_network_pdep_upload`` on the test DB, and reads the
scientific evidence back.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationOptResult,
    CalculationSPResult,
)
from app.db.models.common import CalculationType
from app.db.models.network import NetworkReaction, NetworkSpecies
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkSolve,
    NetworkSolveEnergyTransfer,
    NetworkState,
)
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.workflows.network_pdep import persist_network_pdep_upload
from scripts.pdep_ingestion.arkane_pdep_parser import parse_pdep_reactions
from scripts.pdep_ingestion.builder import (
    build_network_pdep_payload,
    build_network_pdep_request,
)
from scripts.pdep_ingestion.units import (
    HARTREE_TO_J_MOL,
    atm_to_bar,
    j_mol_to_hartree,
    kcal_mol_to_cm_inv,
)

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "pdep" / "hydrazine_mrci"

# The 6x4 Chebyshev grid for H2 + H2NN <=> N2H4 (from the fixture output.py).
_EXPECTED_CHEB = [
    [-2.10678, 1.69152, 0.0388832, -0.0128202],
    [7.28692, 0.517624, -0.063172, 0.0163503],
    [0.476769, -0.295246, 0.0239678, -0.000208037],
    [-0.245158, 0.0852448, 0.0133574, -0.00866986],
    [-0.014641, 0.0265802, -0.0237032, 0.00734324],
    [0.0264297, -0.039152, 0.0107025, -0.000344881],
]


@contextmanager
def _rolled_back_session(db_engine) -> Iterator[Session]:
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Unit conversions (the three parser gotchas)
# ---------------------------------------------------------------------------


def test_grain_size_kcal_mol_to_cm_inv() -> None:
    # 0.5 kcal/mol maximumGrainSize -> cm^-1 (gotcha #1).
    assert kcal_mol_to_cm_inv(0.5) == pytest.approx(174.87755, rel=1e-6)


def test_pressure_atm_to_bar() -> None:
    # 100 bar Chebyshev domain == 98.692 atm in chem.inp (gotcha #2).
    assert atm_to_bar(98.69232667) == pytest.approx(100.0, rel=1e-6)
    assert atm_to_bar(1.0) == pytest.approx(1.01325, rel=1e-9)


def test_energy_j_mol_to_hartree() -> None:
    # N2H4 MRCI+Davidson electronic energy: -293284622.0976 J/mol.
    assert j_mol_to_hartree(-293284622.0976381) == pytest.approx(-111.70621, abs=1e-4)
    assert j_mol_to_hartree(HARTREE_TO_J_MOL) == pytest.approx(1.0, rel=1e-12)


# ---------------------------------------------------------------------------
# Parser: commented pdepreaction blocks are ignored
# ---------------------------------------------------------------------------


def test_parser_skips_commented_pdepreaction() -> None:
    text = (FIXTURE_DIR / "output.py").read_text()
    fits = parse_pdep_reactions(text)
    # The fixture has one active block and one commented-out block.
    assert len(fits) == 1
    fit = fits[0]
    assert fit.reactants == ["H2", "H2NN"]
    assert fit.products == ["N2H4"]
    assert fit.kunits == "cm^3/(mol*s)"
    assert fit.n_temperature == 6
    assert fit.n_pressure == 4
    # output.py labels the pressure domain in bar (no atm->bar conversion).
    assert fit.pressure_units == "bar"
    assert fit.pmin_value == 0.01
    assert fit.pmax_value == 100.0


# ---------------------------------------------------------------------------
# Build: a schema-valid request from the fixture
# ---------------------------------------------------------------------------


def test_fixture_builds_valid_request() -> None:
    request = build_network_pdep_request(FIXTURE_DIR)
    assert isinstance(request, NetworkPDepUploadRequest)

    assert {s.key for s in request.species} == {"N2H4", "H2NN", "H2", "nitrogen"}
    assert len(request.states) == 2
    assert len(request.channels) == 1
    assert len(request.micro_reactions) == 1
    assert {ts.key for ts in request.transition_states} == {"ts1"}

    # Solve: grain size converted kcal/mol -> cm^-1, bar domain, Arkane tool.
    solve = request.solve
    assert solve is not None
    assert solve.grain_size_cm_inv == pytest.approx(174.87755, rel=1e-6)
    assert solve.grain_count == 200
    assert solve.pmin_bar == 0.01
    assert solve.pmax_bar == 100.0
    assert solve.me_method == "modified strong collision"
    assert len(solve.channel_kinetics) == 1

    # Energy-transfer parameters survived the nested-paren parse.
    et = solve.energy_transfer
    assert et is not None
    assert et.alpha0_cm_inv == 175.0
    assert et.t_ref_k == 298.0
    assert et.t_exponent == 0.52


def test_fixture_emits_statmech_and_closes_gap() -> None:
    payload, gap = build_network_pdep_payload(FIXTURE_DIR)
    # optical_isomers/external_symmetry are now storable (PR #19) -> no gap.
    assert gap.unstorable_fields == []
    # Every reactive species carries a statmech block.
    assert set(gap.species_with_statmech) == {"N2H4", "H2NN", "H2"}

    n2h4 = next(s for s in payload["species"] if s["key"] == "N2H4")
    stm = n2h4["statmech"]
    assert stm["external_symmetry"] == 2
    assert stm["optical_isomers"] == 2
    assert stm["point_group"] == "C2"
    assert stm["freq_scale_factor"]["value"] == 0.986
    # N2H4's hindered rotor -> a torsion referencing its own scan calc.
    assert gap.torsions_emitted == ["N2H4"]
    assert stm["torsions"][0]["source_scan_calculation_key"] == "N2H4_scan"
    # source_calculations reference N2H4's own freq/sp calcs.
    roles = {sc["role"]: sc["calculation_key"] for sc in stm["source_calculations"]}
    assert roles["freq"] == "N2H4_freq"
    assert roles["sp"] == "N2H4_sp"


def test_fixture_artifacts_resolve_local_log_paths() -> None:
    # include_artifacts re-roots the embedded home-dir Log() paths onto the
    # fixture Data/ tree (gotcha #3) and attaches the trimmed ESS files.
    payload, _gap = build_network_pdep_payload(FIXTURE_DIR, include_artifacts=True)
    n2h4 = next(s for s in payload["species"] if s["key"] == "N2H4")
    sp_calc = next(c for c in n2h4["calculations"] if c["type"] == "sp")
    assert sp_calc["artifacts"], "expected N2H4 sp.out artifact from re-rooted Log() path"
    assert sp_calc["artifacts"][0]["filename"] == "sp.out"


# ---------------------------------------------------------------------------
# Full pipeline: parse -> persist -> read back
# ---------------------------------------------------------------------------


def test_fixture_full_pipeline_persist_and_read_back(db_engine) -> None:
    request = build_network_pdep_request(FIXTURE_DIR)

    with _rolled_back_session(db_engine) as session:
        network = persist_network_pdep_upload(session, request, created_by=None)
        session.flush()

        # -- Topology counts --
        states = session.scalars(
            select(NetworkState).where(NetworkState.network_id == network.id)
        ).all()
        assert len(states) == 2
        assert {s.kind.value for s in states} == {"well", "bimolecular"}

        channels = session.scalars(
            select(NetworkChannel).where(NetworkChannel.network_id == network.id)
        ).all()
        assert len(channels) == 1

        rxn_links = session.scalars(
            select(NetworkReaction).where(NetworkReaction.network_id == network.id)
        ).all()
        assert len(rxn_links) == 1

        # -- Species entries of THIS network --
        net_species = session.scalars(
            select(NetworkSpecies).where(NetworkSpecies.network_id == network.id)
        ).all()
        net_se_ids = {ns.species_entry_id for ns in net_species}
        assert len(net_se_ids) == 4  # N2H4, H2NN, H2, nitrogen

        calcs = session.scalars(
            select(Calculation).where(Calculation.species_entry_id.in_(net_se_ids))
        ).all()
        calc_by_id = {c.id: c for c in calcs}

        # N2H4 is the species carrying a scan (hindered-rotor) calculation.
        scan_calcs = [c for c in calcs if c.type == CalculationType.scan]
        assert len(scan_calcs) == 1
        n2h4_se_id = scan_calcs[0].species_entry_id

        n2h4_calcs = [c for c in calcs if c.species_entry_id == n2h4_se_id]
        types = sorted(c.type.value for c in n2h4_calcs)
        assert types == ["freq", "opt", "scan", "sp"]

        # -- N2H4 electronic energy (MRCI+Davidson sp) --
        n2h4_sp = next(c for c in n2h4_calcs if c.type == CalculationType.sp)
        sp_res = session.scalars(
            select(CalculationSPResult).where(
                CalculationSPResult.calculation_id == n2h4_sp.id
            )
        ).one()
        assert sp_res.electronic_energy_hartree == pytest.approx(-111.70621, abs=1e-4)

        # -- N2H4 E0 (via ZPE) and frequencies --
        n2h4_freq = next(c for c in n2h4_calcs if c.type == CalculationType.freq)
        freq_res = session.scalars(
            select(CalculationFreqResult).where(
                CalculationFreqResult.calculation_id == n2h4_freq.id
            )
        ).one()
        assert freq_res.n_imag == 0
        assert freq_res.zpe_hartree == pytest.approx(0.0524485, abs=1e-6)

        modes = session.scalars(
            select(CalculationFreqMode).where(
                CalculationFreqMode.calculation_id == n2h4_freq.id
            )
        ).all()
        assert len(modes) == 12  # N2H4 has 3N-6 = 12 vibrational modes
        assert not any(m.is_imaginary for m in modes)
        assert min(m.frequency_cm1 for m in modes) == pytest.approx(459.6)

        # -- TS1: entry with an opt calculation and an imaginary freq mode --
        ts_entries = session.scalars(select(TransitionStateEntry)).all()
        assert len(ts_entries) >= 1
        ts_entry = ts_entries[-1]
        ts_calcs = session.scalars(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry.id
            )
        ).all()
        ts_types = sorted(c.type.value for c in ts_calcs)
        assert ts_types == ["freq", "opt", "sp"]

        ts_freq = next(c for c in ts_calcs if c.type == CalculationType.freq)
        ts_modes = session.scalars(
            select(CalculationFreqMode).where(
                CalculationFreqMode.calculation_id == ts_freq.id
            )
        ).all()
        imag_modes = [m for m in ts_modes if m.is_imaginary]
        assert len(imag_modes) == 1
        assert imag_modes[0].frequency_cm1 == pytest.approx(-1426.6)

        # -- Channel Chebyshev coefficients + units survive --
        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        nk = session.scalars(
            select(NetworkKinetics).where(NetworkKinetics.solve_id == solve.id)
        ).one()
        assert nk.model_kind.value == "chebyshev"
        assert nk.rate_units.value == "cm3_mol_s"
        assert nk.pressure_units.value == "bar"
        assert nk.temperature_units.value == "kelvin"
        assert nk.stores_log10_k is True
        assert nk.pmin_bar == 0.01
        assert nk.pmax_bar == 100.0

        cheb = session.scalars(
            select(NetworkKineticsChebyshev).where(
                NetworkKineticsChebyshev.network_kinetics_id == nk.id
            )
        ).one()
        assert cheb.n_temperature == 6
        assert cheb.n_pressure == 4
        assert cheb.coefficients == {"coeffs": _EXPECTED_CHEB}

        # -- Energy transfer persisted --
        et_rows = session.scalars(
            select(NetworkSolveEnergyTransfer).where(
                NetworkSolveEnergyTransfer.solve_id == solve.id
            )
        ).all()
        assert len(et_rows) == 1
        assert et_rows[0].alpha0_cm_inv == 175.0

        # -- Statmech round-trips (PR #19 closed the optical_isomers gap) --
        from app.db.models.statmech import (
            Statmech,
            StatmechSourceCalculation,
            StatmechTorsion,
        )

        n2h4_stm = session.scalars(
            select(Statmech).where(Statmech.species_entry_id == n2h4_se_id)
        ).one()
        assert n2h4_stm.external_symmetry == 2
        assert n2h4_stm.optical_isomers == 2
        assert n2h4_stm.point_group == "C2"

        stm_sources = session.scalars(
            select(StatmechSourceCalculation).where(
                StatmechSourceCalculation.statmech_id == n2h4_stm.id
            )
        ).all()
        # freq + sp source calcs, both owned by N2H4.
        assert {s.role.value for s in stm_sources} == {"freq", "sp"}
        for s in stm_sources:
            linked = session.get(Calculation, s.calculation_id)
            assert linked.species_entry_id == n2h4_se_id

        # N2H4 hindered rotor -> torsion linking N2H4's own scan calc.
        torsions = session.scalars(
            select(StatmechTorsion).where(
                StatmechTorsion.statmech_id == n2h4_stm.id
            )
        ).all()
        assert len(torsions) == 1
        assert torsions[0].source_scan_calculation_id is not None
        scan_calc = session.get(
            Calculation, torsions[0].source_scan_calculation_id
        )
        assert scan_calc.type == CalculationType.scan
        assert scan_calc.species_entry_id == n2h4_se_id


def test_fixture_full_pipeline_with_artifacts(db_engine, monkeypatch) -> None:
    written: list[str] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append(uri)
        return uri

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )

    request = build_network_pdep_request(FIXTURE_DIR, include_artifacts=True)
    with _rolled_back_session(db_engine) as session:
        network = persist_network_pdep_upload(session, request, created_by=None)
        session.flush()
        arts = session.scalars(
            select(CalculationArtifact).where(
                CalculationArtifact.uri.like("s3://test-bucket/%")
            )
        ).all()
        # At least the N2H4 sp/freq/scan and TS1 sp/freq trimmed logs.
        assert len(arts) >= 3
