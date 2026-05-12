"""Seed TCKDB with a tiny demo dataset for hosted scientific read examples.

This script is opt-in: it does **nothing** unless invoked with ``--yes``.
It creates a small, chemically plausible but **scientifically arbitrary**
dataset so a freshly deployed TCKDB instance can return non-empty
results for chemistry-first ``/api/v1/scientific/*`` queries.

Loud warnings:

- The numeric values (energies, NASA coefficients, Arrhenius parameters,
  temperatures) are illustrative only. **Do not cite this data
  scientifically.**
- Every row is tagged with the marker ``note='TCKDB demo data'`` (where
  the model has a ``note`` column) so it can be audited or removed.
- The script is **not idempotent.** Re-running with ``--yes`` will create
  duplicate demo rows. Refuse to run twice in production.

Usage (run from ``backend/`` with PYTHONPATH set so ``app.*`` resolves)::

    cd backend
    PYTHONPATH=. conda run -n tckdb_env python scripts/seed_scientific_demo_data.py --help
    PYTHONPATH=. conda run -n tckdb_env python scripts/seed_scientific_demo_data.py --yes

After loading, verify the data via ``tckdb-client``::

    python clients/python/tckdb-client/examples/scientific_reads.py \\
        --base-url http://127.0.0.1:8000/api/v1 --smiles "CH4"

Removing the demo data is a manual SQL cleanup; see
``docs/guides/scientific_read_demo_data.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.config import settings
from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import (
    ArrheniusAUnits,
    CalculationGeometryRole,
    CalculationType,
    KineticsModelKind,
    MoleculeKind,
    ReactionRole,
    ScientificOriginKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
    ValidationStatus,
)
from app.db.models.geometry import Geometry
from app.db.models.kinetics import Kinetics
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionParticipant,
)
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.thermo import Thermo, ThermoNASA

DEMO_NOTE = "TCKDB demo data"


# ---------------------------------------------------------------------------
# Tiny inline factories
# ---------------------------------------------------------------------------


_HASH_CTR = 0


def _next_hash(prefix: str) -> str:
    """Generate a unique 27/64-char hash-shaped string for demo rows."""
    global _HASH_CTR
    _HASH_CTR += 1
    raw = f"{prefix}-{_HASH_CTR}".encode()
    return hashlib.sha256(raw).hexdigest()


def _make_species(
    session: Session,
    *,
    smiles: str,
    inchi_key_seed: str,
    multiplicity: int = 1,
    charge: int = 0,
) -> Species:
    inchi_key = (
        hashlib.sha256(inchi_key_seed.encode()).hexdigest()[:14].upper()
        + "-XXXXXXXXXX-N"
    )[:27]
    species = Species(
        kind=MoleculeKind.molecule,
        smiles=smiles,
        inchi_key=inchi_key,
        charge=charge,
        multiplicity=multiplicity,
        stereo_kind=StereoKind.achiral,
    )
    session.add(species)
    session.flush()
    return species


def _make_entry(
    session: Session,
    species: Species,
    *,
    kind: StationaryPointKind = StationaryPointKind.minimum,
    state: SpeciesEntryStateKind = SpeciesEntryStateKind.ground,
) -> SpeciesEntry:
    entry = SpeciesEntry(
        species_id=species.id,
        kind=kind,
        electronic_state_kind=state,
    )
    session.add(entry)
    session.flush()
    return entry


def _make_lot(
    session: Session, *, method: str, basis: str | None
) -> LevelOfTheory:
    raw = f"{method}|{basis or ''}".encode()
    lot = LevelOfTheory(
        method=method,
        basis=basis,
        lot_hash=hashlib.sha256(raw).hexdigest(),
    )
    session.add(lot)
    session.flush()
    return lot


def _make_calc(
    session: Session,
    *,
    type_: CalculationType,
    species_entry_id: int,
    lot_id: int | None = None,
    conformer_observation_id: int | None = None,
) -> Calculation:
    calc = Calculation(
        type=type_,
        species_entry_id=species_entry_id,
        lot_id=lot_id,
        conformer_observation_id=conformer_observation_id,
    )
    session.add(calc)
    session.flush()
    return calc


def _attach_sp(session: Session, calc: Calculation, *, energy: float) -> None:
    session.add(
        CalculationSPResult(
            calculation_id=calc.id, electronic_energy_hartree=energy
        )
    )
    session.flush()


def _attach_opt(session: Session, calc: Calculation, *, energy: float) -> None:
    session.add(
        CalculationOptResult(
            calculation_id=calc.id, final_energy_hartree=energy, converged=True
        )
    )
    session.flush()


def _make_geometry(session: Session, *, natoms: int) -> Geometry:
    g = Geometry(natoms=natoms, geom_hash=_next_hash("geom"))
    session.add(g)
    session.flush()
    return g


def _attach_output_geom(
    session: Session,
    *,
    calc: Calculation,
    geom: Geometry,
    role: CalculationGeometryRole = CalculationGeometryRole.final,
    output_order: int = 1,
) -> None:
    session.add(
        CalculationOutputGeometry(
            calculation_id=calc.id,
            geometry_id=geom.id,
            output_order=output_order,
            role=role,
        )
    )
    session.flush()


def _attach_geom_validation(
    session: Session, *, calc: Calculation, smiles: str
) -> None:
    session.add(
        CalculationGeometryValidation(
            calculation_id=calc.id,
            validation_status=ValidationStatus.passed,
            species_smiles=smiles,
            is_isomorphic=True,
        )
    )
    session.flush()


def _make_thermo_scalar(
    session: Session,
    *,
    entry: SpeciesEntry,
    h298: float,
    s298: float,
    origin: ScientificOriginKind = ScientificOriginKind.computed,
) -> Thermo:
    t = Thermo(
        species_entry_id=entry.id,
        scientific_origin=origin,
        h298_kj_mol=h298,
        s298_j_mol_k=s298,
        note=DEMO_NOTE,
    )
    session.add(t)
    session.flush()
    return t


def _attach_nasa(session: Session, thermo: Thermo) -> None:
    session.add(
        ThermoNASA(
            thermo_id=thermo.id,
            t_low=200.0,
            t_mid=1000.0,
            t_high=6000.0,
            a1=3.5, a2=1e-4, a3=0.0, a4=0.0, a5=0.0, a6=-1000.0, a7=4.0,
            b1=3.2, b2=2e-4, b3=0.0, b4=0.0, b5=0.0, b6=-950.0, b7=5.0,
        )
    )
    session.flush()


def _make_chem_reaction(
    session: Session, *, reactants: list[Species], products: list[Species]
) -> ChemReaction:
    """Create a ChemReaction with stoichiometry-aggregated participants.

    The ``reaction_participant`` table has a primary key over
    ``(reaction_id, species_id, role)``, so a duplicate species on the
    same side (e.g. ``CH3 + CH3 -> C2H6``) must be expressed as a single
    row with ``stoichiometry=2``, not as two rows. Callers can pass the
    duplicate species directly — this helper aggregates them.
    """
    from collections import Counter

    rxn = ChemReaction(reversible=True)
    session.add(rxn)
    session.flush()

    reactant_counts = Counter(sp.id for sp in reactants)
    product_counts = Counter(sp.id for sp in products)

    for species_id, count in reactant_counts.items():
        session.add(
            ReactionParticipant(
                reaction_id=rxn.id,
                species_id=species_id,
                role=ReactionRole.reactant,
                stoichiometry=count,
            )
        )
    for species_id, count in product_counts.items():
        session.add(
            ReactionParticipant(
                reaction_id=rxn.id,
                species_id=species_id,
                role=ReactionRole.product,
                stoichiometry=count,
            )
        )
    session.flush()
    return rxn


def _make_reaction_entry(
    session: Session,
    *,
    reaction: ChemReaction,
    reactant_entries: list[SpeciesEntry],
    product_entries: list[SpeciesEntry],
) -> ReactionEntry:
    entry = ReactionEntry(reaction_id=reaction.id)
    session.add(entry)
    session.flush()
    for idx, se in enumerate(reactant_entries, start=1):
        session.add(
            ReactionEntryStructureParticipant(
                reaction_entry_id=entry.id,
                species_entry_id=se.id,
                role=ReactionRole.reactant,
                participant_index=idx,
            )
        )
    for idx, se in enumerate(product_entries, start=1):
        session.add(
            ReactionEntryStructureParticipant(
                reaction_entry_id=entry.id,
                species_entry_id=se.id,
                role=ReactionRole.product,
                participant_index=idx,
            )
        )
    session.flush()
    return entry


def _make_kinetics(
    session: Session,
    *,
    reaction_entry: ReactionEntry,
    a: float,
    n: float | None,
    ea_kj_mol: float,
    origin: ScientificOriginKind = ScientificOriginKind.computed,
    tmin: float = 300.0,
    tmax: float = 2000.0,
) -> Kinetics:
    k = Kinetics(
        reaction_entry_id=reaction_entry.id,
        scientific_origin=origin,
        model_kind=KineticsModelKind.modified_arrhenius,
        a=a,
        a_units=ArrheniusAUnits.cm3_molecule_s,
        n=n,
        ea_kj_mol=ea_kj_mol,
        tmin_k=tmin,
        tmax_k=tmax,
        note=DEMO_NOTE,
    )
    session.add(k)
    session.flush()
    return k


def _make_conformer_for(
    session: Session, entry: SpeciesEntry, *, label: str
) -> ConformerObservation:
    group = ConformerGroup(species_entry_id=entry.id, label=label, note=DEMO_NOTE)
    session.add(group)
    session.flush()
    obs = ConformerObservation(
        conformer_group_id=group.id,
        torsion_fingerprint_json={"demo": True},
        note=DEMO_NOTE,
    )
    session.add(obs)
    session.flush()
    return obs


# ---------------------------------------------------------------------------
# Demo dataset
# ---------------------------------------------------------------------------


def seed(session: Session) -> dict[str, int]:
    """Build the demo dataset. Returns a dict of created-row counts."""
    counts: dict[str, int] = {}

    # ---- Level of theory shared by computed records ----
    lot = _make_lot(session, method="wb97xd", basis="def2tzvp")
    counts["level_of_theory"] = 1

    # ---- Species + entries (5–10 species per spec) ----
    species_defs = [
        # (smiles, inchi seed, multiplicity, charge)
        ("[H]", "demo-H", 2, 0),
        ("[H][H]", "demo-H2", 1, 0),
        ("C", "demo-CH4", 1, 0),
        ("[CH3]", "demo-CH3", 2, 0),
        ("CC", "demo-C2H6", 1, 0),
        ("[CH2]C", "demo-C2H5", 2, 0),
    ]
    species_by_smiles: dict[str, Species] = {}
    entry_by_smiles: dict[str, SpeciesEntry] = {}
    for smiles, seed_id, mult, ch in species_defs:
        sp = _make_species(
            session,
            smiles=smiles,
            inchi_key_seed=seed_id,
            multiplicity=mult,
            charge=ch,
        )
        species_by_smiles[smiles] = sp
        entry_by_smiles[smiles] = _make_entry(session, sp)
    counts["species"] = len(species_defs)
    counts["species_entry"] = len(species_defs)

    # ---- Thermo records (2–3) ----
    # Scalar-only for CH4 + scalar+NASA for C2H6 + a points-shaped one is
    # skipped to keep the script short; the goal is variety, not coverage.
    thermo_ch4 = _make_thermo_scalar(
        session, entry=entry_by_smiles["C"], h298=-74.6, s298=186.3
    )
    thermo_c2h6 = _make_thermo_scalar(
        session, entry=entry_by_smiles["CC"], h298=-83.8, s298=229.6
    )
    _attach_nasa(session, thermo_c2h6)
    counts["thermo"] = 2
    counts["thermo_nasa"] = 1

    # ---- Calculations (3–5) attached to species entries ----
    geom_for_ch4 = _make_geometry(session, natoms=5)
    geom_for_c2h6 = _make_geometry(session, natoms=8)
    counts["geometry"] = 2

    # CH4: opt + SP at the same LoT, both with energies populated.
    ch4_opt = _make_calc(
        session,
        type_=CalculationType.opt,
        species_entry_id=entry_by_smiles["C"].id,
        lot_id=lot.id,
    )
    _attach_opt(session, ch4_opt, energy=-40.5180)
    _attach_output_geom(session, calc=ch4_opt, geom=geom_for_ch4)
    _attach_geom_validation(session, calc=ch4_opt, smiles="C")

    ch4_sp = _make_calc(
        session,
        type_=CalculationType.sp,
        species_entry_id=entry_by_smiles["C"].id,
        lot_id=lot.id,
    )
    _attach_sp(session, ch4_sp, energy=-40.5183)

    # C2H6: opt with conformer context.
    c2h6_obs = _make_conformer_for(session, entry_by_smiles["CC"], label="anti")
    c2h6_opt = _make_calc(
        session,
        type_=CalculationType.opt,
        species_entry_id=entry_by_smiles["CC"].id,
        lot_id=lot.id,
        conformer_observation_id=c2h6_obs.id,
    )
    _attach_opt(session, c2h6_opt, energy=-79.831)
    _attach_output_geom(session, calc=c2h6_opt, geom=geom_for_c2h6)

    # CH3 radical: opt for completeness.
    ch3_opt = _make_calc(
        session,
        type_=CalculationType.opt,
        species_entry_id=entry_by_smiles["[CH3]"].id,
        lot_id=lot.id,
    )
    _attach_opt(session, ch3_opt, energy=-39.838)

    counts["calculation"] = 4
    counts["conformer_observation"] = 1

    # ---- Reactions (2–3) + reaction entries ----
    # Reaction 1: CH3 + H2 -> CH4 + H (computed)
    rxn_1 = _make_chem_reaction(
        session,
        reactants=[species_by_smiles["[CH3]"], species_by_smiles["[H][H]"]],
        products=[species_by_smiles["C"], species_by_smiles["[H]"]],
    )
    rxn_1_entry = _make_reaction_entry(
        session,
        reaction=rxn_1,
        reactant_entries=[entry_by_smiles["[CH3]"], entry_by_smiles["[H][H]"]],
        product_entries=[entry_by_smiles["C"], entry_by_smiles["[H]"]],
    )

    # Reaction 2: CH3 + CH3 -> C2H6 (experimental — non-TS-backed)
    rxn_2 = _make_chem_reaction(
        session,
        reactants=[species_by_smiles["[CH3]"], species_by_smiles["[CH3]"]],
        products=[species_by_smiles["CC"]],
    )
    rxn_2_entry = _make_reaction_entry(
        session,
        reaction=rxn_2,
        reactant_entries=[entry_by_smiles["[CH3]"], entry_by_smiles["[CH3]"]],
        product_entries=[entry_by_smiles["CC"]],
    )

    counts["chem_reaction"] = 2
    counts["reaction_entry"] = 2

    # ---- Kinetics (2–3); one TS-backed-style (computed), one experimental ----
    _make_kinetics(
        session,
        reaction_entry=rxn_1_entry,
        a=1.2e-12,
        n=2.1,
        ea_kj_mol=51.0,
        origin=ScientificOriginKind.computed,
    )
    _make_kinetics(
        session,
        reaction_entry=rxn_2_entry,
        a=1.0e-11,
        n=0.0,
        ea_kj_mol=0.0,
        origin=ScientificOriginKind.experimental,
        tmin=298.0,
        tmax=1500.0,
    )
    counts["kinetics"] = 2

    return counts


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Required: actually write demo rows to the database configured "
            "by app.api.config.settings.database_url. Without this flag "
            "the script exits without writing anything."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "Override the database URL. Default: app.api.config.settings.database_url. "
            "Use to point the script at a non-production demo instance."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    db_url = args.database_url or settings.database_url
    print(f"TCKDB scientific demo seed")
    print(f"  database_url: {db_url}")
    print(f"  marker note : {DEMO_NOTE!r}")
    print(f"  timestamp   : {datetime.now(timezone.utc).isoformat()}")

    if not args.yes:
        print(
            "\nDry run — no rows written. Pass --yes to actually load demo data.",
            file=sys.stderr,
        )
        return 0

    print(
        "\nWARNING: this script is NOT idempotent. Re-running will create\n"
        "         duplicate demo rows. Numeric values are illustrative only.\n"
    )

    engine = create_engine(db_url, future=True)
    try:
        with Session(engine) as session:
            with session.begin():
                counts = seed(session)
    finally:
        engine.dispose()

    print("Created rows:")
    for table, n in sorted(counts.items()):
        print(f"  {table:<24s} {n}")
    print("\nVerify by running the runnable example, e.g.:")
    print(
        "  python clients/python/tckdb-client/examples/scientific_reads.py"
        " --base-url http://127.0.0.1:8000/api/v1 --smiles 'C'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
