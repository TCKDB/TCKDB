"""The data step of the species-key / entry-descriptor migration (DR-0038).

A species is now its constitution -- stereo-blind canonical SMILES, charge and
multiplicity -- and stereochemistry is resolved on the species entry as a
canonical descriptor. Rows written under the old rule are therefore wrong in two
ways, and this step repairs both inside the migration's own transaction:

1. **Species keyed on stereo marks.** Every species key is recomputed from the
   stored SMILES. Species that now share a key are merged, the oldest id
   surviving. What pointed at a loser -- entries, reaction participants (whose
   primary key carries it; same-side collisions add their stoichiometry) and
   third-body colliders -- is moved. A reaction's ``stoichiometry_hash`` covers
   species ids, so it is recomputed, and reactions that now collide are merged
   the same way (their entries are re-pointed to the surviving reaction).
2. **Entries keyed on a free-text label.** Every entry gets the descriptor its
   own marks and its first linked geometry support (the order of authority the
   live upload applies), plus ``stereo_label``, ``stereo_resolution`` and
   ``stereo_smiles``. Entries whose key now collides are merged, the oldest id
   surviving, and every foreign key to ``species_entry`` is re-pointed. The
   foreign keys are read from the database catalogue at run time, not from a
   list written down here, so a table added since this was written cannot be
   missed.

Frozen rows
-----------
Rows under an accepted record change only through the declared-repair path
(``accepted_science_repair``, ``e2c9a4f7b163``): one declaration per guarded
table, naming exactly the columns written, in this transaction. The trigger is
never disabled. A hash that covers a re-pointed id is recomputed in the same
declaration. What the path cannot express is **not forced**: a change to a
primary-key column under an approved record, or two rows that would become
duplicates (two states of one network with the same composition, two third-body
efficiencies for one collider, ...) stops the migration with
:class:`BlockedMerge`, naming the table. Nothing is half-applied.

Contradictions
--------------
An entry whose SMILES marks and geometry disagree is not relabelled by a guess.
It gets the all-unknown descriptor (``?`` per element), keeps its old label text
for a curator, and is listed in the report.

Polymorphic references
----------------------
``record_review`` rows keep their approval history, which cannot be rewritten; a
loser entry's review is left where it is, and the *survivor's* review is reset to
``not_reviewed`` with an event, because the survivor is no longer the record that
was reviewed. Every other ``(record_type, record_id)`` table is re-pointed or the
migration stops.

Losers are deleted only when nothing references them any more. The mapping
loser -> survivor public ref is printed in the report.

Writes use SQL, not the ORM, so the step is not coupled to the models of a later
revision.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.chemistry import stereo
from app.chemistry.species import (
    identity_mol_from_smiles,
    strip_isotopes,
)
from app.services.conformer_reassignment import reassign_conformer_groups

logger = logging.getLogger(__name__)

_GUARD_FUNCTIONS = (
    "tckdb_guard_accepted_root",
    "tckdb_guard_accepted_child",
    "tckdb_guard_accepted_via_child",
    "tckdb_guard_calculation_geometry",
)

#: Hash columns that cover a re-pointed id, by the table that holds them.
#: ``structure_determination.identity_hash`` names its owning species entry.
#: ``network_state.composition_hash`` names its participants' entries.
_STRUCTURE_DETERMINATION_IDENTITY_VERSION = 1

_REASON = (
    "Species identity is the constitution and stereo is resolved on the species entry ({revision}). "
    "Re-points {columns} from an entry or species merged into its surviving duplicate; no measured, "
    "computed or declared scientific value changes."
)


class BlockedMerge(RuntimeError):
    """The data cannot be merged without a decision this step may not make."""


@dataclass
class MergeReport:
    """What the step did. Counts, and the lists a curator needs."""

    species_examined: int = 0
    species_rekeyed: int = 0
    species_unparseable: int = 0
    species_merged: int = 0
    reactions_rehashed: int = 0
    reactions_merged: int = 0
    entries_examined: int = 0
    entries_described: int = 0
    entries_merged: int = 0
    entries_review_reset: int = 0
    labels_discarded: int = 0
    rows_repointed: int = 0
    conformer_group_labels_cleared: int = 0
    repairs_declared: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    merged_refs: list[tuple[str, str, str]] = field(default_factory=list)
    conformer_reassignment: str = ""

    def summary(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in self.__dict__.items() if isinstance(v, int))
        return (
            f"{counts}, repairs_declared={self.repairs_declared}, "
            f"contradictions_left_for_curation={self.contradictions}, "
            f"merged(record, loser_ref, survivor_ref)={self.merged_refs}, "
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
        return [(r.tbl, r.col) for r in rows]

    def polymorphic_tables(self) -> list[str]:
        rows = self._s.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'record_type' "
                "AND table_name IN (SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'record_id') ORDER BY 1"
            )
        )
        return [r[0] for r in rows]

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
    for ref in ("species", "species_entry"):
        for table, column in catalogue.foreign_keys(ref):
            if (table, column) not in (("species_entry", "species_id"), ("reaction_participant", "species_id")):
                repairs.plan(table, [column])
    repairs.plan("structure_determination", ["identity_hash"])
    repairs.plan("network_state", ["composition_hash"])

    merger = _Merger(session, catalogue, repairs, report)
    merger.merge_species()
    merger.merge_reactions()
    merger.describe_entries()
    survivors = merger.merge_entries()
    merger.rehash_dependents()
    merger.reset_survivor_reviews(survivors)
    if survivors:
        reassignment = reassign_conformer_groups(session, repair_revision=revision, species_entry_ids=sorted(survivors))
        report.conformer_reassignment = reassignment.summary()
    # Run every deferred foreign-key check now: a dangling reference is a failure of this step, and pending trigger
    # events would also block the DDL that follows it.
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return report


# ---------------------------------------------------------------------------
# The work
# ---------------------------------------------------------------------------


class _Merger:
    def __init__(self, session: Session, catalogue: _Catalogue, repairs: _Repairs, report: MergeReport) -> None:
        self.s = session
        self.cat = catalogue
        self.repairs = repairs
        self.report = report
        self.touched_states: set[int] = set()
        self.touched_determination_entries: set[int] = set()
        self.touched_reactions: set[int] = set()

    # -- species -----------------------------------------------------------

    def merge_species(self) -> None:
        rows = self.s.execute(
            text("SELECT id, smiles, charge, multiplicity, kind::text AS kind, public_ref FROM species ORDER BY id")
        ).all()
        keyed: dict[tuple, list] = defaultdict(list)
        new_key: dict[int, str] = {}
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
            from rdkit import Chem
            from rdkit.Chem import inchi

            smiles = Chem.MolToSmiles(constitution, canonical=True)
            new_key[row.id] = (smiles, inchi.MolToInchiKey(constitution))
            keyed[(smiles, row.charge, row.multiplicity)].append(row)

        loser_to_survivor: dict[int, int] = {}
        for group in keyed.values():
            survivor = group[0]
            for loser in group[1:]:
                loser_to_survivor[loser.id] = survivor.id
                self.report.merged_refs.append(("species", loser.public_ref, survivor.public_ref))

        affected_reactions = self._reaction_ids_with_species(set(loser_to_survivor))
        for loser_id, survivor_id in loser_to_survivor.items():
            self.s.execute(
                text("UPDATE species_entry SET species_id = :s WHERE species_id = :l"), {"s": survivor_id, "l": loser_id}
            )
            for table, column in self.cat.foreign_keys("species"):
                if (table, column) in (("species_entry", "species_id"), ("reaction_participant", "species_id")):
                    continue
                self._move(table, column, loser_id, survivor_id)
        self._rewrite_participants(affected_reactions, loser_to_survivor)
        for loser_id in loser_to_survivor:
            self.s.execute(text("DELETE FROM species WHERE id = :l"), {"l": loser_id})
            self.report.species_merged += 1

        for row in rows:
            if row.id in loser_to_survivor or row.id not in new_key:
                continue
            smiles, key = new_key[row.id]
            changed = self.s.execute(
                text(
                    "UPDATE species SET smiles = :smiles, inchi_key = :key "
                    "WHERE id = :id AND (smiles <> :smiles OR inchi_key <> :key)"
                ),
                {"smiles": smiles, "key": key, "id": row.id},
            ).rowcount
            self.report.species_rekeyed += changed or 0

    def _reaction_ids_with_species(self, species_ids: set[int]) -> set[int]:
        if not species_ids:
            return set()
        rows = self.s.execute(
            text("SELECT DISTINCT reaction_id FROM reaction_participant WHERE species_id = ANY(:ids)"),
            {"ids": sorted(species_ids)},
        )
        return {r[0] for r in rows}

    def _rewrite_participants(self, reaction_ids: set[int], loser_to_survivor: dict[int, int]) -> None:
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
        for digest, ids in by_hash.items():
            holders = self.s.execute(
                text("SELECT id FROM chem_reaction WHERE stoichiometry_hash = :h AND id <> ALL(:touched)"),
                {"h": digest, "touched": sorted(self.touched_reactions)},
            ).scalars()
            group = sorted({*ids, *holders})
            survivor, losers = group[0], group[1:]
            for loser in losers:
                self.report.merged_refs.append(
                    (
                        "chem_reaction",
                        self.s.scalar(text("SELECT public_ref FROM chem_reaction WHERE id = :i"), {"i": loser}),
                        self.s.scalar(text("SELECT public_ref FROM chem_reaction WHERE id = :i"), {"i": survivor}),
                    )
                )
                self.s.execute(
                    text("UPDATE reaction_entry SET reaction_id = :s WHERE reaction_id = :l"),
                    {"s": survivor, "l": loser},
                )
                self.s.execute(text("DELETE FROM reaction_participant WHERE reaction_id = :l"), {"l": loser})
                for table, column in self.cat.foreign_keys("chem_reaction"):
                    if (table, column) not in (("reaction_entry", "reaction_id"), ("reaction_participant", "reaction_id")):
                        self._move(table, column, loser, survivor)
                self.s.execute(text("UPDATE chem_reaction SET stoichiometry_hash = NULL WHERE id = :l"), {"l": loser})
                self.s.execute(text("DELETE FROM chem_reaction WHERE id = :l"), {"l": loser})
                self.report.reactions_merged += 1
            self.s.execute(
                text("UPDATE chem_reaction SET stoichiometry_hash = :h WHERE id = :s AND stoichiometry_hash IS DISTINCT FROM :h"),
                {"h": digest, "s": survivor},
            )

    # -- entries: descriptors ---------------------------------------------

    def _first_geometry(self, entry_id: int) -> str | None:
        row = self.s.execute(
            text(
                "SELECT g.id, g.xyz_text FROM calculation c "
                "JOIN calculation_output_geometry o ON o.calculation_id = c.id "
                "JOIN geometry g ON g.id = o.geometry_id "
                "WHERE c.species_entry_id = :e ORDER BY c.id, o.output_order LIMIT 1"
            ),
            {"e": entry_id},
        ).first()
        if row is None:
            row = self.s.execute(
                text(
                    "SELECT g.id, g.xyz_text FROM calculation c "
                    "JOIN calculation_input_geometry i ON i.calculation_id = c.id "
                    "JOIN geometry g ON g.id = i.geometry_id "
                    "WHERE c.species_entry_id = :e ORDER BY c.id, i.input_order LIMIT 1"
                ),
                {"e": entry_id},
            ).first()
        if row is None:
            return None
        if row.xyz_text:
            return row.xyz_text
        atoms = self.s.execute(
            text("SELECT element, x, y, z FROM geometry_atom WHERE geometry_id = :g ORDER BY atom_index"),
            {"g": row.id},
        ).all()
        return "\n".join(f"{a.element} {a.x} {a.y} {a.z}" for a in atoms) if atoms else None

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
            descriptor = label = resolution = smiles = None
            if row.species_kind == "molecule":
                try:
                    frame = stereo.frame_from_mol(
                        strip_isotopes(identity_mol_from_smiles(row.mol_smiles or row.species_smiles))
                    )
                except ValueError:
                    frame = None
                if frame is not None and frame.is_stereo_capable:
                    resolved, contradicted = self._resolve(row, frame)
                    descriptor, label, resolution, smiles = (
                        resolved.descriptor,
                        # A contradictory entry keeps the label text it was deposited with, for the curator.
                        row.stereo_label if contradicted else resolved.label,
                        resolved.resolution.value,
                        resolved.stereo_smiles,
                    )
                if row.stereo_label and label is None and descriptor is None:
                    self.report.labels_discarded += 1
            self.s.execute(
                text(
                    "UPDATE species_entry SET stereo_label = :label, stereo_descriptor = :d, "
                    "stereo_resolution = CAST(:r AS stereo_resolution), stereo_smiles = :smiles WHERE id = :id"
                ),
                {"label": label, "d": descriptor, "r": resolution, "smiles": smiles, "id": row.id},
            )
            self.report.entries_described += 1

    def _resolve(self, row, frame: stereo.StereoFrame) -> tuple[stereo.ResolvedStereo, bool]:
        xyz = self._first_geometry(row.id)
        unknown = ",".join([stereo.UNKNOWN] * len(frame.elements))
        try:
            resolved = stereo.resolve_stereo(frame, geometry_xyz=xyz)
            if resolved.descriptor == unknown and row.stereo_label and len(frame.elements) == 1:
                # Nothing else supports a configuration; an old label is the only remaining evidence, and it is
                # positional, so it is used only where there is a single element for it to describe.
                try:
                    return stereo.resolve_stereo(frame, geometry_xyz=xyz, supplied_label=row.stereo_label.strip()), False
                except CodedValueError:
                    return resolved, False
            return resolved, False
        except CodedValueError as exc:
            if exc.code != stereo.W_SPECIES_STEREO_CONTRADICTION:
                raise
            self.report.contradictions.append(
                f"species_entry {row.public_ref}: SMILES marks and geometry disagree; left unresolved for curation"
            )
            return stereo.finish(frame, [stereo.UNKNOWN] * len(frame.elements)), True

    # -- entries: merging --------------------------------------------------

    def merge_entries(self) -> set[int]:
        rows = self.s.execute(
            text(
                "SELECT id, public_ref, species_id, kind::text AS kind, stereo_descriptor, "
                "electronic_state_kind::text AS esk, electronic_state_label, term_symbol, isotope_key "
                "FROM species_entry ORDER BY id"
            )
        ).all()
        groups: dict[tuple, list] = defaultdict(list)
        for row in rows:
            groups[
                (row.species_id, row.stereo_descriptor, row.esk, row.electronic_state_label, row.term_symbol, row.isotope_key)
            ].append(row)
        survivors: set[int] = set()
        for group in groups.values():
            if len(group) == 1:
                continue
            survivor = group[0]
            for loser in group[1:]:
                if loser.kind != survivor.kind:
                    raise BlockedMerge(
                        "two species entries that now share a key differ in stationary-point kind; "
                        "that is a decision for a curator, not for this migration"
                    )
                self._merge_entry(loser.id, survivor.id)
                self.report.merged_refs.append(("species_entry", loser.public_ref, survivor.public_ref))
                self.report.entries_merged += 1
            survivors.add(survivor.id)
        return survivors

    def _merge_entry(self, loser: int, survivor: int) -> None:
        for table, column in self.cat.foreign_keys("species_entry"):
            self._move(table, column, loser, survivor, entry=True)
        for table in self.cat.polymorphic_tables():
            if table in ("record_review", "accepted_science_repair_change"):
                continue
            self._move_polymorphic(table, loser, survivor)
        self.s.execute(text("DELETE FROM species_entry WHERE id = :l"), {"l": loser})

    def _move_polymorphic(self, table: str, loser: int, survivor: int) -> None:
        count = self.s.scalar(
            text(
                f"SELECT count(*) FROM {table} WHERE record_type = CAST('species_entry' AS submission_record_type) "
                "AND record_id = :l"
            ),
            {"l": loser},
        )
        if not count:
            return
        try:
            with self.s.begin_nested():
                self.s.execute(
                    text(
                        f"UPDATE {table} SET record_id = :s WHERE "
                        "record_type = CAST('species_entry' AS submission_record_type) AND record_id = :l"
                    ),
                    {"s": survivor, "l": loser},
                )
        except DBAPIError as exc:
            raise BlockedMerge(
                f"{table} holds {count} row(s) about a species entry that must be merged and cannot be re-pointed"
            ) from exc
        self.report.rows_repointed += count

    # -- moving one foreign key -------------------------------------------

    def _move(self, table: str, column: str, loser: int, survivor: int, *, entry: bool = False) -> None:
        count = self.s.scalar(text(f"SELECT count(*) FROM {table} WHERE {column} = :l"), {"l": loser})
        if not count:
            return
        self._resolve_collisions(table, column, loser, survivor)
        if entry:
            self._note_dependents(table, column, loser)
        self.repairs.ensure(table)
        try:
            with self.s.begin_nested():
                self.s.execute(text(f"UPDATE {table} SET {column} = :s WHERE {column} = :l"), {"s": survivor, "l": loser})
        except DBAPIError as exc:
            raise BlockedMerge(
                f"{table}.{column} cannot be re-pointed in place: the row sits under an accepted record and the "
                "declared-repair path cannot express this change (a primary-key column, or a column outside the "
                "declaration). Supersede or split the accepted record first."
            ) from exc
        self.report.rows_repointed += count

    def _note_dependents(self, table: str, column: str, loser: int) -> None:
        if table == "network_state_participant":
            self.touched_states.update(
                self.s.execute(text("SELECT state_id FROM network_state_participant WHERE species_entry_id = :l"), {"l": loser}).scalars()
            )
        if table == "structure_determination":
            self.touched_determination_entries.add(loser)

    def _resolve_collisions(self, table: str, column: str, loser: int, survivor: int) -> None:
        for columns, nulls_not_distinct in self.cat.uniques(table):
            if column not in columns:
                continue
            others = [c for c in columns if c != column]
            equal = " AND ".join(
                (f"a.{c} IS NOT DISTINCT FROM b.{c}" if nulls_not_distinct else f"a.{c} = b.{c}") for c in others
            ) or "true"
            collided = self.s.scalar(
                text(f"SELECT count(*) FROM {table} a JOIN {table} b ON {equal} WHERE a.{column} = :l AND b.{column} = :s"),
                {"l": loser, "s": survivor},
            )
            if not collided:
                continue
            if table == "conformer_group":
                cleared = self.s.execute(
                    text(
                        "UPDATE conformer_group SET label = NULL WHERE species_entry_id = :l "
                        "AND label IN (SELECT label FROM conformer_group WHERE species_entry_id = :s)"
                    ),
                    {"l": loser, "s": survivor},
                ).rowcount
                self.report.conformer_group_labels_cleared += cleared or 0
            elif table == "species_entry_review":
                self.s.execute(
                    text(
                        "DELETE FROM species_entry_review a USING species_entry_review b "
                        "WHERE a.species_entry_id = :l AND b.species_entry_id = :s "
                        "AND a.user_id = b.user_id AND a.role = b.role"
                    ),
                    {"l": loser, "s": survivor},
                )
            else:
                raise BlockedMerge(
                    f"{collided} row(s) of {table} would become duplicates ({', '.join(columns)}) when two "
                    "species or entries that now share a key are merged; deciding which one stands is a curator's "
                    "decision, not this migration's"
                )

    # -- hashes that cover a re-pointed id ---------------------------------

    def rehash_dependents(self) -> None:
        if self.touched_determination_entries:
            self._rehash_structure_determinations()
        self._rehash_network_states()

    def _rehash_structure_determinations(self) -> None:
        rows = self.s.execute(
            text(
                "SELECT id, species_entry_id, transition_state_entry_id, conformer_observation_id, target_kind::text AS tk, "
                "literature_id, workflow_tool_release_id, determination_key, identity_hash FROM structure_determination "
                "WHERE species_entry_id IS NOT NULL"
            )
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
            if digest != row.identity_hash:
                self.repairs.ensure("structure_determination")
                try:
                    with self.s.begin_nested():
                        self.s.execute(
                            text("UPDATE structure_determination SET identity_hash = :h WHERE id = :i"),
                            {"h": digest, "i": row.id},
                        )
                except DBAPIError as exc:
                    raise BlockedMerge(
                        "a structure determination's identity hash covers its species entry and cannot be rewritten "
                        "or would duplicate another determination"
                    ) from exc

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
            except DBAPIError as exc:
                raise BlockedMerge(
                    "two states of one network would have the same composition once their entries are merged; "
                    "which state stands is a curator's decision"
                ) from exc

    # -- reviews -----------------------------------------------------------

    def reset_survivor_reviews(self, survivors: set[int]) -> None:
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
