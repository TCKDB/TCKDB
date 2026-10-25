"""The filters a thermo or kinetics selection takes, which are the filters the matching browse and search take.

A candidate rests on source calculations. The provenance filters (level of theory, software, method, basis, workflow
tool, method family, DFT rung) keep a candidate when ONE source calculation it links to, and the read profile shows,
matches every supplied field at once. A candidate never matches through two different calculations, and never through
a calculation it does not link to. Under ``curated`` a hidden calculation contributes nothing (hidden equals unknown
equals absent).

``method_family`` and the DFT rung classify the calculation's method (:mod:`app.chemistry.method_families`). They are
a classification, not a measure of accuracy, and a filter fact only: they never order or select anything.

Public values only: a level of theory is a public ref, never a database id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Filters that are matched through source calculations (the others are matched on the record itself).
_PROVENANCE_FIELDS = (
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
    "method_family",
    "dft_rung_min",
    "dft_rung_max",
)


@dataclass(frozen=True)
class SourceFilters:
    """The caller's filters on a record that rests on source calculations.

    :param model_kind: The record's stored model form.
    :param scientific_origin: The record's own origin (``computed``, ``experimental`` or ``estimated``).
    :param level_of_theory_ref: A shown source calculation ran at this level of theory.
    :param method_family: A shown source calculation's method is in one of these families.
    """

    model_kind: str | None = None
    scientific_origin: str | None = None
    level_of_theory_ref: str | None = None
    method: str | None = None
    basis: str | None = None
    software: str | None = None
    software_version: str | None = None
    workflow_tool: str | None = None
    workflow_tool_version: str | None = None
    method_family: tuple[str, ...] | None = None
    dft_rung_min: int | None = None
    dft_rung_max: int | None = None

    def to_record(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if value is None:
                continue
            out[key] = sorted(set(value)) if key == "method_family" else value
        return out

    @classmethod
    def from_record(cls, raw: dict[str, Any]):
        fields = dict(raw)
        if fields.get("method_family") is not None:
            fields["method_family"] = tuple(sorted(set(fields["method_family"])))
        return cls(**fields)

    @property
    def active(self) -> bool:
        return any(v is not None for v in self.__dict__.values())

    def provenance(self) -> dict[str, Any]:
        """The supplied constraints that one source calculation has to meet together (not the level-of-theory ref)."""
        return {k: getattr(self, k) for k in _PROVENANCE_FIELDS if getattr(self, k) is not None}

    @property
    def has_source_constraint(self) -> bool:
        return self.level_of_theory_ref is not None or bool(self.provenance())
