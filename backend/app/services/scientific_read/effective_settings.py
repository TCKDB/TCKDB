"""What grid and SCF convergence a calculation used, and how we know (read-time view; nothing here is stored).

Best evidence first, per setting:

1. **reported**: the output log prints the value the program used.
2. **requested**: the log's echoed input, else the uploaded input deck, else a value the depositor typed.
3. **assumed default**: the program's documented default for the declared program and version
   (:mod:`tckdb_schemas.program_default_settings`), labelled as assumed, because no log reports or echoes it. With no
   output log at all the label is ``assumed_default_no_output_log``.
4. **not stated**: none of the above, including when the program or version is not declared. A version is never
   guessed.

Gaussian's grid is the exception to step 1: the log prints an internal code (``IRadAn``) whose correspondence to a
named grid is not documented, so it is listed but never sets the state.

A reported value is never turned into a requested one, and a requested value is never turned into a reported one.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.program_default_settings import assumed_default_setting

from app.db.models.calculation import Calculation, CalculationArtifact, CalculationParameter
from app.db.models.common import ArtifactKind, ParameterSource, ParameterStatementKind
from app.schemas.reads.scientific_calculation import (
    EffectiveSettingState,
    EffectiveSettingSummary,
    ReportedSettingValue,
)

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


def _value(rows: list[CalculationParameter], key: str) -> str | None:
    values = [r.canonical_value for r in rows if r.canonical_key == key and r.canonical_value is not None]
    return values[0] if values else None


def build_effective_settings(session: Session, calculation: Calculation) -> list[EffectiveSettingSummary]:
    rows = list(
        session.scalars(
            select(CalculationParameter)
            .where(CalculationParameter.calculation_id == calculation.id)
            .order_by(CalculationParameter.id)
        )
    )
    parser = [r for r in rows if r.source is ParameterSource.parser]
    reported_rows = [r for r in parser if r.statement_kind is ParameterStatementKind.reported]
    requested_parser = [r for r in parser if r.statement_kind is not ParameterStatementKind.reported]
    echo = [r for r in requested_parser if r.artifact_kind is ArtifactKind.output_log]
    deck = [r for r in requested_parser if r.artifact_kind is ArtifactKind.input]
    typed = [r for r in rows if r.source is ParameterSource.upload]
    has_log = (
        session.scalar(
            select(CalculationArtifact.id)
            .where(CalculationArtifact.calculation_id == calculation.id, CalculationArtifact.kind == ArtifactKind.output_log)
            .limit(1)
        )
        is not None
    ) or bool(echo or reported_rows)
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

        requested_value, requested_from = None, None
        for source, rows_of in (("output_log", echo), ("input", deck), ("typed", typed)):
            requested_value = _value(rows_of, request_key)
            if requested_value is not None:
                requested_from = source
                break

        assumed = assumed_default_setting(software, version, setting)
        if deciding:
            state, assumed_value = EffectiveSettingState.reported, None
        elif requested_value is not None:
            state, assumed_value = EffectiveSettingState.requested, None
        elif assumed is not None:
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
                reported=listed,
                assumed_value=assumed_value,
            )
        )
    return out
