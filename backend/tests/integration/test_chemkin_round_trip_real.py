"""Full CHEMKIN round-trip on a **real RMG mechanism** (ammonia-methane system).

Companion to ``test_chemkin_round_trip.py`` (hand-authored fixture). This one
drives the same importer -> persist -> export -> re-parse pipeline against an
unmodified RMG output (``fixtures/rmg_ammonia_methane/``): 21 species, 64
reactions exercising modified Arrhenius, Troe falloff + collider efficiencies,
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
from pathlib import Path

import pytest
from rdkit import Chem

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

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "rmg_ammonia_methane"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


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


def _import_payloads():
    mech = parse_mechanism(_read("chem.inp"))
    mech.transport = parse_transport_file(_read("tran.dat"))
    resolver = IdentityResolver(
        rmg_dict=parse_species_dictionary(_read("species_dictionary.txt"))
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


@pytest.fixture
def round_trip(db_session):
    mech_in, resolved_in, normalized_in, built = _import_payloads()
    species_refs, reaction_refs = _persist_and_approve(db_session, built)
    rs, export = _export_chemkin(db_session, species_refs, reaction_refs)

    blocking = [g for g in export.gaps if g.kind in ("thermo_nasa", "kinetics")]
    assert blocking == [], f"unexpected export gaps: {blocking}"

    mech_out = parse_mechanism(
        export.files["chem.inp"], thermo_text=export.files["therm.dat"]
    )
    mech_out.transport = parse_transport_file(export.files["tran.dat"])
    resolver_out = IdentityResolver()  # rely on inline SMILES comments
    resolved_out = resolver_out.resolve_mechanism(mech_out)
    normalized_out = normalize_mechanism(mech_out)

    return {
        "mech_in": mech_in,
        "resolved_in": resolved_in,
        "normalized_in": normalized_in,
        "mech_out": mech_out,
        "resolved_out": resolved_out,
        "normalized_out": normalized_out,
        "record_set": rs,
        "export": export,
    }


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
    return {
        _canon(resolved[name].smiles): entry
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


def test_species_set_preserved_by_structure(round_trip):
    smiles_in = {_canon(s.smiles) for s in round_trip["resolved_in"].values()}
    smiles_out = {_canon(s.smiles) for s in round_trip["resolved_out"].values()}
    assert smiles_out == smiles_in
    assert len(smiles_in) == 21


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


def test_nasa_high_low_not_swapped_water(round_trip):
    """H2O's fixture card has distinct high/low leading coefficients; a swap in
    either adapter would surface as the wrong block in the high interval."""
    h2o = _canon("O")
    e_in = _thermo_by_smiles(round_trip["mech_in"], round_trip["resolved_in"])[h2o]
    e_out = _thermo_by_smiles(round_trip["mech_out"], round_trip["resolved_out"])[h2o]
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


def test_modified_arrhenius_round_trips(round_trip):
    in_by = _rxn_by_key(round_trip["normalized_in"], round_trip["resolved_in"])
    out_by = _rxn_by_key(round_trip["normalized_out"], round_trip["resolved_out"])

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


def test_troe_falloff_round_trips_with_efficiencies(round_trip):
    in_by = _rxn_by_key(round_trip["normalized_in"], round_trip["resolved_in"])
    out_by = _rxn_by_key(round_trip["normalized_out"], round_trip["resolved_out"])

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

    e_in = eff_by_smiles(t_in, round_trip["resolved_in"])
    e_out = eff_by_smiles(t_out, round_trip["resolved_out"])
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


def test_chebyshev_round_trips(round_trip):
    in_by = _rxn_by_key(round_trip["normalized_in"], round_trip["resolved_in"])
    out_by = _rxn_by_key(round_trip["normalized_out"], round_trip["resolved_out"])

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


def test_all_chebyshev_reactions_round_trip(round_trip):
    """All 9 RMG Chebyshev reactions come back as Chebyshev (guards the
    (+M)+dummy-main-line handling on the whole set, not just one)."""
    n_in = sum(
        1 for r in round_trip["normalized_in"].reactions if r.model_kind == "chebyshev"
    )
    n_out = sum(
        1 for r in round_trip["normalized_out"].reactions if r.model_kind == "chebyshev"
    )
    assert n_in == 9
    assert n_out == n_in


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def test_transport_round_trips(round_trip):
    orig = _transport_by_smiles(round_trip["mech_in"], round_trip["resolved_in"])
    back = _transport_by_smiles(round_trip["mech_out"], round_trip["resolved_out"])
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


def test_duplicate_reactions_round_trip(round_trip):
    """RMG marks 3 reaction identities as DUPLICATE (2 rate rows each = 6
    lines). Each rate persists as its own append-only reaction entry, so all
    64 reaction lines round-trip; the exporter re-emits the ``DUPLICATE``
    keyword on the repeated equations (a CHEMKIN-validity requirement) so they
    come back marked, not silently merged.
    """
    n_in = round_trip["normalized_in"].reactions
    n_out = round_trip["normalized_out"].reactions
    assert len(n_in) == 64
    assert len(n_out) == 64

    # 3 duplicate pairs -> 6 DUPLICATE-marked lines, preserved both ways.
    assert sum(1 for r in n_in if r.duplicate) == 6
    assert sum(1 for r in n_out if r.duplicate) == 6

    # 61 distinct reaction identities (3 pairs share a key); the full set of
    # identities survives the trip.
    in_keys = _rxn_by_key(round_trip["normalized_in"], round_trip["resolved_in"])
    out_keys = _rxn_by_key(round_trip["normalized_out"], round_trip["resolved_out"])
    assert len(in_keys) == 61
    assert set(out_keys) == set(in_keys)


# ---------------------------------------------------------------------------
# Cantera-load validation: the exported mechanism must be a file a downstream
# interpreter actually accepts. This is the authoritative guard (ground truth)
# against handing a user a broken mechanism — in particular one with an
# undeclared duplicate reaction, which real CHEMKIN files frequently ship.
# ---------------------------------------------------------------------------

cantera = pytest.importorskip("cantera")


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

