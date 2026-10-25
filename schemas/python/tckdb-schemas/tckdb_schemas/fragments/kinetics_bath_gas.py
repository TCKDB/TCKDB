"""The bath gas a reaction-level rate was obtained for, shared by every route that deposits one.

A PLOG or Chebyshev fit, or an apparent rate at a stated pressure, is a statement about a
collider: a table fitted for Ar is not one for N2. A record carries a bath gas **only where the
depositor states it**; an absent list reads as "not stated" and nothing is ever inferred, neither
from a falloff's third-body efficiencies (a different statement) nor from a source file that does
not carry one.

Both ``KineticsUploadRequest`` (``POST /uploads/kinetics`` and a kinetics contribution bundle,
which names each component by species content) and ``BundleKineticsIn``
(``POST /uploads/computed-reaction``, which names it by the bundle's species key) use
:func:`check_kinetics_bath_gas`, so the routes cannot disagree about what a valid bath gas is. The
rules mirror the network solve's bath gas (``NetworkSolveIn``): the same mole-fraction range per
component, distinct components, and a composition that sums to 1 within 1e-9.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence

from pydantic import Field

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import PressureContext
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload

#: Absolute tolerance on the mole-fraction sum, the same as a network solve's bath gas.
BATH_GAS_SUM_TOLERANCE = 1e-9

#: A bath gas was given for a rate at the high-pressure limit, where it has no meaning.
W_KINETICS_BATH_GAS_AT_HIGH_PRESSURE_LIMIT = "kinetics_bath_gas_at_high_pressure_limit"
#: The same species entry was named twice.
W_KINETICS_BATH_GAS_DUPLICATE_SPECIES = "kinetics_bath_gas_duplicate_species"
#: The mole fractions do not sum to 1.
W_KINETICS_BATH_GAS_MOLE_FRACTION_SUM = "kinetics_bath_gas_mole_fraction_sum"

__all__ = [
    "BATH_GAS_SUM_TOLERANCE",
    "BundleKineticsBathGasIn",
    "KineticsBathGasUpload",
    "W_KINETICS_BATH_GAS_AT_HIGH_PRESSURE_LIMIT",
    "W_KINETICS_BATH_GAS_DUPLICATE_SPECIES",
    "W_KINETICS_BATH_GAS_MOLE_FRACTION_SUM",
    "check_kinetics_bath_gas",
]


class KineticsBathGasUpload(SchemaBase):
    """One bath-gas component of a rate, named by species content.

    :param species_entry: Species-entry identity of the component (resolved or created, as for a
        reaction participant).
    :param mole_fraction: Mole fraction of this component, in (0, 1].
    """

    species_entry: SpeciesEntryIdentityPayload
    mole_fraction: float = Field(gt=0, le=1)


class BundleKineticsBathGasIn(SchemaBase):
    """One bath-gas component of a bundle fit, named by the bundle's species key.

    :param species_key: Local key referencing a species in the bundle's ``species`` list.
    :param mole_fraction: Mole fraction of this component, in (0, 1].
    """

    species_key: str = Field(min_length=1)
    mole_fraction: float = Field(gt=0, le=1)


def check_kinetics_bath_gas(
    *,
    pressure_context: PressureContext | None,
    mole_fractions: Sequence[float],
    identities: Sequence[Hashable],
    field: str = "bath_gas",
) -> None:
    """Refuse a bath gas that is not a composition a rate could have been obtained for.

    Nothing is checked for an empty bath gas: it is "not stated". Row ids never appear in the
    message; the offending component is named by its position.

    :param pressure_context: The record's stated pressure context.
    :param mole_fractions: The components' mole fractions, in order.
    :param identities: One hashable per component that is equal exactly when two components name
        the same species (species content, or the bundle key).
    :param field: Field path the refusal names.
    :raises CodedValidationError: ``kinetics_bath_gas_at_high_pressure_limit``,
        ``kinetics_bath_gas_duplicate_species`` or ``kinetics_bath_gas_mole_fraction_sum``.
    """
    if not mole_fractions:
        return
    if pressure_context == PressureContext.high_p_limit:  # str enums: the backend keeps its own copy
        raise CodedValidationError(
            W_KINETICS_BATH_GAS_AT_HIGH_PRESSURE_LIMIT,
            f"{field} is given for a rate at the high-pressure limit, where there is no bath gas "
            "to state: the limit is the rate with collisional energy transfer removed. Drop the "
            "bath gas, or state the pressure context the rate was obtained in.",
            context={"field": field, "pressure_context": PressureContext.high_p_limit.value},
            message_prefix=False,
        )
    seen: dict[Hashable, int] = {}
    for position, identity in enumerate(identities):
        if identity in seen:
            raise CodedValidationError(
                W_KINETICS_BATH_GAS_DUPLICATE_SPECIES,
                f"{field}[{position}] names the same species as {field}[{seen[identity]}]. "
                "State each bath-gas component once, with its total mole fraction.",
                context={"field": f"{field}[{position}]", "first": f"{field}[{seen[identity]}]"},
                message_prefix=False,
            )
        seen[identity] = position
    total = math.fsum(mole_fractions)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=BATH_GAS_SUM_TOLERANCE):
        raise CodedValidationError(
            W_KINETICS_BATH_GAS_MOLE_FRACTION_SUM,
            f"{field} mole fractions must sum to 1.0 within {BATH_GAS_SUM_TOLERANCE:g}; they sum to {total:.12g}.",
            context={"field": field, "sum": total, "tolerance": BATH_GAS_SUM_TOLERANCE},
            message_prefix=False,
        )
