"""Operator backfill: link pre-existing ``molecular_property_observation``
rows to a submission wrapper.

Closes issue #514. ``app.services.observation_identity_attach`` records the
curator's identity-attach fact as a :class:`~app.db.models.submission.
SubmissionAuditEvent` on the submission the observation is linked to via
``submission_record_link`` -- an observation with no such link has nowhere
honest to record the fact and the attach is refused
(``observation_identity_attach_requires_submission``). The ThermoML importer
(``app.services.thermoml_cp_import``) has always linked every row it writes.
The CCCBDB importer (``app.services.cccbdb_molecular_property_import``) only
started doing so at Phase C-E5 review round 3 -- rows it wrote *before* that
change carry no link and cannot be curated. This module is the remediation:
given a database, find every unlinked observation row, group it by the
external source that produced it, open one ``Submission`` per source
attributed to a named operator, and link the rows to it.

This is a backfill, not a fresh deposit
----------------------------------------
Neither of the two attestation bases the two importers use fits here.
``depositor_agreement`` (what both importers record automatically via
``app.services.upload_submission.open_upload_submission`` /
``app.services.cccbdb_molecular_property_import._open_bulk_import_submission``)
is the depositor's own statement, made *at deposit time*, about the specific
rows *being deposited*. The operator running this backfill did not deposit
these rows -- somebody, or some importer run, already did, years earlier --
and is not making any statement about licensing terms today. Recording
``depositor_agreement`` here would misattribute the operator as though they
were the source of the science. ``source_terms`` is worse: it requires
quoting the source's own published terms verbatim
(``app.services.rights.record_attestation``'s ``rights_source_terms_required``
check), and CCCBDB has no terms text captured anywhere in this repository
(see ``_open_bulk_import_submission``'s own docstring for why the importer
itself does not use it either).

The basis that actually describes this situation is
:attr:`~app.db.models.common.RightsBasisKind.historical_review`: "a curator
reviewed a deposit that predates rights capture and records the basis after
the fact, with an actor, rather than by a default." These rows predate
*submission* capture rather than *rights* capture, but the shape is
identical -- a human, identified by name, is asserting after the fact that
a pre-existing deposit may stand on a stated license, because nothing
recorded that at the time. ``tests/services/release/test_release_rights.py::
test_a_curator_can_cover_a_historical_record_with_an_actor`` is the existing
precedent for exactly this pattern (a ``migration``-sourced submission
wrapping pre-existing records, attested ``historical_review`` by a curator),
for a different record type (``thermo``) and a different gap (predates
rights capture rather than predates submission-linking). This module follows
the same ``source_kind=migration`` for the same reason: ``migration`` is
honest about how these submissions entered the system now (an operator
remediation run, not a data-ingest event), where ``bulk_import`` would
misrepresent this run as importing new data.

Grouping
--------
"One submission per source" groups by ``external_source_name`` as recorded
on each unlinked row (``"CCCBDB"`` today; a row with no recorded source name
groups under the literal label ``"unknown"`` rather than being skipped, so a
future non-CCCBDB gap of the same shape is still remediated by this same
tool without a code change).

Idempotent by construction
---------------------------
The scope is "rows with no ``submission_record_link``", queried fresh on
every call. A row this function links no longer matches that scope, so a
second run over the same database finds nothing left to do for any source
already remediated and opens no new submission, exactly mirroring how
``app.services.thermoml_cp_import`` and
``app.services.cccbdb_molecular_property_import`` open a submission only
when a run's pre-check finds something new to link. ``link_record`` itself
is additionally idempotent per-row (returns the existing link rather than
inserting a duplicate), so even a concurrent or partially-committed prior
run cannot double-link a single row.

Never touches the science
--------------------------
This module reads ``id`` and ``external_source_name`` off each unlinked
row and writes nothing to ``molecular_property_observation`` itself --
no ``species_entry_id``, no scientific field, nothing. It only ever
inserts a ``Submission``, a ``SubmissionRightsAttestation`` and
``SubmissionRecordLink`` rows. The curator's own later identity-attach
call is the write path that fills ``species_entry_id`` in.

Dry-run by default
-------------------
``commit=False`` (the default) runs the full pipeline inside the caller's
transaction without committing; ``commit=True`` commits on success and
rolls back on unexpected error. Neither path touches the object store --
nothing in this module imports ``app.services.artifact_storage`` or any
other object-store client, so there is no would-be write for a dry run to
suppress.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.common import (
    RightsBasisKind,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
)
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.db.models.submission import SubmissionRecordLink
from app.services.rights import record_attestation
from app.services.submission import create_submission, link_record

#: Label used to group unlinked rows that carry no ``external_source_name``
#: at all. Not skipped -- an unlinked row with no recorded source is still
#: an unlinked row a curator cannot attach an identity to, and grouping it
#: under a stable label keeps it in scope for this tool rather than
#: silently invisible.
UNKNOWN_SOURCE_LABEL = "unknown"


@dataclass
class SourceBackfillOutcome:
    """What happened for one ``external_source_name`` group."""

    source_name: str
    linked_count: int
    submission_id: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "linked_count": self.linked_count,
            "submission_id": self.submission_id,
        }


@dataclass
class ObservationSubmissionBackfillResult:
    """Aggregate report from one
    :func:`backfill_observation_submission_links` invocation."""

    total_unlinked_found: int = 0
    total_linked: int = 0
    outcomes: list[SourceBackfillOutcome] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "total_unlinked_found": self.total_unlinked_found,
            "total_linked": self.total_linked,
            "outcomes": [o.to_json() for o in self.outcomes],
        }


def _unlinked_observations(session: Session) -> list[MolecularPropertyObservation]:
    """Every ``molecular_property_observation`` row with no
    ``submission_record_link`` at all, oldest first.

    A correlated ``NOT EXISTS`` rather than ``id.notin_(...)``: the latter
    reads correctly here (``record_id`` is never NULL on
    ``submission_record_link``) but ``NOT EXISTS`` is the form that stays
    correct even if that ever changed, and is what Postgres itself turns an
    anti-join into.
    """
    link_exists = (
        select(SubmissionRecordLink.id)
        .where(
            SubmissionRecordLink.record_type
            == SubmissionRecordType.molecular_property_observation,
            SubmissionRecordLink.record_id == MolecularPropertyObservation.id,
        )
        .exists()
    )
    stmt = (
        select(MolecularPropertyObservation)
        .where(~link_exists)
        .order_by(MolecularPropertyObservation.id)
    )
    return list(session.scalars(stmt).all())


def _group_by_source(
    rows: list[MolecularPropertyObservation],
) -> dict[str, list[MolecularPropertyObservation]]:
    grouped: dict[str, list[MolecularPropertyObservation]] = {}
    for row in rows:
        label = row.external_source_name or UNKNOWN_SOURCE_LABEL
        grouped.setdefault(label, []).append(row)
    return grouped


def backfill_observation_submission_links(
    session: Session,
    *,
    actor: AppUser,
    license_id: str,
    commit: bool = False,
) -> ObservationSubmissionBackfillResult:
    """Link every unlinked ``molecular_property_observation`` row to a
    fresh per-source backfill submission.

    :param session: An open SQLAlchemy session.
    :param actor: The operator running this backfill. Recorded as the
        creator of each opened ``Submission`` and as the
        ``historical_review`` rights attestor -- must be a curator or
        admin (:func:`app.services.rights.record_attestation` enforces
        this for any basis other than ``depositor_agreement``).
    :param license_id: SPDX identifier to attest for each opened
        submission. See the module docstring for why the basis is
        ``historical_review`` rather than ``depositor_agreement`` or
        ``source_terms``.
    :param commit: When ``True``, commit on success. When ``False``
        (default), every change is rolled back at the end so the caller
        can preview what would have happened.
    :returns: The aggregate report. Empty (``total_unlinked_found=0``,
        no outcomes) when every row already carries a link -- the second
        run over an already-remediated database, or a database that never
        needed remediation.
    """
    result = ObservationSubmissionBackfillResult()

    try:
        rows = _unlinked_observations(session)
        result.total_unlinked_found = len(rows)
        grouped = _group_by_source(rows)

        for source_name in sorted(grouped):
            group_rows = grouped[source_name]
            submission = create_submission(
                session,
                created_by=actor.id,
                submission_kind=SubmissionKind.other,
                source_kind=SubmissionSourceKind.migration,
                title=f"Backfill submission link for legacy {source_name} observations",
                summary=(
                    f"Operator backfill (issue #514): links {len(group_rows)} "
                    f"pre-existing molecular_property_observation row(s) "
                    f"sourced from {source_name!r} that were written before "
                    "submission linking existed for their importer, so a "
                    "curator can attach an identity to them. No scientific "
                    "field on any linked row is changed by this submission."
                ),
            )
            record_attestation(
                session,
                submission=submission,
                license_id=license_id,
                basis=RightsBasisKind.historical_review,
                actor=actor,
                note=(
                    f"Historical coverage recorded by {actor.username} "
                    f"(operator backfill, issue #514): these {source_name} "
                    "rows predate submission linking. No new science is "
                    "being deposited here -- only pre-existing rows are "
                    "being wrapped so they can be curated."
                ),
            )
            for row in group_rows:
                link_record(
                    session,
                    submission=submission,
                    record_type=SubmissionRecordType.molecular_property_observation,
                    record_id=row.id,
                )
            result.total_linked += len(group_rows)
            result.outcomes.append(
                SourceBackfillOutcome(
                    source_name=source_name,
                    linked_count=len(group_rows),
                    submission_id=submission.id,
                )
            )
    except Exception:
        session.rollback()
        raise

    if commit:
        session.commit()
    else:
        session.rollback()

    return result


__all__ = [
    "UNKNOWN_SOURCE_LABEL",
    "ObservationSubmissionBackfillResult",
    "SourceBackfillOutcome",
    "backfill_observation_submission_links",
]
