"""Shared enthalpy declaration rules, independent of persistence and transport."""

from collections.abc import Mapping
from typing import Any

from tckdb_schemas.enums import EnthalpyReferenceKind


W_ENTHALPY_DECLARATION_ABSENT = "enthalpy_declaration_absent"
W_ENTHALPY_DECLARATION_WITHOUT_CONTENT = "enthalpy_declaration_without_content"
W_ENTHALPY_QUANTITY_NOT_STORABLE_HERE = "enthalpy_quantity_not_storable_here"

def enthalpy_reference_error(payload: Any) -> tuple[str, str] | None:
    """Return a refusal code/message, or None for a coherent declaration.

    A 0 K formation scalar has its own reference and is outside this rule.
    Never derive a missing scalar from a fit or infer a declaration.
    """
    def get(obj: Any, key: str) -> Any:
        return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)

    reference = get(payload, "enthalpy_reference_kind")
    if reference is not None and reference != EnthalpyReferenceKind.formation_from_elements_298k:
        return (
            W_ENTHALPY_QUANTITY_NOT_STORABLE_HERE,
            "Thermo accepts only formation_from_elements_298k enthalpies. "
            "Deposit sensible increments and absolute enthalpies through "
            "the molecular_property_observation route instead.",
        )
    content = (
        get(payload, "h298_kj_mol") is not None
        or get(payload, "nasa") is not None
        or bool(get(payload, "nasa9_intervals"))
        or get(get(payload, "wilhoit"), "h0_kj_mol") is not None
        or any(get(point, "h_kj_mol") is not None for point in (get(payload, "points") or []))
    )
    if content and reference is None:
        return (
            W_ENTHALPY_DECLARATION_ABSENT,
            "Enthalpy content requires enthalpy_reference_kind. Declare "
            "formation_from_elements_298k only when the source states that convention; "
            "other enthalpy quantities belong in molecular_property_observation.",
        )
    if reference is not None and not content:
        return (
            W_ENTHALPY_DECLARATION_WITHOUT_CONTENT,
            "enthalpy_reference_kind declares a quantity this thermo record does not carry. "
            "Omit the declaration for entropy and heat-capacity-only records.",
        )
    return None
