"""End-to-end tests for scripts/pdep_ingestion.

Parses a trimmed but REAL excerpt of Michal Keslin's hydrazine
``Final_MRCI_PDep`` Arkane run (committed under
``tests/fixtures/pdep/hydrazine_mrci``) into a ``NetworkPDepUploadRequest``,
persists it via ``persist_network_pdep_upload`` on the test DB, and reads the
scientific evidence back.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationSPResult,
)
from app.db.models.common import CalculationType, NetworkEnergyTransferScope
from app.db.models.network import NetworkReaction, NetworkSpecies
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkSolve,
    NetworkSolveEnergyTransfer,
    NetworkSolveStateEnergy,
    NetworkSolveStateEnergySource,
    NetworkState,
)
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.workflows.network_pdep import persist_network_pdep_upload
from scripts.pdep_ingestion.arkane_pdep_parser import (
    parse_data_file,
    parse_input_file,
    parse_pdep_arrhenius_reactions,
    parse_pdep_reactions,
)
from scripts.pdep_ingestion.builder import (
    _software_release,
    build_dual_form_payload,
    build_dual_form_request,
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
PLOG_FIXTURE_DIR = (
    Path(__file__).parents[1] / "fixtures" / "pdep" / "hydrazine_mrci_plog"
)

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
# Parser: Data/<x>.py commented fields are ignored
# ---------------------------------------------------------------------------


_DATA_FILE_TEMPLATE = (
    "#!/usr/bin/env python3\n"
    "# encoding: utf-8\n"
    "\n"
    "bonds = {{'H-N': 2, 'N-N': 1}}\n"
    "externalSymmetry = 3\n"
    "spinMultiplicity = 2\n"
    "opticalIsomers = 1\n"
    "energy = Log('data/X/sp.out')\n"
    "geometry = Log('data/X/freq.out')\n"
    "frequencies = Log('data/X/freq.out')\n"
    "{rotors_line}\n"
)


def test_data_file_commented_rotor_yields_no_scan_log() -> None:
    """A commented-out ``#rotors = [HinderedRotor(scanLog=Log(...))]`` line must
    NOT be parsed: no ``scanLog`` is picked up, so no spurious scan calc is
    later emitted. Live (non-commented) fields still parse normally."""
    text = _DATA_FILE_TEMPLATE.format(
        rotors_line="#rotors = [HinderedRotor(scanLog=Log('data/X/scan.out'), "
        "pivots=[1, 2], top=[1, 3], symmetry=3, fit='fourier')]"
    )
    data = parse_data_file(text)
    assert data.scan_logs == []
    # The disabled rotor line does not suppress the live scalar/Log fields.
    assert data.external_symmetry == 3
    assert data.spin_multiplicity == 2
    assert data.optical_isomers == 1
    assert data.bonds == {"H-N": 2, "N-N": 1}
    assert data.energy_log == "data/X/sp.out"
    assert data.geometry_log == "data/X/freq.out"
    assert data.frequencies_log == "data/X/freq.out"


def test_data_file_live_rotor_yields_scan_log() -> None:
    """A LIVE (non-commented) ``rotors = [HinderedRotor(scanLog=Log(...))]`` line
    still produces its ``scanLog`` — the fix only drops commented rotors."""
    text = _DATA_FILE_TEMPLATE.format(
        rotors_line="rotors = [HinderedRotor(scanLog=Log('data/X/scan.out'), "
        "pivots=[1, 2], top=[1, 3], symmetry=3, fit='fourier')]"
    )
    data = parse_data_file(text)
    assert data.scan_logs == ["data/X/scan.out"]
    assert data.external_symmetry == 3


# ---------------------------------------------------------------------------
# Parser: PDepArrhenius (PLOG) blocks
# ---------------------------------------------------------------------------


def test_parse_pdep_arrhenius_reactions_per_pressure_values() -> None:
    """The PLOG parser extracts the ordered per-pressure A / n / Ea and units
    from a ``PDepArrhenius`` block, skipping the commented-out one."""
    text = (PLOG_FIXTURE_DIR / "output.py").read_text()
    fits = parse_pdep_arrhenius_reactions(text)
    assert len(fits) == 1  # one active block; commented one ignored
    fit = fits[0]
    assert fit.reactants == ["H2", "H2NN"]
    assert fit.products == ["N2H4"]
    assert fit.pressure_units == "bar"
    assert fit.temperature_units == "K"
    assert fit.tmin_value == 300.0
    assert fit.tmax_value == 2000.0

    # One Arrhenius term per pressure, in file order.
    assert [e.pressure_value for e in fit.entries] == [0.01, 0.1, 1.0, 10.0, 100.0]
    first = fit.entries[0]
    assert first.a_value == pytest.approx(1.23456e12)
    assert first.a_units == "cm^3/(mol*s)"
    assert first.n == pytest.approx(0.42)
    assert first.ea_value == pytest.approx(35.1)
    assert first.ea_units == "kJ/mol"
    last = fit.entries[-1]
    assert last.a_value == pytest.approx(5.67891e12)
    assert last.n == pytest.approx(0.78)
    assert last.ea_value == pytest.approx(39.5)


def _plog_text(t0: str) -> str:
    return (
        "pdepreaction(\n"
        "    reactants = ['N2H4'],\n"
        "    products = ['H2', 'H2NN'],\n"
        "    kinetics = PDepArrhenius(\n"
        "        pressures = ([0.01, 100], 'bar'),\n"
        "        arrhenius = [\n"
        f"            Arrhenius(A=(1.0, 's^-1'), n=0.5, Ea=(0.0, 'kJ/mol'), T0={t0}),\n"
        f"            Arrhenius(A=(2.0, 's^-1'), n=0.5, Ea=(0.0, 'kJ/mol'), T0={t0}),\n"
        "        ],\n"
        "    ),\n"
        ")\n"
    )


def test_plog_terms_at_another_t0_are_refused_not_silently_stored() -> None:
    """A PLOG term has no T0 column: a term fitted at 298 K would be stored as
    though it were at 1 K, a wrong rate by 298**n. T0 = 1 K still parses."""
    assert len(parse_pdep_arrhenius_reactions(_plog_text("(1, 'K')"))[0].entries) == 2
    with pytest.raises(ValueError, match="stored at T0 = 1 K"):
        parse_pdep_arrhenius_reactions(_plog_text("(298, 'K')"))


# ---------------------------------------------------------------------------
# Dual-form build: one network carrying BOTH Chebyshev and PLOG kinetics
# ---------------------------------------------------------------------------


def test_dual_form_build_attaches_both_parameterizations() -> None:
    """``build_dual_form_request`` yields a channel carrying BOTH a chebyshev
    and a plog ``channel_kinetics`` entry, on the same (source, sink) pair."""
    request = build_dual_form_request(FIXTURE_DIR, PLOG_FIXTURE_DIR)
    solve = request.solve
    assert solve is not None
    # 1 channel -> 1 chebyshev + 1 plog = 2 channel_kinetics.
    assert len(solve.channel_kinetics) == 2
    by_kind = {nk.model_kind.value: nk for nk in solve.channel_kinetics}
    assert set(by_kind) == {"chebyshev", "plog"}

    cheb = by_kind["chebyshev"]
    plog = by_kind["plog"]
    # Both attach to the same channel.
    assert (cheb.source_state_key, cheb.sink_state_key) == (
        plog.source_state_key,
        plog.sink_state_key,
    )
    # Chebyshev grid intact.
    assert cheb.chebyshev is not None
    assert cheb.chebyshev.coefficients == _EXPECTED_CHEB
    # PLOG entries intact (5 pressures, kJ/mol Ea passthrough, bimolecular units).
    assert plog.plog is not None
    assert len(plog.plog.entries) == 5
    assert [e.pressure_bar for e in plog.plog.entries] == [0.01, 0.1, 1.0, 10.0, 100.0]
    assert plog.plog.entries[0].a == pytest.approx(1.23456e12)
    assert plog.plog.entries[0].a_units.value == "cm3_mol_s"
    assert plog.plog.entries[0].ea_kj_mol == pytest.approx(35.1)
    assert plog.rate_units.value == "cm3_mol_s"
    assert plog.pmin_bar == 0.01
    assert plog.pmax_bar == 100.0


def test_dual_form_build_rejects_topology_mismatch(tmp_path) -> None:
    """A PLOG run whose channels do not match the Chebyshev run's is rejected
    (STOP, never silently drop/misalign)."""
    # Reverse-direction channel: (N2H4 -> H2+H2NN) is NOT a Chebyshev channel
    # (the Chebyshev fixture has H2+H2NN -> N2H4).
    (tmp_path / "output.py").write_text(
        "pdepreaction(\n"
        "    reactants = ['N2H4'],\n"
        "    products = ['H2', 'H2NN'],\n"
        "    kinetics = PDepArrhenius(\n"
        "        pressures = ([0.01, 100], 'bar'),\n"
        "        arrhenius = [\n"
        "            Arrhenius(A=(1.0, 's^-1'), n=0.0, Ea=(0.0, 'kJ/mol'), T0=(1,'K')),\n"
        "            Arrhenius(A=(2.0, 's^-1'), n=0.0, Ea=(0.0, 'kJ/mol'), T0=(1,'K')),\n"
        "        ],\n"
        "    ),\n"
        ")\n"
    )
    with pytest.raises(ValueError, match="topology does not match"):
        build_dual_form_payload(FIXTURE_DIR, tmp_path)


def _relabelled_plog_run(tmp_path: Path, old: str, new: str) -> Path:
    """Copy the PLOG fixture with one species label renamed.

    Reproduces the real hydrazine case: the two Arkane inputs name isodiazene
    ``H2NN`` and ``NH2N`` while pointing at the same data file and SMILES.
    """
    run_dir = tmp_path / "plog_relabelled"
    run_dir.mkdir()
    text = (PLOG_FIXTURE_DIR / "output.py").read_text()
    assert old in text, f"fixture does not contain {old!r}"
    (run_dir / "output.py").write_text(re.sub(rf"\b{re.escape(old)}\b", new, text))
    return run_dir


def _plog_fingerprint(request) -> list[tuple]:
    return sorted(
        (nk.source_state_key, nk.sink_state_key, round(e.pressure_bar, 9),
         float(e.a), round(e.n, 9), round(e.ea_kj_mol, 9))
        for nk in request.solve.channel_kinetics
        if nk.model_kind.value == "plog"
        for e in nk.plog.entries
    )


def test_species_alias_recovers_a_relabelled_plog_run(tmp_path) -> None:
    """A PLOG run that only *names* a species differently still maps.

    The alias must be equivalent to having relabelled the run by hand: the
    resulting PLOG fits are identical to those built from the unaltered
    fixture, so nothing about the kinetics depends on the rename.
    """
    run_dir = _relabelled_plog_run(tmp_path, "H2NN", "NH2N")
    aliased = build_dual_form_request(
        FIXTURE_DIR, run_dir, species_aliases={"NH2N": "H2NN"}
    )
    baseline = build_dual_form_request(FIXTURE_DIR, PLOG_FIXTURE_DIR)
    assert _plog_fingerprint(aliased) == _plog_fingerprint(baseline)
    assert len(aliased.solve.channel_kinetics) == 2


def test_a_relabelled_plog_run_without_an_alias_is_rejected(tmp_path) -> None:
    """Without the alias the same run fails the topology check rather than
    silently dropping the channel."""
    run_dir = _relabelled_plog_run(tmp_path, "H2NN", "NH2N")
    with pytest.raises(ValueError, match="topology does not match"):
        build_dual_form_payload(FIXTURE_DIR, run_dir)


@pytest.mark.parametrize(
    "aliases, message",
    [
        ({"NH2N": "NOT_A_SPECIES"}, "targets absent from the Chebyshev run"),
        ({"NEVER_APPEARS": "H2NN"}, "sources absent from the PLOG run"),
        ({"NH2N": "NH2N"}, "maps these labels to themselves"),
        ({"NH2N": "H2NN", "H2": "H2NN"}, "merge distinct species"),
    ],
)
def test_species_alias_rejects_maps_that_cannot_be_right(
    tmp_path, aliases, message
) -> None:
    """An alias merges labels, so a wrong one misattaches a fit silently.

    Each rejected map is a distinct way of being wrong: a typo'd target, a
    source the PLOG run never uses, a no-op, and two labels collapsed onto one
    species.
    """
    run_dir = _relabelled_plog_run(tmp_path, "H2NN", "NH2N")
    with pytest.raises(ValueError, match=message):
        build_dual_form_payload(FIXTURE_DIR, run_dir, species_aliases=aliases)


def test_dual_form_persist_two_kinetics_rows_per_channel(db_engine) -> None:
    """Persist the dual-form request and read back: the one channel carries two
    ``NetworkKinetics`` rows — one chebyshev (with coeffs) and one plog (with
    the pressure-indexed entries), values intact."""
    from app.db.models.network_pdep import NetworkKineticsPlog

    request = build_dual_form_request(FIXTURE_DIR, PLOG_FIXTURE_DIR)

    with _rolled_back_session(db_engine) as session:
        network = persist_network_pdep_upload(session, request, created_by=None)
        session.flush()

        channels = session.scalars(
            select(NetworkChannel).where(NetworkChannel.network_id == network.id)
        ).all()
        assert len(channels) == 1
        channel = channels[0]

        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()

        nk_rows = session.scalars(
            select(NetworkKinetics).where(NetworkKinetics.solve_id == solve.id)
        ).all()
        # Both parameterizations coexist on the SAME channel/solve.
        assert len(nk_rows) == 2
        assert {nk.channel_id for nk in nk_rows} == {channel.id}
        by_kind = {nk.model_kind.value: nk for nk in nk_rows}
        assert set(by_kind) == {"chebyshev", "plog"}

        # Chebyshev row -> coefficients intact.
        cheb = session.scalars(
            select(NetworkKineticsChebyshev).where(
                NetworkKineticsChebyshev.network_kinetics_id == by_kind["chebyshev"].id
            )
        ).one()
        assert cheb.n_temperature == 6
        assert cheb.n_pressure == 4
        assert cheb.coefficients == {"coeffs": _EXPECTED_CHEB}
        assert by_kind["chebyshev"].stores_log10_k is True

        # PLOG row -> one child per pressure entry, values intact.
        plog_rows = session.scalars(
            select(NetworkKineticsPlog)
            .where(NetworkKineticsPlog.network_kinetics_id == by_kind["plog"].id)
            .order_by(NetworkKineticsPlog.pressure_bar.asc())
        ).all()
        assert [r.pressure_bar for r in plog_rows] == [0.01, 0.1, 1.0, 10.0, 100.0]
        assert plog_rows[0].a == pytest.approx(1.23456e12)
        assert {r.a_units.value for r in plog_rows} == {"cm3_mol_s"}
        assert plog_rows[0].ea_kj_mol == pytest.approx(35.1)
        assert plog_rows[-1].ea_kj_mol == pytest.approx(39.5)
        assert by_kind["plog"].stores_log10_k is None


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
    assert len(solve.energy_transfer) == 1
    et = solve.energy_transfer[0]
    assert et.alpha0_cm_inv == 175.0
    assert et.t_ref_k == 298.0
    assert et.t_exponent == 0.52
    # The Arkane run declared one energyTransferModel for the whole network,
    # so the ingester deposits exactly that and names no well. It used to
    # expand the value across the (well x collider) cross product to satisfy
    # the old per-pair contract, which made one number look like several
    # determinations (ADR 0009).
    assert et.scope == NetworkEnergyTransferScope.network_wide
    assert et.state_key is None
    assert et.collider_species_key is None


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
    # Principal rotational constants (cm^-1) mapped a/b/c in source order.
    assert stm["rotational_constant_a_cm1"] == pytest.approx(4.89)
    assert stm["rotational_constant_b_cm1"] == pytest.approx(0.82)
    assert stm["rotational_constant_c_cm1"] == pytest.approx(0.82)
    # H2 reports a single rotational constant (linear) -> only 'a' populated.
    h2 = next(s for s in payload["species"] if s["key"] == "H2")
    h2_stm = h2["statmech"]
    assert h2_stm["rotational_constant_a_cm1"] == pytest.approx(61.08)
    assert "rotational_constant_b_cm1" not in h2_stm
    assert "rotational_constant_c_cm1" not in h2_stm
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
        assert n2h4_stm.rotational_constant_a_cm1 == pytest.approx(4.89)
        assert n2h4_stm.rotational_constant_b_cm1 == pytest.approx(0.82)
        assert n2h4_stm.rotational_constant_c_cm1 == pytest.approx(0.82)

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
        persist_network_pdep_upload(session, request, created_by=None)
        session.flush()
        arts = session.scalars(
            select(CalculationArtifact).where(
                CalculationArtifact.uri.like("s3://test-bucket/%")
            )
        ).all()
        # At least the N2H4 sp/freq/scan and TS1 sp/freq trimmed logs.
        assert len(arts) >= 3


# ---------------------------------------------------------------------------
# Provenance is DERIVED from the run, not hardcoded (cross-LOT hardening)
# ---------------------------------------------------------------------------


def test_software_release_derives_from_token() -> None:
    """Declared Arkane software tokens canonicalise to a software_release ref.

    The Gaussian token reproduces the seeded release (name+version), known
    tokens map to their canonical name, an unknown token passes through as the
    name verbatim, and a missing token yields no ref (never an invented
    program).
    """
    assert _software_release("orca") == {"name": "ORCA"}
    assert _software_release("gaussian") == {"name": "Gaussian", "version": "09"}
    assert _software_release("molpro") == {"name": "Molpro"}
    # Case / surrounding whitespace insensitive.
    assert _software_release("  ORCA ") == {"name": "ORCA"}
    # Unknown token -> raw name, no invented program.
    assert _software_release("newprog") == {"name": "newprog"}
    # No declaration -> no ref emitted.
    assert _software_release(None) is None
    assert _software_release("") is None


def test_lot_from_bare_model_chemistry() -> None:
    """A single-LOT ``modelChemistry = LevelOfTheory(...)`` populates BOTH the
    opt/freq and the energy slots (composite ``freq=``/``energy=`` not required)."""
    text = (
        'modelChemistry = LevelOfTheory(method="CCSD(T)-F12", '
        'basis="cc-pVTZ-F12", software="orca")\n'
    )
    inp = parse_input_file(text)
    assert (inp.opt_method, inp.opt_basis, inp.opt_software) == (
        "CCSD(T)-F12",
        "cc-pVTZ-F12",
        "orca",
    )
    # Same LOT covers the single point.
    assert (inp.energy_method, inp.energy_basis, inp.energy_software) == (
        "CCSD(T)-F12",
        "cc-pVTZ-F12",
        "orca",
    )


def test_lot_ignores_commented_block() -> None:
    """A commented-out LOT block above the live declaration is never read: the
    parser must return the LIVE method/software, not the disabled one."""
    text = (
        "# modelChemistry = CompositeLevelOfTheory(\n"
        '#     freq=LevelOfTheory(method="wb97xd", basis="def2tzvp", software="gaussian"),\n'
        '#     energy=LevelOfTheory(method="MRCI+Davidson", basis="aug-cc-pV(T+d)Z", software="molpro"),\n'
        "# )\n"
        'modelChemistry = LevelOfTheory(method="CCSD(T)-F12", basis="cc-pVTZ-F12", software="orca")\n'
    )
    inp = parse_input_file(text)
    # The commented wb97xd / MRCI / gaussian / molpro block must NOT win.
    assert (inp.opt_method, inp.opt_software) == ("CCSD(T)-F12", "orca")
    assert (inp.energy_method, inp.energy_software) == ("CCSD(T)-F12", "orca")


def test_composite_lot_survives_comment_strip() -> None:
    """Comment stripping must not break the live composite ``freq=``/``energy=``
    form (the MRCI run): distinct freq and energy LOTs still parse."""
    text = (
        "#modelChemistry = 'MRCI/cc-pVTZ'\n"
        "modelChemistry = CompositeLevelOfTheory(\n"
        '    freq=LevelOfTheory(method="wb97xd", basis="def2tzvp", software="gaussian"),\n'
        '    energy=LevelOfTheory(method="MRCI+Davidson", basis="aug-cc-pV(T+d)Z", software="molpro"),\n'
        ")\n"
    )
    inp = parse_input_file(text)
    assert (inp.opt_method, inp.opt_software) == ("wb97xd", "gaussian")
    assert (inp.energy_method, inp.energy_software) == ("MRCI+Davidson", "molpro")


def test_yml_species_recognized_by_parser() -> None:
    """``species('X', '.../X.yml', ...)`` is recognised (not silently dropped);
    the ``.yml`` data-file reference is preserved so downstream can name it."""
    text = (
        "species('AA', 'data/AA.py', structure=SMILES('[H][H]'))\n"
        "species('BB', '/home/x/ymal_files/BB.yml', structure=SMILES('[NH2]'))\n"
    )
    inp = parse_input_file(text)
    assert set(inp.species) == {"AA", "BB"}
    assert inp.species["AA"].data_file == "data/AA.py"
    assert inp.species["BB"].data_file is not None
    assert inp.species["BB"].data_file.endswith("BB.yml")


_MIN_CSV_HEADER = (
    "Label,Symmetry Number,Number of optical isomers,Symmetry Group,"
    "Rotational constant (cm-1),"
    '"Calculated Frequencies (unscaled and prior to projection, cm^-1)",'
    "Electronic energy (J/mol),"
    '"E0 (electronic energy + ZPE, J/mol)",'
    "E0 with atom and bond corrections (J/mol),"
    "Atom XYZ coordinates (angstrom),T1 diagnostic,D1 diagnostic\n"
)
_H2_CSV_ROW = (
    "H2,2,1,Dinfh,61.08,4462.3,-3078739.5575972195,-3052786.006271785,"
    '-4637.212477654586,"H    0.0    0.0    0.370025, H    0.0    0.0    -0.370025",,\n'
)


def _write_minimal_run(tmp_path: Path, *, software: str, extra_species: str = "") -> Path:
    """Write a minimal Arkane run dir (input.py / output.py / CSV) for H2."""
    (tmp_path / "input.py").write_text(
        f'modelChemistry = LevelOfTheory(method="CCSD(T)-F12", '
        f'basis="cc-pVTZ-F12", software="{software}")\n'
        "species('H2', 'data/H2.py', structure=SMILES('[H][H]'))\n"
        f"{extra_species}"
    )
    (tmp_path / "output.py").write_text("")  # no conformers / pdep needed here
    (tmp_path / "supporting_information.csv").write_text(
        _MIN_CSV_HEADER + _H2_CSV_ROW
    )
    return tmp_path


def test_calc_software_release_derived_from_declared_run(tmp_path) -> None:
    """The per-calc ``software_release`` comes from the run's DECLARED software,
    not a hardcoded Gaussian/Molpro — an ORCA run yields ORCA on opt/freq/sp."""
    run_dir = _write_minimal_run(tmp_path, software="orca")
    payload, gap = build_network_pdep_payload(run_dir)
    assert "H2" in gap.species_built
    h2 = next(s for s in payload["species"] if s["key"] == "H2")
    opt = h2["conformers"][0]["calculation"]
    assert opt["software_release"] == {"name": "ORCA"}
    by_type = {c["type"]: c for c in h2["calculations"]}
    assert by_type["freq"]["software_release"] == {"name": "ORCA"}
    assert by_type["sp"]["software_release"] == {"name": "ORCA"}
    # And the level of theory is the declared one (not the wb97xd/MRCI default).
    assert opt["level_of_theory"]["method"] == "CCSD(T)-F12"
    assert by_type["sp"]["level_of_theory"]["method"] == "CCSD(T)-F12"


def test_yml_species_dropped_is_named_fail_loud(tmp_path) -> None:
    """A reactive ``.yml`` species with no CSV geometry is recorded in the
    GapReport by name and reason (fail-loud) rather than silently vanishing."""
    run_dir = _write_minimal_run(
        tmp_path,
        software="molpro",
        extra_species="species('BB', '/home/x/ymal_files/BB.yml', structure=SMILES('[NH2]'))\n",
    )
    _payload, gap = build_network_pdep_payload(run_dir)
    assert "H2" in gap.species_built
    skipped = dict(gap.species_skipped)
    assert "BB" in skipped
    assert ".yml" in skipped["BB"] or "yml" in skipped["BB"].lower()


# ---------------------------------------------------------------------------
# A single atom is deposited as its single point (#615)
# ---------------------------------------------------------------------------


def _run_with_hydrogen_atom(tmp_path: Path, *, energy_j_mol: str = "-1313000.0") -> Path:
    """The hydrazine fixture plus a bare H atom Arkane ran a single point on.

    The atom has an energy and a geometry and no frequencies, which is what an
    Arkane run records for it. It is left out of the network topology: the test
    reads what the builder emits for the species, not whether H belongs to the
    fixture's mini-network.
    """
    import shutil

    run = tmp_path / "run"
    shutil.copytree(FIXTURE_DIR, run)
    (run / "input.py").write_text(
        (run / "input.py").read_text().replace(
            "transitionState('TS1'",
            "species('H', 'Data/H.py',\n        structure = SMILES('[H]'),\n)\n"
            "transitionState('TS1'",
            1,
        )
    )
    (run / "Data" / "H.py").write_text(
        "bonds = {}\n\nexternalSymmetry = 1\n\nspinMultiplicity = 2\n\nopticalIsomers = 1\n"
    )
    with (run / "supporting_information.csv").open("a") as fh:
        fh.write(f'H,1,1,,,,{energy_j_mol},{energy_j_mol},,"H    0.0    0.0    0.0",,\n')
    return run


def test_hydrogen_atom_is_sent_as_its_single_point_not_a_fabricated_opt(tmp_path) -> None:
    payload, _gap = build_network_pdep_payload(_run_with_hydrogen_atom(tmp_path))
    atom = next(s for s in payload["species"] if s["key"] == "H")
    (conformer,) = atom["conformers"]
    primary = conformer["calculation"]
    # The honest shape: the single point, once, as the primary.
    assert primary["type"] == "sp"
    assert primary["key"] == "H_sp"
    assert primary["sp_electronic_energy_hartree"] == pytest.approx(
        j_mol_to_hartree(-1313000.0)
    )
    assert atom["calculations"] == []
    assert "opt_converged" not in primary
    assert atom["statmech"]["source_calculations"] == [
        {"calculation_key": "H_sp", "role": "sp"}
    ]
    # The polyatomic species keep their opt primary and separate sp.
    h2 = next(s for s in payload["species"] if s["key"] == "H2")
    assert h2["conformers"][0]["calculation"]["type"] == "opt"
    assert [c["type"] for c in h2["calculations"]] == ["freq", "sp"]
    # The atom is schema-valid as a species of its own.
    from app.schemas.workflows.network_pdep_upload import NetworkSpeciesIn

    NetworkSpeciesIn.model_validate(atom)


def test_atom_without_an_energy_is_skipped_not_given_a_fabricated_opt(tmp_path) -> None:
    """No single point to send means no honest primary: the species is named and dropped."""
    run = _run_with_hydrogen_atom(tmp_path, energy_j_mol="")
    payload, gap = build_network_pdep_payload(run)
    assert "H" not in {s["key"] for s in payload["species"]}
    assert (
        "H",
        "one-atom species with no single-point energy: no honest primary calculation",
    ) in gap.species_skipped


def test_molecule_without_frequencies_keeps_its_opt_primary(tmp_path) -> None:
    """The atom test is the geometry's atom count, not 'no frequencies'."""
    run = _run_with_hydrogen_atom(tmp_path)
    csv_path = run / "supporting_information.csv"
    with csv_path.open("a") as fh:
        fh.write(
            'HF,1,1,,,,-1313000.0,-1313000.0,,"H    0.0    0.0    0.0, F    0.0    0.0    0.92",,\n'
        )
    (run / "Data" / "HF.py").write_text(
        "bonds = {}\n\nexternalSymmetry = 1\n\nspinMultiplicity = 1\n\nopticalIsomers = 1\n"
    )
    (run / "input.py").write_text(
        (run / "input.py").read_text().replace(
            "transitionState('TS1'",
            "species('HF', 'Data/HF.py',\n        structure = SMILES('F'),\n)\n"
            "transitionState('TS1'",
            1,
        )
    )
    payload, _gap = build_network_pdep_payload(run)
    molecule = next(s for s in payload["species"] if s["key"] == "HF")
    assert molecule["conformers"][0]["calculation"]["type"] == "opt"
    assert [c["type"] for c in molecule["calculations"]] == ["sp"]


def test_atom_in_the_network_still_supplies_the_solve_its_energy_and_source(tmp_path) -> None:
    """An atom's sp is its conformer primary, and the solve must still find it.

    The hydrazine fixture's H2 is turned into an atom (its row loses its
    second atom and its frequency). The network topology is not meant to stay
    chemically valid, so the payload is read without validation: what matters
    is that the exit state's energy and the solve's source calculation come
    from the atom's primary single point, not from a species calculation it
    no longer has.
    """
    import shutil

    run = tmp_path / "run"
    shutil.copytree(FIXTURE_DIR, run)
    csv_path = run / "supporting_information.csv"
    lines = csv_path.read_text().splitlines()
    atom_row = [
        'H2,1,1,,,,-3078739.5575972195,-3052786.006271785,,"H    0.0    0.0    0.0",,'
        if line.startswith("H2,")
        else line
        for line in lines
    ]
    csv_path.write_text("\n".join(atom_row) + "\n")

    payload, _gap = build_network_pdep_payload(run)
    atom = next(s for s in payload["species"] if s["key"] == "H2")
    assert atom["conformers"][0]["calculation"]["key"] == "H2_sp"
    assert atom["calculations"] == []
    solve = payload["solve"]
    assert {"calculation_key": "H2_sp", "role": "well_energy"} in solve["source_calculations"]
    assert len(solve["state_energies"]) == 2


# ---------------------------------------------------------------------------
# One source per participant, and the sum check (#678)
# ---------------------------------------------------------------------------


def test_the_two_species_state_cites_every_participants_single_point() -> None:
    """The hydrazine bimolecular state is H2 + H2NN; the well is one species."""
    payload, _gap = build_network_pdep_payload(FIXTURE_DIR)
    by_state = {entry["state_key"]: entry for entry in payload["solve"]["state_energies"]}
    bimolecular = by_state["st_H2_H2NN"]
    assert "source_calculation_key" not in bimolecular
    assert bimolecular["source_calculation_keys"] == [
        {"species_key": "H2", "calculation_key": "H2_sp"},
        {"species_key": "H2NN", "calculation_key": "H2NN_sp"},
    ]
    (well,) = [entry for key, entry in by_state.items() if key != "st_H2_H2NN"]
    assert "source_calculation_keys" not in well and well["source_calculation_key"].endswith("_sp")


def test_the_hydrazine_state_energies_agree_with_the_sum_of_their_stored_sources(db_engine) -> None:
    """Round trip: build, persist, and every state energy is stored as agreeing with its sources.

    The ingester states ``lowest_state`` / ``electronic_only``, so the check that runs here is the
    shared-zero difference between the two states.
    """
    request = build_network_pdep_request(FIXTURE_DIR)
    with _rolled_back_session(db_engine) as session:
        network = persist_network_pdep_upload(session, request, created_by=None)
        session.flush()
        energies = session.scalars(
            select(NetworkSolveStateEnergy)
            .join(NetworkState, NetworkState.id == NetworkSolveStateEnergy.state_id)
            .where(NetworkState.network_id == network.id)
        ).all()
        assert len(energies) == 2
        assert {(e.source_sum_comparison, e.source_sum_not_compared_reason) for e in energies} == {("agrees", None)}
        sources = session.scalars(
            select(NetworkSolveStateEnergySource)
            .join(NetworkState, NetworkState.id == NetworkSolveStateEnergySource.state_id)
            .where(NetworkState.network_id == network.id)
        ).all()
        assert len(sources) == 2  # H2 and H2NN, both under the bimolecular state
        cited = {session.get(Calculation, s.calculation_id).species_entry_id for s in sources}
        assert cited == {s.species_entry_id for s in sources}
        assert len(cited) == 2


def test_a_hydrazine_state_energy_that_is_off_its_sources_is_refused(db_engine) -> None:
    """The same payload with the bimolecular energy moved by 1 kJ/mol."""
    payload, _gap = build_network_pdep_payload(FIXTURE_DIR)
    bimolecular = next(e for e in payload["solve"]["state_energies"] if e["state_key"] == "st_H2_H2NN")
    bimolecular["energy_kj_mol"] += 1.0
    request = NetworkPDepUploadRequest(**payload)
    with _rolled_back_session(db_engine) as session:
        with pytest.raises(CodedValueError) as raised:
            persist_network_pdep_upload(session, request, created_by=None)
    assert raised.value.code == "network_state_energy_sum_mismatch"
