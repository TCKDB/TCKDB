"""Serializing a selected scientific record and its candidates for a release.

A dataset release has to ship numbers a reader can actually use. Emitting only
the ``thermo`` row without its NASA polynomial, or the ``kinetics`` row without
its Arrhenius parameters, would produce a checksummed file that is
scientifically empty.

Which child tables carry a record's *values* is stated explicitly in
:data:`RECORD_VALUE_TABLES` rather than discovered by walking foreign keys.
Generic FK traversal was tried and rejected: from ``statmech`` it reaches
``thermo`` (a sibling product, not a part of statmech), and from
``transition_state_entry`` it reaches the entire calculation graph. An explicit
map is bounded, reviewable, and says what a maintainer means. A test asserts it
covers every selectable record type.

Internal integer keys are stripped using the same predicate the public read API
uses (:func:`app.services.scientific_read.internal_ids.is_internal_id_key`), so
a release artifact addresses records the same way every other public surface
does: by public ref.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.common import SubmissionRecordType
from app.db.models.dataset_release import SELECTABLE_RECORD_TYPES
from app.db.models.kinetics import Kinetics
from app.db.models.network_pdep import NetworkSolve
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from app.db.models.transport import Transport
from app.services.scientific_read.internal_ids import is_internal_id_key


@dataclass(frozen=True)
class ChildTable:
    """One table holding part of a record's scientific value.

    ``fk_column`` is the column pointing back at the parent row. ``children``
    nests one further level for genuinely nested value structures (torsion →
    torsion definition, network kinetics → its Chebyshev/PLOG parameters).
    """

    table: str
    fk_column: str
    children: tuple["ChildTable", ...] = field(default=())


#: Table + column identifying the record itself, per selectable record type.
RECORD_TABLES: dict[SubmissionRecordType, str] = {
    SubmissionRecordType.thermo: "thermo",
    SubmissionRecordType.statmech: "statmech",
    SubmissionRecordType.transport: "transport",
    SubmissionRecordType.kinetics: "kinetics",
    SubmissionRecordType.network_solve: "network_solve",
    SubmissionRecordType.transition_state_entry: "transition_state_entry",
}


#: The value-bearing children of each selectable record type.
RECORD_VALUE_TABLES: dict[SubmissionRecordType, tuple[ChildTable, ...]] = {
    SubmissionRecordType.thermo: (
        ChildTable("thermo_nasa", "thermo_id"),
        ChildTable("thermo_nasa9_interval", "thermo_id"),
        ChildTable("thermo_wilhoit", "thermo_id"),
        ChildTable("thermo_point", "thermo_id"),
        ChildTable("thermo_source_calculation", "thermo_id"),
        # Group-additivity provenance. Without this a GA-*estimated* thermo
        # ships in a citable release looking exactly like a computed one, with
        # no indication that it is an estimate and no record of which Benson
        # groups produced it — the single most misleading thing a curated
        # thermochemistry release could do.
        ChildTable(
            "applied_group_additivity",
            "thermo_id",
            children=(
                ChildTable(
                    "applied_group_additivity_component",
                    "applied_group_additivity_id",
                ),
            ),
        ),
    ),
    SubmissionRecordType.statmech: (
        ChildTable("statmech_electronic_level", "statmech_id"),
        ChildTable(
            "statmech_torsion",
            "statmech_id",
            children=(ChildTable("statmech_torsion_definition", "torsion_id"),),
        ),
        ChildTable("statmech_source_calculation", "statmech_id"),
    ),
    SubmissionRecordType.transport: (
        ChildTable("transport_source_calculation", "transport_id"),
    ),
    SubmissionRecordType.kinetics: (
        ChildTable("kinetics_arrhenius_entry", "kinetics_id"),
        ChildTable("kinetics_chebyshev", "kinetics_id"),
        ChildTable("kinetics_plog", "kinetics_id"),
        ChildTable("kinetics_falloff", "kinetics_id"),
        ChildTable("kinetics_third_body_efficiency", "kinetics_id"),
        ChildTable("kinetics_tunneling_application", "kinetics_id"),
        ChildTable("kinetics_source_calculation", "kinetics_id"),
        # Interpretation provenance, not a curation overlay. It carries
        # ``standard_state_convention`` and ``ensemble_policy``, which change
        # how a released A-factor must be *read* — a rate coefficient reported
        # under a different standard state is a different number. The shipped
        # ``kinetics`` row already discloses degeneracy, its convention, the
        # A-factor units and the tunneling model, so omitting this was
        # survivable; it was never correct.
        ChildTable("kinetics_interpretation_assignment", "kinetics_id"),
    ),
    SubmissionRecordType.network_solve: (
        ChildTable("network_solve_bath_gas", "solve_id"),
        ChildTable("network_solve_energy_transfer", "solve_id"),
        ChildTable("network_solve_state_energy", "solve_id"),
        ChildTable("network_solve_channel_barrier", "solve_id"),
        ChildTable("network_solve_source_calculation", "solve_id"),
        ChildTable(
            "network_kinetics",
            "solve_id",
            children=(
                ChildTable("network_kinetics_chebyshev", "network_kinetics_id"),
                ChildTable("network_kinetics_plog", "network_kinetics_id"),
                ChildTable("network_kinetics_point", "network_kinetics_id"),
            ),
        ),
    ),
    SubmissionRecordType.transition_state_entry: (
        ChildTable("transition_state_validation_evidence", "transition_state_entry_id"),
    ),
}


#: Child tables **deliberately not** shipped under a given parent, and why.
#:
#: Keyed on ``(parent_table, child_table)``, not on the child alone. A global
#: excuse is unsafe: four tables here are legitimately shipped under one parent
#: while being excused under another, so a single-name registry would let a
#: refactor drop ``network_kinetics`` from ``RECORD_VALUE_TABLES[network_solve]``
#: and still satisfy the guard — every PDep release would silently stop shipping
#: its k(T,P). That is exactly the bug class this registry exists to prevent.
#:
#: Every child (and grandchild) of a shipped table must appear either in
#: :data:`RECORD_VALUE_TABLES` under that parent or here under that parent; a
#: test enforces it, so a new table added to the schema fails loudly instead of
#: being silently omitted from every future release. This guard is what caught
#: ``applied_group_additivity`` being missing, which would have shipped
#: Benson-group *estimates* looking exactly like computed values.
RECORD_CHILD_EXCLUSIONS: dict[tuple[str, str], str] = {
    # --- provenance cited by ref, not embedded ----------------------------
    # A calculation is provenance, not part of the product's value. Releases
    # cite it by ref with its level of theory and software (see
    # ``calculation_provenance``); embedding it would pull in the entire ESS
    # result graph.
    ("transition_state_entry", "calculation"): (
        "cited by ref with level of theory and software instead"
    ),
    # --- sibling products with their own selection candidacy ---------------
    # A thermo derived from a statmech is its own selectable record. Nesting it
    # would duplicate it and imply this release endorsed it.
    ("statmech", "thermo"): "sibling product with its own selection candidacy",
    ("transition_state_entry", "statmech"): (
        "sibling product with its own selection candidacy"
    ),
    ("network_kinetics", "kinetics"): (
        "sibling product with its own selection candidacy"
    ),
    # --- owned by a different parent, and shipped there --------------------
    ("statmech", "kinetics_interpretation_assignment"): (
        "owned by its kinetics parent, and shipped under kinetics"
    ),
    ("transition_state_entry", "kinetics_interpretation_assignment"): (
        "owned by its kinetics parent, and shipped under kinetics"
    ),
    ("transition_state_entry", "kinetics_tunneling_application"): (
        "owned by its kinetics parent, and shipped under kinetics"
    ),
    ("transition_state_entry", "network_solve_channel_barrier"): (
        "owned by its network_solve parent, and shipped under network_solve"
    ),
    ("network_channel", "network_kinetics"): (
        "owned by its network_solve parent, and shipped under network_solve"
    ),
    ("transition_state_entry", "transition_state_validation_evidence"): (
        "shipped under transition_state_entry itself"
    ),
    # --- belongs to the network, not to any one solve ----------------------
    ("transition_state_entry", "network_channel_microreaction"): (
        "belongs to the network's composition, not to a solve"
    ),
    # --- corrections reachable through calculation provenance --------------
    ("transition_state_entry", "applied_energy_correction"): (
        "reachable via calculation provenance"
    ),
}


#: How to enumerate every *candidate* of a record type for one subject.
#: ``(orm_class, subject_fk_attribute, subject_record_type)``.
#:
#: This is what makes "you can still retrieve the underlying candidates" a
#: property of the release rather than a promise: a release ships not only what
#: a curator chose but every record it was chosen *from*.
CANDIDATE_SOURCES: dict[SubmissionRecordType, tuple[type, str, SubmissionRecordType]] = {
    SubmissionRecordType.thermo: (
        Thermo,
        "species_entry_id",
        SubmissionRecordType.species_entry,
    ),
    SubmissionRecordType.statmech: (
        Statmech,
        "species_entry_id",
        SubmissionRecordType.species_entry,
    ),
    SubmissionRecordType.transport: (
        Transport,
        "species_entry_id",
        SubmissionRecordType.species_entry,
    ),
    SubmissionRecordType.kinetics: (
        Kinetics,
        "reaction_entry_id",
        SubmissionRecordType.reaction_entry,
    ),
    SubmissionRecordType.network_solve: (
        NetworkSolve,
        "network_id",
        SubmissionRecordType.network,
    ),
    SubmissionRecordType.transition_state_entry: (
        TransitionStateEntry,
        "transition_state_id",
        SubmissionRecordType.transition_state,
    ),
}


class UnsupportedRecordTypeError(ValueError):
    """Raised for a record type the release layer cannot serialize."""


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def encode_scalar(value: Any) -> Any:
    """Coerce a database value to a JSON-safe, byte-stable representation.

    Determinism matters more than prettiness here: these values are hashed, so
    two renderings of the same row must produce identical bytes. ``Decimal``
    becomes a string rather than a float precisely because float repr is where
    that guarantee would quietly break.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [encode_scalar(v) for v in value]
    if isinstance(value, dict):
        return {str(k): encode_scalar(v) for k, v in sorted(value.items())}
    return str(value)


#: Stable natural keys for FK targets that carry no ``public_ref``.
#:
#: ``network_channel`` and ``network_state`` are *intra-network* structure —
#: wells and channels of one master-equation network — rather than
#: independently addressable records, so they were never given public refs.
#: Without a substitute, a released ``network_solve`` shipped
#: ``network_solve_channel_barrier`` and ``network_solve_state_energy`` rows
#: naming no channel and no state: barriers and well energies that could not be
#: attached to anything. ``channel_key`` and ``composition_hash`` are both
#: already unique within their network and stable across reseeds.
#:
#: Targets absent from here *and* lacking a public ref keep the old behaviour
#: of dropping the key, which is correct for ``created_by`` (a user primary key
#: must never reach a public artifact) and for ``calculation_artifact`` ids,
#: whose omission the manifest's ``omits`` block already declares.
NATURAL_KEYS: dict[str, str] = {
    "network_channel": "channel_key",
    "network_state": "composition_hash",
}


class RefResolver:
    """Turns internal foreign keys into public refs, batched and cached.

    A release artifact must not leak integer primary keys — but stripping them
    with no substitute is worse than leaking them. It was: a published
    ``thermo`` line kept its numbers and lost ``species_entry_id``,
    ``statmech_id``, ``literature_id``, ``software_release_id`` and
    ``workflow_tool_release_id``, and each ``thermo_source_calculation`` row
    serialized to a bare ``{"role": "..."}`` naming no calculation. The
    deposited file stated a heat of formation for an opaque handle with no
    level of theory, no software and no citation — strictly less science than
    the unauthenticated read API already gives away.

    Substitution is driven by real SQLAlchemy foreign-key metadata rather than
    a hand-written column list, so a new FK to a ref-bearing table is carried
    into releases automatically instead of being silently dropped.

    Some FK targets have no ``public_ref`` because they are *intra-network*
    structure rather than independently addressable records —
    ``network_channel`` and ``network_state``. Dropping those left a released
    ``network_solve`` shipping channel barriers and state energies that named
    no channel and no state, so the numbers could not be attached to the wells
    or channels they belong to. :data:`NATURAL_KEYS` gives each a stable
    natural key to emit instead.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._cache: dict[tuple[str, int], str | None] = {}

    @staticmethod
    def fk_targets(table: Table) -> dict[str, tuple[str, str]]:
        """``{column: (target_table, identifying_column)}`` for resolvable FKs.

        The identifying column is ``public_ref`` where the target has one, and
        otherwise the target's declared natural key. A target with neither is
        omitted, and its FK is dropped as before — that is correct for
        ``created_by`` (a user primary key must never reach a public artifact)
        and for artifact ids, which the manifest's ``omits`` block declares.
        """
        targets: dict[str, tuple[str, str]] = {}
        for column in table.c:
            for fk in column.foreign_keys:
                target = fk.column.table
                if "public_ref" in target.c:
                    targets[column.name] = (target.name, "public_ref")
                elif target.name in NATURAL_KEYS:
                    targets[column.name] = (target.name, NATURAL_KEYS[target.name])
        return targets

    @staticmethod
    def emitted_field(fk_column: str, identifying_column: str) -> str:
        """Deterministic output field name for a substituted foreign key.

        ``lot_id`` + ``public_ref`` → ``lot_ref``;
        ``channel_id`` + ``channel_key`` → ``channel_key``;
        ``state_id`` + ``composition_hash`` → ``state_composition_hash``.
        """
        base = fk_column[:-3] if fk_column.endswith("_id") else fk_column
        if identifying_column == "public_ref":
            return f"{base}_ref"
        if identifying_column.startswith(f"{base}_"):
            return identifying_column
        return f"{base}_{identifying_column}"

    def prime(self, table: Table, mappings: list[dict[str, Any]]) -> None:
        """Bulk-load every identifier this batch will need: one query per target."""
        wanted: dict[tuple[str, str], set[int]] = {}
        for column_name, (target_name, id_column) in self.fk_targets(table).items():
            for mapping in mappings:
                value = mapping.get(column_name)
                if isinstance(value, int) and (target_name, value) not in self._cache:
                    wanted.setdefault((target_name, id_column), set()).add(value)
        for (target_name, id_column), ids in wanted.items():
            target = _table(target_name)
            rows = self._session.execute(
                select(target.c.id, target.c[id_column]).where(
                    target.c.id.in_(sorted(ids))
                )
            ).all()
            found = dict(rows)
            for row_id in ids:
                self._cache[(target_name, row_id)] = found.get(row_id)

    def identifier(self, target_table: str, id_column: str, row_id: int) -> str | None:
        key = (target_table, row_id)
        if key not in self._cache:
            target = _table(target_table)
            self._cache[key] = self._session.scalar(
                select(target.c[id_column]).where(target.c.id == row_id)
            )
        return self._cache[key]

    def ref(self, target_table: str, row_id: int) -> str | None:
        """Public ref for a ref-bearing target."""
        return self.identifier(target_table, "public_ref", row_id)

    def encode_row(self, table: Table, mapping: dict[str, Any]) -> dict[str, Any]:
        """JSON-safe row payload: integer keys replaced by stable identifiers."""
        targets = self.fk_targets(table)
        out: dict[str, Any] = {}
        for key, value in sorted(mapping.items()):
            if not is_internal_id_key(key):
                out[key] = encode_scalar(value)
                continue
            resolved = targets.get(key)
            if resolved is None or not isinstance(value, int):
                # No resolvable target (or a NULL FK): the raw key stays out.
                continue
            target_name, id_column = resolved
            out[self.emitted_field(key, id_column)] = encode_scalar(
                self.identifier(target_name, id_column, value)
            )
        return out


def _encode_row(mapping: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe row payload with internal integer keys removed (no refs)."""
    return {
        key: encode_scalar(value)
        for key, value in sorted(mapping.items())
        if not is_internal_id_key(key)
    }


def _fetch_children(
    session: Session,
    spec: ChildTable,
    parent_ids: list[int],
    resolver: RefResolver,
) -> dict[int, list[dict[str, Any]]]:
    """Load one child table's rows for a set of parent ids, keyed by parent."""
    if not parent_ids:
        return {}
    table = _table(spec.table)
    fk = table.c[spec.fk_column]
    stmt = select(table).where(fk.in_(parent_ids)).order_by(*table.primary_key.columns)
    out: dict[int, list[dict[str, Any]]] = {}
    raw: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    payloads: list[dict[str, Any]] = []
    for row in session.execute(stmt).mappings():
        payload = dict(row)
        parent = payload[spec.fk_column]
        pk = tuple(payload[c.name] for c in table.primary_key.columns)
        raw.setdefault(parent, []).append((pk, payload))
        payloads.append(payload)
    resolver.prime(table, payloads)

    for parent, entries in raw.items():
        rendered: list[dict[str, Any]] = []
        for pk, payload in entries:
            encoded = resolver.encode_row(table, payload)
            for nested in spec.children:
                nested_rows = _fetch_children(session, nested, [pk[0]], resolver)
                encoded[nested.table] = nested_rows.get(pk[0], [])
            rendered.append(encoded)
        out[parent] = rendered
    return out


def serialize_records(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_ids: list[int],
    resolver: "RefResolver | None" = None,
) -> dict[int, dict[str, Any]]:
    """Render each record's own row plus its value-bearing child rows.

    Foreign keys become public refs (``statmech_ref``, ``literature_ref``,
    ``calculation_ref``, …) rather than being dropped, so a released record
    still points at the science that produced it.

    :raises UnsupportedRecordTypeError: ``record_type`` is not selectable.
    """
    if record_type not in SELECTABLE_RECORD_TYPES:
        raise UnsupportedRecordTypeError(
            f"unsupported_release_record_type: {record_type.value}"
        )
    if not record_ids:
        return {}

    resolver = resolver or RefResolver(session)
    table = _table(RECORD_TABLES[record_type])
    stmt = select(table).where(table.c.id.in_(record_ids)).order_by(table.c.id)
    rows = {row["id"]: dict(row) for row in session.execute(stmt).mappings()}
    resolver.prime(table, list(rows.values()))

    child_payloads = {
        spec.table: _fetch_children(session, spec, list(rows), resolver)
        for spec in RECORD_VALUE_TABLES[record_type]
    }

    rendered: dict[int, dict[str, Any]] = {}
    for record_id, payload in rows.items():
        encoded = resolver.encode_row(table, payload)
        for spec in RECORD_VALUE_TABLES[record_type]:
            encoded[spec.table] = child_payloads[spec.table].get(record_id, [])
        rendered[record_id] = encoded
    return rendered


def subject_identities(
    session: Session,
    *,
    subject_type: SubmissionRecordType,
    subject_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Chemical identity for each subject a release makes a claim about.

    A released heat of formation attached only to ``spe_2mvs3dbw…`` is not
    checkable offline — the reader cannot even tell which molecule it is. This
    emits the identity a chemist actually needs (SMILES, InChIKey, charge,
    multiplicity, electronic state; reactant/product SMILES for a reaction) so
    a deposited file stands on its own.

    Unknown subject types degrade to just the ref rather than raising: a
    missing label is a gap, not a reason to refuse to publish.
    """
    if not subject_ids:
        return {}
    ids = sorted(set(subject_ids))

    if subject_type is SubmissionRecordType.species_entry:
        entry = _table("species_entry")
        species = _table("species")
        stmt = (
            select(
                entry.c.id,
                entry.c.public_ref,
                entry.c.stereo_label,
                entry.c.electronic_state_label,
                entry.c.isotopologue_label,
                entry.c.kind,
                species.c.public_ref.label("species_ref"),
                species.c.smiles,
                species.c.inchi_key,
                species.c.charge,
                species.c.multiplicity,
            )
            .select_from(entry.join(species, entry.c.species_id == species.c.id))
            .where(entry.c.id.in_(ids))
        )
        return {
            row.id: {
                "species_entry_ref": row.public_ref,
                "species_ref": row.species_ref,
                "smiles": row.smiles,
                "inchi_key": row.inchi_key,
                "charge": row.charge,
                "multiplicity": row.multiplicity,
                "stereo_label": row.stereo_label,
                "electronic_state_label": row.electronic_state_label,
                "isotopologue_label": row.isotopologue_label,
                "stationary_point_kind": encode_scalar(row.kind),
            }
            for row in session.execute(stmt)
        }

    if subject_type is SubmissionRecordType.reaction_entry:
        return _reaction_entry_identities(session, ids)

    if subject_type is SubmissionRecordType.transition_state:
        ts = _table("transition_state")
        rows = session.execute(
            select(ts.c.id, ts.c.public_ref, ts.c.label, ts.c.reaction_entry_id).where(
                ts.c.id.in_(ids)
            )
        ).all()
        reactions = _reaction_entry_identities(
            session, sorted({r.reaction_entry_id for r in rows if r.reaction_entry_id})
        )
        return {
            row.id: {
                "transition_state_ref": row.public_ref,
                "label": row.label,
                "reaction": reactions.get(row.reaction_entry_id),
            }
            for row in rows
        }

    if subject_type is SubmissionRecordType.network:
        net = _table("network")
        return {
            row.id: {
                "network_ref": row.public_ref,
                "name": row.name,
                "description": row.description,
            }
            for row in session.execute(
                select(net.c.id, net.c.public_ref, net.c.name, net.c.description).where(
                    net.c.id.in_(ids)
                )
            )
        }

    table = _table(subject_type.value)
    if "public_ref" not in table.c:
        return {}
    return {
        row.id: {f"{subject_type.value}_ref": row.public_ref}
        for row in session.execute(
            select(table.c.id, table.c.public_ref).where(table.c.id.in_(ids))
        )
    }


def _reaction_entry_identities(
    session: Session, ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Reaction identity with reactant/product SMILES, so it reads as chemistry."""
    if not ids:
        return {}
    entry = _table("reaction_entry")
    reaction = _table("chem_reaction")
    rows = session.execute(
        select(
            entry.c.id,
            entry.c.public_ref,
            reaction.c.public_ref.label("reaction_ref"),
            reaction.c.reversible,
            reaction.c.reaction_family_raw,
            entry.c.reaction_id,
        )
        .select_from(entry.join(reaction, entry.c.reaction_id == reaction.c.id))
        .where(entry.c.id.in_(ids))
    ).all()

    participants = _table("reaction_participant")
    species = _table("species")
    by_reaction: dict[int, dict[str, list[str]]] = {}
    reaction_ids = sorted({r.reaction_id for r in rows})
    if reaction_ids:
        prows = session.execute(
            select(
                participants.c.reaction_id,
                participants.c.role,
                species.c.smiles,
            )
            .select_from(
                participants.join(species, participants.c.species_id == species.c.id)
            )
            .where(participants.c.reaction_id.in_(reaction_ids))
            .order_by(participants.c.reaction_id, species.c.smiles)
        ).all()
        for prow in prows:
            side = by_reaction.setdefault(
                prow.reaction_id, {"reactants": [], "products": []}
            )
            key = "reactants" if str(prow.role).endswith("reactant") else "products"
            side[key].append(prow.smiles)

    return {
        row.id: {
            "reaction_entry_ref": row.public_ref,
            "reaction_ref": row.reaction_ref,
            "reversible": row.reversible,
            "reaction_family": row.reaction_family_raw,
            **by_reaction.get(row.reaction_id, {"reactants": [], "products": []}),
        }
        for row in rows
    }


def calculation_provenance(
    session: Session, calculation_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Level of theory and software for each cited calculation.

    A ``*_source_calculation`` row that names only a role
    (``{"role": "sp"}``) tells a reader nothing. This is what makes the cited
    provenance interpretable without querying the database the release came
    from.
    """
    if not calculation_ids:
        return {}
    calc = _table("calculation")
    lot = _table("level_of_theory")
    srel = _table("software_release")
    soft = _table("software")
    stmt = (
        select(
            calc.c.id,
            calc.c.public_ref,
            calc.c.type,
            lot.c.public_ref.label("lot_ref"),
            lot.c.method,
            lot.c.basis,
            lot.c.dispersion,
            lot.c.solvent,
            lot.c.solvent_model,
            soft.c.name.label("software_name"),
            srel.c.version.label("software_version"),
            srel.c.public_ref.label("software_release_ref"),
        )
        .select_from(
            calc.outerjoin(lot, calc.c.lot_id == lot.c.id)
            .outerjoin(srel, calc.c.software_release_id == srel.c.id)
            .outerjoin(soft, srel.c.software_id == soft.c.id)
        )
        .where(calc.c.id.in_(sorted(set(calculation_ids))))
    )
    return {
        row.id: {
            "calculation_ref": row.public_ref,
            "calculation_type": encode_scalar(row.type),
            "level_of_theory": (
                {
                    "level_of_theory_ref": row.lot_ref,
                    "method": row.method,
                    "basis": row.basis,
                    "dispersion": row.dispersion,
                    "solvent": row.solvent,
                    "solvent_model": row.solvent_model,
                }
                if row.lot_ref is not None
                else None
            ),
            "software": (
                {
                    "software_release_ref": row.software_release_ref,
                    "name": row.software_name,
                    "version": row.software_version,
                }
                if row.software_release_ref is not None
                else None
            ),
        }
        for row in session.execute(stmt)
    }


def cited_calculation_ids(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_ids: list[int],
) -> dict[int, list[int]]:
    """Calculation ids each record cites through its ``*_source_calculation``."""
    if not record_ids:
        return {}
    out: dict[int, list[int]] = {}
    for spec in RECORD_VALUE_TABLES.get(record_type, ()):
        if not spec.table.endswith("_source_calculation"):
            continue
        table = _table(spec.table)
        if "calculation_id" not in table.c:
            continue
        fk = table.c[spec.fk_column]
        rows = session.execute(
            select(fk, table.c.calculation_id).where(fk.in_(sorted(set(record_ids))))
        ).all()
        for parent_id, calculation_id in rows:
            if calculation_id is not None:
                out.setdefault(parent_id, []).append(calculation_id)
    return {k: sorted(set(v)) for k, v in out.items()}


def candidate_ids_for_subjects(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    subject_ids: list[int],
) -> dict[int, list[int]]:
    """Every candidate record id of ``record_type``, grouped by subject id.

    :raises UnsupportedRecordTypeError: ``record_type`` is not selectable.
    """
    if record_type not in CANDIDATE_SOURCES:
        raise UnsupportedRecordTypeError(
            f"unsupported_release_record_type: {record_type.value}"
        )
    if not subject_ids:
        return {}
    model, fk_attr, _subject_type = CANDIDATE_SOURCES[record_type]
    fk = getattr(model, fk_attr)
    stmt = select(model.id, fk).where(fk.in_(subject_ids)).order_by(model.id)
    out: dict[int, list[int]] = {}
    for record_id, subject_id in session.execute(stmt):
        out.setdefault(subject_id, []).append(record_id)
    return out


def public_refs_for(
    session: Session, *, record_type: SubmissionRecordType, record_ids: list[int]
) -> dict[int, str]:
    """Map record ids to public refs for any ref-bearing selectable type."""
    if not record_ids:
        return {}
    table = _table(RECORD_TABLES.get(record_type) or record_type.value)
    if "public_ref" not in table.c:
        return {}
    stmt = select(table.c.id, table.c.public_ref).where(table.c.id.in_(record_ids))
    return dict(session.execute(stmt).all())


__all__ = [
    "CANDIDATE_SOURCES",
    "NATURAL_KEYS",
    "RECORD_CHILD_EXCLUSIONS",
    "RECORD_TABLES",
    "RECORD_VALUE_TABLES",
    "ChildTable",
    "RefResolver",
    "UnsupportedRecordTypeError",
    "calculation_provenance",
    "candidate_ids_for_subjects",
    "cited_calculation_ids",
    "encode_scalar",
    "public_refs_for",
    "serialize_records",
    "subject_identities",
]
