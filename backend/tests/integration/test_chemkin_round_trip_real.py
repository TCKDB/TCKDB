"""Full CHEMKIN round-trip on a **real RMG mechanism** (ammonia-methane system).

Companion to ``test_chemkin_round_trip.py`` (hand-authored fixture). This one
drives the same importer -> persist -> export -> re-parse pipeline against two
unmodified RMG outputs. The first, ``fixtures/rmg_ammonia_methane/``, is described
here; the second, the published formic acid model of Wu and Grinberg Dana
(``fixtures/rmg_formic_acid_wu2026/``, 123 species and 3002 reactions), is covered
by the generic tests below (which run on both) and by the ``formic acid`` section
at the end.

Ammonia-methane: 21 species and 64
rate rows representing 61 reaction identities, exercising modified Arrhenius,
Troe falloff + collider efficiencies,
Chebyshev pressure-dependence (written by RMG as ``R(+M)<=>P(+M)`` with a dummy
``1.0 0 0`` main line + ``TCHEB/PCHEB/CHEB``), duplicate reactions, KCAL/MOLE
units, and transport.

The single most load-bearing assertion is still the NASA a/b convention: the
HIGH-temperature block must come back in the HIGH interval (``a=LOW``,
``b=HIGH``). A bonus independent physics check asserts NASA Cp continuity at
T_mid survives the trip.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest
from rdkit import Chem

from tests._ci_dependency import require_module

# The CHEMKIN importer adapter (``tckdb_chemkin``) lives under
# clients/python/adapters/chemkin and is not installed into tckdb_env, so its
# package root is appended to sys.path here — inline (rather than in a
# tests/integration/conftest.py) to avoid a bare-``conftest`` module-name
# collision. APPEND (not insert(0)): this directory also carries its own
# ``tests`` package, and fronting sys.path would shadow the backend ``tests``
# package for the rest of the session.
_CHEMKIN_ADAPTER = (
    Path(__file__).resolve().parents[3] / "clients" / "python" / "adapters" / "chemkin"
)
if _CHEMKIN_ADAPTER.is_dir() and str(_CHEMKIN_ADAPTER) not in sys.path:
    sys.path.append(str(_CHEMKIN_ADAPTER))

from tckdb_chemkin.duplicates import duplicate_flag_warnings
from tckdb_chemkin.identity import (
    IdentityResolver,
    parse_species_dictionary,
)
from tckdb_chemkin.normalizer import normalize_mechanism
from tckdb_chemkin.parser import parse_mechanism
from tckdb_chemkin.payloads import ImportConfig, build_all_payloads
from tckdb_chemkin.transport import parse_transport_file

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transport_upload import TransportUploadRequest
from app.services.scientific_read.chemkin_serialize import (
    ChemkinOptions,
    _assign_names,
    _composition,
    serialize_chemkin,
    validate_chemkin_mechanism,
)
from app.services.scientific_read.export import (
    SeedSelection,
    build_export_record_set,
)
from app.workflows.kinetics import persist_kinetics_upload
from app.workflows.thermo import persist_thermo_upload
from app.workflows.transport import persist_transport_upload
from tests.services.scientific_read._factories import set_review

FIXTURES = Path(__file__).parent / "fixtures"


class _Case:
    """One real RMG model: where its files are and what it ships."""

    def __init__(self, name, directory, mechanism, transport=None):
        self.name = name
        self.dir = FIXTURES / directory
        self.mechanism = mechanism
        self.transport = transport

    def read(self, name: str) -> str:
        return (self.dir / name).read_text()


AMMONIA = _Case("ammonia", "rmg_ammonia_methane", "chem.inp", transport="tran.dat")
FORMIC = _Case("formic", "rmg_formic_acid_wu2026", "chem_annotated.inp")
CASES = {c.name: c for c in (AMMONIA, FORMIC)}
FIXTURE_DIR = AMMONIA.dir


def _read(name: str) -> str:
    """Read a file of the ammonia-methane fixture (also used by the numerical-grid test)."""
    return AMMONIA.read(name)


def _canon(smiles: str) -> str:
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None, f"unparseable SMILES {smiles!r}"
    return Chem.MolToSmiles(mol)


# Activation-energy magnitude in kJ/mol, so a reaction can be compared across a
# unit change. The fixture header is KCAL/MOLE; the exporter re-emits in
# CAL/MOLE, so the round trip legitimately changes the reported *unit* while
# preserving the physical energy — the comparison must therefore be unit-aware.
_EA_TO_KJ_MOL = {
    "kcal_mol": 4.184,
    "cal_mol": 4.184e-3,
    "kj_mol": 1.0,
    "j_mol": 1e-3,
}


def _ea_kj(nr) -> float:
    assert nr.reported_ea is not None and nr.reported_ea_units is not None
    return nr.reported_ea * _EA_TO_KJ_MOL[nr.reported_ea_units]


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _import_payloads(case: _Case = AMMONIA):
    mech = parse_mechanism(case.read(case.mechanism))
    if case.transport:
        mech.transport = parse_transport_file(case.read(case.transport))
    resolver = IdentityResolver(
        rmg_dict=parse_species_dictionary(case.read("species_dictionary.txt"))
    )
    resolved = resolver.resolve_mechanism(mech)
    normalized = normalize_mechanism(mech)
    config = ImportConfig(scientific_origin="experimental")
    built = build_all_payloads(mech, resolver, config, normalized=normalized)
    return mech, resolved, normalized, built


def _persist_and_approve(session, built):
    species_entry_refs: set[str] = set()
    reaction_entry_refs: set[str] = set()

    for payload in built.thermo:
        req = ThermoUploadRequest(**payload)
        row = persist_thermo_upload(session, req, review_policy=None)
        set_review(
            session,
            record_type=SubmissionRecordType.thermo,
            record_id=row.id,
            status=RecordReviewStatus.approved,
        )
        species_entry_refs.add(row.species_entry.public_ref)

    for payload in built.transport:
        req = TransportUploadRequest(**payload)
        row = persist_transport_upload(session, req, review_policy=None)
        set_review(
            session,
            record_type=SubmissionRecordType.transport,
            record_id=row.id,
            status=RecordReviewStatus.approved,
        )

    for payload in built.kinetics:
        req = KineticsUploadRequest(**payload)
        row = persist_kinetics_upload(session, req, review_policy=None)
        set_review(
            session,
            record_type=SubmissionRecordType.kinetics,
            record_id=row.id,
            status=RecordReviewStatus.approved,
        )
        reaction_entry_refs.add(row.reaction_entry.public_ref)

    return species_entry_refs, reaction_entry_refs


def _export_chemkin(session, species_entry_refs, reaction_entry_refs):
    rs = build_export_record_set(
        session,
        seed=SeedSelection(
            species_refs=sorted(species_entry_refs),
            reaction_refs=sorted(reaction_entry_refs),
        ),
    )
    compositions = {
        sr.species_entry.id: _composition(sr.species.smiles)
        for sr in rs.species_records
    }
    names = _assign_names(
        rs.species_records, naming_policy="formula", compositions=compositions
    )
    collider_names = {
        sr.species.id: names[sr.species_entry.id] for sr in rs.species_records
    }
    export = serialize_chemkin(
        rs,
        options=ChemkinOptions(energy_units="cal/mol"),
        collider_names=collider_names,
    )
    return rs, export


# The persist-and-export step for the formic acid model takes about a minute, so
# each model is run once per module and the plain-data results (parsed
# mechanisms, export text, warnings) are shared. The database session of the
# test that first needs a model carries it; it is rolled back afterwards like any
# other, and nothing cached refers to it.
_RUNS: dict[str, dict] = {}


def _run_round_trip(db_session, case: _Case) -> dict:
    if case.name in _RUNS:
        return _RUNS[case.name]
    mech_in, resolved_in, normalized_in, built = _import_payloads(case)
    species_refs, reaction_refs = _persist_and_approve(db_session, built)
    _rs, export = _export_chemkin(db_session, species_refs, reaction_refs)

    blocking = [g for g in export.gaps if g.kind in ("thermo_nasa", "kinetics")]
    assert blocking == [], f"unexpected export gaps: {blocking}"

    mech_out = parse_mechanism(
        export.files["chem.inp"], thermo_text=export.files["therm.dat"]
    )
    if "tran.dat" in export.files:
        mech_out.transport = parse_transport_file(export.files["tran.dat"])
    resolver_out = IdentityResolver()  # rely on inline SMILES + MULT comments
    resolved_out = resolver_out.resolve_mechanism(mech_out)
    normalized_out = normalize_mechanism(mech_out)

    _RUNS[case.name] = {
        "case": case,
        "mech_in": mech_in,
        "resolved_in": resolved_in,
        "normalized_in": normalized_in,
        "built": built,
        "mech_out": mech_out,
        "resolved_out": resolved_out,
        "normalized_out": normalized_out,
        "export": export,
    }
    return _RUNS[case.name]


@pytest.fixture
def ammonia_round_trip(db_session):
    return _run_round_trip(db_session, AMMONIA)


@pytest.fixture
def formic_round_trip(db_session):
    return _run_round_trip(db_session, FORMIC)


@pytest.fixture(params=sorted(CASES), ids=sorted(CASES))
def round_trip(request, db_session):
    """Either real model: for the checks that hold for any RMG mechanism."""
    return _run_round_trip(db_session, CASES[request.param])


# ---------------------------------------------------------------------------
# Indexing helpers
# ---------------------------------------------------------------------------


def _rxn_key(nr, resolved):
    reactants = tuple(sorted(_canon(resolved[n].smiles) for n in nr.reactant_names))
    products = tuple(sorted(_canon(resolved[n].smiles) for n in nr.product_names))
    return (reactants, products, nr.reversible)


def _rxn_by_key(normalized, resolved):
    return {_rxn_key(nr, resolved): nr for nr in normalized.reactions}


def _thermo_by_smiles(mech, resolved):
    """Thermo keyed by (SMILES, multiplicity): a structure can have two spin states."""
    return {
        (_canon(resolved[name].smiles), resolved[name].multiplicity): entry
        for name, entry in mech.thermo.items()
        if name in resolved
    }


def _transport_by_smiles(mech, resolved):
    return {
        _canon(resolved[name].smiles): entry
        for name, entry in mech.transport.items()
        if name in resolved
    }


# ---------------------------------------------------------------------------
# Species identity
# ---------------------------------------------------------------------------


def _identity(res, name):
    return (_canon(res[name].smiles), res[name].multiplicity)


def test_species_set_preserved_by_structure_and_spin(round_trip):
    """Every species comes back, matched by SMILES *and* multiplicity, none merged.

    The multiplicity is what tells the singlet from the triplet of one structure,
    so it is read back from the export's ``MULT=`` comment, not guessed from the
    SMILES.
    """
    res_in, res_out = round_trip["resolved_in"], round_trip["resolved_out"]
    ids_in = Counter(_identity(res_in, n) for n in res_in)
    ids_out = Counter(_identity(res_out, n) for n in res_out)
    assert max(ids_in.values()) == 1, "two names in the input share a structure and spin"
    assert max(ids_out.values()) == 1, "two names in the export share a structure and spin"
    assert ids_out == ids_in
    assert len(res_in) == len(res_out)
    assert len(ids_in) == {"ammonia": 21, "formic": 123}[round_trip["case"].name]


def test_every_exported_species_comment_states_multiplicity(round_trip):
    text = round_trip["export"].files["chem.inp"]
    lines = text.split("SPECIES", 1)[1].split("END", 1)[0].splitlines()
    commented = [ln for ln in lines if "!" in ln and "SMILES=" in ln]
    assert len(commented) == len(round_trip["mech_out"].species)
    for ln in commented:
        comment = ln.split("!", 1)[1].split()
        assert comment[0].startswith("SMILES="), ln
        assert comment[1].startswith("MULT="), ln


# ---------------------------------------------------------------------------
# NASA thermo — coefficient-convention guard + continuity physics check
# ---------------------------------------------------------------------------


def test_nasa_thermo_round_trips_in_same_intervals(round_trip):
    orig = _thermo_by_smiles(round_trip["mech_in"], round_trip["resolved_in"])
    back = _thermo_by_smiles(round_trip["mech_out"], round_trip["resolved_out"])
    assert set(back) == set(orig)

    for smiles, e_in in orig.items():
        e_out = back[smiles]
        assert e_out.t_low == pytest.approx(e_in.t_low)
        assert e_out.t_common == pytest.approx(e_in.t_common)
        assert e_out.t_high == pytest.approx(e_in.t_high)
        # HIGH stays HIGH, LOW stays LOW (a=LOW, b=HIGH convention).
        assert e_out.coeffs_high == pytest.approx(e_in.coeffs_high, rel=1e-6)
        assert e_out.coeffs_low == pytest.approx(e_in.coeffs_low, rel=1e-6)


def test_nasa_high_low_not_swapped_water(ammonia_round_trip):
    """H2O's fixture card has distinct high/low leading coefficients; a swap in
    either adapter would surface as the wrong block in the high interval."""
    h2o = _canon("O")
    e_in = _thermo_by_smiles(ammonia_round_trip["mech_in"], ammonia_round_trip["resolved_in"])[(h2o, 1)]
    e_out = _thermo_by_smiles(ammonia_round_trip["mech_out"], ammonia_round_trip["resolved_out"])[(h2o, 1)]
    # HIGH interval leading coeff (2.731...) distinct from LOW (4.201...).
    assert e_in.coeffs_high[0] == pytest.approx(2.73118, rel=1e-4)
    assert e_in.coeffs_low[0] == pytest.approx(4.20148, rel=1e-4)
    assert e_out.coeffs_high[0] == pytest.approx(e_in.coeffs_high[0], rel=1e-6)
    assert e_out.coeffs_low[0] == pytest.approx(e_in.coeffs_low[0], rel=1e-6)


def test_nasa_cp_continuity_survives_round_trip(round_trip):
    """Independent physics check: low & high Cp/R polynomials agree at T_mid
    after the trip (RMG fits are continuous to ~1e-5)."""
    back = _thermo_by_smiles(round_trip["mech_out"], round_trip["resolved_out"])

    def cp_over_r(coeffs, t):
        return sum(coeffs[i] * t**i for i in range(5))

    for smiles, e in back.items():
        tm = e.t_common
        lo = cp_over_r(e.coeffs_low, tm)
        hi = cp_over_r(e.coeffs_high, tm)
        assert hi == pytest.approx(lo, rel=1e-3), f"Cp discontinuity for {smiles}"


# ---------------------------------------------------------------------------
# Kinetics — Arrhenius, Troe falloff + efficiencies, Chebyshev
# ---------------------------------------------------------------------------


def test_modified_arrhenius_round_trips(ammonia_round_trip):
    in_by = _rxn_by_key(ammonia_round_trip["normalized_in"], ammonia_round_trip["resolved_in"])
    out_by = _rxn_by_key(ammonia_round_trip["normalized_out"], ammonia_round_trip["resolved_out"])

    # HO2 + NH2 <=> O2 + NH3  (A=2.179e6 n=2.08 Ea=-4.76 kcal/mol)
    key = (
        tuple(sorted((_canon("[O]O"), _canon("[NH2]")))),
        tuple(sorted((_canon("[O][O]"), _canon("N")))),
        True,
    )
    a_in, a_out = in_by[key], out_by[key]
    assert a_in.model_kind == a_out.model_kind == "modified_arrhenius"
    assert a_out.a == pytest.approx(a_in.a, rel=1e-4)
    assert a_out.a_units == a_in.a_units == "cm3_mol_s"
    assert a_out.n == pytest.approx(a_in.n)
    # Ea enters as KCAL/MOLE and leaves as CAL/MOLE (exporter header): the
    # *unit* changes but the physical energy is preserved.
    assert a_in.reported_ea_units == "kcal_mol"
    assert a_out.reported_ea_units == "cal_mol"
    assert _ea_kj(a_out) == pytest.approx(_ea_kj(a_in), rel=1e-4)
    assert _ea_kj(a_in) == pytest.approx(-4.76 * 4.184, rel=1e-4)


def test_troe_falloff_round_trips_with_efficiencies(ammonia_round_trip):
    in_by = _rxn_by_key(ammonia_round_trip["normalized_in"], ammonia_round_trip["resolved_in"])
    out_by = _rxn_by_key(ammonia_round_trip["normalized_out"], ammonia_round_trip["resolved_out"])

    # H + CH3 (+M) <=> CH4 (+M): Troe with 4 broadening params + 5 colliders.
    key = (
        tuple(sorted((_canon("[H]"), _canon("[CH3]")))),
        (_canon("C"),),
        True,
    )
    t_in, t_out = in_by[key], out_by[key]
    assert t_in.model_kind == t_out.model_kind == "troe"

    assert t_out.a == pytest.approx(t_in.a, rel=1e-4)
    assert t_out.a_units == t_in.a_units == "cm3_mol_s"
    assert t_out.n == pytest.approx(t_in.n)
    assert _ea_kj(t_out) == pytest.approx(_ea_kj(t_in), rel=1e-4)

    f_in, f_out = t_in.falloff, t_out.falloff
    assert f_out is not None
    assert f_out.low_a == pytest.approx(f_in.low_a, rel=1e-4)
    assert f_out.low_a_units == f_in.low_a_units == "cm6_mol2_s"
    assert f_out.low_n == pytest.approx(f_in.low_n)
    assert f_out.low_ea_kj_mol == pytest.approx(f_in.low_ea_kj_mol, rel=1e-4)
    assert f_out.troe_alpha == pytest.approx(f_in.troe_alpha)
    assert f_out.troe_t3 == pytest.approx(f_in.troe_t3)
    assert f_out.troe_t1 == pytest.approx(f_in.troe_t1)
    assert f_out.troe_t2 == pytest.approx(f_in.troe_t2)

    def eff_by_smiles(nr, resolved):
        return {_canon(resolved[n].smiles): v for n, v in nr.efficiencies.items()}

    e_in = eff_by_smiles(t_in, ammonia_round_trip["resolved_in"])
    e_out = eff_by_smiles(t_out, ammonia_round_trip["resolved_out"])
    assert set(e_out) == set(e_in)
    for smiles, val in e_in.items():
        assert e_out[smiles] == pytest.approx(val, rel=1e-3)
    assert e_in == pytest.approx(
        {
            _canon("C"): 3.0,
            _canon("CC"): 3.0,
            _canon("[H][H]"): 2.0,
            _canon("O"): 6.0,
            _canon("[Ar]"): 0.7,
        }
    )


def test_chebyshev_round_trips(ammonia_round_trip):
    in_by = _rxn_by_key(ammonia_round_trip["normalized_in"], ammonia_round_trip["resolved_in"])
    out_by = _rxn_by_key(ammonia_round_trip["normalized_out"], ammonia_round_trip["resolved_out"])

    # NO[O] (+M) <=> O2 + NH2 (+M): a 6x4 Chebyshev surface.
    key = (
        (_canon("NO[O]"),),
        tuple(sorted((_canon("[O][O]"), _canon("[NH2]")))),
        True,
    )
    c_in, c_out = in_by[key], out_by[key]
    assert c_in.model_kind == c_out.model_kind == "chebyshev"

    cb_in, cb_out = c_in.chebyshev, c_out.chebyshev
    assert cb_out is not None
    assert (cb_out.n_temperature, cb_out.n_pressure) == (
        cb_in.n_temperature,
        cb_in.n_pressure,
    )
    assert cb_out.tmin_k == pytest.approx(cb_in.tmin_k, rel=1e-4)
    assert cb_out.tmax_k == pytest.approx(cb_in.tmax_k, rel=1e-4)
    assert cb_out.pmin_bar == pytest.approx(cb_in.pmin_bar, rel=1e-3)
    assert cb_out.pmax_bar == pytest.approx(cb_in.pmax_bar, rel=1e-3)
    assert len(cb_out.coefficients) == len(cb_in.coefficients)
    for row_in, row_out in zip(cb_in.coefficients, cb_out.coefficients, strict=True):
        assert row_out == pytest.approx(row_in, rel=1e-4)


def test_all_chebyshev_reactions_round_trip(ammonia_round_trip):
    """All 9 RMG Chebyshev reactions come back as Chebyshev (guards the
    (+M)+dummy-main-line handling on the whole set, not just one)."""
    n_in = sum(
        1 for r in ammonia_round_trip["normalized_in"].reactions if r.model_kind == "chebyshev"
    )
    n_out = sum(
        1 for r in ammonia_round_trip["normalized_out"].reactions if r.model_kind == "chebyshev"
    )
    assert n_in == 9
    assert n_out == n_in


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def test_transport_round_trips(ammonia_round_trip):
    orig = _transport_by_smiles(ammonia_round_trip["mech_in"], ammonia_round_trip["resolved_in"])
    back = _transport_by_smiles(ammonia_round_trip["mech_out"], ammonia_round_trip["resolved_out"])
    assert set(back) == set(orig)

    for smiles, t_in in orig.items():
        t_out = back[smiles]
        assert t_out.sigma_angstrom == pytest.approx(t_in.sigma_angstrom, rel=1e-4)
        assert t_out.eps_over_k == pytest.approx(t_in.eps_over_k, rel=1e-4)
        assert t_out.dipole_debye == pytest.approx(t_in.dipole_debye, rel=1e-4)
        assert t_out.polarizability_angstrom3 == pytest.approx(
            t_in.polarizability_angstrom3, rel=1e-4
        )

    h2o = orig[_canon("O")]
    assert h2o.sigma_angstrom == pytest.approx(2.605)
    assert h2o.eps_over_k == pytest.approx(572.402)
    assert h2o.dipole_debye == pytest.approx(1.844)


# ---------------------------------------------------------------------------
# Duplicate reactions — every rate row round-trips (nothing dropped).
# ---------------------------------------------------------------------------


def test_duplicate_reactions_round_trip(ammonia_round_trip):
    """RMG marks 3 reaction identities as DUPLICATE (2 rate rows each = 6
    lines). The normalizer represents each pair as one ``multi_arrhenius``
    record, so 64 rate rows become 61 normalized reaction records without
    losing a rate term. The exporter expands the pairs and re-emits the
    ``DUPLICATE`` keyword; re-parsing collapses them back to the same model.
    """
    n_in = ammonia_round_trip["normalized_in"].reactions
    n_out = ammonia_round_trip["normalized_out"].reactions
    assert len(n_in) == 61
    assert len(n_out) == 61

    def rate_term_count(reactions):
        return sum(len(r.arrhenius_entries) or 1 for r in reactions)

    assert rate_term_count(n_in) == 64
    assert rate_term_count(n_out) == 64

    multi_in = [r for r in n_in if r.model_kind == "multi_arrhenius"]
    multi_out = [r for r in n_out if r.model_kind == "multi_arrhenius"]
    assert len(multi_in) == 3
    assert len(multi_out) == 3
    assert all(r.duplicate and len(r.arrhenius_entries) == 2 for r in multi_in)
    assert all(r.duplicate and len(r.arrhenius_entries) == 2 for r in multi_out)

    # 61 distinct reaction identities (3 pairs share a key); the full set of
    # identities survives the trip.
    in_keys = _rxn_by_key(ammonia_round_trip["normalized_in"], ammonia_round_trip["resolved_in"])
    out_keys = _rxn_by_key(ammonia_round_trip["normalized_out"], ammonia_round_trip["resolved_out"])
    assert len(in_keys) == 61
    assert set(out_keys) == set(in_keys)

    for key, r_in in in_keys.items():
        if r_in.model_kind != "multi_arrhenius":
            continue
        r_out = out_keys[key]
        assert r_out.model_kind == "multi_arrhenius"
        assert len(r_out.arrhenius_entries) == len(r_in.arrhenius_entries)
        for term_in, term_out in zip(
            r_in.arrhenius_entries, r_out.arrhenius_entries, strict=True
        ):
            assert term_out.a == pytest.approx(term_in.a)
            assert term_out.a_units == term_in.a_units
            assert term_out.n == pytest.approx(term_in.n)
            assert _ea_kj(term_out) == pytest.approx(_ea_kj(term_in))


# ---------------------------------------------------------------------------
# Cantera-load validation: the exported mechanism must be a file a downstream
# interpreter actually accepts. This is the authoritative guard (ground truth)
# against handing a user a broken mechanism — in particular one with an
# undeclared duplicate reaction, which real CHEMKIN files frequently ship.
# ---------------------------------------------------------------------------

cantera = require_module("cantera", install="conda install -c conda-forge cantera=3.2.0 (backend/environment.yml)")


def test_exported_mechanism_passes_cantera_validation(round_trip):
    """The real ammonia-methane export loads cleanly in Cantera (ck2yaml,
    strict/non-permissive) — every DUPLICATE is declared, reactions balance,
    thermo/transport resolve."""
    validate_chemkin_mechanism(round_trip["export"].files)


def test_cantera_rejects_undeclared_duplicate(round_trip):
    """Proof the guard actually catches the failure mode: strip the DUPLICATE
    keywords the exporter emitted and confirm Cantera then REJECTS the file.
    This both validates the guard and proves those markers are load-bearing
    (the mechanism genuinely contains duplicate reactions)."""
    files = dict(round_trip["export"].files)
    stripped = "\n".join(
        ln for ln in files["chem.inp"].splitlines()
        if ln.strip().upper() not in ("DUPLICATE", "DUP")
    ) + "\n"
    assert stripped != files["chem.inp"], "fixture expected to contain duplicates"
    files["chem.inp"] = stripped
    with pytest.raises(ValueError, match="Cantera validation"):
        validate_chemkin_mechanism(files)


# ===========================================================================
# Formic acid model (Wu and Grinberg Dana 2026): 123 species, 3002 reactions
# ===========================================================================
#
# A published model, imported and exported unmodified. Its CHEMKIN file has five
# wrong DUPLICATE flags (see fixtures/rmg_formic_acid_wu2026/PROVENANCE.md) and
# no transport file.

# Reactions by normalized form, in and out. 3002 parsed rows become 2986 records
# because 14 DUPLICATE groups of Arrhenius rows collapse to one multi_arrhenius
# record each.
_FORMIC_FORMS = {
    "chebyshev": 1674,
    "modified_arrhenius": 653,
    "arrhenius": 622,
    "multi_arrhenius": 14,
    "plog": 11,
    "troe": 8,
    "lindemann": 4,
}

# The five wrong flags, as lines of chem_annotated.inp: reactions 223/313 and
# 387/389 are repeated without DUPLICATE; reaction 412 (line 3449) has a DUPLICATE
# (line 3459) and no partner.
_UNDECLARED_PAIRS = [(1976, 2660), (3262, 3284)]
_ORPHAN = (3449, 3459)


def _structure_key(res, name):
    return (_canon(res[name].smiles), res[name].multiplicity)


def _rel(a, b):
    if a is None and b is None:
        return 0.0
    assert a is not None and b is not None, (a, b)
    return 0.0 if a == b else abs(a - b) / max(abs(a), 1e-300)


def _group_by_key(normalized, resolved):
    out: dict[tuple, list] = {}
    for nr in normalized.reactions:
        key = (
            tuple(sorted(_structure_key(resolved, n) for n in nr.reactant_names)),
            tuple(sorted(_structure_key(resolved, n) for n in nr.product_names)),
            nr.reversible,
        )
        out.setdefault(key, []).append(nr)
    return out


def test_formic_model_size_matches_the_authors_cantera_file():
    yaml = pytest.importorskip("yaml")
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    doc = yaml.load((FORMIC.dir / "chem_annotated.yaml").read_text(), Loader=loader)
    assert len(doc["species"]) == 123
    assert len(doc["reactions"]) == 3002
    assert sum(1 for r in doc["reactions"] if r.get("duplicate")) == 114


def test_formic_species_counts_and_nasa_map(formic_round_trip):
    rt = formic_round_trip
    res_in, res_out = rt["resolved_in"], rt["resolved_out"]
    assert len(res_in) == len(res_out) == 123
    # Same structure and spin, and no two names collapsed onto one species.
    by_id_in = {_structure_key(res_in, n): n for n in res_in}
    by_id_out = {_structure_key(res_out, n): n for n in res_out}
    assert len(by_id_in) == len(by_id_out) == 123
    assert set(by_id_in) == set(by_id_out)

    # Two structures exist in two spin states (CH2 triplet/singlet, O triplet/
    # singlet); they stay distinct because the spin is read back from MULT=, not
    # guessed from the SMILES.
    states = Counter(smiles for smiles, _ in by_id_out)
    assert {smiles for smiles, n in states.items() if n > 1} == {_canon("[CH2]"), _canon("[O]")}

    # NASA map: each exported card belongs to the input species with the same
    # structure and spin, and the cards alone identify the species (so the
    # coefficient map agrees with the identity map).
    def card(e):
        return tuple(round(x, 9) for x in (*e.coeffs_low, *e.coeffs_high))

    cards_in = {card(e): n for n, e in rt["mech_in"].thermo.items()}
    assert len(cards_in) == len(rt["mech_in"].thermo) == 123
    assert len(rt["mech_out"].thermo) == 123
    worst = 0.0
    for name_out, e_out in rt["mech_out"].thermo.items():
        name_in = by_id_in[_structure_key(res_out, name_out)]
        e_in = rt["mech_in"].thermo[name_in]
        assert cards_in[card(e_out)] == name_in
        assert (e_out.t_low, e_out.t_common, e_out.t_high) == pytest.approx(
            (e_in.t_low, e_in.t_common, e_in.t_high)
        )
        for a, b in zip(
            (*e_in.coeffs_low, *e_in.coeffs_high), (*e_out.coeffs_low, *e_out.coeffs_high), strict=True
        ):
            worst = max(worst, _rel(a, b))
    assert worst <= 1e-9, f"largest NASA relative difference {worst:.3g}"


def test_formic_reactions_by_form_and_tolerances(formic_round_trip):
    rt = formic_round_trip
    assert len(rt["mech_in"].reactions) == 3002
    forms_in = Counter(r.model_kind for r in rt["normalized_in"].reactions)
    forms_out = Counter(r.model_kind for r in rt["normalized_out"].reactions)
    assert dict(forms_in) == _FORMIC_FORMS
    assert dict(forms_out) == _FORMIC_FORMS
    assert sum(forms_in.values()) == 2986

    kin_in = _group_by_key(rt["normalized_in"], rt["resolved_in"])
    kin_out = _group_by_key(rt["normalized_out"], rt["resolved_out"])
    assert set(kin_out) == set(kin_in)
    assert len(kin_in) == 2926

    worst: dict[str, dict[str, float]] = {}

    def note(form, what, value):
        d = worst.setdefault(form, {})
        d[what] = max(d.get(what, 0.0), value)

    def order(r):
        return (r.model_kind, r.a if r.a is not None else 0.0, r.n if r.n is not None else 0.0)

    for key, rows_in in kin_in.items():
        rows_out = kin_out[key]
        assert len(rows_in) == len(rows_out), key
        for a, b in zip(sorted(rows_in, key=order), sorted(rows_out, key=order), strict=True):
            assert a.model_kind == b.model_kind
            form = a.model_kind
            if a.a is not None:
                note(form, "a", _rel(a.a, b.a))
                note(form, "n", abs(a.n - b.n))
                assert a.a_units == b.a_units
            if a.reported_ea is not None:
                note(form, "ea", _rel(_ea_kj(a), _ea_kj(b)))
            if form == "chebyshev":
                ca, cb = a.chebyshev, b.chebyshev
                assert (ca.n_temperature, ca.n_pressure) == (cb.n_temperature, cb.n_pressure)
                for what in ("tmin_k", "tmax_k", "pmin_bar", "pmax_bar"):
                    note(form, what, _rel(getattr(ca, what), getattr(cb, what)))
                for ra, rb in zip(ca.coefficients, cb.coefficients, strict=True):
                    for x, y in zip(ra, rb, strict=True):
                        note(form, "coefficient", abs(x - y) / max(abs(x), 1.0))
            if form in ("troe", "lindemann"):
                fa, fb = a.falloff, b.falloff
                for what in ("low_a", "low_ea_kj_mol"):
                    note(form, what, _rel(getattr(fa, what), getattr(fb, what)))
                note(form, "low_n", abs(fa.low_n - fb.low_n))
                if form == "troe":
                    for what in ("troe_alpha", "troe_t3", "troe_t1", "troe_t2"):
                        note(form, what, _rel(getattr(fa, what), getattr(fb, what)))
                eff_a = {_structure_key(rt["resolved_in"], n): v for n, v in a.efficiencies.items()}
                eff_b = {_structure_key(rt["resolved_out"], n): v for n, v in b.efficiencies.items()}
                assert set(eff_a) == set(eff_b)
                for k, v in eff_a.items():
                    note(form, "efficiency", _rel(v, eff_b[k]))
            if form == "plog":
                assert len(a.plog) == len(b.plog)
                for pa, pb in zip(a.plog, b.plog, strict=True):
                    note(form, "plog_a", _rel(pa.a, pb.a))
            if form == "multi_arrhenius":
                assert len(a.arrhenius_entries) == len(b.arrhenius_entries)
                for ta, tb in zip(a.arrhenius_entries, b.arrhenius_entries, strict=True):
                    note(form, "term_a", _rel(ta.a, tb.a))
                    note(form, "term_ea", _rel(_ea_kj(ta), _ea_kj(tb)))

    # Largest differences after the trip. The exporter prints rates to a fixed
    # number of digits; that rounding is the only source of a nonzero entry.
    limits = {"a": 1e-4, "term_a": 1e-4, "plog_a": 1e-4, "low_a": 1e-4}
    for form, values in worst.items():
        for what, value in values.items():
            limit = limits.get(what, 1e-6)
            assert value <= limit, f"{form} {what}: {value:.3g} > {limit}"


def test_formic_import_reports_exactly_the_five_bad_flags(formic_round_trip):
    rt = formic_round_trip
    warnings = [w for w in rt["built"].warnings if "DUPLICATE" in w]
    assert len(warnings) == 3, warnings
    undeclared = sorted((w for w in warnings if "undeclared" in w), key=lambda w: w.split("lines ")[1])
    orphans = [w for w in warnings if "no partner" in w]
    assert len(undeclared) == 2 and len(orphans) == 1
    for (first, second), warning in zip(_UNDECLARED_PAIRS, undeclared, strict=True):
        assert f"(lines {first}, {second})" in warning
        assert f"line(s) {first}, {second} carry no DUPLICATE" in warning
    assert f"line {_ORPHAN[0]}, DUPLICATE on line {_ORPHAN[1]}" in orphans[0]
    # Nothing was changed to make the warnings go away: the model carries its
    # 111 markers and 3002 rows as written.
    assert sum(1 for r in rt["mech_in"].reactions if r.duplicate) == 111
    assert len(rt["mech_in"].reactions) == 3002


def test_formic_reported_lines_are_the_five_flags_in_the_file(formic_round_trip):
    lines = FORMIC.read("chem_annotated.inp").splitlines()
    equations = [
        "O(6)+CO3(212)(+M)<=>O2(2)+CO2(12)(+M)",
        "O(6)+CO3(212)(+M)<=>O2(2)+CO2(158)(+M)",
    ]
    for (first, second), equation in zip(_UNDECLARED_PAIRS, equations, strict=True):
        for ln in (first, second):
            assert lines[ln - 1].startswith(equation)
            assert lines[ln].strip().upper() not in ("DUPLICATE", "DUP")  # no marker after it
    assert lines[_ORPHAN[0] - 1].startswith("HOCH2O(35)(+M)<=>H(3)+HOCHO(1)(+M)")
    assert lines[_ORPHAN[1] - 1].strip().upper() in ("DUPLICATE", "DUP")


def test_formic_export_declares_the_pairs_and_leaves_the_orphan_unmarked(formic_round_trip):
    rt = formic_round_trip
    res_in, res_out = rt["resolved_in"], rt["resolved_out"]
    by_line = {r.line_no: r for r in rt["mech_in"].reactions}

    def rows_out_like(line):
        r = by_line[line]
        key = (
            sorted(_structure_key(res_in, n) for n in r.reactant_names),
            sorted(_structure_key(res_in, n) for n in r.product_names),
        )
        return [
            o
            for o in rt["mech_out"].reactions
            if (
                sorted(_structure_key(res_out, n) for n in o.reactant_names),
                sorted(_structure_key(res_out, n) for n in o.product_names),
            )
            == key
            and (o.chebyshev is not None) == (r.chebyshev is not None)
        ]

    for first, second in _UNDECLARED_PAIRS:
        rows = rows_out_like(first)
        assert len(rows) == 2, (first, len(rows))
        assert all(o.duplicate for o in rows)  # declared by the export
        assert rows_out_like(second)[0] in rows

    orphan = rows_out_like(_ORPHAN[0])
    assert len(orphan) == 1
    assert orphan[0].duplicate is False  # the marker without a partner is not written

    # The export, read by the adapter, has no wrong flag left to report; it
    # states one DUPLICATE per repeated row (146 lines).
    assert duplicate_flag_warnings(rt["mech_out"].reactions) == []
    text = rt["export"].files["chem.inp"]
    assert sum(1 for ln in text.splitlines() if ln.strip().upper() == "DUPLICATE") == 146


def test_formic_transport_is_absent_and_stated(formic_round_trip):
    export = formic_round_trip["export"]
    assert "tran.dat" not in export.files
    assert set(export.files) == {"chem.inp", "therm.dat"}
    assert export.transport["written"] is False
    assert "no exported species had transport data" in export.transport["reason"]
    assert not [g for g in export.gaps if g.kind == "transport"]


def test_formic_original_fails_cantera_strict_and_the_export_passes(formic_round_trip):
    """The model as published does not load; its export (two files) does."""
    with pytest.raises(ValueError, match="Cantera validation") as excinfo:
        validate_chemkin_mechanism({"chem.inp": FORMIC.read("chem_annotated.inp")})
    assert "uplicate" in str(excinfo.value)
    validate_chemkin_mechanism(formic_round_trip["export"].files)
