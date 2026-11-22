"""Admin deletion of an approved scientific record.

An approved record is immutable (``tckdb_raise_if_accepted``). This module is the one application path that removes
one, and it removes only what the record owns:

* **Owned rows** (:data:`OWNED_ROWS`) are deleted with the record: its children, their children, and its review and
  curation overlay rows (:data:`OVERLAY_ROWS`).
* **Citations** (:data:`CITATIONS`) are rows of *other* records that point at the record or at a row it owns. Any one
  of them refuses the deletion with ``record_deletion_cited``; nothing is cascaded, so the admin deletes the citing
  records first.
* **History links** (:data:`HISTORY_LINKS`): a supersession edge in either direction, a structure evidence finding
  about a calculation (with the adjudications settling it), and a structure-determination source pinning a
  calculation. They record what was said about the record rather than build on it, so they are copied into the audit
  row and removed with it. The record on the other side reads the deleted ref and its deletion date.
* A standing selection in a draft dataset release is a citation. A standing selection in a published release refuses
  with ``record_deletion_in_published_release``; the release is withdrawn first. Selection rows of withdrawn releases
  and superseded or withdrawn selections stay as release history, are copied into the audit row, and read the deleted
  ref and its deletion date.

The delete writes a ``record_deletion`` row first (a JSON copy of everything removed), names the record in the
transaction-local setting ``tckdb.record_deletion``, then deletes. The database guards stand down for a DELETE of the
record's rows only while both hold in the same transaction (``tckdb_record_deletion_permits``). Everything runs in one
savepoint: if any step fails, the audit row and the deletes roll back together.

``backend/tests/services/test_record_deletion_inventory.py`` fails if a foreign key into a deletable table, or a table
carrying a ``SubmissionRecordType`` column, is not classified here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.app_user import AppUser
from app.db.models.common import SubmissionRecordType
from app.db.models.record_deletion import RecordDeletion
from app.services.record_containers import resolve_record_container, resolve_record_containers
from app.services.record_models import RECORD_MODELS

#: The record types the admin route deletes. Every accepted-science root except ``applied_energy_correction``, which
#: has no public ref to address it by (see :mod:`app.services.record_refs`).
DELETABLE_RECORD_TYPES: tuple[SubmissionRecordType, ...] = (
    SubmissionRecordType.calculation,
    SubmissionRecordType.conformer_observation,
    SubmissionRecordType.kinetics,
    SubmissionRecordType.network,
    SubmissionRecordType.network_solve,
    SubmissionRecordType.statmech,
    SubmissionRecordType.thermo,
    SubmissionRecordType.transition_state_entry,
    SubmissionRecordType.transport,
)

#: Accepted-science roots the route does not address, and the reason.
UNADDRESSABLE_ROOTS: dict[str, str] = {
    "applied_energy_correction": "no public_ref column, so no record of this type can be named",
}


@dataclass(frozen=True)
class OwnedTable:
    """Rows of ``table`` that belong to one record: ``predicate`` selects them, with ``:id`` the record's id.

    ``owner`` is the foreign key the rows belong through, ``(column, referred_table)``; ``None`` where they belong
    through a composite key that the inventory lists in :data:`INTERNAL_LINKS`.
    """

    table: str
    predicate: str
    owner: tuple[str, str] | None


@dataclass(frozen=True)
class Citation:
    """A row of another record whose ``column`` points at the deleted record (``cites`` is its table) or at a row it owns.

    ``citer`` is what the citing record is called on the wire and ``ref_sql`` its public ref, as a SQL expression over
    the citing row aliased ``t``. ``NULL`` where the citing record has no public ref.
    """

    table: str
    column: str
    cites: str
    citer: str
    ref_sql: str
    #: The citing row has no public ref: ``ref_sql`` selects its id, and the record it belongs to is named instead.
    named_by_container: bool = False


def _direct(table: str, column: str, root: str) -> OwnedTable:
    return OwnedTable(table, f"{column} = :id", (column, root))


def _via(table: str, column: str, parent: OwnedTable) -> OwnedTable:
    return OwnedTable(table, f"{column} IN (SELECT id FROM {parent.table} WHERE {parent.predicate})", (column, parent.table))


def _calculation_owned() -> tuple[OwnedTable, ...]:
    direct = tuple(
        _direct(table, "calculation_id", "calculation")
        for table in (
            "calc_composite_input",
            "calc_composite_log_check",
            "calc_composite_result",
            "calc_composite_term",
            "calc_freq_mode",
            "calc_freq_result",
            "calc_geometry_validation",
            "calc_hessian",
            "calc_irc_point",
            "calc_irc_result",
            "calc_opt_result",
            "calc_path_search_point",
            "calc_path_search_result",
            "calc_scan_coordinate",
            "calc_scan_point",
            "calc_scan_result",
            "calc_scf_stability",
            "calc_sp_energy_component",
            "calc_sp_result",
            "calc_spin_diagnostic",
            "calc_wavefunction_diagnostic",
            "calculation_artifact",
            "calculation_constraint",
            "calculation_input_geometry",
            "calculation_output_geometry",
            "calculation_parameter",
            "software_version_attestation_calculation",
        )
    )
    artifact = next(owned for owned in direct if owned.table == "calculation_artifact")
    return (
        *direct,
        # Owned through a composite key into calc_scan_point / calc_scan_coordinate; see INTERNAL_LINKS.
        OwnedTable("calc_scan_point_coordinate_value", "calculation_id = :id", None),
        # The edge records what this calculation was derived from. An edge naming it as the parent is a citation.
        _direct("calculation_dependency", "child_calculation_id", "calculation"),
        _via("artifact_integrity_event", "artifact_id", artifact),
    )


def _owned_by(root: str, column: str, tables: tuple[str, ...]) -> tuple[OwnedTable, ...]:
    return tuple(_direct(table, column, root) for table in tables)


def _thermo_owned() -> tuple[OwnedTable, ...]:
    direct = _owned_by(
        "thermo",
        "thermo_id",
        (
            "applied_group_additivity",
            "thermo_nasa",
            "thermo_nasa9_interval",
            "thermo_point",
            "thermo_source_calculation",
            "thermo_wilhoit",
        ),
    )
    return (*direct, _via("applied_group_additivity_component", "applied_group_additivity_id", direct[0]))


def _statmech_owned() -> tuple[OwnedTable, ...]:
    declaration = _direct("statmech_model_declaration", "statmech_id", "statmech")
    torsion = _direct("statmech_torsion", "statmech_id", "statmech")
    return (
        _direct("statmech_electronic_level", "statmech_id", "statmech"),
        _direct("statmech_source_calculation", "statmech_id", "statmech"),
        declaration,
        torsion,
        _via("statmech_model_member", "declaration_id", declaration),
        _via("statmech_model_source", "declaration_id", declaration),
        _via("statmech_torsion_definition", "torsion_id", torsion),
    )


def _network_owned() -> tuple[OwnedTable, ...]:
    state = _direct("network_state", "network_id", "network")
    channel = _direct("network_channel", "network_id", "network")
    return (
        state,
        channel,
        _direct("network_reaction", "network_id", "network"),
        _direct("network_species", "network_id", "network"),
        _via("network_state_participant", "state_id", state),
        _via("network_channel_microreaction", "channel_id", channel),
    )


def _network_solve_owned() -> tuple[OwnedTable, ...]:
    direct = _owned_by(
        "network_solve",
        "solve_id",
        (
            "network_kinetics",
            "network_kinetics_determination",
            "network_solve_bath_gas",
            "network_solve_channel_barrier",
            "network_solve_energy_transfer",
            "network_solve_source_calculation",
            "network_solve_state_energy",
            "network_solve_state_energy_source",
        ),
    )
    fit = direct[0]
    return (
        *direct,
        _via("network_kinetics_chebyshev", "network_kinetics_id", fit),
        _via("network_kinetics_plog", "network_kinetics_id", fit),
        _via("network_kinetics_point", "network_kinetics_id", fit),
    )


def _transition_state_entry_owned() -> tuple[OwnedTable, ...]:
    atom_map = _direct("reaction_atom_map", "transition_state_entry_id", "transition_state_entry")
    evidence = _direct("transition_state_validation_evidence", "transition_state_entry_id", "transition_state_entry")
    return (
        atom_map,
        evidence,
        _via("reaction_atom_map_pair", "atom_map_id", atom_map),
        _via("transition_state_validation_energy", "evidence_id", evidence),
    )


#: Per deletable record type, every row it owns outside the polymorphic overlays. Deleted children-first, in the
#: reverse of ``Base.metadata.sorted_tables``.
OWNED_ROWS: dict[SubmissionRecordType, tuple[OwnedTable, ...]] = {
    SubmissionRecordType.calculation: _calculation_owned(),
    # A merge queued for the observation is bookkeeping about it, not a record of its own.
    SubmissionRecordType.conformer_observation: (
        _direct("conformer_group_merge_pending", "conformer_observation_id", "conformer_observation"),
    ),
    SubmissionRecordType.kinetics: _owned_by(
        "kinetics",
        "kinetics_id",
        (
            "kinetics_arrhenius_entry",
            "kinetics_bath_gas",
            "kinetics_chebyshev",
            "kinetics_falloff",
            "kinetics_interpretation_assignment",
            "kinetics_plog",
            "kinetics_source_calculation",
            "kinetics_third_body_efficiency",
            "kinetics_tunneling_application",
        ),
    ),
    SubmissionRecordType.network: _network_owned(),
    SubmissionRecordType.network_solve: _network_solve_owned(),
    SubmissionRecordType.statmech: _statmech_owned(),
    SubmissionRecordType.thermo: _thermo_owned(),
    SubmissionRecordType.transition_state_entry: _transition_state_entry_owned(),
    SubmissionRecordType.transport: (_direct("transport_source_calculation", "transport_id", "transport"),),
}

#: Foreign keys between two rows of the same record (a composite key that carries the owner's id, or a link between two
#: children of one network). ``(table, column, referred_table)``.
INTERNAL_LINKS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("calc_scan_point_coordinate_value", "calculation_id", "calc_scan_coordinate"),
        ("calc_scan_point_coordinate_value", "calculation_id", "calc_scan_point"),
        ("network_channel", "sink_state_id", "network_state"),
        ("network_channel", "source_state_id", "network_state"),
        ("network_kinetics", "determination_id", "network_kinetics_determination"),
        ("network_solve_state_energy_source", "solve_id", "network_solve_state_energy"),
        ("statmech_model_source", "declaration_id", "statmech_model_member"),
    }
)


def _ref_of(table: str, column: str) -> str:
    return f"(SELECT c.public_ref FROM {table} c WHERE c.id = t.{column})"


_SELF = "t.public_ref"
_ROW_ID = "t.id"
_SOLVE = _ref_of("network_solve", "solve_id")
_KINETICS = _ref_of("kinetics", "kinetics_id")
_CALCULATION = _ref_of("calculation", "calculation_id")
_STATMECH = _ref_of("statmech", "statmech_id")
_DECLARED_STATMECH = (
    "(SELECT s.public_ref FROM statmech_model_declaration d JOIN statmech s ON s.id = d.statmech_id "
    "WHERE d.id = t.declaration_id)"
)
_DETERMINATION = _ref_of("structure_determination", "determination_id")

#: Every foreign key by which another record points at a deletable record or at a row one owns.
CITATIONS: tuple[Citation, ...] = (
    # -- calculation --
    Citation(
        "applied_energy_correction",
        "source_calculation_id",
        "calculation",
        "applied_energy_correction",
        _ROW_ID,
        named_by_container=True,
    ),
    Citation("calc_composite_input", "input_calculation_id", "calculation", "calculation", _CALCULATION),
    Citation("calc_scf_stability", "source_calculation_id", "calculation", "calculation", _CALCULATION),
    Citation(
        "calculation_dependency",
        "parent_calculation_id",
        "calculation",
        "calculation",
        _ref_of("calculation", "child_calculation_id"),
    ),
    Citation("kinetics_source_calculation", "calculation_id", "calculation", "kinetics", _KINETICS),
    Citation("kinetics_tunneling_application", "source_calculation_id", "calculation", "kinetics", _KINETICS),
    Citation(
        "molecular_property_observation", "source_calculation_id", "calculation", "molecular_property_observation", _SELF
    ),
    Citation("network_solve_channel_barrier", "source_calculation_id", "calculation", "network_solve", _SOLVE),
    Citation("network_solve_source_calculation", "calculation_id", "calculation", "network_solve", _SOLVE),
    Citation("network_solve_state_energy", "source_calculation_id", "calculation", "network_solve", _SOLVE),
    Citation("network_solve_state_energy_source", "calculation_id", "calculation", "network_solve", _SOLVE),
    Citation("network_solve_state_energy_source", "zpe_calculation_id", "calculation", "network_solve", _SOLVE),
    Citation("statmech_model_source", "calculation_id", "calculation", "statmech", _DECLARED_STATMECH),
    Citation("statmech_source_calculation", "calculation_id", "calculation", "statmech", _STATMECH),
    Citation("statmech_torsion", "source_scan_calculation_id", "calculation", "statmech", _STATMECH),
    Citation("thermo_source_calculation", "calculation_id", "calculation", "thermo", _ref_of("thermo", "thermo_id")),
    Citation(
        "transition_state_validation_energy",
        "source_calculation_id",
        "calculation",
        "transition_state_entry",
        "(SELECT e.public_ref FROM transition_state_validation_evidence v "
        "JOIN transition_state_entry e ON e.id = v.transition_state_entry_id WHERE v.id = t.evidence_id)",
    ),
    Citation(
        "transition_state_validation_evidence",
        "reconstruction_calculation_id",
        "calculation",
        "transition_state_entry",
        _ref_of("transition_state_entry", "transition_state_entry_id"),
    ),
    Citation(
        "transport_source_calculation", "calculation_id", "calculation", "transport", _ref_of("transport", "transport_id")
    ),
    Citation("calc_scf_stability", "source_artifact_id", "calculation_artifact", "calculation", _CALCULATION),
    Citation("kinetics_tunneling_application", "result_artifact_id", "calculation_artifact", "kinetics", _KINETICS),
    Citation(
        "kinetics_tunneling_application", "sct_path_integral_artifact_id", "calculation_artifact", "kinetics", _KINETICS
    ),
    # -- conformer_observation --
    Citation(
        "applied_energy_correction",
        "source_conformer_observation_id",
        "conformer_observation",
        "applied_energy_correction",
        _ROW_ID,
        named_by_container=True,
    ),
    Citation("calculation", "conformer_observation_id", "conformer_observation", "calculation", _SELF),
    Citation("conformer_group", "representative_observation_id", "conformer_observation", "conformer_group", _SELF),
    Citation(
        "structure_determination", "conformer_observation_id", "conformer_observation", "structure_determination", _SELF
    ),
    Citation(
        "structure_determination_source",
        "conformer_observation_id",
        "conformer_observation",
        "structure_determination",
        _DETERMINATION,
    ),
    # -- network --
    Citation("network_solve", "network_id", "network", "network_solve", _SELF),
    Citation("kinetics_determination", "target_network_channel_id", "network_channel", "kinetics_determination", _SELF),
    Citation("network_kinetics", "channel_id", "network_channel", "network_solve", _SOLVE),
    Citation("network_kinetics_determination", "channel_id", "network_channel", "network_solve", _SOLVE),
    Citation("network_solve_channel_barrier", "channel_id", "network_channel", "network_solve", _SOLVE),
    Citation("network_solve_energy_transfer", "state_id", "network_state", "network_solve", _SOLVE),
    Citation("network_solve_state_energy", "state_id", "network_state", "network_solve", _SOLVE),
    Citation("network_solve_state_energy_source", "state_id", "network_state", "network_solve", _SOLVE),
    Citation("network_solve_state_energy_source", "state_id", "network_state_participant", "network_solve", _SOLVE),
    # -- network_solve --
    Citation("kinetics", "network_kinetics_id", "network_kinetics", "kinetics", _SELF),
    # -- statmech --
    Citation("kinetics_interpretation_assignment", "statmech_id", "statmech", "kinetics", _KINETICS),
    Citation("statmech_model_member", "member_statmech_id", "statmech", "statmech", _DECLARED_STATMECH),
    Citation("thermo", "statmech_id", "statmech", "thermo", _SELF),
    # -- transition_state_entry --
    Citation(
        "applied_energy_correction",
        "target_transition_state_entry_id",
        "transition_state_entry",
        "applied_energy_correction",
        _ROW_ID,
        named_by_container=True,
    ),
    Citation("calculation", "transition_state_entry_id", "transition_state_entry", "calculation", _SELF),
    Citation(
        "kinetics_determination",
        "target_transition_state_entry_id",
        "transition_state_entry",
        "kinetics_determination",
        _SELF,
    ),
    Citation(
        "kinetics_interpretation_assignment", "transition_state_entry_id", "transition_state_entry", "kinetics", _KINETICS
    ),
    Citation(
        "kinetics_tunneling_application", "transition_state_entry_id", "transition_state_entry", "kinetics", _KINETICS
    ),
    Citation(
        "network_channel_microreaction",
        "transition_state_entry_id",
        "transition_state_entry",
        "network",
        "(SELECT n.public_ref FROM network_channel ch JOIN network n ON n.id = ch.network_id WHERE ch.id = t.channel_id)",
    ),
    Citation(
        "network_solve_channel_barrier", "transition_state_entry_id", "transition_state_entry", "network_solve", _SOLVE
    ),
    Citation("statmech", "transition_state_entry_id", "transition_state_entry", "statmech", _SELF),
    Citation(
        "structure_determination",
        "transition_state_entry_id",
        "transition_state_entry",
        "structure_determination",
        _SELF,
    ),
    Citation(
        "structure_determination_source",
        "transition_state_entry_id",
        "transition_state_entry",
        "structure_determination",
        _DETERMINATION,
    ),
)


@dataclass(frozen=True)
class OverlayTable:
    """A review or curation table keyed by ``(record_type, record_id)``, directly or through ``parent``."""

    table: str
    parent: tuple[str, str] | None = None  # (column, parent overlay table)


#: Review and curation rows about a record, deleted with it. Children first.
OVERLAY_ROWS: tuple[OverlayTable, ...] = (
    OverlayTable("record_review_event", ("record_review_id", "record_review")),
    OverlayTable("record_review"),
    OverlayTable("record_machine_review"),
    OverlayTable("machine_review_curator_task"),
    OverlayTable("record_reproducibility_assessment"),
    OverlayTable("deposit_warning_retirement", ("deposit_warning_id", "deposit_warning")),
    OverlayTable("deposit_warning"),
    OverlayTable("submission_record_link"),
    OverlayTable("accepted_science_repair_change"),
)

@dataclass(frozen=True)
class HistoryLink:
    """Rows recording what was said about a record, removed with it under the same permit.

    ``predicate`` selects them for a record (``:id``, ``:record_type``); ``record_types`` limits it to the types it
    applies to (``None``: every deletable type); ``links`` are the foreign keys it covers, ``(column, referred table)``.
    """

    table: str
    predicate: str
    record_types: frozenset[SubmissionRecordType] | None
    links: tuple[tuple[str, str], ...] = ()


#: Findings about a calculation, and every adjudication settling one, with their distance from the first finding.
_FINDING_CHAIN = (
    "WITH RECURSIVE chain AS ("
    "SELECT id, 1 AS depth FROM structure_evidence_finding "
    "WHERE subject_calculation_id = :id OR source_calculation_id = :id "
    "UNION ALL SELECT f.id, chain.depth + 1 FROM structure_evidence_finding f "
    "JOIN chain ON f.supersedes_finding_id = chain.id WHERE chain.depth < 64"
    ") SELECT id, max(depth) AS depth FROM chain GROUP BY id"
)

_CALCULATION_ONLY = frozenset({SubmissionRecordType.calculation})

#: History links deleted with a record, after being copied into its audit row.
HISTORY_LINKS: tuple[HistoryLink, ...] = (
    HistoryLink(
        "scientific_record_supersession",
        "record_type = CAST(:record_type AS submission_record_type) "
        "AND (superseded_record_id = :id OR superseding_record_id = :id)",
        None,
    ),
    HistoryLink(
        "structure_evidence_finding",
        f"id IN (SELECT id FROM ({_FINDING_CHAIN}) AS findings)",
        _CALCULATION_ONLY,
        (("subject_calculation_id", "calculation"), ("source_calculation_id", "calculation")),
    ),
    HistoryLink(
        "structure_determination_source",
        "calculation_id = :id",
        _CALCULATION_ONLY,
        (("calculation_id", "calculation"),),
    ),
)

#: Tables with a ``SubmissionRecordType`` column that are not deleted with the record. Release selections refuse while
#: standing in a draft or published release and are otherwise kept as release history; ``record_deletion`` is this
#: audit.
POLYMORPHIC_NOT_DELETED: frozenset[str] = frozenset({"release_selection", "record_deletion"})


#: The code a read or a repeated deletion of a deleted record answers with (410).
RECORD_DELETED_CODE = "record_deleted"


class RecordDeletionError(Exception):
    """A refusal, carrying its HTTP status, stable code and structured context (public refs only)."""

    def __init__(self, *, status: int, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.context = dict(context or {})


def _owned_tables(record_type: SubmissionRecordType) -> dict[str, OwnedTable]:
    return {owned.table: owned for owned in OWNED_ROWS[record_type]}


def _delete_order(record_type: SubmissionRecordType) -> list[OwnedTable]:
    owned = _owned_tables(record_type)
    order = [table.name for table in reversed(Base.metadata.sorted_tables) if table.name in owned]
    missing = set(owned) - set(order)
    if missing:
        raise RuntimeError(f"owned tables missing from the ORM metadata: {sorted(missing)}")
    return [owned[name] for name in order]


def _overlay_keys(record_type: SubmissionRecordType) -> list[str]:
    """Predicates over ``(record_type, record_id)`` naming the record, and its artifacts for a calculation."""
    keys = ["(record_type = CAST(:record_type AS submission_record_type) AND record_id = :id)"]
    if record_type is SubmissionRecordType.calculation:
        keys.append("(record_type = 'artifact' AND record_id IN (SELECT id FROM calculation_artifact WHERE calculation_id = :id))")
    return keys


def _overlay_predicate(overlay: OverlayTable, key: str) -> str:
    if overlay.parent is None:
        return key
    column, parent = overlay.parent
    return f"{column} IN (SELECT id FROM {parent} WHERE {key})"


def _rows(session: Session, table: str, predicate: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return session.execute(
        text(f"SELECT coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb) FROM {table} t WHERE {predicate}"), params
    ).scalar_one()


def _citations(session: Session, record_type: SubmissionRecordType, record_id: int) -> list[dict[str, Any]]:
    root = record_type.value
    owned = _owned_tables(record_type)
    params = {"id": record_id, "record_type": root}
    found: list[dict[str, Any]] = []
    for citation in CITATIONS:
        if citation.cites == root:
            cited = ":id"
        elif citation.cites in owned:
            cited = f"SELECT id FROM {citation.cites} WHERE {owned[citation.cites].predicate}"
        else:
            continue
        exclude = f" AND NOT ({owned[citation.table].predicate})" if citation.table in owned else ""
        if citation.table == root:
            exclude += " AND t.id <> :id"
        refs = list(
            session.execute(
                text(
                    f"SELECT DISTINCT {citation.ref_sql} AS ref FROM {citation.table} t "
                    f"WHERE t.{citation.column} IN ({cited}){exclude}"
                ),
                params,
            ).scalars()
        )
        via = f"{citation.table}.{citation.column}"
        if citation.named_by_container:
            containers = resolve_record_containers(
                session, [(SubmissionRecordType(citation.citer), row_id) for row_id in refs]
            )
            for row_id in refs:
                container = containers.get((SubmissionRecordType(citation.citer), row_id))
                found.append(
                    {"record_type": container.container_type.value, "public_ref": container.container_ref, "via": via}
                    if container is not None
                    else {"record_type": citation.citer, "public_ref": None, "via": via}
                )
        else:
            found.extend({"record_type": citation.citer, "public_ref": ref, "via": via} for ref in refs)
    return found


_STANDING_SELECTIONS = text(
    "SELECT s.public_ref, r.tag, r.public_ref AS release_ref, r.status::text "
    "FROM release_selection s JOIN dataset_release r ON r.id = s.dataset_release_id "
    "WHERE s.record_type = CAST(:record_type AS submission_record_type) AND s.record_id = :id "
    "AND s.action <> 'withdraw' "
    "AND NOT EXISTS (SELECT 1 FROM release_selection n WHERE n.supersedes_selection_id = s.id) "
    "ORDER BY r.tag, s.public_ref"
)


def _subject_chain(session: Session, record_type: SubmissionRecordType, record_id: int) -> list[dict[str, str]]:
    chain: list[dict[str, str]] = []
    current: tuple[SubmissionRecordType, int] = (record_type, record_id)
    while True:
        container = resolve_record_container(session, record_type=current[0], record_id=current[1])
        if container is None:
            return chain
        chain.append({"record_type": container.container_type.value, "public_ref": container.container_ref})
        model = _model_for(container.container_type)
        container_id = session.scalar(select(model.id).where(model.public_ref == container.container_ref))
        if container_id is None:
            return chain
        current = (container.container_type, container_id)


def _model_for(record_type: SubmissionRecordType) -> Any:
    return RECORD_MODELS[record_type]


def _history_links(record_type: SubmissionRecordType) -> list[HistoryLink]:
    return [link for link in HISTORY_LINKS if link.record_types is None or record_type in link.record_types]


def _delete_history(session: Session, link: HistoryLink, params: dict[str, Any]) -> None:
    if link.table != "structure_evidence_finding":
        session.execute(text(f"DELETE FROM {link.table} WHERE {link.predicate}"), params)
        return
    # An adjudication names the finding it settles, so the latest are removed first.
    by_depth: dict[int, list[int]] = {}
    for finding_id, depth in session.execute(text(_FINDING_CHAIN), params).all():
        by_depth.setdefault(depth, []).append(finding_id)
    for depth in sorted(by_depth, reverse=True):
        session.execute(text("DELETE FROM structure_evidence_finding WHERE id = ANY(:ids)"), {"ids": by_depth[depth]})


def delete_approved_record(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    public_ref: str,
    reason: str,
    actor: AppUser,
) -> RecordDeletion:
    """Delete one approved record and everything it owns, after writing its audit row.

    :raises RecordDeletionError: 422 ``record_deletion_type_not_supported``; 404 ``handle_not_found``;
        410 ``record_deleted``; 409 ``record_deletion_not_approved``, ``record_deletion_in_published_release`` or
        ``record_deletion_cited``.
    """
    if record_type not in DELETABLE_RECORD_TYPES:
        raise RecordDeletionError(
            status=422,
            code="record_deletion_type_not_supported",
            message=f"records of type {record_type.value} cannot be deleted through this route.",
            context={"record_type": record_type.value, "supported": [kind.value for kind in DELETABLE_RECORD_TYPES]},
        )
    reason = reason.strip()
    root = record_type.value
    found = session.execute(
        text(f"SELECT id FROM {root} WHERE public_ref = :ref FOR UPDATE"), {"ref": public_ref}
    ).first()
    if found is None:
        raise_if_deleted(session, record_type=record_type, public_ref=public_ref)
        raise RecordDeletionError(
            status=404,
            code="handle_not_found",
            message=f"no {root} record has public ref {public_ref!r}.",
            context={"record_type": root, "public_ref": public_ref},
        )
    record_id = found[0]
    params = {"id": record_id, "record_type": root}
    earlier = session.scalar(
        select(RecordDeletion).where(RecordDeletion.record_type == record_type, RecordDeletion.record_id == record_id)
    )
    if earlier is not None:
        raise _deleted_error(earlier)

    first_approved_at = session.scalar(
        text(
            "SELECT first_approved_at FROM record_review "
            "WHERE record_type = CAST(:record_type AS submission_record_type) AND record_id = :id"
        ),
        params,
    )
    if first_approved_at is None:
        raise RecordDeletionError(
            status=409,
            code="record_deletion_not_approved",
            message=f"{root} {public_ref} was never approved; reject it through record review instead.",
            context={"record_type": root, "public_ref": public_ref},
        )

    selections = session.execute(_STANDING_SELECTIONS, params).all()
    published = [row for row in selections if row.status == "published"]
    if published:
        raise RecordDeletionError(
            status=409,
            code="record_deletion_in_published_release",
            message=f"{root} {public_ref} is selected in {len(published)} published dataset release(s); withdraw them first.",
            context={
                "record_type": root,
                "public_ref": public_ref,
                "releases": [{"tag": row.tag, "release_ref": row.release_ref} for row in published],
            },
        )

    cited_by = _citations(session, record_type, record_id)
    cited_by.extend(
        {"record_type": "release_selection", "public_ref": row.public_ref, "via": f"dataset_release {row.tag}"}
        for row in selections
        if row.status == "draft"
    )
    if cited_by:
        raise RecordDeletionError(
            status=409,
            code="record_deletion_cited",
            message=f"{root} {public_ref} is cited by {len(cited_by)} other record(s); delete or detach them first.",
            context={"record_type": root, "public_ref": public_ref, "cited_by": cited_by},
        )

    delete_order = _delete_order(record_type)
    keys = _overlay_keys(record_type)
    history = _history_links(record_type)
    snapshot = {
        "record": session.execute(text(f"SELECT to_jsonb(t) FROM {root} t WHERE id = :id"), params).scalar_one(),
        "owned": {owned.table: _rows(session, owned.table, owned.predicate, params) for owned in delete_order},
        "overlays": {
            overlay.table: _rows(
                session, overlay.table, " OR ".join(_overlay_predicate(overlay, key) for key in keys), params
            )
            for overlay in OVERLAY_ROWS
        },
        "history": {link.table: _rows(session, link.table, link.predicate, params) for link in history},
        "release_selections": _rows(
            session,
            "release_selection",
            "record_type = CAST(:record_type AS submission_record_type) AND record_id = :id",
            params,
        ),
    }
    chain = _subject_chain(session, record_type, record_id)

    with session.begin_nested():
        deletion = RecordDeletion(
            record_type=record_type,
            record_id=record_id,
            record_public_ref=public_ref,
            subject_type=SubmissionRecordType(chain[0]["record_type"]) if chain else None,
            subject_public_ref=chain[0]["public_ref"] if chain else None,
            subject_chain=chain,
            first_approved_at=first_approved_at,
            reason=reason,
            deleted_by=actor.id,
            snapshot=snapshot,
        )
        try:
            with session.begin_nested():
                session.add(deletion)
                session.flush()
        except IntegrityError:
            # Another deletion of the same record committed first: answer as for any deleted record.
            winner = deleted_records(session, [(record_type, record_id)]).get((record_type, record_id))
            if winner is None:
                raise
            raise _deleted_error(winner) from None
        # The guards stand down for this record only while this setting and the row above share a transaction.
        session.execute(
            text("SELECT set_config('tckdb.record_deletion', :record, true)"), {"record": f"{root}:{record_id}"}
        )
        for link in history:
            _delete_history(session, link, params)
        # Artifact overlays first: the guard finds their calculation through the artifact row.
        for key in keys[1:]:
            for overlay in OVERLAY_ROWS:
                session.execute(text(f"DELETE FROM {overlay.table} WHERE {_overlay_predicate(overlay, key)}"), params)
        for owned in delete_order:
            session.execute(text(f"DELETE FROM {owned.table} WHERE {owned.predicate}"), params)
        session.execute(text(f"DELETE FROM {root} WHERE id = :id"), params)
        for overlay in OVERLAY_ROWS:
            session.execute(text(f"DELETE FROM {overlay.table} WHERE {_overlay_predicate(overlay, keys[0])}"), params)
        session.execute(text("SELECT set_config('tckdb.record_deletion', '', true)"))
    # Raw deletes bypass the identity map; nothing loaded before them may be served afterwards.
    session.expire_all()
    return deletion


def find_deletion(session: Session, *, public_ref: str) -> RecordDeletion | None:
    return session.scalar(select(RecordDeletion).where(RecordDeletion.record_public_ref == public_ref))


def deleted_records(
    session: Session, pairs: Iterable[tuple[SubmissionRecordType, int]]
) -> dict[tuple[SubmissionRecordType, int], RecordDeletion]:
    """The audit rows of whichever of ``pairs`` an admin deleted, in one query."""
    wanted = {(SubmissionRecordType(record_type), int(record_id)) for record_type, record_id in pairs}
    if not wanted:
        return {}
    rows = session.scalars(
        select(RecordDeletion).where(tuple_(RecordDeletion.record_type, RecordDeletion.record_id).in_(sorted(wanted)))
    )
    return {(row.record_type, row.record_id): row for row in rows}


def record_type_for_model(model_cls: type) -> SubmissionRecordType | None:
    """The deletable record type an ORM class stores, or ``None``."""
    for record_type in DELETABLE_RECORD_TYPES:
        if RECORD_MODELS[record_type] is model_cls:
            return record_type
    return None


def _deleted_error(deletion: RecordDeletion) -> RecordDeletionError:
    """410 ``record_deleted``. The admin's reason is free text and travels only under ``context.admin_supplied``; the
    message is built from the code and values."""
    return RecordDeletionError(
        status=410,
        code=RECORD_DELETED_CODE,
        message=f"{deletion.record_type.value} {deletion.record_public_ref} was deleted by an administrator on "
        f"{deletion.deleted_at.date().isoformat()}.",
        context={
            "record_type": deletion.record_type.value,
            "public_ref": deletion.record_public_ref,
            "deleted_at": deletion.deleted_at.isoformat(),
            "admin_supplied": {"reason": deletion.reason},
        },
    )


def raise_if_deleted(session: Session, *, record_type: SubmissionRecordType | None, public_ref: str) -> None:
    """Refuse with 410 ``record_deleted`` when ``public_ref`` names a record an admin deleted."""
    deletion = find_deletion(session, public_ref=public_ref)
    if deletion is None or (record_type is not None and deletion.record_type is not record_type):
        return
    raise _deleted_error(deletion)


def raise_if_deleted_id(session: Session, *, record_type: SubmissionRecordType, record_id: int) -> None:
    """Refuse with 410 ``record_deleted`` when ``record_id`` was a record of ``record_type`` an admin deleted."""
    deletion = deleted_records(session, [(record_type, record_id)]).get((record_type, record_id))
    if deletion is not None:
        raise _deleted_error(deletion)


__all__ = [
    "CITATIONS",
    "DELETABLE_RECORD_TYPES",
    "HISTORY_LINKS",
    "INTERNAL_LINKS",
    "OVERLAY_ROWS",
    "OWNED_ROWS",
    "POLYMORPHIC_NOT_DELETED",
    "UNADDRESSABLE_ROOTS",
    "Citation",
    "HistoryLink",
    "OverlayTable",
    "OwnedTable",
    "RecordDeletionError",
    "delete_approved_record",
    "deleted_records",
    "find_deletion",
    "raise_if_deleted",
    "raise_if_deleted_id",
    "record_type_for_model",
]
