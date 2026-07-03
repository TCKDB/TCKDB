"""End-to-end CHEMKIN round-trip interoperability test.

This is the headline test that guards the two *separately-packaged* CHEMKIN
adapters against silent drift:

* the **importer** (client adapter, ``clients/python/adapters/chemkin`` —
  ``parser`` / ``normalizer`` / ``identity`` / ``payloads``), and
* the **exporter** (backend ``app/services/scientific_read/export.py`` +
  ``chemkin_serialize.py``).

Flow: CHEMKIN text -> importer payloads -> real Pydantic upload schemas ->
backend persist workflows -> test DB -> backend export service + CHEMKIN
serializer -> CHEMKIN text -> importer parser again -> diff.

The single most load-bearing assertion is the NASA a/b coefficient
convention: the high-temperature block must come back in the *high*
temperature interval (not swapped with the low block). A naive per-adapter
test cannot catch a convention that is flipped consistently in only one of
the two adapters; only a full round trip can.

Covered forms: modified Arrhenius, Troe falloff + collider efficiencies,
PLOG, Chebyshev, NASA-7 thermo, transport. The simple third-body (A+B+M)
case is deliberately excluded (see the skipped placeholder at the bottom).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdkit import Chem

# The CHEMKIN importer adapter (``tckdb_chemkin``) lives under
# clients/python/adapters/chemkin and is not installed into tckdb_env, so its
# package root is prepended to sys.path here — inline (rather than in a
# tests/integration/conftest.py) to avoid a bare-``conftest`` module-name
# collision with tests that do ``from conftest import ...``. Only the pure
# stages are used; the network ``uploader`` stage is never imported.
_CHEMKIN_ADAPTER = (
    Path(__file__).resolve().parents[3] / "clients" / "python" / "adapters" / "chemkin"
)
if _CHEMKIN_ADAPTER.is_dir() and str(_CHEMKIN_ADAPTER) not in sys.path:
    # APPEND (not insert(0)): this directory also contains its own ``tests``
    # package, and inserting it at the front would shadow the backend ``tests``
    # package for the rest of the session, breaking other tests that do
    # ``from tests... import``. Appending keeps backend ``tests`` first while
    # still making the unique ``tckdb_chemkin`` package importable.
    sys.path.append(str(_CHEMKIN_ADAPTER))

from tckdb_chemkin.identity import (
    IdentityResolver,
    parse_species_dictionary,
)
from tckdb_chemkin.normalizer import normalize_mechanism
from tckdb_chemkin.parser import parse_mechanism
from tckdb_chemkin.payloads import ImportConfig, build_all_payloads
from tckdb_chemkin.transport import parse_transport_file

# --- backend upload schemas + persist workflows
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transport_upload import TransportUploadRequest

# --- backend export service + CHEMKIN serializer
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

# --- test helpers reused from the export service test-suite
from tests.services.scientific_read._factories import set_review

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


def _canon(smiles: str) -> str:
    """Canonical SMILES for structure-based comparison across the round trip."""
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None, f"unparseable SMILES {smiles!r}"
    return Chem.MolToSmiles(mol)


# ---------------------------------------------------------------------------
# Fixtures: import -> persist -> export -> re-parse, done once per test module.
# ---------------------------------------------------------------------------


def _import_payloads():
    """Stage 1-4 of the importer: CHEMKIN text -> TCKDB upload payload dicts."""
    mech = parse_mechanism(_read("mech.inp"), thermo_text=_read("therm.dat"))
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
    """Validate every payload through the real Pydantic upload schema, persist
    it via the backend workflow, and approve it so the default export trust
    filter (``min_review_status=approved``) includes it.

    Returns the sets of species-entry and reaction-entry public refs to seed
    the export with.
    """
    species_entry_refs: set[str] = set()
    reaction_entry_refs: set[str] = set()

    # Species-carrying records first (thermo, transport) so the reaction
    # uploads reuse the already-resolved species entries — mirrors the
    # importer's uploader ordering.
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
    """Backend export + CHEMKIN serialization for the imported mechanism.

    Seeds both the reactions and every species explicitly so third-body
    colliders that are not reaction participants (e.g. the bath gas) are part
    of the closure and therefore get a mechanism name. ``collider_names`` is
    reconstructed with the serializer's own naming helper so efficiency lines
    reference the same names as the SPECIES declarations (otherwise colliders
    fall back to a bare species id and cannot be re-parsed).
    """
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
    """Full pipeline; exposes both the original and re-parsed mechanisms."""
    mech_in, resolved_in, normalized_in, built = _import_payloads()
    species_refs, reaction_refs = _persist_and_approve(db_session, built)
    rs, export = _export_chemkin(db_session, species_refs, reaction_refs)

    # No serialization gaps for a fully-populated mechanism (every species
    # has approved NASA thermo + transport, every reaction approved kinetics).
    blocking = [g for g in export.gaps if g.kind in ("thermo_nasa", "kinetics")]
    assert blocking == [], f"unexpected export gaps: {blocking}"

    # Stage 1-3 of the importer, run again on the *exported* text. Identity is
    # resolved purely from the `! SMILES=` traceability comments the exporter
    # writes on every SPECIES declaration (no external structure map needed).
    mech_out = parse_mechanism(export.files["chem.inp"], thermo_text=export.files["therm.dat"])
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
# Helpers to index reactions/thermo/transport by structure across the trip.
# ---------------------------------------------------------------------------


def _rxn_key(nr, resolved):
    reactants = tuple(sorted(_canon(resolved[n].smiles) for n in nr.reactant_names))
    products = tuple(sorted(_canon(resolved[n].smiles) for n in nr.product_names))
    return (reactants, products, nr.reversible)


def _rxn_by_key(normalized, resolved):
    return {_rxn_key(nr, resolved): nr for nr in normalized.reactions}


def _thermo_by_smiles(mech, resolved):
    out = {}
    for name, entry in mech.thermo.items():
        if name in resolved:
            out[_canon(resolved[name].smiles)] = entry
    return out


def _transport_by_smiles(mech, resolved):
    out = {}
    for name, entry in mech.transport.items():
        if name in resolved:
            out[_canon(resolved[name].smiles)] = entry
    return out


# ---------------------------------------------------------------------------
# Species identity
# ---------------------------------------------------------------------------


def test_species_set_preserved_by_structure(round_trip):
    """Every imported species round-trips back by canonical SMILES."""
    smiles_in = {_canon(s.smiles) for s in round_trip["resolved_in"].values()}
    smiles_out = {_canon(s.smiles) for s in round_trip["resolved_out"].values()}
    assert smiles_out == smiles_in
    # Sanity: the mechanism actually carries the 9 fixture species.
    assert len(smiles_in) == 9


# ---------------------------------------------------------------------------
# NASA thermo — the coefficient-convention guard
# ---------------------------------------------------------------------------


def test_nasa_thermo_round_trips_in_the_same_intervals(round_trip):
    """Both 7-coefficient blocks and all three temperatures round-trip, and —
    critically — the HIGH-temperature block stays HIGH (a=LOW / b=HIGH is the
    exact convention that was just fixed in both adapters)."""
    orig = _thermo_by_smiles(round_trip["mech_in"], round_trip["resolved_in"])
    back = _thermo_by_smiles(round_trip["mech_out"], round_trip["resolved_out"])

    assert set(back) == set(orig)

    for smiles, e_in in orig.items():
        e_out = back[smiles]
        assert e_out.t_low == pytest.approx(e_in.t_low)
        assert e_out.t_common == pytest.approx(e_in.t_common)
        assert e_out.t_high == pytest.approx(e_in.t_high)
        assert e_out.coeffs_high == pytest.approx(e_in.coeffs_high, rel=1e-9)
        assert e_out.coeffs_low == pytest.approx(e_in.coeffs_low, rel=1e-9)


def test_nasa_high_low_blocks_not_swapped_ch4(round_trip):
    """Explicit non-swap guard on CH4, whose fixture card seeds the HIGH-T
    block with 15.xx and the LOW-T block with 16.xx. If a/b were flipped in
    either adapter, the exported high block would come back as 16.xx."""
    ch4 = _canon("C")
    e_in = _thermo_by_smiles(round_trip["mech_in"], round_trip["resolved_in"])[ch4]
    e_out = _thermo_by_smiles(round_trip["mech_out"], round_trip["resolved_out"])[ch4]

    # HIGH-temperature interval: the 15.xx block, in both directions.
    assert e_in.coeffs_high[0] == pytest.approx(15.0)
    assert e_out.coeffs_high[0] == pytest.approx(15.0)
    assert e_out.coeffs_high[6] == pytest.approx(15.06)
    # LOW-temperature interval: the 16.xx block.
    assert e_in.coeffs_low[0] == pytest.approx(16.0)
    assert e_out.coeffs_low[0] == pytest.approx(16.0)
    assert e_out.coeffs_low[6] == pytest.approx(16.06)


# ---------------------------------------------------------------------------
# Kinetics — Arrhenius, Troe falloff, PLOG, Chebyshev
# ---------------------------------------------------------------------------


def test_arrhenius_round_trips(round_trip):
    in_by = _rxn_by_key(round_trip["normalized_in"], round_trip["resolved_in"])
    out_by = _rxn_by_key(round_trip["normalized_out"], round_trip["resolved_out"])

    key = ((_canon("[H]"), _canon("[O][O]")), tuple(sorted((_canon("[O]"), _canon("[OH]")))), True)
    a_in, a_out = in_by[key], out_by[key]
    assert a_in.model_kind == a_out.model_kind == "arrhenius"
    assert a_out.a == pytest.approx(a_in.a, rel=1e-4)
    assert a_out.a_units == a_in.a_units == "cm3_mol_s"
    assert a_out.n == pytest.approx(a_in.n)
    # Ea travels kJ/mol through the DB and back to cal/mol on export.
    assert a_out.reported_ea == pytest.approx(a_in.reported_ea, rel=1e-4)
    assert a_out.reported_ea_units == a_in.reported_ea_units == "cal_mol"


def test_troe_falloff_round_trips_with_efficiencies(round_trip):
    in_by = _rxn_by_key(round_trip["normalized_in"], round_trip["resolved_in"])
    out_by = _rxn_by_key(round_trip["normalized_out"], round_trip["resolved_out"])

    key = ((_canon("[CH3]"), _canon("[H]")), (_canon("C"),), True)
    t_in, t_out = in_by[key], out_by[key]
    assert t_in.model_kind == t_out.model_kind == "troe"

    # k-inf Arrhenius on the main line.
    assert t_out.a == pytest.approx(t_in.a, rel=1e-4)
    assert t_out.n == pytest.approx(t_in.n)
    assert t_out.reported_ea == pytest.approx(t_in.reported_ea, rel=1e-4)

    # k0 (LOW) and the Troe broadening coefficients.
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

    # Third-body collider efficiencies, compared by collider structure.
    def eff_by_smiles(nr, resolved):
        return {_canon(resolved[n].smiles): v for n, v in nr.efficiencies.items()}

    e_in = eff_by_smiles(t_in, round_trip["resolved_in"])
    e_out = eff_by_smiles(t_out, round_trip["resolved_out"])
    assert set(e_out) == set(e_in)
    for smiles, val in e_in.items():
        assert e_out[smiles] == pytest.approx(val, rel=1e-3)
    # Spot-check the actual collider set (H2, H2O, Ar).
    assert e_in == pytest.approx(
        {_canon("[H][H]"): 2.0, _canon("O"): 6.0, _canon("[Ar]"): 0.7}
    )


def test_plog_round_trips(round_trip):
    in_by = _rxn_by_key(round_trip["normalized_in"], round_trip["resolved_in"])
    out_by = _rxn_by_key(round_trip["normalized_out"], round_trip["resolved_out"])

    key = (
        tuple(sorted((_canon("[OH]"), _canon("[H][H]")))),
        tuple(sorted((_canon("[H]"), _canon("O")))),
        True,
    )
    p_in, p_out = in_by[key], out_by[key]
    assert p_in.model_kind == p_out.model_kind == "plog"
    assert len(p_out.plog) == len(p_in.plog) == 3

    by_idx_in = {e.entry_index: e for e in p_in.plog}
    by_idx_out = {e.entry_index: e for e in p_out.plog}
    assert set(by_idx_out) == set(by_idx_in)
    for idx, e_in in by_idx_in.items():
        e_out = by_idx_out[idx]
        assert e_out.pressure_bar == pytest.approx(e_in.pressure_bar, rel=1e-4)
        assert e_out.a == pytest.approx(e_in.a, rel=1e-4)
        assert e_out.n == pytest.approx(e_in.n)
        assert e_out.ea_kj_mol == pytest.approx(e_in.ea_kj_mol, rel=1e-4)
        assert e_out.a_units == e_in.a_units


def test_chebyshev_round_trips(round_trip):
    in_by = _rxn_by_key(round_trip["normalized_in"], round_trip["resolved_in"])
    out_by = _rxn_by_key(round_trip["normalized_out"], round_trip["resolved_out"])

    key = (
        tuple(sorted((_canon("[H][H]"), _canon("[O][O]")))),
        (_canon("[OH]"), _canon("[OH]")),  # OH + OH -> two product participants
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
    assert cb_out.pmin_bar == pytest.approx(cb_in.pmin_bar, rel=1e-4)
    assert cb_out.pmax_bar == pytest.approx(cb_in.pmax_bar, rel=1e-4)
    assert len(cb_out.coefficients) == len(cb_in.coefficients)
    for row_in, row_out in zip(cb_in.coefficients, cb_out.coefficients, strict=True):
        assert row_out == pytest.approx(row_in, rel=1e-5)


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

    # Spot-check water's LJ parameters, dipole and polarizability explicitly.
    h2o = orig[_canon("O")]
    assert h2o.sigma_angstrom == pytest.approx(2.605)
    assert h2o.eps_over_k == pytest.approx(572.4)
    assert h2o.dipole_debye == pytest.approx(1.844)


# ---------------------------------------------------------------------------
# Regression guard: the NDJSON export path's NASA a/b labelling in
# ``SelectedThermo.to_dict`` must follow the authoritative convention
# (a=LOW, b=HIGH), consistent with the CHEMKIN serializer and the importer.
# The round-trip test originally surfaced this path swapping the blocks; the
# bug was fixed in export.py and this now guards against regression.
# ---------------------------------------------------------------------------


def test_ndjson_thermo_high_low_labels_match_chemkin_convention(round_trip):
    rs = round_trip["record_set"]
    ch4_smiles = _canon("C")
    sr = next(r for r in rs.species_records if _canon(r.species.smiles) == ch4_smiles)
    nasa = next(t for t in sr.thermos if t.model_kind == "nasa")
    d = nasa.to_dict()["nasa"]
    # Authoritative: high-T interval is the 15.xx block (b1..b7).
    assert d["high_coefficients"][0] == pytest.approx(15.0)
    assert d["low_coefficients"][0] == pytest.approx(16.0)


# ---------------------------------------------------------------------------
# Deliberately-excluded case: simple third body (A + B + M).
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "Simple third-body (A+B+M) round trip is intentionally out of scope: a "
        "backend fix for its Arrhenius A-units validation is landing separately. "
        "Enable this once that fix merges: import a bare-(+M) third-body "
        "reaction (e.g. 'O + O + M => O2 + M', order+1 A-units => cm6_mol2_s), "
        "persist it, export it, and assert the cm6_mol2_s A-factor and the (+M) "
        "third-body marker round-trip."
    )
)
def test_simple_third_body_round_trips():  # pragma: no cover - placeholder
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Cantera-load validation (belt-and-suspenders on the hand-authored fixture).
# Same authoritative guard as the real-mechanism suite: the exported CHEMKIN
# must load in Cantera (strict ck2yaml + Solution), not merely re-parse in our
# own lenient parser.
# ---------------------------------------------------------------------------

pytest.importorskip("cantera")


def test_handauthored_export_passes_cantera_validation(round_trip):
    """The hand-authored fixture's export must also be a file Cantera accepts —
    catches format regressions (NASA card columns, undeclared duplicates) that
    a parser->parser round trip cannot see."""
    validate_chemkin_mechanism(round_trip["export"].files)
