"""Calculation-origin metadata: an optional, namespaced JSONB block under
``calculation.parameters_json["tckdb_origin"]`` that records *how* a
calculation row came into existence — independently executed, reused
from another calculation, imported from external source, or derived by
post-processing.

This is producer-side metadata; TCKDB stores it but does not enforce a
schema column for ``origin_kind`` today (see DR-0026). When the column
is later promoted, ``CalculationOriginKind`` should move alongside the
backend DB enums and this fragment should be re-shaped to read from /
write to that column.

Validation rule: if ``parameters_json["tckdb_origin"]`` is present, it
must conform to :class:`CalculationOriginMetadata`. Absence is allowed
and means "executed" by default.
"""

from __future__ import annotations

from enum import Enum
from typing import Self

from pydantic import BaseModel, model_validator

from tckdb_schemas.enums import CalculationType


class CalculationOriginKind(str, Enum):
    """How a ``calculation`` row came to exist.

    Vocabulary is intentionally producer-agnostic so that ARC, curator
    tooling, post-processing scripts, and replay scripts can share it.

    - ``executed``: an external ESS (Gaussian, ORCA, …) actually ran
      this calculation and produced its output. Default when the
      ``tckdb_origin`` block is absent.
    - ``reused_result``: another row's value was copied/asserted as the
      value for this row. The classic case is ARC reusing the opt
      energy as the SP energy when ``sp_level == opt_level``.
    - ``imported``: pulled from external source (e.g. literature DOI,
      published supporting information, prior database).
    - ``derived``: produced by post-processing operating on existing
      rows (e.g. an empirical-correction workflow that emits a new
      calculation from an existing one).
    """

    executed = "executed"
    reused_result = "reused_result"
    imported = "imported"
    derived = "derived"


class ReusedFromCalculationRef(BaseModel):
    """Lightweight reference to the source calculation when ``origin_kind``
    is ``reused_result`` or ``derived``.

    Carries only the *type* of the source calculation, not its database
    id. The relational link from this row to its source is provided by
    ``calculation_dependency`` (which IS FK-enforced); duplicating the
    id here would create a soft pointer the database cannot validate.
    See DR-0026 for the rationale.
    """

    calculation_type: CalculationType


class CalculationOriginMetadata(BaseModel):
    """Validated shape for ``parameters_json["tckdb_origin"]``.

    All fields except ``origin_kind`` are optional. Producers populate
    only what they know; consumers tolerate absent fields.

    The ``model_validator`` below enforces the cross-field rule that
    ``origin_kind == reused_result`` requires ``reused_from`` and is
    incompatible with ``independent_ess_job=True``. Other origin_kind
    values currently impose no cross-field constraints — they may grow
    them as the vocabulary matures.
    """

    origin_kind: CalculationOriginKind
    reused_from: ReusedFromCalculationRef | None = None
    reason: str | None = None
    independent_ess_job: bool | None = None
    producer: str | None = None

    @model_validator(mode="after")
    def validate_reused_result_constraints(self) -> Self:
        if self.origin_kind is CalculationOriginKind.reused_result:
            if self.reused_from is None:
                raise ValueError(
                    "tckdb_origin.origin_kind='reused_result' requires "
                    "reused_from.calculation_type to identify the source "
                    "calculation type. The relational link to the actual "
                    "parent row is recorded in calculation_dependency."
                )
            if self.independent_ess_job is True:
                raise ValueError(
                    "tckdb_origin.origin_kind='reused_result' is "
                    "incompatible with independent_ess_job=True; "
                    "a reused result by definition did not run an "
                    "independent ESS job."
                )
        return self


__all__ = [
    "CalculationOriginKind",
    "ReusedFromCalculationRef",
    "CalculationOriginMetadata",
]
