"""Shared enthalpy declaration rules, independent of persistence and transport."""

from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any

from tckdb_schemas.enums import EnthalpyReferenceKind
from tckdb_schemas.producer_rule import producer_rule

W_ENTHALPY_DECLARATION_ABSENT = "enthalpy_declaration_absent"
W_ENTHALPY_DECLARATION_WITHOUT_CONTENT = "enthalpy_declaration_without_content"
W_ENTHALPY_QUANTITY_NOT_STORABLE_HERE = "enthalpy_quantity_not_storable_here"
W_ENTHALPY_REFERENCE_KIND_UNRECOGNIZED = "enthalpy_reference_kind_unrecognized"

#: Reasons :func:`shared_enthalpy_reference` declines to name a shared basis.
ENTHALPY_REFERENCE_UNRECORDED = "enthalpy_reference_unrecorded"
ENTHALPY_REFERENCE_MIXED = "enthalpy_reference_mixed"


def shared_enthalpy_reference(kinds: Iterable[Any]) -> tuple[str | None, str | None]:
    """Return ``(kind, None)`` when every term declares one shared enthalpy basis.

    This is the rule for combining enthalpies from several thermo records
    (a reaction enthalpy, a Hess cycle): every term must *declare* its
    ``enthalpy_reference_kind``, and all must declare the *same* one.

    * Any term ``None`` -> ``(None, "enthalpy_reference_unrecorded")``. An
      undeclared enthalpy means its reference was never recorded, not that
      it defaults to a formation quantity; this wins over "mixed".
    * Two or more distinct declared kinds -> ``(None, "enthalpy_reference_mixed")``.
      Only one kind exists today, so this cannot fire yet; it is the check
      that stays correct if a second kind is ever added.
    * Otherwise ``(token, None)``, where ``token`` is the declared kind's
      string value (enum members of the backend's and this package's
      ``EnthalpyReferenceKind`` and plain strings all compare by value).

    An empty collection raises ``ValueError``: zero terms share nothing, and
    answering "shared" for them would be a vacuous pass. Callers decide what
    having no terms means before asking.
    """
    tokens = [kind.value if isinstance(kind, Enum) else kind for kind in kinds]
    if not tokens:
        raise ValueError("shared_enthalpy_reference needs at least one term")
    distinct = set(tokens)
    if None in distinct:
        return None, ENTHALPY_REFERENCE_UNRECORDED
    if len(distinct) > 1:
        return None, ENTHALPY_REFERENCE_MIXED
    return tokens[0], None


@producer_rule
def enthalpy_reference_error(payload: Any) -> tuple[str, str] | None:
    """A thermo record with enthalpy content must declare what its enthalpies mean.

    ``enthalpy_reference_kind`` accepts one value, ``formation_298k``: every
    enthalpy on the record is a standard enthalpy of formation, pinned at
    298.15 K, with the species' own increment above 298.15 K added at other
    temperatures. It is never defaulted or inferred. Any other value is
    refused; a near-miss spelling of ``formation_298k`` gets its own refusal.

    Enthalpy content, any of which requires ``enthalpy_reference_kind`` and
    without all of which a declaration is refused:

    * ``h298_kj_mol``;
    * a ``nasa`` block or ``nasa9_intervals`` (their enthalpy constants);
    * ``wilhoit.h0_kj_mol``;
    * a point ``h_kj_mol``;
    * a point ``g_kj_mol``. A tabulated Gibbs energy is H(T) - T*S(T) on the
      record's enthalpy zero, so it carries H's reference exactly as a point
      H does, even on a point with no H of its own.

    A 0 K formation scalar has its own reference and is outside this rule.
    Never derive a missing scalar from a fit or infer a declaration.

    Returns a refusal ``(code, message)``, or ``None`` for a coherent
    declaration.
    """
    def get(obj: Any, key: str) -> Any:
        return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)

    reference = get(payload, "enthalpy_reference_kind")
    if reference is not None and reference != EnthalpyReferenceKind.formation_298k:
        # A near-miss of the one legal value (wrong case, stray whitespace)
        # is a typo, not a depositor naming some other quantity -- pointing
        # a typo at molecular_property_observation is wrong advice, so it
        # gets its own outcome rather than falling through to the "not
        # storable here" refusal below.
        canonical = EnthalpyReferenceKind.formation_298k.value
        if isinstance(reference, str) and reference.strip().lower() == canonical:
            return (
                W_ENTHALPY_REFERENCE_KIND_UNRECOGNIZED,
                f"'{reference}' is not a recognized enthalpy_reference_kind -- did you mean "
                f"'{canonical}'? Matching is exact and case-sensitive.",
            )
        return (
            W_ENTHALPY_QUANTITY_NOT_STORABLE_HERE,
            "Thermo accepts only formation_298k enthalpies. "
            "Deposit sensible increments and absolute enthalpies through "
            "the molecular_property_observation route instead.",
        )
    content = (
        get(payload, "h298_kj_mol") is not None
        or get(payload, "nasa") is not None
        or bool(get(payload, "nasa9_intervals"))
        or get(get(payload, "wilhoit"), "h0_kj_mol") is not None
        or any(
            get(point, "h_kj_mol") is not None or get(point, "g_kj_mol") is not None
            for point in (get(payload, "points") or [])
        )
    )
    if content and reference is None:
        return (
            W_ENTHALPY_DECLARATION_ABSENT,
            "Enthalpy content requires enthalpy_reference_kind. Declare "
            "formation_298k only when the source states that convention; "
            "other enthalpy quantities belong in molecular_property_observation.",
        )
    if reference is not None and not content:
        return (
            W_ENTHALPY_DECLARATION_WITHOUT_CONTENT,
            "enthalpy_reference_kind declares a quantity this thermo record does not carry. "
            "Omit the declaration for entropy and heat-capacity-only records.",
        )
    return None
