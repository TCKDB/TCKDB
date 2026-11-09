"""What grid and SCF convergence a calculation used, and how we know (read-time view; nothing here is stored).

Best evidence first, per setting:

1. **reported**: the output log prints the value the program used.
2. **requested**: the log's echoed input, else the uploaded input deck, else a value the depositor typed.
3. **assumed default**: the program's documented default for the declared program and version
   (:mod:`tckdb_schemas.program_default_settings`), labelled as assumed, because no log reports or echoes it. With no
   output log at all the label is ``assumed_default_no_output_log``.
4. **not stated**: none of the above, including when the program or version is not declared, and when the output
   log holds several jobs and none could be bound to this calculation (the ``output_log_job_not_bound`` warning, with
   no row from the log): that log was not read, so no default is assumed on its account. A version is never guessed.

Gaussian's grid is the exception to step 1: the log prints an internal code (``IRadAn``) whose correspondence to a
named grid is not documented, so it is listed but never sets the state.

A reported value is never turned into a requested one, and a requested value is never turned into a reported one.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from tckdb_schemas.program_default_settings import assumed_default_setting

from app.db.models.calculation import Calculation, CalculationArtifact, CalculationParameter
from app.db.models.common import ArtifactKind, ParameterSource, ParameterStatementKind, SubmissionRecordType
from app.db.models.deposit_warning import DepositWarning
from app.db.models.software import SoftwareRelease
from app.schemas.reads.scientific_calculation import (
    EffectiveSettingState,
    EffectiveSettingSummary,
    ReportedSettingValue,
)
from app.services.deposit_warnings import current_warnings
from app.services.parameter_reconciliation import W_OUTPUT_LOG_JOB_NOT_BOUND

#: setting -> (requested canonical key, reported canonical keys that set the state, raw keys listed but not deciding)
_SETTINGS = {
    "grid": ("grid.quality", ("grid.integration_accuracy", "grid.angular_max"), ("IRadAn",)),
    "scf_convergence": (
        "scf.convergence",
        (
            "scf.tol_energy_eh",
            "scf.tol_orbital_gradient",
            "scf.tol_orbital_rotation",
            "scf.tol_diis_error",
            "scf.tol_rms_density",
            "scf.tol_max_density",
        ),
        (),
    ),
}


def _values(rows: list[CalculationParameter], key: str) -> set[str]:
    return {r.canonical_value for r in rows if r.canonical_key == key and r.canonical_value is not None}


@dataclass(frozen=True)
class SettingsInputs:
    """Everything the effective settings of one calculation are computed from, loaded ahead of the computation.

    Loading is separate from computing so that a page of records loads these for all its calculations in a fixed number
    of statements (:func:`load_settings_inputs`) and the calculation read loads them for one; both then run the one
    computation, :func:`effective_settings_from`.
    """

    calculation: Calculation
    rows: tuple[CalculationParameter, ...]
    has_log_artifact: bool
    log_not_bound: bool


def load_settings_inputs(
    session: Session,
    calculation_ids: Collection[int],
    *,
    calculations: Mapping[int, Calculation] | None = None,
) -> dict[int, SettingsInputs]:
    """Load the calculations (level of theory and software release eagerly), their parameter rows, output-log presence and
    unbound-log warnings for ``calculation_ids``: at most four statements whatever the number of calculations.

    ``calculations`` are calculations the caller already holds (the calculation read has its own); they are used as they
    are, not reloaded. The warnings are looked up only for a calculation that has an output log and no row read from one,
    the only case in which they change the answer.
    """
    ids = set(calculation_ids)
    if not ids:
        return {}
    held = dict(calculations or {})
    missing = ids - held.keys()
    if missing:
        held.update(
            {
                c.id: c
                for c in session.scalars(
                    select(Calculation)
                    .where(Calculation.id.in_(missing))
                    .options(
                        selectinload(Calculation.lot),
                        selectinload(Calculation.software_release).selectinload(SoftwareRelease.software),
                    )
                )
            }
        )
    rows: dict[int, list[CalculationParameter]] = {}
    for row in session.scalars(
        select(CalculationParameter).where(CalculationParameter.calculation_id.in_(ids)).order_by(CalculationParameter.id)
    ):
        rows.setdefault(row.calculation_id, []).append(row)
    with_log = set(
        session.scalars(
            select(CalculationArtifact.calculation_id)
            .where(CalculationArtifact.calculation_id.in_(ids), CalculationArtifact.kind == ArtifactKind.output_log)
            .distinct()
        )
    )
    # A log that was read leaves rows; only a log with none can have been refused as unbound.
    unread = {
        cid
        for cid in with_log
        if not any(
            r.source is ParameterSource.parser
            and (r.statement_kind is ParameterStatementKind.reported or r.artifact_kind is ArtifactKind.output_log)
            for r in rows.get(cid, ())
        )
    }
    unbound = (
        set(
            session.scalars(
                current_warnings(
                    select(DepositWarning.record_id).where(
                        DepositWarning.record_type == SubmissionRecordType.calculation,
                        DepositWarning.record_id.in_(unread),
                        DepositWarning.code == W_OUTPUT_LOG_JOB_NOT_BOUND,
                    )
                )
            )
        )
        if unread
        else set()
    )
    return {
        cid: SettingsInputs(
            calculation=calc,
            rows=tuple(rows.get(cid, ())),
            has_log_artifact=cid in with_log,
            log_not_bound=cid in unbound,
        )
        for cid, calc in held.items()
        if cid in ids
    }


def build_effective_settings(session: Session, calculation: Calculation) -> list[EffectiveSettingSummary]:
    """The effective settings of one calculation (the calculation read's entry point)."""
    inputs = load_settings_inputs(session, [calculation.id], calculations={calculation.id: calculation})[calculation.id]
    return effective_settings_from(inputs)


def effective_settings_from(inputs: SettingsInputs) -> list[EffectiveSettingSummary]:
    calculation = inputs.calculation
    rows = list(inputs.rows)
    parser = [r for r in rows if r.source is ParameterSource.parser]
    reported_rows = [r for r in parser if r.statement_kind is ParameterStatementKind.reported]
    requested_parser = [r for r in parser if r.statement_kind is not ParameterStatementKind.reported]
    echo = [r for r in requested_parser if r.artifact_kind is ArtifactKind.output_log]
    deck = [r for r in requested_parser if r.artifact_kind is ArtifactKind.input]
    typed = [r for r in rows if r.source is ParameterSource.upload]
    has_log = inputs.has_log_artifact or bool(echo or reported_rows)
    # A multi-job log that could not be bound to this calculation was deliberately not read, so "the log neither
    # reports nor echoes it" would be false. Rows always win: once a refresh has read the log, its rows decide.
    log_not_read = not (echo or reported_rows) and inputs.log_not_bound

    release = calculation.software_release
    software = release.software.name if release is not None and release.software is not None else None
    version = release.version if release is not None else None

    out: list[EffectiveSettingSummary] = []
    for setting, (request_key, report_keys, listed_raw) in _SETTINGS.items():
        listed = [
            ReportedSettingValue(key=r.canonical_key, value=r.canonical_value)
            for r in reported_rows
            if r.canonical_key in report_keys and r.canonical_value is not None
        ] + [ReportedSettingValue(key=r.raw_key, value=r.raw_value) for r in reported_rows if r.raw_key in listed_raw]
        deciding = any(item.key in report_keys for item in listed)

        requested_value, requested_from, conflicting = None, None, False
        for source, rows_of in (("output_log", echo), ("input", deck), ("typed", typed)):
            values = _values(rows_of, request_key)
            if values:
                requested_from = source
                conflicting = len(values) > 1
                requested_value = None if conflicting else next(iter(values))
                break

        assumed = assumed_default_setting(
            software,
            version,
            setting,
            calculation.lot.method if calculation.lot is not None else None,
            calculation.type,
        )
        if deciding:
            state, assumed_value = EffectiveSettingState.reported, None
        elif requested_from is not None:
            state, assumed_value = EffectiveSettingState.requested, None
        elif assumed is not None and not log_not_read:
            state = (
                EffectiveSettingState.assumed_default if has_log else EffectiveSettingState.assumed_default_no_output_log
            )
            assumed_value = assumed
        else:
            state, assumed_value = EffectiveSettingState.not_stated, None
        out.append(
            EffectiveSettingSummary(
                setting=setting,
                state=state,
                requested_value=requested_value,
                requested_from=requested_from,
                conflicting=conflicting,
                reported=listed,
                assumed_value=assumed_value,
            )
        )
    return out
