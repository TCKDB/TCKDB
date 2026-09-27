"""Fill a NULL ``software_release.version`` from artifact evidence (issue #305, item 3).

A calculation cites a release whose ``version`` is NULL: the program was
recorded, its version was not. If the calculation's own stored output log
carries the program's startup banner, the version is *observed* there --
DR-0008's "parsed" source -- and the citation can be made exact.

The ingest seam never did this for deposited logs. DR-0008 reconciliation
runs inside parameter extraction, which reads only ``input`` artifacts, and
an input deck carries no version banner; so ``observed_software_banner`` is
NULL across the archive even where the ``output_log`` holding the banner
was stored. This module reads the output log.

What it does, per calculation citing the target release
--------------------------------------------------------
1. Load its artifacts, ``output_log`` first then ``input``, and take the
   first one whose banner names a program and a version.
2. Refuse to act on anything but DR-0008's ``enriched`` outcome: the banner
   must name the *same* program and only fill fields the release left NULL,
   ``version`` among them. A banner naming another program, or disagreeing
   with a declared field, is reported and left alone -- correcting a
   software identity is the upload path's business, not a curator backfill's.
3. Resolve (get-or-create) the release the enriched ref describes and
   re-point the calculation at it. Record the evidence on the calculation
   exactly as the parser seam would: ``observed_software_banner`` and
   ``software_reconciliation_status = enriched``.

Why a new release and a re-point, never an in-place fill
---------------------------------------------------------
The release row is an *identity* row. Its ``public_ref`` is content-derived
from ``(software_id, version, revision, build, release_date)``, and it is a
dedupe target: every future deposit of "ORCA, no version" resolves onto it.
Filling its version in place would (a) leave a published ``srel_`` ref that
no longer describes its row, (b) assert the new version for every record
that cites the row -- other depositors' calculations, thermo, frequency
scale factors -- when the evidence speaks for one calculation's log, and
(c) break the next version-less deposit outright: its freshly computed ref
equals the stale ref still on the filled row, the insert fails on
``public_ref``'s unique index, and the resolver returns nothing.
``tests/services/test_software_release_version_fill.py`` reproduces (c).

So the version-less row is never modified. It stays, correct, for whatever
still cites it; once nothing does, ``scripts/ops/prune_orphan_provenance.py``
lists it.

Accepted calculations
---------------------
``calculation`` is an accepted-science root: an UPDATE on a calculation that
was ever approved is refused by ``trg_as_root_calculation`` unless an
``accepted_science_repair`` declaration is in force, and that declaration is
made from an Alembic revision. Those calculations are reported as
``accepted`` and skipped; re-pointing them is a migration, not this.

Owner attestation is deliberately not an evidence source here: recording
who attested what, when, needs somewhere structured to put it, and there is
no such column today (see the #305 PR for the proposed design).

Never prints or returns a database primary key -- only public refs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationArtifact
from app.db.models.common import ArtifactKind, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.services import (
    gaussian_parameter_parser,
    molpro_parameter_parser,
    orca_parameter_parser,
)
from app.services.artifact_storage import load_artifact_bytes
from app.services.calculation_resolution import (
    record_software_reconciliation,
    software_release_to_declared_ref,
)
from app.services.ess_software_detection import detect_software_from_text
from app.services.software_reconciliation import reconcile_software_provenance
from app.services.software_resolution import (
    normalize_software_name,
    resolve_software_release_ref,
)

_VERSION_PARSERS: dict[str, Callable[[str], dict | None]] = {
    "gaussian": gaussian_parameter_parser.parse_software_version,
    "orca": orca_parameter_parser.parse_software_version,
    "molpro": molpro_parameter_parser.parse_software_version,
}

#: Output logs carry the banner; inputs almost never do, but are tried last
#: rather than skipped, because a producer can deposit a log under ``input``.
_ARTIFACT_ORDER = (ArtifactKind.output_log, ArtifactKind.input)


@dataclass(frozen=True)
class CalculationOutcome:
    """What the evidence said for one calculation.

    ``status`` is one of: ``fillable``, ``accepted`` (fillable, but the
    calculation is accepted science), ``no_artifact``, ``no_banner``,
    ``other_program``, ``disagrees``, ``unreadable``.
    """

    calculation_ref: str
    status: str
    observed_version: str | None = None
    observed_banner: str | None = None
    target: tuple[str | None, str | None, str | None] | None = None
    detail: str | None = None


@dataclass
class ReleaseFillPlan:
    release_ref: str
    software_name: str
    outcomes: list[CalculationOutcome] = field(default_factory=list)

    def by_status(self, status: str) -> list[CalculationOutcome]:
        return [o for o in self.outcomes if o.status == status]


class VersionFillRefused(ValueError):
    """The target release is not a version-less release this tool may fill."""


def version_less_releases(session: Session, software_name: str) -> list[SoftwareRelease]:
    """Every ``software_release`` of *software_name* whose version is NULL."""

    name = normalize_software_name(software_name).lower()
    return list(
        session.scalars(
            select(SoftwareRelease)
            .join(Software, Software.id == SoftwareRelease.software_id)
            .where(func.lower(Software.name) == name, SoftwareRelease.version.is_(None))
            .order_by(SoftwareRelease.public_ref)
        )
    )


def _is_accepted(session: Session, calculation: Calculation) -> bool:
    return (
        session.scalar(
            select(RecordReview.id).where(
                RecordReview.record_type == SubmissionRecordType.calculation,
                RecordReview.record_id == calculation.id,
                RecordReview.first_approved_at.is_not(None),
            )
        )
        is not None
    )


def _observe_banner(
    session: Session,
    calculation: Calculation,
    load_bytes: Callable[[str], bytes] | None,
) -> tuple[dict | None, str | None, str | None]:
    """First parseable ``(parsed_software, program, failure)`` for the calculation.

    ``failure`` is ``no_artifact`` / ``unreadable`` / ``no_banner`` when
    nothing parsed.
    """

    artifacts = list(
        session.scalars(
            select(CalculationArtifact).where(
                CalculationArtifact.calculation_id == calculation.id,
                CalculationArtifact.kind.in_(_ARTIFACT_ORDER),
            )
        )
    )
    if not artifacts:
        return None, None, "no_artifact"
    artifacts.sort(key=lambda a: (_ARTIFACT_ORDER.index(a.kind), a.filename))
    # Resolved per call, not bound as a default, so an operator run (and a
    # test) reads whatever store is configured at the time.
    load = load_bytes or load_artifact_bytes

    unreadable = 0
    for artifact in artifacts:
        try:
            text = load(artifact.sha256).decode("utf-8", errors="replace")
        except Exception:  # storage down, object missing, digest mismatch
            unreadable += 1
            continue
        program = detect_software_from_text(text)
        parser = _VERSION_PARSERS.get(program or "")
        if parser is None:
            continue
        parsed = parser(text)
        if parsed and parsed.get("version"):
            return parsed, program, None
    if unreadable and unreadable == len(artifacts):
        return None, None, "unreadable"
    return None, None, "no_banner"


def plan_release_fill(
    session: Session,
    release: SoftwareRelease,
    *,
    load_bytes: Callable[[str], bytes] | None = None,
) -> ReleaseFillPlan:
    """Read the evidence for every calculation citing *release*. Writes nothing.

    :raises VersionFillRefused: *release* already has a version.
    """

    if release.version is not None:
        raise VersionFillRefused(
            f"{release.public_ref} already has version={release.version!r}; "
            "a recorded version is never overwritten."
        )
    declared = software_release_to_declared_ref(release)
    plan = ReleaseFillPlan(release.public_ref, release.software.name)

    calculations = session.scalars(
        select(Calculation)
        .where(Calculation.software_release_id == release.id)
        .order_by(Calculation.public_ref)
    )
    for calc in calculations:
        parsed, program, failure = _observe_banner(session, calc, load_bytes)
        if parsed is None:
            plan.outcomes.append(CalculationOutcome(calc.public_ref, failure or "no_banner"))
            continue
        banner = " ".join(
            str(parsed[k])
            for k in ("name", "version", "build", "release_date_raw")
            if parsed.get(k)
        )
        # Case-insensitive: the parsers emit lowercase tokens and the
        # alias table does not canonicalise every program ("molpro").
        if (program or "").lower() != release.software.name.lower():
            plan.outcomes.append(
                CalculationOutcome(
                    calc.public_ref,
                    "other_program",
                    observed_version=parsed["version"],
                    observed_banner=banner,
                    detail=f"banner names {program!r}",
                )
            )
            continue
        result = reconcile_software_provenance(declared=declared, parsed=parsed)
        if result.match_status != "enriched" or "version" not in result.mismatches:
            plan.outcomes.append(
                CalculationOutcome(
                    calc.public_ref,
                    "disagrees",
                    observed_version=parsed["version"],
                    observed_banner=banner,
                    detail=f"reconciliation {result.match_status}: {sorted(result.mismatches)}",
                )
            )
            continue
        target = (
            result.resolved_ref.version,
            result.resolved_ref.revision,
            result.resolved_ref.build,
        )
        plan.outcomes.append(
            CalculationOutcome(
                calc.public_ref,
                "accepted" if _is_accepted(session, calc) else "fillable",
                observed_version=parsed["version"],
                observed_banner=banner,
                target=target,
            )
        )
    return plan


def apply_release_fill(
    session: Session,
    release: SoftwareRelease,
    *,
    load_bytes: Callable[[str], bytes] | None = None,
) -> tuple[ReleaseFillPlan, dict[str, str]]:
    """Re-point every ``fillable`` calculation at its evidenced release.

    Re-reads the evidence rather than trusting an earlier plan, so what is
    written is what the artifacts say now. The version-less release itself
    is never modified. The caller owns the commit.

    :returns: the plan it acted on, and ``{calculation_ref: new release ref}``.
    """

    plan = plan_release_fill(session, release, load_bytes=load_bytes)
    moved: dict[str, str] = {}
    for outcome in plan.by_status("fillable"):
        calc = session.scalars(
            select(Calculation).where(Calculation.public_ref == outcome.calculation_ref)
        ).one()
        parsed, _program, _failure = _observe_banner(session, calc, load_bytes)
        declared = software_release_to_declared_ref(calc.software_release)
        # Records observed_software_banner + software_reconciliation_status
        # exactly as the parser seam does (DR-0008); its identity-correction
        # branch cannot fire here because other_program was filtered above.
        result = record_software_reconciliation(
            session, calc, declared_ref=declared, parsed_software=parsed
        )
        assert result is not None and result.match_status == "enriched"
        new_release = resolve_software_release_ref(session, result.resolved_ref)
        calc.software_release_id = new_release.id
        calc.software_release = new_release
        moved[calc.public_ref] = new_release.public_ref
    session.flush()
    return plan, moved
