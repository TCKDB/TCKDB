"""Write the bath gas a reaction-level rate states (``kinetics_bath_gas``).

The wire schemas have already refused a bath gas that is not a composition (see
``tckdb_schemas.fragments.kinetics_bath_gas``). What only the write path can see is that two
differently spelled components resolved to the *same* species entry; the primary key would
otherwise answer that with an integrity error, so it is refused here with the same code.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session
from tckdb_schemas.fragments.kinetics_bath_gas import W_KINETICS_BATH_GAS_DUPLICATE_SPECIES

from app.api.error_contract import CodedValueError
from app.db.models.kinetics import Kinetics, KineticsBathGas
from app.db.models.species import SpeciesEntry


def persist_kinetics_bath_gas(
    session: Session,
    kinetics: Kinetics,
    components: Sequence[tuple[SpeciesEntry, float]],
    *,
    field: str = "bath_gas",
) -> None:
    """Add one ``kinetics_bath_gas`` row per resolved component.

    :param components: ``(species_entry, mole_fraction)`` in the order the depositor stated them.
    :param field: Field path a refusal names.
    :raises CodedValueError: ``kinetics_bath_gas_duplicate_species`` when two components resolve
        to the same species entry.
    """
    first_position: dict[int, int] = {}
    for position, (entry, mole_fraction) in enumerate(components):
        if entry.id in first_position:
            raise CodedValueError(
                W_KINETICS_BATH_GAS_DUPLICATE_SPECIES,
                f"{field}[{position}] names the same species as {field}[{first_position[entry.id]}]. "
                "State each bath-gas component once, with its total mole fraction.",
                context={"field": f"{field}[{position}]", "first": f"{field}[{first_position[entry.id]}]"},
                message_prefix=False,
            )
        first_position[entry.id] = position
        session.add(KineticsBathGas(kinetics_id=kinetics.id, species_entry_id=entry.id, mole_fraction=mole_fraction))
