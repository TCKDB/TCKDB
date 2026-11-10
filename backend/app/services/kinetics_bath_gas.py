"""Write the bath gas a reaction-level rate states (``kinetics_bath_gas``).

The wire schemas have already refused a bath gas that is not a composition (see
``tckdb_schemas.fragments.kinetics_bath_gas``). What only the write path can see is that two
differently spelled components resolved to the *same* species entry; the primary key would
otherwise answer that with an integrity error, so it is refused here with the same code.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.kinetics_bath_gas import W_KINETICS_BATH_GAS_DUPLICATE_SPECIES
from tckdb_schemas.kinetics_declarations import (
    W_KINETICS_DECLARATION_CONTRADICTS_RECORD,
    kinetics_bath_gas_species_error,
)

from app.api.error_contract import CodedValueError
from app.db.models.kinetics import Kinetics, KineticsBathGas
from app.db.models.network_pdep import NetworkKinetics, NetworkSolveBathGas
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
    if components:
        _refuse_contradictions(session, kinetics, components, field=field)


def _refuse_contradictions(
    session: Session, kinetics: Kinetics, components: Sequence[tuple[SpeciesEntry, float]], *, field: str
) -> None:
    """A stated bath gas must not contradict the other statements the record makes about its collider.

    Compared at species level (the entry's species public ref), where species are resolved. The
    applicability declaration's colliders and the network solve's bath gas are read, never copied:
    no statement is inferred from another.

    :raises CodedValueError: ``kinetics_declaration_contradicts_record``.
    """
    stated: dict[str, float] = {}
    for entry, fraction in components:
        ref = entry.species.public_ref
        stated[ref] = stated.get(ref, 0.0) + fraction
    contradiction = kinetics_bath_gas_species_error(kinetics.applicability_declaration, stated)
    if contradiction is not None:
        code, message = contradiction
        raise CodedValueError(code, message, context={"field": field, "reason": "collider"}, message_prefix=False)
    if kinetics.network_kinetics_id is None:
        return
    solve_id = session.scalar(select(NetworkKinetics.solve_id).where(NetworkKinetics.id == kinetics.network_kinetics_id))
    rows = session.execute(
        select(SpeciesEntry, NetworkSolveBathGas.mole_fraction)
        .join(NetworkSolveBathGas, NetworkSolveBathGas.species_entry_id == SpeciesEntry.id)
        .where(NetworkSolveBathGas.solve_id == solve_id)
    ).all()
    solved: dict[str, float] = {}
    for entry, fraction in rows:
        solved[entry.species.public_ref] = solved.get(entry.species.public_ref, 0.0) + fraction
    if solved and (
        set(solved) != set(stated) or any(abs(solved[ref] - stated[ref]) > 1e-9 for ref in solved)
    ):
        raise CodedValueError(
            W_KINETICS_DECLARATION_CONTRADICTS_RECORD,
            "The record states a bath gas that differs from the bath gas of the network solve it is linked to.",
            context={"field": field, "reason": "network"},
            message_prefix=False,
        )
