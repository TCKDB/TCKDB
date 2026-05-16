"""Species builder.

A :class:`Species` instance is a local upload-construction object: it
carries the identity fragment that ``ComputedSpeciesUpload`` will emit
as the ``species_entry`` block of the bundle payload. It has no
database id and never round-trips through the server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_int,
    ensure_non_empty_str,
    ensure_optional_non_empty_str,
    ensure_positive_int,
)

__all__ = ["Species"]


# ``eq=False`` keeps the builder identity-hashable. Two ``Species(smiles="O")``
# instances are *distinct* builders (they get distinct local keys, distinct
# dict slots), even though the server canonicalises them to one row at
# upload time. The whole builder layer is already identity-tracked
# (``KeyMinter`` uses ``is``-comparison); making ``Species`` content-equal
# would break the ``species_calculations: dict[Species, …]`` API where two
# identity-distinct reactants might share SMILES.
@dataclass(eq=False)
class Species:
    """A chemical species identity (no DB id, no server round-trip).

    At least one of ``smiles`` / ``inchi`` / ``inchi_key`` must be
    supplied. ``charge`` and ``multiplicity`` are required because
    they determine which canonical species the server resolves the
    upload against.
    """

    smiles: str | None = None
    charge: int = 0
    multiplicity: int = 1
    label: str | None = None
    inchi: str | None = None
    inchi_key: str | None = None

    # Internal flag set by :meth:`__post_init__` once validation has
    # run successfully. Builder code can rely on it without re-running
    # validation on every payload emission.
    _validated: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate()
        self._validated = True

    def _validate(self) -> None:
        any_identifier = (
            self.smiles is not None
            or self.inchi is not None
            or self.inchi_key is not None
        )
        if not any_identifier:
            raise TCKDBBuilderValidationError(
                "Species requires at least one of smiles / inchi / inchi_key."
            )
        if self.smiles is not None:
            self.smiles = ensure_non_empty_str(self.smiles, field="smiles")
        self.inchi = ensure_optional_non_empty_str(self.inchi, field="inchi")
        self.inchi_key = ensure_optional_non_empty_str(
            self.inchi_key, field="inchi_key"
        )
        self.charge = ensure_int(self.charge, field="charge")
        self.multiplicity = ensure_positive_int(
            self.multiplicity, field="multiplicity", minimum=1
        )
        self.label = ensure_optional_non_empty_str(self.label, field="label")

    def to_identity_payload(self) -> dict[str, Any]:
        """Return the ``species_entry`` fragment for the bundle payload.

        The shape matches
        ``app.schemas.fragments.identity.SpeciesEntryIdentityPayload``
        — ``smiles`` is the load-bearing identifier today; ``inchi`` and
        ``inchi_key`` are kept on the builder for forward compatibility
        but not emitted (the server resolves identity from ``smiles``).
        """
        if self.smiles is None:
            raise TCKDBBuilderValidationError(
                "Species.to_identity_payload requires smiles; the bundle "
                "endpoint does not yet accept inchi/inchi_key as the sole "
                "identifier."
            )
        return {
            "smiles": self.smiles,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
        }
