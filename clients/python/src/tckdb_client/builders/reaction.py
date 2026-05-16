"""Reaction-side builders: ``TransitionState`` and ``ChemReaction``.

A :class:`ChemReaction` groups reactant / product :class:`Species`
identities, an optional :class:`TransitionState`, and zero or more
:class:`tckdb_client.builders.kinetics.Kinetics` records. Together
with the :class:`tckdb_client.builders.uploads.ComputedReactionUpload`
top-level builder, it produces the dict the backend's
``ComputedReactionUploadRequest`` accepts.

Phase-2 scope: no canonicalisation, no RDKit, no chemistry-aware
checks. The server is still the source of truth for identity and
reaction-family validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tckdb_client.builders.geometry import Geometry
from tckdb_client.builders.species import Species
from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_int,
    ensure_optional_non_empty_str,
    ensure_positive_int,
)

if TYPE_CHECKING:  # pragma: no cover — import for type hints only
    from tckdb_client.builders.kinetics import Kinetics

__all__ = [
    "ChemReaction",
    "TransitionState",
]


@dataclass
class TransitionState:
    """Transition-state identity for one reaction.

    Geometry is optional at the builder level — some uploads attach
    the TS geometry indirectly through the primary TS optimisation's
    ``output_geometry``. The ``ComputedReactionUpload`` assembler
    will pick the geometry up from there when needed.
    """

    charge: int
    multiplicity: int = 1
    geometry: Geometry | None = None
    label: str | None = None
    smiles: str | None = None
    _validated: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.charge = ensure_int(self.charge, field="charge")
        self.multiplicity = ensure_positive_int(
            self.multiplicity, field="multiplicity", minimum=1
        )
        if self.geometry is not None and not isinstance(self.geometry, Geometry):
            raise TCKDBBuilderValidationError(
                "TransitionState.geometry, when supplied, must be a "
                "Geometry builder."
            )
        self.label = ensure_optional_non_empty_str(self.label, field="label")
        self.smiles = ensure_optional_non_empty_str(self.smiles, field="smiles")
        self._validated = True


@dataclass
class ChemReaction:
    """One elementary chemical reaction.

    Holds reactant and product :class:`Species` identities, an optional
    :class:`TransitionState`, and a mutable list of
    :class:`Kinetics` records. The same :class:`Species` instance is
    allowed on both reactant and product sides (e.g. catalyst-style
    appearances); duplicate detection is intentionally not done here
    because the server canonicalises identity.
    """

    reactants: list[Species]
    products: list[Species]
    family: str | None = None
    transition_state: TransitionState | None = None
    kinetics: list["Kinetics"] = field(default_factory=list)
    label: str | None = None
    reversible: bool = True
    family_source_note: str | None = None
    _validated: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # Coerce ``None`` for kinetics (the dataclass default is the
        # mutable empty list, but callers commonly pass ``None`` for
        # "no kinetics yet").
        if self.kinetics is None:  # type: ignore[truthy-bool]
            self.kinetics = []
        if not isinstance(self.reversible, bool):
            raise TCKDBBuilderValidationError(
                "ChemReaction.reversible must be a bool."
            )
        if not self.reactants:
            raise TCKDBBuilderValidationError(
                "ChemReaction requires at least one reactant."
            )
        if not self.products:
            raise TCKDBBuilderValidationError(
                "ChemReaction requires at least one product."
            )
        for side, items in (("reactants", self.reactants), ("products", self.products)):
            for i, item in enumerate(items):
                if not isinstance(item, Species):
                    raise TCKDBBuilderValidationError(
                        f"{side}[{i}] must be a Species builder, got "
                        f"{type(item).__name__}."
                    )
        if self.transition_state is not None and not isinstance(
            self.transition_state, TransitionState
        ):
            raise TCKDBBuilderValidationError(
                "ChemReaction.transition_state must be a TransitionState "
                "builder when supplied."
            )

        # Avoid importing Kinetics at module load (circular: kinetics
        # could one day import reaction-side helpers). The runtime
        # ``hasattr`` check + isinstance below is enough.
        from tckdb_client.builders.kinetics import Kinetics as _Kinetics

        for i, k in enumerate(self.kinetics):
            if not isinstance(k, _Kinetics):
                raise TCKDBBuilderValidationError(
                    f"kinetics[{i}] must be a Kinetics builder, got "
                    f"{type(k).__name__}."
                )

        self.family = ensure_optional_non_empty_str(self.family, field="family")
        self.family_source_note = ensure_optional_non_empty_str(
            self.family_source_note, field="family_source_note"
        )
        self.label = ensure_optional_non_empty_str(self.label, field="label")
        self._validated = True

    def add_kinetics(self, kinetics: "Kinetics") -> "ChemReaction":
        """Append a kinetics record; returns ``self`` for chaining."""
        from tckdb_client.builders.kinetics import Kinetics as _Kinetics

        if not isinstance(kinetics, _Kinetics):
            raise TCKDBBuilderValidationError(
                "add_kinetics requires a Kinetics builder."
            )
        self.kinetics.append(kinetics)
        return self

    def unique_species(self) -> list[Species]:
        """Return the unique :class:`Species` instances on either side.

        Uses ``is``-identity to avoid relying on dataclass ``__eq__``
        (two ``Species(smiles="O")`` instances are still distinct
        builders, and the server canonicalises only at upload time).
        Preserves first-seen order across reactants then products.
        """
        seen: list[Species] = []
        for source in (self.reactants, self.products):
            for sp in source:
                if not any(s is sp for s in seen):
                    seen.append(sp)
        return seen

    def all_calculations(self) -> list[Any]:
        """Return every :class:`Calculation` referenced via kinetics roles.

        Used by :class:`ComputedReactionUpload` to enforce the
        "every referenced calc must also be in calculations" rule.
        """
        out: list[Any] = []
        for k in self.kinetics:
            for _role, calc in k.source_calculations_iter():
                if not any(c is calc for c in out):
                    out.append(calc)
        return out
