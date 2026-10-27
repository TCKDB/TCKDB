"""The data step of the species-key / entry-descriptor migration (DR-0038).

A species is now its constitution -- stereo-blind canonical SMILES, charge and multiplicity -- and stereochemistry is
resolved on the species entry as a canonical descriptor. Rows written under the old rule are wrong in two ways, and this
step repairs both inside the migration's own transaction. **It deletes nothing.**

Merged rows are kept and forward
--------------------------------
When two rows turn out to be one thing, the row that loses is *kept*: its public ref still resolves, and everything
recorded **about that row** stays on it -- ``record_review`` and its events, machine reviews, curator tasks, release
selections, supersession edges, reproducibility assessments, idempotency bodies, per-user entry reviews. Its
``merged_into_*`` column names the survivor (one hop, never a chain), and the **results** (entries, thermo, statmech,
calculations, conformer groups, kinetics, network references, reaction entries, ...) move to the survivor, so a read of
the survivor never has to aggregate. Unique keys apply to live rows only (partial unique indexes), which is what makes
keeping the loser possible.

1. **Species keyed on stereo marks.** Every species key is recomputed. Species that now share a key: the survivor is the
   row that already holds the key's content ref (what a fresh database would mint), else the row that already holds the
   key's SMILES, else the oldest; the others are marked merged. Entries, reaction participants (a live reaction's
   participants are rewritten to the survivor; same-side collisions add their stoichiometry) and third-body colliders
   move to the survivor. A reaction's ``stoichiometry_hash`` covers species ids, so it is recomputed; reactions that now
   share a hash merge the same way (survivor: the row holding the hash's content ref, else a row already holding the
   hash, else the oldest) and hand their reaction entries to it.
2. **Entries keyed on a free-text label.** Every entry gets the descriptor its own SMILES marks and **every** linked
   geometry support, plus an old label where it is usable: a label already in the descriptor vocabulary with a single
   element to describe, or ``cis``/``trans`` where the words mean one thing (:func:`stereo.translate_cis_trans`). Entries
   whose live key now collides merge (oldest survives) and every foreign key to ``species_entry`` is re-pointed, read from
   the database catalogue at run time, not from a list.

Frozen rows
-----------
Rows under an accepted record change only through the declared-repair path (``accepted_science_repair``, one declaration
per guarded table naming exactly the columns written); the trigger is never disabled. A hash that covers a re-pointed id
is recomputed in the same declaration, for the rows that moved only. A row the path cannot express (a primary-key column
under an approved record) is **left on the kept row and listed**; it is not forced, and the kept row forwards. A row that
would duplicate one the survivor already has stops the migration instead: both would be served as live.

What stops the migration (and why)
----------------------------------
A decision this step may not make: two entries that now share a key but carry *different* old labels with nothing in the
data to say they are the same configuration; an entry whose SMILES marks, geometries and label disagree (D4) that would
share a key with another; entries of different stationary-point kinds that would merge; a composite foreign key into the
three identity tables (not understood, never skipped).

Writes use SQL, not the ORM, so the step is not coupled to the models of a later revision.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem import inchi
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.chemistry import stereo
from app.chemistry.species import classify_stereo_kind, identity_mol_from_smiles, strip_isotopes
from app.db.models.common import StereoKind
from app.services.conformer_basin_moves import REASSIGNMENT_REPAIR_COLUMNS
from app.services.conformer_reassignment import reassign_conformer_groups
from app.services.public_refs import PREFIXES, make_content_ref

logger = logging.getLogger(__name__)

_GUARD_FUNCTIONS = (
    "tckdb_guard_accepted_root",
    "tckdb_guard_accepted_child",
    "tckdb_guard_accepted_via_child",
    "tckdb_guard_calculation_geometry",
)

#: The marker columns. They are never moved: they say which row a row was merged into.
_MARKERS = {
    "species": "merged_into_species_id",
    "species_entry": "merged_into_species_entry_id",
    "chem_reaction": "merged_into_chem_reaction_id",
}

#: Tables whose rows are *about* the kept row itself (curation), so they stay on it instead of moving.
_STAYS_ON_MERGED_ROW = {
    ("species_entry_review", "species_entry_id"),
}

#: ``structure_determination.identity_hash`` names its owning species entry.
_STRUCTURE_DETERMINATION_IDENTITY_VERSION = 1

_VOCABULARY = re.compile(stereo.DESCRIPTOR_PATTERN)

_REASON = (
    "Species identity is the constitution and stereo is resolved on the species entry ({revision}). "
    "Re-points {columns} from a merged-away species or entry to the row it was merged into; "
    "no measured, computed or declared scientific value changes."
)


class BlockedMerge(RuntimeError):
    """The data needs a decision this step may not make."""


@dataclass
class MergeReport:
    """What the step did. Counts, and the lists a curator needs."""

    species_examined: int = 0
    species_rekeyed: int = 0
    #: Entries whose symmetry is past the automorphism cap: the elements inside their symmetry classes are stated as ``?``.
    symmetry_capped: list[str] = field(default_factory=list)
    #: Species whose ``stereo_kind`` now follows the explicit-hydrogen perception (chiral radicals, allenes).
    species_reclassified: int = 0
    species_unparseable: int = 0
    species_merged: int = 0
    reactions_rehashed: int = 0
    reactions_merged: int = 0
    entries_examined: int = 0
    entries_described: int = 0
    entries_merged: int = 0
    entries_review_reset: int = 0
    labels_not_carried: int = 0
    labels_translated: int = 0
    rows_repointed: int = 0
    conformer_group_labels_cleared: int = 0
    repairs_declared: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    merged_refs: list[tuple[str, str, str]] = field(default_factory=list)
    left_on_merged_rows: list[tuple[str, str, int]] = field(default_factory=list)
    uncarried_labels: dict[str, int] = field(default_factory=dict)
    conformer_reassignment: str = ""

    def summary(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in self.__dict__.items() if isinstance(v, int))
        return (
            f"{counts}, repairs_declared={self.repairs_declared}, "
            f"contradictions_left_for_curation={self.contradictions}, "
            f"stereo_unsupported={self.unsupported}, "
            f"symmetry_past_cap_entries={self.symmetry_capped}, "
            f"labels_not_carried={self.uncarried_labels}, "
            f"left_on_merged_rows(table, column, rows)={self.left_on_merged_rows}, "
            f"merged(record, merged_ref, into_ref)={self.merged_refs}, "
            f"conformers=[{self.conformer_reassignment}]"
        )


# ---------------------------------------------------------------------------
# Catalogue: what points at what, read from the database
# ---------------------------------------------------------------------------


class _Catalogue:
    def __init__(self, session: Session) -> None:
        self._s = session
        self.guarded = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT DISTINCT c.relname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_proc p ON p.oid = t.tgfoid "
                    "WHERE NOT t.tgisinternal AND p.proname = ANY(:names)"
                ),
                {"names": list(_GUARD_FUNCTIONS)},
            )
        }
        self._uniques: dict[str, list[tuple[list[str], bool]]] = {}
        self._assert_no_composite_foreign_keys()

    def _assert_no_composite_foreign_keys(self) -> None:
        rows = self._s.execute(
            text(
                "SELECT c.conrelid::regclass::text AS tbl, c.conname FROM pg_constraint c "
                "WHERE c.contype = 'f' AND array_length(c.conkey, 1) > 1 "
                "AND c.confrelid = ANY(ARRAY['species'::regclass, 'species_entry'::regclass, 'chem_reaction'::regclass])"
            )
        ).all()
        if rows:
            raise BlockedMerge(
                "a composite foreign key points at species, species_entry or chem_reaction "
                f"({', '.join(f'{r.tbl}.{r.conname}' for r in rows)}); this step does not know how to re-point it"
            )

    def foreign_keys(self, referenced: str) -> list[tuple[str, str]]:
        """``(table, column)`` of every single-column foreign key into ``referenced``."""
        rows = self._s.execute(
            text(
                "SELECT c.conrelid::regclass::text AS tbl, a.attname AS col "
                "FROM pg_constraint c "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
                "WHERE c.contype = 'f' AND c.confrelid = CAST(:ref AS regclass) "
                "AND array_length(c.conkey, 1) = 1 ORDER BY 1, 2"
            ),
            {"ref": referenced},
        )
        markers = set(_MARKERS.values())
        return [(r.tbl, r.col) for r in rows if r.col not in markers]

    def uniques(self, table: str) -> list[tuple[list[str], bool]]:
        if table not in self._uniques:
            rows = self._s.execute(
                text(
                    "SELECT array_agg(a.attname ORDER BY k.ord) AS cols, i.indnullsnotdistinct AS nnd "
                    "FROM pg_constraint c "
                    "JOIN pg_index i ON i.indexrelid = c.conindid "
                    "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
                    "WHERE c.contype IN ('p', 'u') AND c.conrelid = CAST(:t AS regclass) "
                    "GROUP BY c.conname, i.indnullsnotdistinct"
                ),
                {"t": table},
            )
            self._uniques[table] = [(list(r.cols), bool(r.nnd)) for r in rows]
        return self._uniques[table]


class _Repairs:
    """Declares each accepted-science repair once per transaction, naming every column it may write."""

    def __init__(self, session: Session, revision: str, report: MergeReport, catalogue: _Catalogue) -> None:
        self._s = session
        self._revision = revision
        self._report = report
        self._cat = catalogue
        self._columns: dict[str, set[str]] = defaultdict(set)
        self._declared: set[str] = set()

    def plan(self, table: str, columns: list[str]) -> None:
        """Say, before the first write, which columns of ``table`` this step may change."""
        self._columns[table].update(columns)

    def ensure(self, table: str) -> None:
        if table in self._declared or table not in self._cat.guarded:
            return
        columns = sorted(self._columns[table])
        self._s.execute(
            text(
                "INSERT INTO accepted_science_repair (target_table, declared_columns, alembic_revision, reason) "
                "VALUES (:t, CAST(:cols AS text[]), :rev, :reason)"
            ),
            {
                "t": table,
                "cols": "{" + ",".join(columns) + "}",
                "rev": self._revision,
                "reason": _REASON.format(revision=self._revision, columns=", ".join(columns)),
            },
        )
        self._declared.add(table)
        self._report.repairs_declared.append(table)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def merge_stereo_identity(session: Session, *, revision: str) -> MergeReport:
    """Re-key species, describe entries, merge what now collides. The caller commits.

    :raises BlockedMerge: When the data needs a decision this step may not make.
    """
    report = MergeReport()
    # Composite foreign keys (``calculation(id, species_entry_id)`` and friends) cannot be satisfied by
    # one statement at a time; every one of these constraints is DEFERRABLE.
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    catalogue = _Catalogue(session)
    repairs = _Repairs(session, revision, report, catalogue)
    _plan_repairs(catalogue, repairs)

    merger = _Merger(session, catalogue, repairs, report)
    merger.merge_species()
    merger.merge_reactions()
    merger.describe_entries()
    survivors = merger.merge_entries()
    merger.rehash_dependents()
    merger.reset_survivor_reviews(survivors)
    if survivors:
        # Only surviving entries are reassigned. Their groups have all moved to them already, and no merged entry may
        # be walked (the database refuses a write onto one).
        merged_survivors = session.scalars(
            text(
                "SELECT public_ref FROM species_entry WHERE id = ANY(:ids) AND merged_into_species_entry_id IS NOT NULL"
            ),
            {"ids": sorted(survivors)},
        ).all()
        assert not merged_survivors, f"reassignment would walk merged entries: {merged_survivors}"
        reassignment = reassign_conformer_groups(session, repair_revision=revision, species_entry_ids=sorted(survivors))
        report.conformer_reassignment = reassignment.summary()
    # Run every deferred foreign-key check now: a dangling reference is a failure of this step, and pending trigger
    # events would also block the DDL that follows it.
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return report


def _plan_repairs(catalogue: _Catalogue, repairs: _Repairs) -> None:
    """Name every column this step may write, by table, including those ``reassign_conformer_groups`` may write."""
    for ref in ("species", "species_entry", "chem_reaction"):
        for table, column in catalogue.foreign_keys(ref):
            if (table, column) not in _SPECIAL_COLUMNS and (table, column) not in _STAYS_ON_MERGED_ROW:
                repairs.plan(table, [column])
    repairs.plan("structure_determination", ["identity_hash"])
    repairs.plan("network_state", ["composition_hash"])
    # The database allows ONE repair declaration per table per transaction, so a table this step writes AND the
    # reassignment may write is declared once with the union of both column sets.
    for table, columns in REASSIGNMENT_REPAIR_COLUMNS.items():
        repairs.plan(table, list(columns))


#: Foreign keys this step moves by hand, not through the generic mover.
_SPECIAL_COLUMNS = {
    ("species_entry", "species_id"),
    ("reaction_participant", "species_id"),
    ("reaction_participant", "reaction_id"),
}


# ---------------------------------------------------------------------------
# The work
# ---------------------------------------------------------------------------


@dataclass
class _Entry:
    id: int
    public_ref: str
    species_id: int
    kind: str
    label: str | None
    descriptor: str | None
    unresolved: bool
    contradicted: bool
    key: tuple


def _species_ref(smiles: str, charge: int, multiplicity: int, stereo_kind: str) -> str:
    return make_content_ref(
        PREFIXES["Species"],
        f"species:smiles={smiles};charge={charge};multiplicity={multiplicity};stereo_kind={stereo_kind}",
    )


class _Merger:
    def __init__(self, session: Session, catalogue: _Catalogue, repairs: _Repairs, report: MergeReport) -> None:
        self.s = session
        self.cat = catalogue
        self.repairs = repairs
        self.report = report
        self.touched_states: set[int] = set()
        self.touched_determinations: set[int] = set()
        self.touched_reactions: set[int] = set()
        #: Each entry's label as it was deposited: ``stereo_label`` is rewritten to its derived display text.
        self.old_labels: dict[int, str | None] = {}
        #: Entries whose symmetry is past the cap: what their sources said at the positions stated as ``?``.
        self.cap_evidence: dict[int, tuple[str, ...]] = {}

    # -- species -----------------------------------------------------------

    def merge_species(self) -> None:
        rows = self.s.execute(
            text(
                "SELECT id, smiles, charge, multiplicity, kind::text AS kind, public_ref FROM species "
                "WHERE merged_into_species_id IS NULL ORDER BY id"
            )
        ).all()
        keyed: dict[tuple, list] = defaultdict(list)
        new_key: dict[int, tuple[str, str]] = {}
        for row in rows:
            self.report.species_examined += 1
            if row.kind != "molecule":
                continue
            try:
                constitution = stereo.constitution_mol(strip_isotopes(identity_mol_from_smiles(row.smiles)))
            except ValueError:
                self.report.species_unparseable += 1
                logger.warning("species %s: stored SMILES does not parse; key left as stored", row.id)
                continue
            smiles = Chem.MolToSmiles(constitution, canonical=True)
            new_key[row.id] = (smiles, inchi.MolToInchiKey(constitution))
            keyed[(smiles, row.charge, row.multiplicity)].append(row)

        loser_to_survivor: dict[int, int] = {}
        for (smiles, charge, multiplicity), group in keyed.items():
            if len(group) == 1:
                continue
            kind = classify_stereo_kind(identity_mol_from_smiles(smiles))[0].value
            canonical_ref = _species_ref(smiles, charge, multiplicity, kind)
            # A row minted before ``stereo_kind`` followed the explicit-hydrogen perception (chiral radicals) holds the
            # ref of the same key under another kind; it is still the row that already holds the key's ref.
            any_kind_refs = {_species_ref(smiles, charge, multiplicity, k.value) for k in StereoKind}
            survivor = (
                next((r for r in group if r.public_ref == canonical_ref), None)
                or next((r for r in group if r.public_ref in any_kind_refs), None)
                or next((r for r in group if r.smiles == smiles), None)
                or group[0]
            )
            for loser in group:
                if loser.id != survivor.id:
                    loser_to_survivor[loser.id] = survivor.id
                    self.report.merged_refs.append(("species", loser.public_ref, survivor.public_ref))

        # Mark first, then move: the markers are what make the row "merged".
        for loser_id, survivor_id in loser_to_survivor.items():
            self.s.execute(
                text("UPDATE species SET merged_into_species_id = :s WHERE id = :l"), {"s": survivor_id, "l": loser_id}
            )
            self.report.species_merged += 1
        for row in rows:
            if row.id in loser_to_survivor or row.id not in new_key:
                continue
            smiles, key = new_key[row.id]
            kind = classify_stereo_kind(identity_mol_from_smiles(smiles))[0].value
            # Only a kind the old heavy-atom perception could have produced for a stereo-capable molecule is replaced.
            reclassified = self.s.execute(
                text(
                    "UPDATE species SET stereo_kind = CAST(:kind AS stereo_kind) "
                    "WHERE id = :id AND stereo_kind::text <> :kind AND stereo_kind::text IN ('achiral', 'ez_isomer')"
                ),
                {"kind": kind, "id": row.id},
            ).rowcount
            self.report.species_reclassified += reclassified or 0
            changed = self.s.execute(
                text(
                    "UPDATE species SET smiles = :smiles, inchi_key = :key "
                    "WHERE id = :id AND (smiles <> :smiles OR inchi_key <> :key)"
                ),
                {"smiles": smiles, "key": key, "id": row.id},
            ).rowcount
            self.report.species_rekeyed += changed or 0

        affected_reactions = self._reaction_ids_with_species(set(loser_to_survivor))
        for loser_id, survivor_id in loser_to_survivor.items():
            self.s.execute(
                text("UPDATE species_entry SET species_id = :s WHERE species_id = :l"),
                {"s": survivor_id, "l": loser_id},
            )
            for table, column in self.cat.foreign_keys("species"):
                if (table, column) not in _SPECIAL_COLUMNS:
                    self._move(table, column, loser_id, survivor_id)
        self._rewrite_participants(affected_reactions, loser_to_survivor)

    def _reaction_ids_with_species(self, species_ids: set[int]) -> set[int]:
        if not species_ids:
            return set()
        rows = self.s.execute(
            text(
                "SELECT DISTINCT p.reaction_id FROM reaction_participant p "
                "JOIN chem_reaction r ON r.id = p.reaction_id "
                "WHERE p.species_id = ANY(:ids) AND r.merged_into_chem_reaction_id IS NULL"
            ),
            {"ids": sorted(species_ids)},
        )
        return {r[0] for r in rows}

    def _rewrite_participants(self, reaction_ids: set[int], loser_to_survivor: dict[int, int]) -> None:
        """A live reaction names live species: rewrite its participants, adding same-side collisions."""
        for reaction_id in sorted(reaction_ids):
            rows = self.s.execute(
                text(
                    "SELECT species_id, role::text AS role, stoichiometry FROM reaction_participant "
                    "WHERE reaction_id = :r"
                ),
                {"r": reaction_id},
            ).all()
            merged: dict[tuple[int, str], int] = defaultdict(int)
            for row in rows:
                merged[(loser_to_survivor.get(row.species_id, row.species_id), row.role)] += row.stoichiometry
            self.s.execute(text("DELETE FROM reaction_participant WHERE reaction_id = :r"), {"r": reaction_id})
            for (species_id, role), stoichiometry in merged.items():
                self.s.execute(
                    text(
                        "INSERT INTO reaction_participant (reaction_id, species_id, role, stoichiometry) "
                        "VALUES (:r, :sp, CAST(:role AS reaction_role), :n)"
                    ),
                    {"r": reaction_id, "sp": species_id, "role": role, "n": stoichiometry},
                )
            self.touched_reactions.add(reaction_id)

    # -- reactions ---------------------------------------------------------

    @staticmethod
    def _stoichiometry_hash(reversible: bool, reactants: dict[int, int], products: dict[int, int]) -> str:
        """Frozen copy of ``app.services.reaction_resolution.reaction_stoichiometry_hash``."""
        payload = {
            "reversible": reversible,
            "reactants": sorted(reactants.items()),
            "products": sorted(products.items()),
        }
        return hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()

    def merge_reactions(self) -> None:
        if not self.touched_reactions:
            return
        new_hash: dict[int, str] = {}
        for reaction_id in sorted(self.touched_reactions):
            reversible = self.s.scalar(text("SELECT reversible FROM chem_reaction WHERE id = :r"), {"r": reaction_id})
            sides: dict[str, dict[int, int]] = {"reactant": {}, "product": {}}
            for row in self.s.execute(
                text("SELECT species_id, role::text AS role, stoichiometry FROM reaction_participant WHERE reaction_id = :r"),
                {"r": reaction_id},
            ):
                sides[row.role][row.species_id] = row.stoichiometry
            new_hash[reaction_id] = self._stoichiometry_hash(bool(reversible), sides["reactant"], sides["product"])
        self.report.reactions_rehashed = len(new_hash)

        by_hash: dict[str, list[int]] = defaultdict(list)
        for reaction_id, digest in new_hash.items():
            by_hash[digest].append(reaction_id)
        for digest, touched in by_hash.items():
            holders = list(
                self.s.execute(
                    text(
                        "SELECT id FROM chem_reaction WHERE stoichiometry_hash = :h AND id <> ALL(:touched) "
                        "AND merged_into_chem_reaction_id IS NULL"
                    ),
                    {"h": digest, "touched": sorted(self.touched_reactions)},
                ).scalars()
            )
            group = sorted({*touched, *holders})
            refs = dict(
                self.s.execute(
                    text("SELECT id, public_ref FROM chem_reaction WHERE id = ANY(:ids)"), {"ids": group}
                ).all()
            )
            canonical_ref = make_content_ref(PREFIXES["ChemReaction"], f"chem_reaction:stoichiometry_hash={digest}")
            survivor = (
                next((i for i in group if refs[i] == canonical_ref), None)
                or next((i for i in holders), None)
                or group[0]
            )
            for reaction_id in group:
                self.s.execute(
                    text("UPDATE chem_reaction SET stoichiometry_hash = :h WHERE id = :i"), {"h": digest, "i": reaction_id}
                )
            for loser in group:
                if loser == survivor:
                    continue
                self.s.execute(
                    text("UPDATE chem_reaction SET merged_into_chem_reaction_id = :s WHERE id = :l"),
                    {"s": survivor, "l": loser},
                )
                self.report.merged_refs.append(("chem_reaction", refs[loser], refs[survivor]))
                self.report.reactions_merged += 1
                for table, column in self.cat.foreign_keys("chem_reaction"):
                    if (table, column) not in _SPECIAL_COLUMNS:
                        self._move(table, column, loser, survivor)

    # -- entries: describing ----------------------------------------------

    def _geometries(self, entry_id: int) -> list[tuple[str, dict[int, int]]]:
        """Every geometry linked to the entry through a calculation: ``(xyz_text, isotopes)``."""
        rows = self.s.execute(
            text(
                "SELECT g.id, g.xyz_text FROM geometry g WHERE g.id IN ("
                "  SELECT o.geometry_id FROM calculation c JOIN calculation_output_geometry o ON o.calculation_id = c.id "
                "  WHERE c.species_entry_id = :e "
                "  UNION SELECT i.geometry_id FROM calculation c JOIN calculation_input_geometry i ON i.calculation_id = c.id "
                "  WHERE c.species_entry_id = :e) ORDER BY g.id"
            ),
            {"e": entry_id},
        ).all()
        out: list[tuple[str, dict[int, int]]] = []
        for row in rows:
            atoms = self.s.execute(
                text(
                    "SELECT atom_index, element, x, y, z, isotope_mass_number FROM geometry_atom "
                    "WHERE geometry_id = :g ORDER BY atom_index"
                ),
                {"g": row.id},
            ).all()
            if not atoms:
                continue
            xyz = row.xyz_text or "\n".join(f"{a.element} {a.x} {a.y} {a.z}" for a in atoms)
            out.append((xyz, {a.atom_index: a.isotope_mass_number for a in atoms if a.isotope_mass_number}))
        return out

    def describe_entries(self) -> None:
        rows = self.s.execute(
            text(
                "SELECT e.id, e.public_ref, e.stereo_label, mol_to_smiles(e.mol)::text AS mol_smiles, "
                "s.smiles AS species_smiles, s.kind::text AS species_kind "
                "FROM species_entry e JOIN species s ON s.id = e.species_id ORDER BY e.id"
            )
        ).all()
        for row in rows:
            self.report.entries_examined += 1
            self.old_labels[row.id] = row.stereo_label
            descriptor = label = resolution = smiles = None
            if row.species_kind == "molecule":
                frame, problem = self._frame(row)
                if frame is not None and frame.is_stereo_capable:
                    resolved, contradicted = self._resolve(row, frame)
                    descriptor, resolution, smiles = (
                        resolved.descriptor,
                        resolved.resolution.value,
                        resolved.stereo_smiles,
                    )
                    if "symmetry" in resolved.unrepresented:
                        self.report.symmetry_capped.append(row.public_ref)
                        self.cap_evidence[row.id] = resolved.evidence
                    # A contradictory entry keeps the label text it was deposited with, for the curator.
                    label = row.stereo_label if contradicted else resolved.label
                    self._count_label(row, frame, resolved, contradicted)
                elif problem is not None:
                    self.report.unsupported.append(f"species_entry {row.public_ref}: {problem}")
                    self._count_label(row, None, None, False)
                else:
                    self._count_label(row, None, None, False)
            elif row.stereo_label:
                self._count_label(row, None, None, False)
            self.s.execute(
                text(
                    "UPDATE species_entry SET stereo_label = :label, stereo_descriptor = :d, "
                    "stereo_resolution = CAST(:r AS stereo_resolution), stereo_smiles = :smiles WHERE id = :id"
                ),
                {"label": label, "d": descriptor, "r": resolution, "smiles": smiles, "id": row.id},
            )
            self.report.entries_described += 1

    def _frame(self, row):
        try:
            return stereo.frame_from_mol(identity_mol_from_smiles(row.mol_smiles or row.species_smiles)), None
        except CodedValueError as exc:
            return None, str(exc)
        except ValueError:
            return None, None

    def _usable_label(self, frame: stereo.StereoFrame, old: str | None) -> tuple[str | None, bool]:
        """The old label as a descriptor-vocabulary string, and whether it was translated (``cis``/``trans``)."""
        if not old or not old.strip():
            return None, False
        text_ = old.strip()
        if _VOCABULARY.fullmatch(text_):
            tokens = text_.split(",")
            kinds = [e.kind for e in frame.elements]
            if len(tokens) != len(kinds):
                return None, False
            # The old label's order was the old code's rank order, which is not this one's. It can still be read
            # when each kind of element occurs once, because a token's kind then says which element it describes.
            if len(set(kinds)) != len(kinds) and len(kinds) > 1:
                return None, False
            by_kind = {}
            for token in tokens:
                by_kind["tetrahedral" if token in stereo.TETRAHEDRAL_TOKENS else "double_bond" if token in stereo.DOUBLE_BOND_TOKENS else "?"] = token
            if len(kinds) > 1:
                try:
                    return ",".join(by_kind[k] for k in kinds), False
                except KeyError:
                    return None, False
            return text_, False
        translated = stereo.translate_cis_trans(frame, text_)
        if translated is not None:
            return translated[0], True
        return None, False

    def _resolve(self, row, frame: stereo.StereoFrame) -> tuple[stereo.ResolvedStereo, bool]:
        geometries = self._geometries(row.id)
        label, _translated = self._usable_label(frame, row.stereo_label)
        unknown = [stereo.UNKNOWN] * len(frame.elements)
        try:
            return stereo.resolve_stereo(frame, geometries=geometries, supplied_label=label), False
        except CodedValueError as exc:
            if exc.code not in (stereo.W_SPECIES_STEREO_CONTRADICTION, stereo.W_SPECIES_STEREO_LABEL_INVALID):
                raise
            self.report.contradictions.append(
                f"species_entry {row.public_ref}: SMILES marks, geometries and label disagree; left unresolved for curation"
            )
            return stereo.finish(frame, unknown), True

    def _count_label(self, row, frame, resolved, contradicted: bool) -> None:
        """Count (and name) every old label the new entry does not carry; count the translated ones."""
        old = (row.stereo_label or "").strip()
        if not old:
            return
        if frame is not None and resolved is not None and not contradicted:
            usable, translated = self._usable_label(frame, old)
            if usable is not None:
                if translated:
                    self.report.labels_translated += 1
                return
        if contradicted:
            return
        self.report.labels_not_carried += 1
        self.report.uncarried_labels[old] = self.report.uncarried_labels.get(old, 0) + 1

    # -- entries: merging --------------------------------------------------

    def merge_entries(self) -> set[int]:
        rows = self.s.execute(
            text(
                "SELECT id, public_ref, species_id, kind::text AS kind, stereo_label, stereo_descriptor, stereo_resolution::text AS res, "
                "electronic_state_kind::text AS esk, electronic_state_label, term_symbol, isotope_key "
                "FROM species_entry WHERE merged_into_species_entry_id IS NULL ORDER BY id"
            )
        ).all()
        contradicted = {c.split()[1].rstrip(":") for c in self.report.contradictions}
        groups: dict[tuple, list] = defaultdict(list)
        for row in rows:
            groups[
                (row.species_id, row.stereo_descriptor, row.esk, row.electronic_state_label, row.term_symbol, row.isotope_key)
            ].append(row)
        survivors: set[int] = set()
        for group in groups.values():
            if len(group) == 1:
                continue
            bad = [r for r in group if r.public_ref in contradicted]
            if bad:
                raise BlockedMerge(
                    "species entries whose SMILES marks, geometries and label disagree would share a key with "
                    f"{', '.join(r.public_ref for r in group if r not in bad)}; a curator decides which stands: "
                    + ", ".join(r.public_ref for r in bad)
                )
            if {r.kind for r in group} != {group[0].kind}:
                raise BlockedMerge(
                    "species entries that now share a key differ in stationary-point kind ("
                    + ", ".join(r.public_ref for r in group)
                    + "); that is a decision for a curator, not for this migration"
                )
            # On a descriptor that still has a ``?``, only entries whose old labels are identical (or all absent) may merge:
            # an entry labelled ``R,S`` that nothing could apply must not vanish into an unlabelled one.
            old = {r.id: (self.old_labels.get(r.id) or "").strip().lower() for r in group}
            if len(set(old.values())) > 1 and "?" in (group[0].stereo_descriptor or ""):
                raise BlockedMerge(
                    "species entries with different stereo labels (or one labelled and one not) would merge although "
                    "nothing in their marks or geometries says they are one configuration; a curator fixes their data "
                    "first: "
                    + ", ".join(f"{r.public_ref} (label {self.old_labels.get(r.id)!r})" for r in group)
                )
            self._assert_cap_evidence_agrees(group)
            self._assert_no_double_standing_selection(group)
            survivor = group[0]
            for loser in group[1:]:
                self._merge_entry(loser.id, survivor.id)
                self.report.merged_refs.append(("species_entry", loser.public_ref, survivor.public_ref))
                self.report.entries_merged += 1
            survivors.add(survivor.id)
        self._assert_no_conformer_rows_left_on_merged_entries()
        return survivors

    def _assert_no_conformer_rows_left_on_merged_entries(self) -> None:
        """A later conformer reassignment walks every entry that has a group; none may be a merged one.

        Groups (and with them their observations) move to the survivor like every other result. One that could not
        move would be walked, and written to, by the next step that sweeps all entries, and the database refuses a
        write onto a merged entry; so the migration stops here and says which.
        """
        left = self.s.execute(
            text(
                "SELECT e.public_ref, count(*) AS groups FROM conformer_group g "
                "JOIN species_entry e ON e.id = g.species_entry_id "
                "WHERE e.merged_into_species_entry_id IS NOT NULL GROUP BY e.public_ref ORDER BY e.public_ref"
            )
        ).all()
        if left:
            raise BlockedMerge(
                "conformer groups stayed on merged-away species entries (a repair could not move them): "
                + ", ".join(f"{r.public_ref} ({r.groups})" for r in left)
            )

    def _assert_cap_evidence_agrees(self, group) -> None:
        """Stop when two entries past the automorphism cap would merge although their evidence differs.

        Past the cap the symmetric positions are stated as ``?``, so a merge there could lump distinct isomers without
        anyone being told. Their sources' tokens at those positions, taken before they were collapsed, are compared; two
        different tokens at one position are two configurations, and a curator decides. No evidence on either side, or
        identical tokens, lets the merge proceed.
        """
        entries = [(r, self.cap_evidence.get(r.id, ())) for r in group if r.id in self.cap_evidence]
        if not any(evidence for _r, evidence in entries):
            return  # nobody has evidence at the symmetric positions: nothing distinguishes them
        # Otherwise every member must carry the identical tokens: a letter against a ``?`` is a difference too, because
        # the entry without evidence may be any configuration, and merging it into a stated one would lose that.
        width = max(len(evidence) for _r, evidence in entries)
        padded = [(r, evidence or ("?",) * width) for r, evidence in entries]
        first, a = padded[0]
        for second, b in padded[1:]:
            if a != b:
                raise BlockedMerge(
                    "species entries too symmetric for the stereo descriptor would merge although their sources do "
                    "not say the same at the symmetric positions "
                    f"({first.public_ref}: {','.join(a)}; {second.public_ref}: {','.join(b)}); a curator decides "
                    "whether they are one configuration"
                )

    def _assert_no_double_standing_selection(self, group) -> None:
        """Stop when merging this group would leave one subject with two standing release selections.

        Selections are append-only and stay on the row they were made about; afterwards a selection about a merged-away
        entry counts as one for the survivor. Two standing selections of one record type in one release (draft or
        published) would then both be "the" selection for the same subject, which is not a state to create silently.
        """
        rows = self.s.execute(
            text(
                "SELECT id, public_ref, dataset_release_id, record_type::text AS record_type, action::text AS action, "
                "supersedes_selection_id FROM release_selection "
                "WHERE subject_type = CAST('species_entry' AS submission_record_type) AND subject_id = ANY(:ids) "
                "ORDER BY id"
            ),
            {"ids": [r.id for r in group]},
        ).all()
        superseded = {r.supersedes_selection_id for r in rows if r.supersedes_selection_id is not None}
        standing: dict[tuple, list[str]] = defaultdict(list)
        for r in rows:
            if r.id not in superseded and r.action != "withdraw":
                standing[(r.dataset_release_id, r.record_type)].append(r.public_ref)
        doubled = {key: refs for key, refs in standing.items() if len(refs) > 1}
        if doubled:
            raise BlockedMerge(
                "merging these species entries would leave one subject with two standing release selections "
                f"({'; '.join(', '.join(refs) for refs in doubled.values())}); a curator withdraws or supersedes one "
                "first. Entries: " + ", ".join(r.public_ref for r in group)
            )

    def _merge_entry(self, loser: int, survivor: int) -> None:
        self.s.execute(
            text("UPDATE species_entry SET merged_into_species_entry_id = :s WHERE id = :l"), {"s": survivor, "l": loser}
        )
        for table, column in self.cat.foreign_keys("species_entry"):
            if (table, column) in _SPECIAL_COLUMNS or (table, column) in _STAYS_ON_MERGED_ROW:
                continue
            self._move(table, column, loser, survivor, entry=True)

    # -- moving one foreign key -------------------------------------------

    def _move(self, table: str, column: str, loser: int, survivor: int, *, entry: bool = False) -> None:
        """Move every row of ``table`` that names ``loser`` to ``survivor``, except what cannot move.

        A row the survivor already has an equivalent of (a unique key would be violated), or one a guard refuses to
        let a declared repair change (a primary-key column under an approved record), stays on the kept row and is
        listed; it is not forced.
        """
        count = self.s.scalar(text(f"SELECT count(*) FROM {table} WHERE {column} = :l"), {"l": loser})
        if not count:
            return
        if table == "conformer_group":
            self._clear_colliding_group_labels(loser, survivor)
        collisions = self._collision_predicate(table, column)
        if entry:
            self._note_dependents(table, column, loser)
        self.repairs.ensure(table)
        try:
            with self.s.begin_nested():
                moved = self.s.execute(
                    text(f"UPDATE {table} AS t SET {column} = :s WHERE t.{column} = :l {collisions}"),
                    {"s": survivor, "l": loser},
                ).rowcount
        except DBAPIError:
            logger.info("%s.%s: left on the merged row (an accepted record guards it)", table, column)
            moved = 0
            guarded = True
        else:
            guarded = False
        self.report.rows_repointed += moved or 0
        left = count - (moved or 0)
        if left and not guarded:
            # What did not move collided with a row the survivor already has. Two rows for one thing would both be read
            # as live, so a curator picks one.
            raise BlockedMerge(
                f"{table}.{column}: {left} row(s) of a merged-away row duplicate rows the surviving one already has "
                f"({', '.join(self._colliding_pairs(table, column, loser, survivor))}); a curator decides which stands"
            )
        if left:
            self.report.left_on_merged_rows.append((table, column, left))

    def _colliding_pairs(self, table: str, column: str, loser: int, survivor: int) -> list[str]:
        """``id`` pairs (``loser row / survivor row``) of the rows that collided, for the operator's list."""
        pairs: list[str] = []
        for columns, nulls_not_distinct in self.cat.uniques(table):
            if column not in columns:
                continue
            equal = " AND ".join(
                (f"b.{c} IS NOT DISTINCT FROM t.{c}" if nulls_not_distinct else f"b.{c} = t.{c}")
                for c in columns
                if c != column
            ) or "true"
            for row in self.s.execute(
                text(
                    f"SELECT to_jsonb(t) ->> 'id' AS t_id, to_jsonb(b) ->> 'id' AS b_id FROM {table} t "
                    f"JOIN {table} b ON b.{column} = :s AND {equal} WHERE t.{column} = :l"
                ),
                {"s": survivor, "l": loser},
            ):
                pairs.append(f"{table} {row.t_id} / {row.b_id}")
        return pairs[:20] or [table]

    def _collision_predicate(self, table: str, column: str) -> str:
        """``AND NOT EXISTS (...)`` clauses keeping a move from violating a unique key the survivor already holds."""
        clauses = []
        for columns, nulls_not_distinct in self.cat.uniques(table):
            if column not in columns:
                continue
            others = [c for c in columns if c != column]
            equal = " AND ".join(
                (f"b.{c} IS NOT DISTINCT FROM t.{c}" if nulls_not_distinct else f"b.{c} = t.{c}") for c in others
            ) or "true"
            clauses.append(f"AND NOT EXISTS (SELECT 1 FROM {table} b WHERE b.{column} = :s AND {equal})")
        return " ".join(clauses)

    def _clear_colliding_group_labels(self, loser: int, survivor: int) -> None:
        cleared = self.s.execute(
            text(
                "UPDATE conformer_group SET label = NULL WHERE species_entry_id = :l "
                "AND label IN (SELECT label FROM conformer_group WHERE species_entry_id = :s)"
            ),
            {"l": loser, "s": survivor},
        ).rowcount
        self.report.conformer_group_labels_cleared += cleared or 0

    def _note_dependents(self, table: str, column: str, loser: int) -> None:
        if table == "network_state_participant":
            self.touched_states.update(
                self.s.execute(
                    text("SELECT state_id FROM network_state_participant WHERE species_entry_id = :l"), {"l": loser}
                ).scalars()
            )
        if table == "structure_determination":
            self.touched_determinations.update(
                self.s.execute(text("SELECT id FROM structure_determination WHERE species_entry_id = :l"), {"l": loser}).scalars()
            )

    # -- hashes that cover a re-pointed id ---------------------------------

    def rehash_dependents(self) -> None:
        self._rehash_structure_determinations()
        self._rehash_network_states()

    def _rehash_structure_determinations(self) -> None:
        if not self.touched_determinations:
            return
        rows = self.s.execute(
            text(
                "SELECT id, species_entry_id, transition_state_entry_id, conformer_observation_id, target_kind::text AS tk, "
                "literature_id, workflow_tool_release_id, determination_key, identity_hash FROM structure_determination "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": sorted(self.touched_determinations)},
        ).all()
        for row in rows:
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "version": _STRUCTURE_DETERMINATION_IDENTITY_VERSION,
                        "species_entry_id": row.species_entry_id,
                        "transition_state_entry_id": row.transition_state_entry_id,
                        "conformer_observation_id": row.conformer_observation_id if row.tk == "conformer_basin" else None,
                        "literature_id": row.literature_id,
                        "workflow_tool_release_id": row.workflow_tool_release_id,
                        "determination_key": row.determination_key,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if digest == row.identity_hash:
                continue
            self.repairs.ensure("structure_determination")
            try:
                with self.s.begin_nested():
                    self.s.execute(
                        text("UPDATE structure_determination SET identity_hash = :h WHERE id = :i"),
                        {"h": digest, "i": row.id},
                    )
            except DBAPIError:
                self.report.left_on_merged_rows.append(("structure_determination", "identity_hash", 1))

    def _rehash_network_states(self) -> None:
        for state_id in sorted(self.touched_states):
            participants = [
                [r.species_entry_id, r.stoichiometry]
                for r in self.s.execute(
                    text("SELECT species_entry_id, stoichiometry FROM network_state_participant WHERE state_id = :s"),
                    {"s": state_id},
                )
            ]
            digest = hashlib.sha256(json.dumps(sorted(participants), separators=(",", ":")).encode("utf-8")).hexdigest()
            self.repairs.ensure("network_state")
            try:
                with self.s.begin_nested():
                    self.s.execute(
                        text("UPDATE network_state SET composition_hash = :h WHERE id = :s AND composition_hash <> :h"),
                        {"h": digest, "s": state_id},
                    )
            except DBAPIError:
                # Another state of the same network already has this composition; both keep what they have.
                self.report.left_on_merged_rows.append(("network_state", "composition_hash", 1))

    # -- reviews -----------------------------------------------------------

    def reset_survivor_reviews(self, survivors: set[int]) -> None:
        """The survivor now holds results its review never saw: withdraw the review, with an event.

        Only the survivor's own review changes. A merged-away row's reviews stay on it untouched.
        """
        for entry_id in sorted(survivors):
            row = self.s.execute(
                text(
                    "SELECT id, status::text AS status FROM record_review "
                    "WHERE record_type = CAST('species_entry' AS submission_record_type) AND record_id = :i FOR UPDATE"
                ),
                {"i": entry_id},
            ).first()
            if row is None or row.status == "not_reviewed":
                continue
            self.s.execute(
                text(
                    "UPDATE record_review SET status = CAST('not_reviewed' AS record_review_status), "
                    "reviewed_by = NULL, reviewed_at = NULL WHERE id = :id"
                ),
                {"id": row.id},
            )
            self.s.execute(
                text(
                    "INSERT INTO record_review_event (record_review_id, event_kind, from_status, to_status, reason, details_json) "
                    "VALUES (:rid, CAST('status_change' AS record_review_event_kind), CAST(:from AS record_review_status), "
                    "CAST('not_reviewed' AS record_review_status), :reason, CAST(:details AS jsonb))"
                ),
                {
                    "rid": row.id,
                    "from": row.status,
                    "reason": (
                        "Species entries that differed only by stereo notation were merged into this one; the review "
                        "of the entry before the merge no longer applies."
                    ),
                    "details": json.dumps({"cause": "species_entry_merged"}),
                },
            )
            self.report.entries_review_reset += 1
