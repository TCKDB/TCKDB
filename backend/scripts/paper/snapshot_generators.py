"""Generators for the demonstration snapshot (``scripts/paper_snapshot``).

* ``hessian_reanalysis``: every calculation with a stored Hessian or frequency list; a missing Hessian is counted.
* ``conformer_basins``: per species entry, its conformer groups and the levels that observed each.
* ``calculation_evidence``: job files, settings read from them, software and level per calculation.
* ``deposit_warnings``: stored deposit warnings by code.
* ``thermo_selection_outcomes``: the thermo selection answer per species entry (sole candidate, alternatives, none).
* ``structure_selection_outcomes``: the calculation and conformer selection answers per species entry.

Rows are ordered by content, not by public ref; no wall-clock value or database id is emitted.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationParameter,
)
from app.db.models.common import ThermoTargetKind
from app.db.models.deposit_warning import DepositWarning
from app.db.models.species import ConformerGroup, ConformerObservation, SpeciesEntry
from app.db.models.thermo import Thermo
from app.services.hessian_reanalysis import hessian_reanalysis as _hessian_reanalysis


def _level_label(lot) -> str:
    """``method/basis`` plus whatever else the level states, in a fixed order."""
    if lot is None:
        return "(no level of theory)"
    parts = [lot.method]
    if lot.basis:
        parts.append("/" + lot.basis)
    extras = [
        value
        for value in (
            lot.dispersion,
            lot.aux_basis,
            lot.cabs_basis,
            lot.solvent,
            lot.solvent_model,
            getattr(lot.spin_treatment, "value", lot.spin_treatment),
            getattr(lot.core_treatment, "value", lot.core_treatment),
        )
        if value
    ]
    label = "".join(parts)
    return label + (" [" + ", ".join(str(x) for x in extras) + "]" if extras else "")


_REF = re.compile(r"\b[a-z]{2,6}_[a-z2-7]{26}\b")


def _content_key(row: dict[str, Any]) -> str:
    """The row as canonical JSON with public refs blanked, so ordering does not depend on them."""

    return _REF.sub("", json.dumps(row, sort_keys=True, default=str))


def _software_label(release) -> str:
    if release is None:
        return "(no software)"
    pieces = [release.software.name, release.version or "", release.revision or ""]
    return " ".join(p for p in pieces if p)


# ---------------------------------------------------------------------------
# hessian_reanalysis
# ---------------------------------------------------------------------------


def hessian_reanalysis(session: Session) -> dict[str, Any]:
    """The spectrum recovered from every stored Hessian beside the stored frequency list.

    Scope is every calculation with a stored Hessian or a stored frequency
    list. A calculation without a stored Hessian is reported with status
    ``hessian_not_stored`` and counted in ``scope.by_status``; refusals are
    counted, never dropped.

    Each record also names the species (SMILES and multiplicity), the level of
    theory and the calculation type, and the records are ordered by those and
    then by content, not by public ref, so two builds of the same data list
    them in the same order.
    """
    out = _hessian_reanalysis(session, include_modes=True)
    by_ref = {
        calc.public_ref: calc
        for calc in session.scalars(select(Calculation).where(Calculation.public_ref.in_([r["calculation_ref"] for r in out["records"]])))
    }
    for record in out["records"]:
        calc = by_ref.get(record["calculation_ref"])
        entry = calc.species_entry if calc is not None else None
        record["species_smiles"] = entry.species.smiles if entry is not None else None
        record["species_multiplicity"] = entry.species.multiplicity if entry is not None else None
        record["level_of_theory"] = _level_label(calc.lot) if calc is not None else None
    out["records"].sort(
        key=lambda r: (
            r["species_smiles"] or "",
            r["species_multiplicity"] or 0,
            r["level_of_theory"] or "",
            r["calculation_type"] or "",
            _content_key(r),
        )
    )
    return out


# ---------------------------------------------------------------------------
# conformer_basins
# ---------------------------------------------------------------------------


def conformer_basins(session: Session) -> dict[str, Any]:
    """Per species entry: conformer groups, observations, and the levels that saw each group."""
    entries = list(session.scalars(select(SpeciesEntry).order_by(SpeciesEntry.public_ref)))
    rows: list[dict[str, Any]] = []
    spanned: Counter[int] = Counter()
    total_groups = total_observations = 0
    for entry in entries:
        groups = sorted(
            session.scalars(
                select(ConformerGroup).where(
                    ConformerGroup.species_entry_id == entry.id,
                    ConformerGroup.merged_into_conformer_group_id.is_(None),
                )
            ),
            key=lambda group: group.public_ref,
        )
        if not groups:
            continue
        group_rows = []
        entry_levels: Counter[str] = Counter()
        entry_observations = 0
        for group in groups:
            observations = list(
                session.scalars(
                    select(ConformerObservation).where(ConformerObservation.conformer_group_id == group.id)
                )
            )
            levels: Counter[str] = Counter()
            for observation in observations:
                calc_levels = {
                    _level_label(calc.lot)
                    for calc in session.scalars(
                        select(Calculation).where(Calculation.conformer_observation_id == observation.id)
                    )
                }
                for label in calc_levels:
                    levels[label] += 1
            entry_observations += len(observations)
            entry_levels.update(levels)
            spanned[len(levels)] += 1
            group_rows.append(
                {
                    "conformer_group_ref": group.public_ref,
                    "observations": len(observations),
                    "levels_observing": dict(sorted(levels.items())),
                    "number_of_levels": len(levels),
                }
            )
        group_rows.sort(key=lambda g: (g["number_of_levels"], sorted(g["levels_observing"].items()), g["observations"], _content_key(g)))
        total_groups += len(groups)
        total_observations += entry_observations
        rows.append(
            {
                "species_entry_ref": entry.public_ref,
                "species_ref": entry.species.public_ref,
                "species_smiles": entry.species.smiles,
                "multiplicity": entry.species.multiplicity,
                "conformer_groups": len(groups),
                "conformer_observations": entry_observations,
                "observations_by_level": dict(sorted(entry_levels.items())),
                "groups": group_rows,
            }
        )
    rows.sort(key=lambda r: (r["species_smiles"], r["multiplicity"], _content_key(r)))
    return {
        "species_entries_with_conformers": len(rows),
        "conformer_groups": total_groups,
        "conformer_observations": total_observations,
        "groups_by_number_of_levels": {str(k): v for k, v in sorted(spanned.items())},
        "entries": rows,
    }


# ---------------------------------------------------------------------------
# calculation_evidence
# ---------------------------------------------------------------------------


def calculation_evidence(session: Session) -> dict[str, Any]:
    """Job files, settings read from them, and the software and level behind each calculation."""
    calculations = list(session.scalars(select(Calculation).order_by(Calculation.public_ref)))

    artifact_rows = list(session.execute(select(CalculationArtifact.kind, func.count(), func.sum(CalculationArtifact.bytes)).group_by(CalculationArtifact.kind)))
    by_kind = {kind.value: {"files": int(n), "bytes": int(b or 0)} for kind, n, b in artifact_rows}
    distinct_digests = int(session.scalar(select(func.count(func.distinct(CalculationArtifact.sha256)))) or 0)
    with_files = int(session.scalar(select(func.count(func.distinct(CalculationArtifact.calculation_id)))) or 0)

    parameter_rows = list(
        session.execute(
            select(CalculationParameter.parser_name, CalculationParameter.statement_kind, CalculationParameter.artifact_kind, func.count())
            .group_by(CalculationParameter.parser_name, CalculationParameter.statement_kind, CalculationParameter.artifact_kind)
        )
    )
    parameters = sorted(
        (
            {
                "parser": parser or "(none)",
                "statement_kind": getattr(statement, "value", statement) or "(none)",
                "artifact_kind": getattr(artifact, "value", artifact) or "(none)",
                "rows": int(n),
            }
            for parser, statement, artifact, n in parameter_rows
        ),
        key=lambda row: (row["parser"], row["statement_kind"], row["artifact_kind"]),
    )
    with_parameters = int(session.scalar(select(func.count(func.distinct(CalculationParameter.calculation_id)))) or 0)

    recipes: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for calc in calculations:
        recipes[(_software_label(calc.software_release), _level_label(calc.lot))][calc.type.value] += 1
    recipe_rows = [
        {"software": software, "level_of_theory": level, "calculations": sum(counts.values()), "by_type": dict(sorted(counts.items()))}
        for (software, level), counts in sorted(recipes.items())
    ]
    return {
        "calculations": len(calculations),
        "calculations_with_job_files": with_files,
        "calculations_with_settings_read_from_files": with_parameters,
        "job_files_by_kind": dict(sorted(by_kind.items())),
        "distinct_job_file_digests": distinct_digests,
        "settings_rows": parameters,
        "software_and_level_of_theory": recipe_rows,
    }


# ---------------------------------------------------------------------------
# deposit_warnings
# ---------------------------------------------------------------------------


def deposit_warnings(session: Session) -> dict[str, Any]:
    """Every stored deposit warning, by code, with the record types it was written against."""
    rows = list(
        session.execute(
            select(DepositWarning.code, DepositWarning.register_entry, DepositWarning.record_type, func.count())
            .where(DepositWarning.code.is_not(None))
            .group_by(DepositWarning.code, DepositWarning.register_entry, DepositWarning.record_type)
        )
    )
    by_code: dict[str, dict[str, Any]] = {}
    for code, entry, record_type, n in rows:
        slot = by_code.setdefault(code, {"count": 0, "register_entry": entry, "record_types": {}})
        slot["count"] += int(n)
        slot["record_types"][getattr(record_type, "value", record_type) or "(submission)"] = int(n)
    for slot in by_code.values():
        slot["record_types"] = dict(sorted(slot["record_types"].items()))
    checked = int(session.scalar(select(func.count()).select_from(DepositWarning).where(DepositWarning.code.is_(None))) or 0)
    return {
        "warnings_total": sum(slot["count"] for slot in by_code.values()),
        "distinct_codes": len(by_code),
        "checked_markers": checked,
        "by_code": dict(sorted(by_code.items())),
    }


# ---------------------------------------------------------------------------
# thermo_selection_outcomes
# ---------------------------------------------------------------------------


def _selection_session(session: Session):
    """A read-only snapshot session, as the route opens one, or the caller's own in a test harness."""
    from app.services.read_snapshot import begin_read_snapshot
    from app.services.thermo_selection.public import SNAPSHOT_OPT_OUT

    bind = session.get_bind()
    if isinstance(bind, Engine):
        snapshot = Session(bind=bind)
        begin_read_snapshot(snapshot, require=True)
        return snapshot, True
    session.info[SNAPSHOT_OPT_OUT] = True
    return session, False


def thermo_selection_outcomes(session: Session) -> dict[str, Any]:
    """For each species entry with a thermo record: what the H298 selection route answers.

    Runs the service behind ``POST /species-entries/{ref}/thermo/select`` with
    its default request (equilibrium-ensemble target, default eligibility,
    exploratory profile). The answer is one of ``sole_eligible_candidate``,
    ``incomparable_alternatives`` or ``no_applicable_candidate``; no outcome
    names a best record, and ``result_mode`` is left at ``all``.
    """
    from app.schemas.reads.scientific_thermo_selection import ThermoSelectionRequest, ThermoSelectionTargetIn
    from app.services.thermo_selection.public import run_selection

    work, owned = _selection_session(session)
    try:
        entry_ids = list(work.scalars(select(Thermo.species_entry_id).distinct()))
        entries = sorted((work.get(SpeciesEntry, i) for i in entry_ids), key=lambda e: e.public_ref)
        body = ThermoSelectionRequest(target=ThermoSelectionTargetIn(kind=ThermoTargetKind.equilibrium_ensemble))
        rows: list[dict[str, Any]] = []
        tally: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        for entry in entries:
            selection, _ = run_selection(work, species_entry_id=entry.id, body=body)
            manifest = selection.manifest
            decision = manifest.get("decision") or {}
            candidates = sorted(manifest.get("candidates") or [], key=lambda c: c["thermo_ref"])
            eligible = [c["thermo_ref"] for c in candidates if c.get("eligible")]
            for candidate in candidates:
                for reason in candidate["assessment"]["reasons"]:
                    reasons[reason["code"]] += 1
            outcome = selection.outcome.value
            tally[outcome] += 1
            rows.append(
                {
                    "species_entry_ref": entry.public_ref,
                    "species_smiles": entry.species.smiles,
                    "multiplicity": entry.species.multiplicity,
                    "outcome": outcome,
                    "thermo_records": len(candidates),
                    "eligible_thermo_refs": eligible,
                    "selected_thermo_ref": decision.get("selected_ref"),
                    "reason_codes": sorted({r["code"] for c in candidates for r in c["assessment"]["reasons"]}),
                }
            )
        rows.sort(key=lambda r: (r["species_smiles"], r["multiplicity"], _content_key(r)))
        return {
            "species_entries_assessed": len(rows),
            "outcomes": dict(sorted(tally.items())),
            "candidate_reason_codes": dict(sorted(reasons.items())),
            "entries": rows,
        }
    finally:
        if owned:
            work.rollback()
            work.close()


# ---------------------------------------------------------------------------
# structure_selection_outcomes
# ---------------------------------------------------------------------------


def structure_selection_outcomes(session: Session) -> dict[str, Any]:
    """For each species entry with conformers: what the calculation and conformer selections answer.

    Runs the services behind ``POST /species-entries/{ref}/calculations/select``
    (intent ``recorded_minimum``) and ``.../conformers/select`` (intent
    ``validated_minimum``), each with its default request: electronic energy,
    default eligibility, exploratory profile, ``result_mode`` all. A value is
    ordered only inside one cohort (the same established recipe); entries with
    determinations at several recipes end as ``incomparable_alternatives``,
    and no outcome names a method winner. ``administrative_first_named`` is
    reported so a change that starts naming one is visible.
    """
    from app.schemas.reads.scientific_structure_selection import (
        CalculationSelectionRequest,
        ConformerSelectionRequest,
    )
    from app.services.structure_selection.public import run_selection

    work, owned = _selection_session(session)
    try:
        entry_ids = sorted({g.species_entry_id for g in work.scalars(select(ConformerGroup))})
        entries = [work.get(SpeciesEntry, i) for i in entry_ids]
        requests = (("calculations", CalculationSelectionRequest()), ("conformers", ConformerSelectionRequest()))
        rows: list[dict[str, Any]] = []
        tally: dict[str, Counter[str]] = {grain: Counter() for grain, _ in requests}
        for entry in entries:
            row: dict[str, Any] = {
                "species_smiles": entry.species.smiles,
                "multiplicity": entry.species.multiplicity,
                "species_entry_ref": entry.public_ref,
            }
            for grain, body in requests:
                selection = run_selection(work, entry_id=entry.id, body=body)
                manifest = selection.manifest
                decision = manifest["decision"]
                outcome = manifest["outcome"]
                tally[grain][outcome] += 1
                row[grain] = {
                    "outcome": outcome,
                    "visible_units": manifest["population"]["visible_units"],
                    "cohorts": len(decision["cohorts"]),
                    "selected_refs": len(decision["selected_refs"]),
                    "administrative_first_named": decision["administrative_first"] is not None,
                    "unresolved": {u["code"]: len(u["refs"]) for u in sorted(decision["unresolved"], key=lambda u: u["code"])},
                }
            rows.append(row)
        rows.sort(key=lambda r: (r["species_smiles"], r["multiplicity"], _content_key(r)))
        return {
            "species_entries_assessed": len(rows),
            "outcomes_by_grain": {grain: dict(sorted(counts.items())) for grain, counts in tally.items()},
            "entries": rows,
        }
    finally:
        if owned:
            work.rollback()
            work.close()


__all__ = [
    "calculation_evidence",
    "conformer_basins",
    "deposit_warnings",
    "hessian_reanalysis",
    "structure_selection_outcomes",
    "thermo_selection_outcomes",
]
