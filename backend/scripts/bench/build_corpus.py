"""Build a disposable, catalog-scale benchmark corpus (Stage 4).

The deployed database is tiny — 55 species — so no query shape in the read API
has ever been exercised at catalog scale. This script builds a **disposable**
``tckdb_bench_*`` database whose table cardinalities are scaled from the
*measured* production inventory, so a plan recorded against it says something
about the plan the deployment will eventually produce.

Usage
-----
::

    python -m bench.build_corpus --db tckdb_bench_s4 --species 50000
    python -m bench.build_corpus --db tckdb_bench_s4 --drop

Safety
------
The target database name must match ``tckdb_bench_[a-z0-9_]*``. Anything else
— ``tckdb_dev``, ``tckdb``, ``postgres``, a hosted name — is refused before a
connection is opened. The script only ever talks to the host in ``DB_HOST``,
which the caller must point at a local Postgres.

Measured production inventory (2026-07-30, 55 species) and the per-species
ratios derived from it are in :data:`PRODUCTION_INVENTORY`. Deviations from
those ratios are declared in :data:`DEVIATIONS` and are written into the
corpus manifest, so a number quoted from this corpus can always be traced back
to whether it was built at production ratio or not.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.molecules import BenchSpecies, generate_species

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Only these database names may be created, written or dropped.
_BENCH_DB_NAME = re.compile(r"^tckdb_bench_[a-z0-9_]+$")


# ---------------------------------------------------------------------------
# Production inventory — measured, not invented
# ---------------------------------------------------------------------------

#: Row counts measured on the deployed Raspberry Pi database on 2026-07-30.
#: Every ratio this script scales by is derived from these numbers; nothing
#: here is a guess. Tables absent from this dict were **not** measured, and the
#: assumption used in their place is recorded in :data:`DEVIATIONS`.
PRODUCTION_INVENTORY: dict[str, int] = {
    "species": 55,
    "species_entry": 56,
    "calculation": 460,
    "statmech": 77,
    "kinetics": 15,
    "network": 2,
    "network_channel": 42,
    "geometry_atom": 41207,
    "calc_hessian": 77,
    "calculation_artifact": 512,
    "record_review": 1069,
}

_PRODUCTION_SPECIES = PRODUCTION_INVENTORY["species"]


def production_ratio(table: str) -> float:
    """Rows of ``table`` per species on the deployed database."""
    return PRODUCTION_INVENTORY[table] / _PRODUCTION_SPECIES


@dataclass(frozen=True)
class Deviation:
    """A documented departure from the production ratio.

    ``effect`` must say which query shapes stop being representative. A
    deviation without a stated effect is exactly the "silently thin the data
    and report numbers as if it were full scale" failure this field exists to
    prevent.
    """

    table: str
    production_ratio: float | None
    built_ratio: float
    reason: str
    effect: str

    def as_dict(self) -> dict[str, object]:
        return {
            "table": self.table,
            "production_ratio_per_species": self.production_ratio,
            "built_ratio_per_species": self.built_ratio,
            "reason": self.reason,
            "effect_on_representativeness": self.effect,
        }


#: Review-state mix. Stage 3's curated profile filters on ``approved``, so the
#: corpus must contain a realistic minority of approved records rather than
#: making everything approved (which would make the curated profile free) or
#: nothing approved (which would make it return empty).
REVIEW_MIX: tuple[tuple[str, float], ...] = (
    ("not_reviewed", 0.42),
    ("under_review", 0.34),
    ("approved", 0.18),
    ("rejected", 0.04),
    ("deprecated", 0.02),
)


@dataclass
class CorpusPlan:
    """Row counts to build, derived from ``species_count`` and the ratios."""

    species_count: int
    geometry_fraction: float
    seed: int

    species_entry: int = 0
    calculation: int = 0
    statmech: int = 0
    thermo: int = 0
    transport: int = 0
    conformer_group: int = 0
    reaction_entry: int = 0
    kinetics: int = 0
    calculation_artifact: int = 0
    record_review: int = 0
    deviations: list[Deviation] = field(default_factory=list)

    def __post_init__(self) -> None:
        n = self.species_count
        self.species_entry = round(n * production_ratio("species_entry"))
        self.calculation = round(n * production_ratio("calculation"))
        self.statmech = round(n * production_ratio("statmech"))
        self.kinetics = round(n * production_ratio("kinetics"))
        self.calculation_artifact = round(n * production_ratio("calculation_artifact"))
        self.record_review = round(n * production_ratio("record_review"))

        # --- Tables the production inventory did not measure -------------
        # thermo, transport, conformer_group and reaction_entry were not in
        # the measured inventory. Rather than invent a ratio and present it as
        # production-derived, each is pinned to a *stated* assumption and
        # recorded as a deviation.
        self.thermo = round(n * 1.2)
        self.transport = round(n * 0.6)
        self.conformer_group = round(n * 1.0)
        self.reaction_entry = round(n * 0.30)

        for table, ratio, reason in (
            (
                "thermo",
                1.2,
                "not present in the measured production inventory; assumed "
                "slightly above one thermo record per species, mirroring the "
                "measured statmech ratio of 1.4",
            ),
            (
                "transport",
                0.6,
                "not present in the measured production inventory; assumed "
                "roughly half of species carry transport data",
            ),
            (
                "conformer_group",
                1.0,
                "not present in the measured production inventory; assumed "
                "one conformer group per species entry",
            ),
            (
                "reaction_entry",
                0.30,
                "not present in the measured production inventory; derived "
                "from the measured kinetics ratio (0.273/species) on the "
                "assumption that most reaction entries carry one kinetics "
                "record",
            ),
        ):
            self.deviations.append(
                Deviation(
                    table=table,
                    production_ratio=None,
                    built_ratio=ratio,
                    reason=reason,
                    effect=(
                        "absolute row count for this table is an assumption, "
                        "not a measurement; relative query cost within the "
                        "table is still exercised at the stated scale"
                    ),
                )
            )

        # --- The deliberate, declared reduction --------------------------
        full_geometry_atoms = n * production_ratio("geometry_atom")
        built_geometry_atoms = full_geometry_atoms * self.geometry_fraction
        self.deviations.append(
            Deviation(
                table="geometry_atom",
                production_ratio=production_ratio("geometry_atom"),
                built_ratio=production_ratio("geometry_atom") * self.geometry_fraction,
                reason=(
                    f"building the full production ratio at {n} species implies "
                    f"{full_geometry_atoms:,.0f} rows; the corpus builds "
                    f"{built_geometry_atoms:,.0f} ({self.geometry_fraction:.0%}) by "
                    "giving geometries to a random subset of species, at full "
                    "atoms-per-geometry fidelity for those it does build"
                ),
                effect=(
                    "NOT representative for: geometry detail reads, total "
                    "database size, and shared-buffer cache pressure. "
                    "REMAINS representative for: every benchmarked search "
                    "shape, none of which joins geometry_atom — species, "
                    "reaction, structure, thermo, kinetics, statmech, "
                    "calculation and analytics searches all read only "
                    "identity, product, provenance and review tables"
                ),
            )
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "species": self.species_count,
            "species_entry": self.species_entry,
            "calculation": self.calculation,
            "statmech": self.statmech,
            "thermo": self.thermo,
            "transport": self.transport,
            "conformer_group": self.conformer_group,
            "reaction_entry": self.reaction_entry,
            "kinetics": self.kinetics,
            "calculation_artifact": self.calculation_artifact,
            "record_review": self.record_review,
            "geometry_fraction": self.geometry_fraction,
            "seed": self.seed,
        }


#: Convenience alias used by the manifest writer.
DEVIATIONS = Deviation


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _env() -> dict[str, str]:
    return {
        "DB_USER": os.environ.get("DB_USER", "tckdb"),
        "DB_PASSWORD": os.environ.get("DB_PASSWORD", "tckdb"),
        "DB_HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "DB_PORT": os.environ.get("DB_PORT", "5432"),
    }


def _validate_db_name(name: str) -> str:
    if not _BENCH_DB_NAME.fullmatch(name):
        raise SystemExit(
            f"refusing to operate on database {name!r}: the benchmark builder "
            "only creates, writes and drops databases matching "
            "'tckdb_bench_<alnum_or_underscore>'."
        )
    return name


def _dsn(db_name: str) -> str:
    env = _env()
    # client_encoding is explicit: without it the server may negotiate
    # SQL_ASCII, and psycopg then hands text columns back as bytes rather than
    # str. The test conftest sets the same option for the same reason.
    return (
        f"host={env['DB_HOST']} port={env['DB_PORT']} user={env['DB_USER']} "
        f"password={env['DB_PASSWORD']} dbname={db_name} client_encoding=utf8"
    )


def _admin_connection() -> psycopg.Connection:
    return psycopg.connect(_dsn("postgres"), autocommit=True)


def drop_database(db_name: str) -> None:
    """Terminate backends and drop the benchmark database."""
    _validate_db_name(db_name)
    with _admin_connection() as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (db_name,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


def create_database(db_name: str) -> None:
    """Recreate the benchmark database and migrate it to the Alembic head."""
    _validate_db_name(db_name)
    drop_database(db_name)
    with _admin_connection() as conn:
        conn.execute(f'CREATE DATABASE "{db_name}"')

    env = os.environ.copy()
    env.update(_env())
    env["DB_NAME"] = db_name
    result = subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"alembic upgrade head failed for {db_name}:\n"
            f"{result.stdout}\n{result.returncode}\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Bulk loading
# ---------------------------------------------------------------------------


def _copy(conn: psycopg.Connection, table: str, columns: list[str], rows) -> int:
    """COPY ``rows`` into ``table``; returns the number of rows written."""
    column_sql = ", ".join(columns)
    written = 0
    with conn.cursor().copy(f"COPY {table} ({column_sql}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
            written += 1
    return written


def _weighted_choice(rng: random.Random, options: tuple[tuple[str, float], ...]) -> str:
    roll = rng.random()
    cumulative = 0.0
    for value, weight in options:
        cumulative += weight
        if roll < cumulative:
            return value
    return options[-1][0]


def _zipf_species_popularity(rng: random.Random, count: int) -> list[int]:
    """Draw a reaction participant with a realistic popularity skew.

    In a kinetics database a handful of species — OH, O2, H, HO2 — appear in a
    large fraction of all reactions, while most species appear in one or two.
    Sampling participants uniformly would hide exactly the cost that makes
    broad reaction search expensive, so participants are drawn from a
    Zipf-like distribution over species rank.
    """
    # Precompute a cumulative table once; sampling is then a bisect.
    weights = [1.0 / (rank + 1) ** 0.8 for rank in range(count)]
    total = sum(weights)
    cumulative: list[float] = []
    running = 0.0
    for weight in weights:
        running += weight / total
        cumulative.append(running)
    return cumulative


def build_corpus(db_name: str, plan: CorpusPlan, *, verbose: bool = True) -> dict:
    """Populate ``db_name`` and return the corpus manifest."""
    import bisect

    rng = random.Random(plan.seed)
    started = time.time()
    counts: dict[str, int] = {}

    def log(message: str) -> None:
        if verbose:
            elapsed = time.time() - started
            print(f"[{elapsed:7.1f}s] {message}", flush=True)

    log(f"generating {plan.species_count} RDKit molecules")
    molecules: list[BenchSpecies] = generate_species(
        plan.species_count, seed=plan.seed
    )
    log(f"generated {len(molecules)} distinct species identities")

    base_time = datetime(2024, 1, 1, 0, 0, 0)

    with psycopg.connect(_dsn(db_name)) as conn:
        conn.execute("SET synchronous_commit = off")

        # -- reviewer identity --------------------------------------------
        # ``ck_record_review_record_review_terminal_requires_reviewer``
        # requires approved/rejected/deprecated rows to name a reviewer, so a
        # curator account has to exist before the review overlay is loaded.
        log("loading curator accounts")
        counts["app_user"] = _copy(
            conn,
            "app_user",
            ["id", "username", "email", "password_hash", "role", "is_active", "created_at"],
            (
                (
                    uid,
                    f"bench_curator_{uid}",
                    f"bench-curator-{uid}@example.invalid",
                    # Not a valid hash for any password: the benchmark corpus
                    # must never contain an account that can actually log in.
                    "!bench-no-login",
                    "curator",
                    True,
                    base_time,
                )
                for uid in range(1, 6)
            ),
        )
        curator_ids = list(range(1, 6))
        conn.commit()

        # -- provenance dimension tables ---------------------------------
        log("loading provenance dimensions")
        software_names = ["Gaussian", "ORCA", "Molpro", "Psi4", "QChem", "TeraChem"]
        counts["software"] = _copy(
            conn,
            "software",
            ["id", "name", "created_at"],
            ((i + 1, name, base_time) for i, name in enumerate(software_names)),
        )
        software_release_ids = list(range(1, len(software_names) * 3 + 1))
        counts["software_release"] = _copy(
            conn,
            "software_release",
            ["id", "software_id", "version", "created_at"],
            (
                (rid, (rid - 1) // 3 + 1, f"{(rid - 1) % 3 + 16}.0", base_time)
                for rid in software_release_ids
            ),
        )

        workflow_names = ["ARC", "RMG", "AutoTST", "Sella"]
        counts["workflow_tool"] = _copy(
            conn,
            "workflow_tool",
            ["id", "name", "created_at"],
            ((i + 1, name, base_time) for i, name in enumerate(workflow_names)),
        )
        workflow_release_ids = list(range(1, len(workflow_names) * 2 + 1))
        counts["workflow_tool_release"] = _copy(
            conn,
            "workflow_tool_release",
            ["id", "workflow_tool_id", "version", "created_at"],
            (
                (rid, (rid - 1) // 2 + 1, f"1.{(rid - 1) % 2}.0", base_time)
                for rid in workflow_release_ids
            ),
        )

        methods = [
            ("CCSD(T)-F12", "cc-pVTZ-F12"), ("CCSD(T)", "cc-pVQZ"),
            ("B3LYP", "6-31G(d,p)"), ("B3LYP", "def2-TZVP"),
            ("wB97X-D", "def2-TZVP"), ("M06-2X", "cc-pVTZ"),
            ("CBS-QB3", None), ("G4", None),
            ("MP2", "aug-cc-pVDZ"), ("DLPNO-CCSD(T)", "def2-TZVPP"),
        ]
        counts["level_of_theory"] = _copy(
            conn,
            "level_of_theory",
            ["id", "method", "basis", "lot_hash", "created_at"],
            (
                (i + 1, method, basis, f"{i:064d}", base_time)
                for i, (method, basis) in enumerate(methods)
            ),
        )
        lot_ids = list(range(1, len(methods) + 1))

        literature_count = 400
        counts["literature"] = _copy(
            conn,
            "literature",
            ["id", "kind", "title", "year", "created_at"],
            (
                (
                    i + 1,
                    "article",
                    f"Benchmark reference study {i + 1}",
                    1990 + (i % 35),
                    base_time,
                )
                for i in range(literature_count)
            ),
        )
        literature_ids = list(range(1, literature_count + 1))

        # -- species identity --------------------------------------------
        log(f"loading {len(molecules)} species")
        counts["species"] = _copy(
            conn,
            "species",
            [
                "id", "kind", "smiles", "inchi_key", "charge",
                "multiplicity", "stereo_kind", "created_at",
            ],
            (
                (
                    i + 1,
                    "molecule",
                    m.smiles,
                    m.inchi_key,
                    m.charge,
                    m.multiplicity,
                    "unspecified",
                    base_time + timedelta(minutes=i),
                )
                for i, m in enumerate(molecules)
            ),
        )
        species_ids = list(range(1, len(molecules) + 1))

        # species_entry: one per species, plus a second entry for the
        # fraction implied by the measured 56/55 ratio.
        log("loading species_entry")
        extra_entries = max(0, plan.species_entry - len(species_ids))
        entry_rows: list[tuple] = []
        entry_species: list[int] = []
        next_entry_id = 1
        for species_id in species_ids:
            entry_rows.append(
                (
                    next_entry_id, species_id, "minimum", "ground",
                    base_time + timedelta(minutes=species_id),
                )
            )
            entry_species.append(species_id)
            next_entry_id += 1
        # Second entries are *excited* electronic states, which is what the
        # uq_species_entry_species_id constraint actually distinguishes.
        for species_id in rng.sample(species_ids, min(extra_entries, len(species_ids))):
            entry_rows.append(
                (
                    next_entry_id, species_id, "minimum", "excited",
                    base_time + timedelta(minutes=species_id),
                )
            )
            entry_species.append(species_id)
            next_entry_id += 1

        counts["species_entry"] = _copy(
            conn,
            "species_entry",
            [
                "id", "species_id", "kind", "electronic_state_kind", "created_at",
            ],
            entry_rows,
        )
        entry_ids = [row[0] for row in entry_rows]
        conn.commit()

        # Populate the RDKit mol column through mol_from_smiles so the GiST
        # index is built over real cartridge molecules. Done as an UPDATE
        # rather than inside COPY because mol_from_smiles returns NULL for an
        # unparseable SMILES, whereas a COPY into a mol column would abort the
        # whole load on the first one.
        log("populating species_entry.mol via the RDKit cartridge")
        conn.execute(
            "UPDATE species_entry se SET mol = mol_from_smiles(s.smiles::cstring) "
            "FROM species s WHERE s.id = se.species_id"
        )
        conn.commit()
        populated = conn.execute(
            "SELECT count(*) FROM species_entry WHERE mol IS NOT NULL"
        ).fetchone()[0]
        counts["species_entry_with_mol"] = populated
        log(f"  {populated}/{len(entry_ids)} entries have a cartridge mol")

        # -- conformer groups --------------------------------------------
        log("loading conformer groups")
        conformer_targets = rng.sample(
            entry_ids, min(plan.conformer_group, len(entry_ids))
        )
        counts["conformer_group"] = _copy(
            conn,
            "conformer_group",
            ["id", "species_entry_id", "created_at"],
            (
                (i + 1, entry_id, base_time)
                for i, entry_id in enumerate(conformer_targets)
            ),
        )
        conn.commit()

        # -- calculations -------------------------------------------------
        log(f"loading {plan.calculation} calculations")
        calc_types = ["opt", "freq", "sp", "sp", "scan", "irc", "conf"]
        calc_rows: list[tuple] = []
        for calc_id in range(1, plan.calculation + 1):
            entry_id = rng.choice(entry_ids)
            calc_rows.append(
                (
                    calc_id,
                    rng.choice(calc_types),
                    "curated" if rng.random() < 0.25 else "raw",
                    entry_id,
                    rng.choice(software_release_ids),
                    rng.choice(workflow_release_ids),
                    rng.choice(lot_ids),
                    rng.choice(literature_ids) if rng.random() < 0.2 else None,
                    base_time + timedelta(seconds=calc_id * 7),
                )
            )
        counts["calculation"] = _copy(
            conn,
            "calculation",
            [
                "id", "type", "quality", "species_entry_id",
                "software_release_id", "workflow_tool_release_id", "lot_id",
                "literature_id", "created_at",
            ],
            calc_rows,
        )
        conn.commit()

        calc_ids = [row[0] for row in calc_rows]
        calc_type_by_id = {row[0]: row[1] for row in calc_rows}

        # -- calculation results -----------------------------------------
        log("loading calculation results and diagnostics")
        sp_calcs = [cid for cid in calc_ids if calc_type_by_id[cid] == "sp"]
        counts["calc_sp_result"] = _copy(
            conn,
            "calc_sp_result",
            [
                "calculation_id", "electronic_energy_hartree",
                "electronic_energy_uncertainty_hartree",
            ],
            (
                (
                    cid,
                    -rng.uniform(40.0, 800.0),
                    rng.uniform(1e-6, 5e-4) if rng.random() < 0.3 else None,
                )
                for cid in sp_calcs
            ),
        )

        freq_calcs = [cid for cid in calc_ids if calc_type_by_id[cid] == "freq"]
        counts["calc_freq_result"] = _copy(
            conn,
            "calc_freq_result",
            ["calculation_id", "n_imag", "imag_freq_cm1", "zpe_hartree"],
            (
                (
                    cid,
                    n_imag := (1 if rng.random() < 0.18 else 0),
                    -rng.uniform(200.0, 2200.0) if n_imag else None,
                    rng.uniform(0.02, 0.35),
                )
                for cid in freq_calcs
            ),
        )

        opt_calcs = [cid for cid in calc_ids if calc_type_by_id[cid] == "opt"]
        counts["calc_opt_result"] = _copy(
            conn,
            "calc_opt_result",
            ["calculation_id", "converged", "n_steps", "final_energy_hartree"],
            (
                (
                    cid,
                    rng.random() > 0.06,
                    rng.randint(3, 180),
                    -rng.uniform(40.0, 800.0),
                )
                for cid in opt_calcs
            ),
        )

        # Wavefunction and spin diagnostics on the correlated subset.
        diag_calcs = rng.sample(sp_calcs, max(1, len(sp_calcs) // 3)) if sp_calcs else []
        counts["calc_wavefunction_diagnostic"] = _copy(
            conn,
            "calc_wavefunction_diagnostic",
            ["calculation_id", "t1_diagnostic", "d1_diagnostic", "created_at"],
            # T1/D1 are magnitudes and the schema enforces >= 0. Values are
            # drawn around the multireference-suspicion thresholds that
            # actually matter scientifically (T1 ~ 0.02, D1 ~ 0.05) so a range
            # filter over them selects a meaningful subset rather than noise.
            (
                (
                    cid,
                    abs(rng.gauss(0.014, 0.007)),
                    abs(rng.gauss(0.045, 0.02)),
                    base_time,
                )
                for cid in diag_calcs
            ),
        )
        spin_calcs = rng.sample(calc_ids, max(1, len(calc_ids) // 5))
        counts["calc_spin_diagnostic"] = _copy(
            conn,
            "calc_spin_diagnostic",
            [
                "calculation_id", "s_squared", "s_squared_expected", "created_at",
            ],
            (
                (cid, abs(rng.gauss(0.78, 0.06)), 0.75, base_time)
                for cid in spin_calcs
            ),
        )
        conn.commit()

        # -- geometries (deliberately reduced; see DEVIATIONS) ------------
        geometry_calcs = rng.sample(
            calc_ids, int(len(calc_ids) * plan.geometry_fraction)
        )
        log(f"loading {len(geometry_calcs)} geometries (reduced ratio)")
        atoms_per_species = {
            i + 1: max(3, m.heavy_atoms * 2 + 2) for i, m in enumerate(molecules)
        }
        geometry_rows: list[tuple] = []
        for geometry_id in range(1, len(geometry_calcs) + 1):
            natoms = atoms_per_species[rng.choice(species_ids)]
            geometry_rows.append((geometry_id, natoms, f"{geometry_id:064d}", base_time))
        counts["geometry"] = _copy(
            conn,
            "geometry",
            ["id", "natoms", "geom_hash", "created_at"],
            geometry_rows,
        )
        conn.commit()

        elements = ["C ", "H ", "O ", "N "]

        def atom_rows():
            for geometry_id, natoms, _hash, _created in geometry_rows:
                # atom_index is 1-based (ck_geometry_atom_atom_index_ge_1).
                for atom_index in range(1, natoms + 1):
                    yield (
                        geometry_id,
                        atom_index,
                        elements[atom_index % len(elements)],
                        rng.uniform(-8.0, 8.0),
                        rng.uniform(-8.0, 8.0),
                        rng.uniform(-8.0, 8.0),
                    )

        counts["geometry_atom"] = _copy(
            conn,
            "geometry_atom",
            ["geometry_id", "atom_index", "element", "x", "y", "z"],
            atom_rows(),
        )
        conn.commit()
        log(f"  {counts['geometry_atom']:,} geometry atoms")

        counts["calculation_output_geometry"] = _copy(
            conn,
            "calculation_output_geometry",
            ["calculation_id", "geometry_id", "output_order"],
            (
                (cid, geometry_id, 1)
                for geometry_id, cid in enumerate(geometry_calcs, start=1)
            ),
        )
        conn.commit()

        # -- artifacts -----------------------------------------------------
        log(f"loading {plan.calculation_artifact} calculation artifacts")
        artifact_kinds = ["input", "output_log", "checkpoint", "ancillary", "hessian"]
        counts["calculation_artifact"] = _copy(
            conn,
            "calculation_artifact",
            [
                "id", "calculation_id", "kind", "uri", "sha256", "bytes",
                "filename", "created_at",
            ],
            (
                (
                    aid,
                    rng.choice(calc_ids),
                    kind := rng.choice(artifact_kinds),
                    f"file:///bench/artifacts/{aid}",
                    f"{aid:064x}",
                    rng.randint(1024, 40 * 1024 * 1024),
                    f"job_{aid}.{kind}",
                    base_time,
                )
                for aid in range(1, plan.calculation_artifact + 1)
            ),
        )
        conn.commit()

        # -- scientific products -------------------------------------------
        log("loading statmech / thermo / transport")
        origins = ("computed", "computed", "computed", "experimental", "estimated")
        point_groups = ("C1", "Cs", "C2v", "C3v", "D2h", "D3h", "Td", "C2h", "Ci")
        treatments = ("rrho", "rrho_1d", "rrho_nd", "rrho_1d_nd", "rrho_ad", "rrao")
        rotors = ("atom", "linear", "spherical_top", "symmetric_top", "asymmetric_top")

        statmech_rows = [
            (
                sid,
                rng.choice(entry_ids),
                rng.choice(origins),
                rng.choice(literature_ids) if rng.random() < 0.25 else None,
                rng.choice(workflow_release_ids),
                rng.choice(software_release_ids),
                rng.randint(1, 12),
                rng.choice(point_groups),
                rng.random() < 0.12,
                rng.choice(rotors),
                rng.choice(treatments),
                rng.choice((1, 1, 1, 2, 2, 4)),
                rng.uniform(0.2, 9.0),
                rng.uniform(0.05, 3.0),
                rng.uniform(0.03, 2.0),
                base_time + timedelta(seconds=sid * 11),
            )
            for sid in range(1, plan.statmech + 1)
        ]
        counts["statmech"] = _copy(
            conn,
            "statmech",
            [
                "id", "species_entry_id", "scientific_origin", "literature_id",
                "workflow_tool_release_id", "software_release_id",
                "external_symmetry", "point_group", "is_linear",
                "rigid_rotor_kind", "statmech_treatment", "optical_isomers",
                "rotational_constant_a_cm1", "rotational_constant_b_cm1",
                "rotational_constant_c_cm1", "created_at",
            ],
            statmech_rows,
        )
        statmech_ids = [row[0] for row in statmech_rows]
        conn.commit()

        log("loading statmech electronic levels and torsions")
        counts["statmech_electronic_level"] = _copy(
            conn,
            "statmech_electronic_level",
            ["id", "statmech_id", "level_index", "energy_cm1", "degeneracy"],
            (
                (
                    level_id,
                    sid,
                    level_index,
                    0.0 if level_index == 1 else rng.uniform(50.0, 20000.0),
                    rng.choice((1, 2, 3)),
                )
                # level_index is 1-based; level 1 is the ground state at 0 cm-1.
                for level_id, (sid, level_index) in enumerate(
                    (
                        (sid, level_index)
                        for sid in statmech_ids
                        for level_index in range(
                            1, 2 if rng.random() < 0.6 else 4
                        )
                    ),
                    start=1,
                )
            ),
        )
        conn.commit()

        thermo_phases = ("gas", "gas", "gas", "liquid", "solid", "aqueous")
        thermo_models = ("nasa7", "nasa7", "nasa9", "wilhoit", "tabulated", "scalar")
        thermo_rows = [
            (
                tid,
                rng.choice(entry_ids),
                rng.choice(origins),
                rng.choice(literature_ids) if rng.random() < 0.3 else None,
                rng.choice(workflow_release_ids),
                rng.choice(software_release_ids),
                rng.gauss(-80.0, 160.0),
                rng.uniform(120.0, 480.0),
                rng.uniform(0.5, 12.0) if rng.random() < 0.4 else None,
                rng.uniform(1.0, 20.0) if rng.random() < 0.4 else None,
                200.0 + rng.choice((0.0, 100.0)),
                1500.0 + rng.choice((0.0, 500.0, 1500.0)),
                rng.gauss(-70.0, 160.0),
                1.0,
                rng.choice(thermo_phases),
                rng.choice(statmech_ids) if rng.random() < 0.5 else None,
                rng.choice(thermo_models),
                base_time + timedelta(seconds=tid * 13),
            )
            for tid in range(1, plan.thermo + 1)
        ]
        counts["thermo"] = _copy(
            conn,
            "thermo",
            [
                "id", "species_entry_id", "scientific_origin", "literature_id",
                "workflow_tool_release_id", "software_release_id",
                "h298_kj_mol", "s298_j_mol_k", "h298_uncertainty_kj_mol",
                "s298_uncertainty_j_mol_k", "tmin_k", "tmax_k",
                "enthalpy_formation_0k_kj_mol", "reference_pressure_bar",
                "phase", "statmech_id", "model_kind", "created_at",
            ],
            thermo_rows,
        )
        conn.commit()

        counts["transport"] = _copy(
            conn,
            "transport",
            [
                "id", "species_entry_id", "scientific_origin",
                "workflow_tool_release_id", "created_at",
            ],
            (
                (
                    tid,
                    rng.choice(entry_ids),
                    rng.choice(origins),
                    rng.choice(workflow_release_ids),
                    base_time,
                )
                for tid in range(1, plan.transport + 1)
            ),
        )
        conn.commit()

        # -- reactions ------------------------------------------------------
        log(f"loading {plan.reaction_entry} reactions with skewed participation")
        counts["chem_reaction"] = _copy(
            conn,
            "chem_reaction",
            ["id", "reversible", "created_at"],
            (
                (rid, True, base_time + timedelta(seconds=rid * 3))
                for rid in range(1, plan.reaction_entry + 1)
            ),
        )
        counts["reaction_entry"] = _copy(
            conn,
            "reaction_entry",
            ["id", "reaction_id", "created_at"],
            (
                (rid, rid, base_time + timedelta(seconds=rid * 3))
                for rid in range(1, plan.reaction_entry + 1)
            ),
        )
        conn.commit()

        cumulative = _zipf_species_popularity(rng, len(entry_ids))

        def draw_entry() -> int:
            return entry_ids[bisect.bisect_left(cumulative, rng.random())]

        def participant_rows():
            participant_id = 0
            for reaction_entry_id in range(1, plan.reaction_entry + 1):
                n_reactants = rng.choice((1, 2, 2, 2, 3))
                n_products = rng.choice((1, 2, 2, 2, 3))
                for role, n in (("reactant", n_reactants), ("product", n_products)):
                    # participant_index is 1-based per role.
                    for index in range(1, n + 1):
                        participant_id += 1
                        yield (
                            participant_id,
                            reaction_entry_id,
                            draw_entry(),
                            role,
                            index,
                            base_time,
                        )

        counts["reaction_entry_structure_participant"] = _copy(
            conn,
            "reaction_entry_structure_participant",
            [
                "id", "reaction_entry_id", "species_entry_id", "role",
                "participant_index", "created_at",
            ],
            participant_rows(),
        )
        conn.commit()

        # -- kinetics -------------------------------------------------------
        log(f"loading {plan.kinetics} kinetics records")
        a_units = ("per_s", "cm3_mol_s", "cm6_mol2_s")
        models = ("arrhenius", "modified_arrhenius", "modified_arrhenius",
                  "plog", "troe", "chebyshev", "lindemann", "multi_arrhenius")
        directions = ("forward", "forward", "forward", "reverse", "net")
        contexts = ("high_p_limit", "high_p_limit", "apparent_at_pressure",
                    "pressure_dependent")
        tunneling = ("none", "wigner", "eckart", "eckart", "sct", "other")
        def kinetics_rows():
            for kid in range(1, plan.kinetics + 1):
                # ck_kinetics_a_uncertainty_kind_required_with_value: the
                # value and its kind are present or absent together, never one
                # without the other.
                if rng.random() < 0.4:
                    a_uncertainty = rng.uniform(1.2, 5.0)
                    a_uncertainty_kind = "multiplicative"
                else:
                    a_uncertainty = None
                    a_uncertainty_kind = None
                context = rng.choice(contexts)
                yield (
                    kid,
                    rng.randint(1, plan.reaction_entry),
                    rng.choice(origins),
                    rng.choice(models),
                    rng.choice(literature_ids) if rng.random() < 0.35 else None,
                    rng.choice(workflow_release_ids),
                    rng.choice(software_release_ids),
                    10 ** rng.uniform(4.0, 15.0),
                    rng.choice(a_units),
                    rng.uniform(-1.5, 3.5),
                    rng.uniform(-20.0, 400.0),
                    a_uncertainty,
                    a_uncertainty_kind,
                    rng.uniform(1.0, 25.0) if rng.random() < 0.3 else None,
                    300.0,
                    rng.choice((1500.0, 2000.0, 2500.0)),
                    rng.choice((1.0, 1.0, 2.0, 3.0, 6.0)),
                    rng.choice(tunneling),
                    context,
                    rng.choice((0.1, 1.0, 10.0))
                    if context == "apparent_at_pressure"
                    else None,
                    rng.random() < 0.08,
                    rng.choice(directions),
                    base_time + timedelta(seconds=kid * 17),
                )

        counts["kinetics"] = _copy(
            conn,
            "kinetics",
            [
                "id", "reaction_entry_id", "scientific_origin", "model_kind",
                "literature_id", "workflow_tool_release_id",
                "software_release_id", "a", "a_units", "n", "ea_kj_mol",
                "a_uncertainty", "a_uncertainty_kind", "ea_uncertainty_kj_mol",
                "tmin_k", "tmax_k", "degeneracy", "tunneling_model",
                "pressure_context", "pressure_bar", "is_third_body",
                "direction", "created_at",
            ],
            kinetics_rows(),
        )
        conn.commit()

        # -- review overlay --------------------------------------------------
        # ``record_review`` carries UNIQUE(record_type, record_id), so it holds
        # at most one row per reviewable record. The production ratio of 19.4
        # reviews per species is spread across more reviewable record types
        # than this corpus models (it omits conformer observations, transition
        # state entries, networks, network solves and artifacts), so the built
        # count falls short of the scaled target. The shortfall is measured and
        # declared in the manifest rather than being papered over.
        log(f"loading up to {plan.record_review} record reviews")
        reviewable: list[tuple[str, list[int]]] = [
            ("species_entry", entry_ids),
            ("calculation", calc_ids),
            ("statmech", statmech_ids),
            ("thermo", [row[0] for row in thermo_rows]),
            ("kinetics", list(range(1, plan.kinetics + 1))),
            ("transport", list(range(1, plan.transport + 1))),
        ]
        # record_review has a UNIQUE(record_type, record_id) constraint, so
        # each (type, id) pair may appear at most once.
        review_rows: list[tuple] = []
        review_id = 0
        budget = plan.record_review
        per_type = budget // len(reviewable)
        for record_type, ids in reviewable:
            chosen = rng.sample(ids, min(per_type, len(ids)))
            for record_id in chosen:
                review_id += 1
                status = _weighted_choice(rng, REVIEW_MIX)
                # ck_record_review_record_review_terminal_requires_reviewer:
                # approved / rejected / deprecated must name both a reviewer
                # and a review timestamp. under_review and not_reviewed carry
                # neither, which is what "nobody has signed this off" means.
                terminal = status in ("approved", "rejected", "deprecated")
                review_rows.append(
                    (
                        review_id,
                        record_type,
                        record_id,
                        status,
                        rng.choice(curator_ids) if terminal else None,
                        base_time + timedelta(seconds=review_id) if terminal else None,
                        base_time,
                    )
                )
        counts["record_review"] = _copy(
            conn,
            "record_review",
            [
                "id", "record_type", "record_id", "status", "reviewed_by",
                "reviewed_at", "created_at",
            ],
            review_rows,
        )
        conn.commit()

        # -- sequence fixup ---------------------------------------------------
        log("resetting sequences and analyzing")
        conn.execute(
            """
            DO $$
            DECLARE r record;
            BEGIN
              FOR r IN
                SELECT c.relname AS seq,
                       t.relname AS tbl,
                       a.attname AS col
                FROM pg_class c
                JOIN pg_depend d ON d.objid = c.oid AND d.deptype = 'a'
                JOIN pg_class t ON t.oid = d.refobjid
                JOIN pg_attribute a
                  ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
                WHERE c.relkind = 'S'
              LOOP
                EXECUTE format(
                  'SELECT setval(%L, COALESCE((SELECT max(%I) FROM %I), 0) + 1, false)',
                  r.seq, r.col, r.tbl
                );
              END LOOP;
            END $$;
            """
        )
        conn.commit()
        conn.execute("ANALYZE")
        conn.commit()

    # Every number the manifest publishes is re-read from the database rather
    # than taken from the plan, and any table that came up short of its scaled
    # target is reported as a measured shortfall. A corpus that quietly built
    # 40% of a table and then reported plan figures would make every timing
    # taken against it a claim nobody had checked.
    measured = measure_corpus(db_name)
    shortfalls = _shortfalls(plan, measured)

    manifest = {
        "database": db_name,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "build_seconds": round(time.time() - started, 1),
        "plan": plan.as_dict(),
        "row_counts": counts,
        "measured_row_counts": measured,
        "measured_shortfalls": shortfalls,
        "production_inventory": PRODUCTION_INVENTORY,
        "production_ratios_per_species": {
            table: round(count / _PRODUCTION_SPECIES, 4)
            for table, count in PRODUCTION_INVENTORY.items()
        },
        "review_mix": dict(REVIEW_MIX),
        "deviations": [d.as_dict() for d in plan.deviations],
    }
    log(f"done in {manifest['build_seconds']}s")
    return manifest


#: Why a table came up short of its scaled target. A shortfall without an
#: entry here is reported as ``undeclared``, which is a bug in the corpus, not
#: a footnote.
SHORTFALL_REASONS: dict[str, str] = {
    "record_review": (
        "record_review is UNIQUE(record_type, record_id) — at most one row per "
        "reviewable record. The measured production ratio of 19.44 reviews per "
        "species is spread over more reviewable record types than this corpus "
        "models; the corpus reviews only species_entry, calculation, statmech, "
        "thermo, kinetics and transport. EFFECT: review-badge joins and the "
        "curated-profile approval floor are exercised at full fidelity for "
        "those six record types (386,514 badge rows, 69,409 approved), which "
        "covers every benchmarked query shape. Review lookups for conformer, "
        "transition-state, network and artifact records are NOT exercised."
    ),
}


def measure_corpus(db_name: str) -> dict[str, int]:
    """Read live row counts for every non-empty table in ``db_name``."""
    _validate_db_name(db_name)
    with psycopg.connect(_dsn(db_name)) as conn:
        rows = conn.execute(
            """
            -- relname is the pg `name` type, which psycopg hands back as
            -- bytes; cast to text so it can be used as a dict key.
            SELECT c.relname::text, c.reltuples::bigint
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY c.relname
            """
        ).fetchall()
        # reltuples is an estimate; re-count the tables that matter exactly.
        exact: dict[str, int] = {}
        for name, estimate in rows:
            if estimate <= 0:
                continue
            exact[name] = conn.execute(
                f'SELECT count(*) FROM "{name}"'
            ).fetchone()[0]
    return {k: v for k, v in sorted(exact.items()) if v}


def _shortfalls(plan: CorpusPlan, measured: dict[str, int]) -> list[dict[str, object]]:
    """Tables built to fewer rows than the plan asked for, with reasons."""
    planned = plan.as_dict()
    # ``seed`` and ``geometry_fraction`` are build parameters, not tables.
    non_tables = {"seed", "geometry_fraction"}
    out: list[dict[str, object]] = []
    for table, target in planned.items():
        if table in non_tables:
            continue
        if not isinstance(target, int) or target <= 0:
            continue
        built = measured.get(table, 0)
        if built >= target:
            continue
        out.append(
            {
                "table": table,
                "planned": target,
                "built": built,
                "fraction_of_plan": round(built / target, 4),
                "reason": SHORTFALL_REASONS.get(table, "undeclared"),
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="tckdb_bench_s4",
        help="target database name (must match tckdb_bench_*)",
    )
    parser.add_argument("--species", type=int, default=50_000)
    parser.add_argument(
        "--geometry-fraction", type=float, default=0.15,
        help="fraction of calculations given a geometry (declared deviation)",
    )
    parser.add_argument("--seed", type=int, default=20260801)
    parser.add_argument(
        "--drop", action="store_true", help="drop the database and exit"
    )
    parser.add_argument(
        "--describe", action="store_true",
        help="measure an already-built corpus and emit its manifest",
    )
    parser.add_argument(
        "--manifest", type=Path, default=None,
        help="write the corpus manifest JSON here",
    )
    args = parser.parse_args(argv)

    db_name = _validate_db_name(args.db)
    if args.drop:
        drop_database(db_name)
        print(f"dropped {db_name}")
        return 0

    plan = CorpusPlan(
        species_count=args.species,
        geometry_fraction=args.geometry_fraction,
        seed=args.seed,
    )
    if args.describe:
        measured = measure_corpus(db_name)
        manifest = {
            "database": db_name,
            "described_at": datetime.now().isoformat(timespec="seconds"),
            "plan": plan.as_dict(),
            "measured_row_counts": measured,
            "measured_shortfalls": _shortfalls(plan, measured),
            "production_inventory": PRODUCTION_INVENTORY,
            "production_ratios_per_species": {
                table: round(count / _PRODUCTION_SPECIES, 4)
                for table, count in PRODUCTION_INVENTORY.items()
            },
            "review_mix": dict(REVIEW_MIX),
            "deviations": [d.as_dict() for d in plan.deviations],
        }
    else:
        create_database(db_name)
        manifest = build_corpus(db_name, plan)

    payload = json.dumps(manifest, indent=2, sort_keys=True)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(payload + "\n", encoding="utf-8")
        print(f"manifest written to {args.manifest}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
