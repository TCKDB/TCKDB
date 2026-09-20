"""One generator per ``[DATA]`` claim in ``paper/19__TCKDB_skeleton/``.

Each function takes a :class:`~sqlalchemy.orm.Session` on the (restored)
publication database and returns a plain mapping. Rules, enforced by the byte
comparison in the deposit round trip rather than by convention:

* every collection is ordered by public ref (or, for the fixture mechanism,
  by source order), never by set/dict iteration or database id;
* no wall-clock values -- timestamps that appear are *data* (a submission's
  ``submitted_at``), never ``now()``;
* database primary keys never appear; public refs do.

Correspondence (skeleton line -> generator) is in
``docs/research/tckdb-phase-b-implementation-plan.md``, section B3.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from importlib import metadata
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationFreqMode,
    CalculationHessian,
)
from app.db.models.common import (
    CalculationType,
    DatasetReleaseStatus,
    ExternalSourceRecordKind,
    RecordReviewStatus,
    ReleaseSelectionAction,
    SubmissionRecordType,
)
from app.db.models.dataset_release import DatasetRelease, ReleaseSelection
from app.db.models.external_source import ExternalSourceRecord
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.network import Network
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.record_review import RecordReview
from app.db.models.software import SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.thermo import Thermo, ThermoSourceCalculation
from app.db.models.transition_state import TransitionStateEntry
from app.db.models.workflow import WorkflowToolRelease
from app.services.external_comparison.cp import RUNNER_VERSION as EXTERNAL_CP_COMPARISON_RUNNER_VERSION

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
MECHANISM_FIXTURE_DIR = BACKEND_ROOT / "tests" / "integration" / "fixtures" / "rmg_ammonia_methane"
MECHANISM_FIXTURE_FILES = ("chem.inp", "species_dictionary.txt", "tran.dat", "PROVENANCE.md")
CHEMKIN_ADAPTER = REPO_ROOT / "clients" / "python" / "adapters" / "chemkin"


def _count(session: Session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


# ---------------------------------------------------------------------------
# 4_limitations.md:7 -- corpus counts at snapshot
# ---------------------------------------------------------------------------


def corpus_counts(session: Session) -> dict[str, Any]:
    """Sizes of the deposited corpus, every one read from the database."""
    calculations_by_type = {kind.value: 0 for kind in CalculationType}
    for kind, count in session.execute(
        select(Calculation.type, func.count()).group_by(Calculation.type)
    ):
        calculations_by_type[kind.value] = int(count)

    review_states = {status.value: 0 for status in RecordReviewStatus}
    for status, count in session.execute(
        select(RecordReview.status, func.count()).group_by(RecordReview.status)
    ):
        review_states[status.value] = int(count)

    releases_by_status = {status.value: 0 for status in DatasetReleaseStatus}
    for status, count in session.execute(
        select(DatasetRelease.status, func.count()).group_by(DatasetRelease.status)
    ):
        releases_by_status[status.value] = int(count)

    return {
        "species": _count(session, Species),
        "species_entries": _count(session, SpeciesEntry),
        "reactions": _count(session, ChemReaction),
        "reaction_entries": _count(session, ReactionEntry),
        "calculations": sum(calculations_by_type.values()),
        "calculations_by_type": calculations_by_type,
        "networks": _count(session, Network),
        "submissions": _count(session, Submission),
        "distinct_depositors": int(
            session.scalar(select(func.count(func.distinct(Submission.created_by)))) or 0
        ),
        "workflow_tool_releases": _count(session, WorkflowToolRelease),
        "ess_software_releases": _count(session, SoftwareRelease),
        "levels_of_theory": _count(session, LevelOfTheory),
        "review_states": review_states,
        "dataset_releases": sum(releases_by_status.values()),
        "dataset_releases_by_status": releases_by_status,
    }


# ---------------------------------------------------------------------------
# 3_results.md:16 -- the fixture mechanism as the CHEMKIN round trip sees it
# ---------------------------------------------------------------------------


def _parse_fixture_mechanism():
    if str(CHEMKIN_ADAPTER) not in sys.path:
        sys.path.append(str(CHEMKIN_ADAPTER))
    from tckdb_chemkin.parser import parse_mechanism
    from tckdb_chemkin.transport import parse_transport_file

    mechanism = parse_mechanism((MECHANISM_FIXTURE_DIR / "chem.inp").read_text())
    mechanism.transport = parse_transport_file((MECHANISM_FIXTURE_DIR / "tran.dat").read_text())
    return mechanism


def mechanism_roundtrip_counts(session: Session) -> dict[str, Any]:
    """Species, rate expressions, distinct reactions and rate forms in the fixture.

    Reads the committed fixture through the importer's parser, the same
    reader the round-trip test uses, so the number the paper quotes is the
    number the test asserts. ``session`` is unused: the mechanism is a
    file, not a database record.
    """
    del session
    mechanism = _parse_fixture_mechanism()

    def _key(reaction) -> tuple:
        return (
            tuple(sorted(reaction.reactants)),
            tuple(sorted(reaction.products)),
        )

    forms = Counter()
    for reaction in mechanism.reactions:
        if reaction.chebyshev is not None:
            forms["chebyshev"] += 1
        elif reaction.plog:
            forms["plog"] += 1
        elif reaction.troe is not None:
            forms["troe"] += 1
            if reaction.efficiencies:
                forms["troe_with_third_body_efficiencies"] += 1
        elif reaction.sri is not None:
            forms["sri"] += 1
        elif reaction.is_falloff:
            forms["lindemann"] += 1
        elif reaction.is_third_body:
            forms["third_body"] += 1
        else:
            forms["arrhenius"] += 1
        if reaction.duplicate:
            forms["duplicate"] += 1

    return {
        "species": len(mechanism.species),
        "rate_expressions": len(mechanism.reactions),
        "distinct_reactions": len({_key(reaction) for reaction in mechanism.reactions}),
        "forms": dict(sorted(forms.items())),
        "nasa7_thermo_entries": len(mechanism.thermo),
        "transport_entries": len(mechanism.transport or {}),
    }


# ---------------------------------------------------------------------------
# 3_results.md:33 -- selected thermo per species (generalised from ethylene)
# ---------------------------------------------------------------------------


def _standing_selections(session: Session, release: DatasetRelease) -> list[ReleaseSelection]:
    """Head of each append-only chain that is not withdrawn, in public-ref order.

    Same rule as :func:`app.services.release.curation.current_selection`,
    applied to every subject of the release at once.
    """
    rows = list(
        session.scalars(
            select(ReleaseSelection)
            .where(ReleaseSelection.dataset_release_id == release.id)
            .order_by(ReleaseSelection.id)
        )
    )
    superseded = {row.supersedes_selection_id for row in rows if row.supersedes_selection_id}
    standing: dict[tuple, ReleaseSelection] = {}
    for row in rows:
        key = (row.subject_type, row.subject_id, row.record_type)
        if row.id in superseded:
            continue
        if row.action is ReleaseSelectionAction.withdraw:
            standing.pop(key, None)
            continue
        standing[key] = row
    return sorted(standing.values(), key=lambda row: row.public_ref)


def selected_thermo_by_species(session: Session) -> dict[str, Any]:
    """For every species with a standing thermo selection: the selected values."""
    releases = list(
        session.scalars(
            select(DatasetRelease)
            .where(DatasetRelease.status != DatasetReleaseStatus.draft)
            .order_by(DatasetRelease.tag)
        )
    )
    rows: list[dict[str, Any]] = []
    for release in releases:
        for selection in _standing_selections(session, release):
            if selection.record_type is not SubmissionRecordType.thermo:
                continue
            if selection.subject_type is not SubmissionRecordType.species_entry:
                continue
            thermo = session.get(Thermo, selection.record_id)
            entry = session.get(SpeciesEntry, selection.subject_id)
            if thermo is None or entry is None:
                continue
            rows.append(
                {
                    "release_tag": release.tag,
                    "species_ref": entry.species.public_ref,
                    "species_smiles": entry.species.smiles,
                    "species_entry_ref": entry.public_ref,
                    "thermo_ref": thermo.public_ref,
                    "selection_ref": selection.public_ref,
                    "h298_kj_mol": thermo.h298_kj_mol,
                    "s298_j_mol_k": thermo.s298_j_mol_k,
                    "representation": thermo.model_kind.value if thermo.model_kind else None,
                    "scientific_origin": thermo.scientific_origin.value,
                }
            )
    rows.sort(key=lambda row: (row["release_tag"], row["species_ref"], row["species_entry_ref"], row["thermo_ref"]))
    return {"selected_thermo": rows, "selections": len(rows)}


# ---------------------------------------------------------------------------
# 3_results.md:35, SI.md:33 -- lineage of every multi-candidate species entry
# ---------------------------------------------------------------------------


def _review_status(session: Session, record_type: SubmissionRecordType, record_id: int) -> str:
    review = session.scalars(
        select(RecordReview).where(
            RecordReview.record_type == record_type, RecordReview.record_id == record_id
        )
    ).first()
    return review.status.value if review is not None else RecordReviewStatus.not_reviewed.value


def candidate_lineage(session: Session) -> dict[str, Any]:
    """Per species entry with more than one thermo candidate: where each came from."""
    multi = [
        entry_id
        for entry_id, count in session.execute(
            select(Thermo.species_entry_id, func.count()).group_by(Thermo.species_entry_id)
        )
        if count > 1
    ]
    entries = sorted(
        (session.get(SpeciesEntry, entry_id) for entry_id in multi),
        key=lambda entry: entry.public_ref,
    )
    out: list[dict[str, Any]] = []
    for entry in entries:
        candidates = sorted(
            session.scalars(select(Thermo).where(Thermo.species_entry_id == entry.id)),
            key=lambda thermo: thermo.public_ref,
        )
        rows = []
        for thermo in candidates:
            submissions = sorted(
                (
                    link.submission
                    for link in session.scalars(
                        select(SubmissionRecordLink).where(
                            SubmissionRecordLink.record_type == SubmissionRecordType.thermo,
                            SubmissionRecordLink.record_id == thermo.id,
                        )
                    )
                ),
                key=lambda submission: submission.public_ref,
            )
            source_calculations = sorted(
                (
                    row.calculation
                    for row in session.scalars(
                        select(ThermoSourceCalculation).where(ThermoSourceCalculation.thermo_id == thermo.id)
                    )
                ),
                key=lambda calculation: calculation.public_ref,
            )
            commits: set[str] = set()
            if thermo.workflow_tool_release is not None and thermo.workflow_tool_release.git_commit:
                commits.add(thermo.workflow_tool_release.git_commit)
            digests: set[str] = set()
            for calculation in source_calculations:
                release = calculation.workflow_tool_release
                if release is not None and release.git_commit:
                    commits.add(release.git_commit)
                for artifact in session.scalars(
                    select(CalculationArtifact).where(CalculationArtifact.calculation_id == calculation.id)
                ):
                    digests.add(artifact.sha256)
            rows.append(
                {
                    "thermo_ref": thermo.public_ref,
                    "h298_kj_mol": thermo.h298_kj_mol,
                    "s298_j_mol_k": thermo.s298_j_mol_k,
                    "review_state": _review_status(session, SubmissionRecordType.thermo, thermo.id),
                    "submissions": [
                        {"submission_ref": s.public_ref, "submitted_at": s.submitted_at} for s in submissions
                    ],
                    "source_calculation_refs": [c.public_ref for c in source_calculations],
                    "workflow_tool_release_commits": sorted(commits),
                    "artifact_digests": sorted(digests),
                }
            )
        out.append(
            {
                "species_entry_ref": entry.public_ref,
                "species_ref": entry.species.public_ref,
                "species_smiles": entry.species.smiles,
                "candidate_count": len(rows),
                "distinct_submissions": len({s["submission_ref"] for r in rows for s in r["submissions"]}),
                "distinct_workflow_tool_release_commits": len(
                    {c for r in rows for c in r["workflow_tool_release_commits"]}
                ),
                "distinct_artifact_digests": len({d for r in rows for d in r["artifact_digests"]}),
                "candidates": rows,
            }
        )
    return {"species_entries_with_multiple_candidates": len(out), "lineage": out}


# ---------------------------------------------------------------------------
# 3_results.md:43 -- transition-state entries, imaginary modes, Hessians
# ---------------------------------------------------------------------------


def transition_state_evidence(session: Session) -> dict[str, Any]:
    """TS entries; those with exactly one imaginary mode; those with a stored Hessian."""
    entries = list(
        session.scalars(select(TransitionStateEntry).order_by(TransitionStateEntry.public_ref))
    )
    rows: list[dict[str, Any]] = []
    for entry in entries:
        calculations = sorted(
            session.scalars(
                select(Calculation).where(Calculation.transition_state_entry_id == entry.id)
            ),
            key=lambda calculation: calculation.public_ref,
        )
        imaginary_counts: list[int] = []
        hessian_count = 0
        for calculation in calculations:
            modes = session.scalar(
                select(func.count())
                .select_from(CalculationFreqMode)
                .where(
                    CalculationFreqMode.calculation_id == calculation.id,
                    CalculationFreqMode.is_imaginary.is_(True),
                )
            )
            has_freq = session.scalar(
                select(func.count()).select_from(CalculationFreqMode).where(
                    CalculationFreqMode.calculation_id == calculation.id
                )
            )
            if has_freq:
                imaginary_counts.append(int(modes or 0))
            if session.scalar(
                select(func.count()).select_from(CalculationHessian).where(
                    CalculationHessian.calculation_id == calculation.id
                )
            ):
                hessian_count += 1
        rows.append(
            {
                "transition_state_entry_ref": entry.public_ref,
                "transition_state_ref": entry.transition_state.public_ref,
                "calculations": len(calculations),
                "imaginary_mode_counts": imaginary_counts,
                "has_exactly_one_imaginary_mode": 1 in imaginary_counts,
                "has_stored_hessian": hessian_count > 0,
            }
        )
    return {
        "transition_state_entries": len(rows),
        "with_exactly_one_imaginary_mode": sum(1 for r in rows if r["has_exactly_one_imaginary_mode"]),
        "with_stored_hessian": sum(1 for r in rows if r["has_stored_hessian"]),
        "entries": rows,
    }


# ---------------------------------------------------------------------------
# SI.md:25 -- fixture mechanism provenance and the validating Cantera version
# ---------------------------------------------------------------------------


def _distribution_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def mechanism_fixture_provenance(session: Session) -> dict[str, Any]:
    """Digests of the fixture files, the provenance note, and the Cantera version."""
    del session
    files = []
    for name in MECHANISM_FIXTURE_FILES:
        content = (MECHANISM_FIXTURE_DIR / name).read_bytes()
        files.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
    provenance = (MECHANISM_FIXTURE_DIR / "PROVENANCE.md").read_text(encoding="utf-8")
    citation = [line.strip("> ").strip() for line in provenance.splitlines() if line.startswith(">")]
    return {
        "fixture_directory": str(MECHANISM_FIXTURE_DIR.relative_to(REPO_ROOT)),
        "files": files,
        "emulated_publication": " ".join(citation),
        "cantera_version": _distribution_version("cantera"),
        "round_trip_test": "backend/tests/integration/test_chemkin_round_trip_real.py",
    }


# ---------------------------------------------------------------------------
# Phase C-E4 demonstration -- computed Cp against external ThermoML data
# ---------------------------------------------------------------------------


def experimental_cp_comparison(session: Session) -> dict[str, Any]:
    """Latest ``external_cp_comparison_v1`` review-tier row per thermo record.

    ``record_machine_review`` is a private, append-only table with no public
    ref of its own (``docs/specs/record_machine_review_policy.md``), so this
    reads it directly through the ORM the way every other generator reads its
    tables, and renders only what a reader needs: the thermo and species it
    is about (by public ref), the review's status and rubric version, and
    every field of every per-observation finding -- decoded back out of the
    finding's ``message`` (see ``app.services.external_comparison.cp``, whose
    fixed :class:`~app.services.machine_review.schemas.MachineReviewFinding`
    shape carries the full per-observation comparison as canonical JSON
    there because it has no free-form numeric field of its own). No
    accuracy threshold is applied here or anywhere upstream of it (ADR 0008;
    ``docs/research/tckdb-phase-c-implementation-plan.md`` C4) -- this
    generator reports residuals, never a verdict on them.
    """
    rows = list(
        session.scalars(
            select(RecordMachineReviewRow).where(
                RecordMachineReviewRow.model == EXTERNAL_CP_COMPARISON_RUNNER_VERSION,
                RecordMachineReviewRow.record_type == SubmissionRecordType.thermo,
            )
        )
    )
    latest_by_thermo_id: dict[int, RecordMachineReviewRow] = {}
    for row in rows:
        current = latest_by_thermo_id.get(row.record_id)
        if current is None or (row.reviewed_at, row.id) > (current.reviewed_at, current.id):
            latest_by_thermo_id[row.record_id] = row

    comparisons: list[dict[str, Any]] = []
    for thermo_id, row in latest_by_thermo_id.items():
        thermo = session.get(Thermo, thermo_id)
        if thermo is None:
            continue
        species_entry = thermo.species_entry
        findings = []
        for raw_finding in row.findings_json:
            detail = json.loads(raw_finding["message"])
            findings.append({"severity": raw_finding["severity"], **detail})
        findings.sort(key=lambda f: (f["temperature_k"], f["observation_ref"]))
        comparisons.append(
            {
                "thermo_ref": thermo.public_ref,
                "species_ref": species_entry.species.public_ref,
                "species_entry_ref": species_entry.public_ref,
                "species_smiles": species_entry.species.smiles,
                "status": row.status.value,
                "rubric_versions": dict(row.rubric_versions_json),
                "reviewed_at": row.reviewed_at,
                "findings": findings,
                "finding_count": len(findings),
            }
        )
    comparisons.sort(key=lambda c: (c["species_ref"], c["thermo_ref"]))
    return {"comparisons": comparisons, "thermo_count": len(comparisons)}


def _mapping_report_counts(mapping_report: dict[str, Any] | None) -> dict[str, Any]:
    """Collapse a mapping report to counts: list/dict length, scalar as-is."""
    if not mapping_report:
        return {}
    return {
        key: (len(value) if isinstance(value, (list, dict)) else value)
        for key, value in sorted(mapping_report.items())
    }


def thermoml_source_provenance(session: Session) -> dict[str, Any]:
    """Every ThermoML article custody row: source, digest, parser/mapping versions.

    One row per ``external_source_record`` of kind ``thermoml_article`` --
    the Phase C-E1 custody chain (C2 design note) an importer attaches
    instead of the CCCBDB importer's flattened ``external_source_*``
    columns. Reports the natural per-value ``source_record_key``, never the
    row's internal id.
    """
    records = list(
        session.scalars(
            select(ExternalSourceRecord).where(
                ExternalSourceRecord.record_kind == ExternalSourceRecordKind.thermoml_article
            )
        )
    )
    rows: list[dict[str, Any]] = []
    for record in records:
        source = record.external_source
        rows.append(
            {
                "source_name": source.source_name,
                "source_release": source.source_release,
                "source_database_doi": source.source_database_doi,
                "record_key": record.source_record_key,
                "content_sha256": record.content_sha256,
                "schema_id": record.schema_id,
                "schema_valid": record.schema_valid,
                "parser_name": record.parser_name,
                "parser_version": record.parser_version,
                "mapping_version": record.mapping_version,
                "mapping_report_counts": _mapping_report_counts(record.mapping_report_json),
            }
        )
    rows.sort(key=lambda r: r["record_key"])
    return {"thermoml_source_records": rows, "record_count": len(rows)}


__all__ = [
    "candidate_lineage",
    "corpus_counts",
    "experimental_cp_comparison",
    "mechanism_fixture_provenance",
    "mechanism_roundtrip_counts",
    "selected_thermo_by_species",
    "thermoml_source_provenance",
    "transition_state_evidence",
]
