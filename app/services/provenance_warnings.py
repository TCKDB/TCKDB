"""Provenance-presence warnings emitted at workflow boundaries.

Scientific-product uploads (thermo, transport, statmech, kinetics) all
carry optional provenance fragments — literature, software release,
workflow-tool release, level of theory, frequency scale factor — whose
absence is currently invisible: the resolved FK columns are simply
NULL and the upload succeeds silently. That lets records accumulate
with no audit trail even when upstream provenance likely existed.

This module emits structured :class:`UploadWarning` entries at the
upload boundary whenever provenance *that is scientifically meaningful
for the record type and origin* was omitted. It is deliberately
conservative: only a small set of high-signal categories, no warnings
for fields that are not typically expected for a given origin.

Two complementary behaviors are expected and live elsewhere:

* Provenance fragments that are **supplied and valid** are resolved
  and persisted by the existing resolution services — no silent drop.
* Provenance fragments that are **supplied but malformed or
  unresolvable** fail at Pydantic validation (required names, etc.) or
  in the resolver service (e.g. ``resolve_or_create_literature``
  raising for an unresolvable DOI) — silent NULL persistence is not a
  normal outcome for attempted provenance.
"""

from __future__ import annotations

from app.db.models.common import ScientificOriginKind
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transport_upload import TransportUploadRequest

# ---------------------------------------------------------------------------
# Warning codes
# ---------------------------------------------------------------------------

W_MISSING_LITERATURE_PROVENANCE = "missing_literature_provenance"
W_MISSING_SOFTWARE_RELEASE_PROVENANCE = "missing_software_release_provenance"
W_MISSING_WORKFLOW_TOOL_PROVENANCE = "missing_workflow_tool_provenance"
W_MISSING_LEVEL_OF_THEORY_PROVENANCE = "missing_level_of_theory_provenance"
W_MISSING_FREQUENCY_SCALE_FACTOR_PROVENANCE = (
    "missing_frequency_scale_factor_provenance"
)


# Origins for which computational provenance (software + workflow tool)
# is the expected audit trail. Non-computational origins expect a
# literature anchor instead.
_COMPUTATIONAL_ORIGINS = frozenset({ScientificOriginKind.computed})


# ---------------------------------------------------------------------------
# Warning constructors
# ---------------------------------------------------------------------------


def _literature_warning(field: str = "literature") -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_LITERATURE_PROVENANCE,
        message=(
            "No literature provenance was supplied. Non-computed records "
            "(experimental or estimated) should carry a literature "
            "reference so the source of the data can be audited."
        ),
    )


def _software_release_warning(
    field: str = "software_release",
) -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_SOFTWARE_RELEASE_PROVENANCE,
        message=(
            "No software release provenance was supplied. Computed "
            "records should identify which electronic-structure or "
            "post-processing software produced them."
        ),
    )


def _workflow_tool_release_warning(
    field: str = "workflow_tool_release",
) -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_WORKFLOW_TOOL_PROVENANCE,
        message=(
            "No workflow-tool release provenance was supplied. Computed "
            "records should identify the orchestration tool (e.g. ARC) "
            "that produced them."
        ),
    )


def _level_of_theory_warning(
    field: str = "energy_level_of_theory",
) -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_LEVEL_OF_THEORY_PROVENANCE,
        message=(
            "No energy level-of-theory provenance was supplied. Computed "
            "kinetics should declare the electronic-energy level of "
            "theory so source SP calculations can be anchored to it."
        ),
    )


def _freq_scale_factor_warning(
    field: str = "freq_scale_factor",
) -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_FREQUENCY_SCALE_FACTOR_PROVENANCE,
        message=(
            "No frequency scale factor provenance was supplied. Computed "
            "statmech should record the scaling applied to harmonic "
            "frequencies; use value=1.0 for explicitly unscaled results."
        ),
    )


def _computed_common_warnings(
    *,
    software_release: object | None,
    workflow_tool_release: object | None,
) -> list[UploadWarning]:
    warnings: list[UploadWarning] = []
    if software_release is None:
        warnings.append(_software_release_warning())
    if workflow_tool_release is None:
        warnings.append(_workflow_tool_release_warning())
    return warnings


# ---------------------------------------------------------------------------
# Per-product entry points
# ---------------------------------------------------------------------------


def collect_thermo_provenance_warnings(
    request: ThermoUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a thermo upload."""
    if request.scientific_origin in _COMPUTATIONAL_ORIGINS:
        return _computed_common_warnings(
            software_release=request.software_release,
            workflow_tool_release=request.workflow_tool_release,
        )
    if request.literature is None:
        return [_literature_warning()]
    return []


def collect_transport_provenance_warnings(
    request: TransportUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a transport upload."""
    if request.scientific_origin in _COMPUTATIONAL_ORIGINS:
        return _computed_common_warnings(
            software_release=request.software_release,
            workflow_tool_release=request.workflow_tool_release,
        )
    if request.literature is None:
        return [_literature_warning()]
    return []


def collect_statmech_provenance_warnings(
    request: StatmechUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a statmech upload.

    Computed statmech additionally expects a ``freq_scale_factor``
    anchor: a NULL value means "unknown/not recorded", and leaving it
    implicit erases a scientifically meaningful piece of the record.
    """
    warnings: list[UploadWarning] = []
    if request.scientific_origin in _COMPUTATIONAL_ORIGINS:
        warnings.extend(
            _computed_common_warnings(
                software_release=request.software_release,
                workflow_tool_release=request.workflow_tool_release,
            )
        )
        if request.freq_scale_factor is None:
            warnings.append(_freq_scale_factor_warning())
    elif request.literature is None:
        warnings.append(_literature_warning())
    return warnings


def collect_kinetics_provenance_warnings(
    request: KineticsUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a kinetics upload.

    Computed kinetics additionally expects ``energy_level_of_theory``:
    without it, source SP calculations cannot be auto-resolved and the
    kinetics record loses its electronic-energy anchor.
    """
    warnings: list[UploadWarning] = []
    if request.scientific_origin in _COMPUTATIONAL_ORIGINS:
        warnings.extend(
            _computed_common_warnings(
                software_release=request.software_release,
                workflow_tool_release=request.workflow_tool_release,
            )
        )
        if request.energy_level_of_theory is None:
            warnings.append(_level_of_theory_warning())
    elif request.literature is None:
        warnings.append(_literature_warning())
    return warnings
