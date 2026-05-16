"""Geometry builder.

The bundle endpoint accepts a free-form XYZ text block per geometry
(see ``app.schemas.fragments.geometry.GeometryPayload``). The
:class:`Geometry` builder is therefore a thin wrapper over that text:
it normalises atomic symbols (``"h"`` → ``"H"``) and stores the
preserved source string. Geometry canonicalisation and hashing live
on the server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_non_empty_str,
    ensure_optional_non_empty_str,
)

__all__ = ["Geometry"]


# Matches a Cartesian XYZ line: "<symbol> <x> <y> <z>" with optional
# trailing comment columns. Lenient on whitespace; the server is the
# final authority on parsing.
_XYZ_LINE = re.compile(
    r"^\s*([A-Za-z][A-Za-z]?)(\s+[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?){3}"
)


@dataclass
class Geometry:
    """Cartesian XYZ geometry, stored as text plus the parsed atom count.

    Construct via :meth:`from_xyz`; the bare constructor is reserved
    for tooling/tests.
    """

    xyz_text: str
    label: str | None = None
    natoms: int | None = None
    _validated: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.xyz_text = ensure_non_empty_str(self.xyz_text, field="xyz_text")
        self.label = ensure_optional_non_empty_str(self.label, field="label")
        if self.natoms is not None and self.natoms < 0:
            raise TCKDBBuilderValidationError(
                f"natoms must be >= 0, got {self.natoms}."
            )
        self._validated = True

    @classmethod
    def from_xyz(cls, xyz: str, *, label: str | None = None) -> "Geometry":
        """Parse a Cartesian XYZ block into a :class:`Geometry`.

        Two input shapes are accepted:

        - Standard XYZ — first line is an atom count, second is a free
          comment, remaining lines are ``<symbol> x y z``.
        - Bare body — only the ``<symbol> x y z`` lines.

        Atomic symbols are lower-cased to canonical capitalised form
        (``"h"`` → ``"H"``, ``"NA"`` → ``"Na"``). The server still owns
        periodic-table validation and any chemistry-aware checks.
        """
        if not isinstance(xyz, str):
            raise TCKDBBuilderValidationError(
                "Geometry.from_xyz requires a string."
            )
        text = xyz.strip()
        if not text:
            raise TCKDBBuilderValidationError(
                "Geometry.from_xyz requires non-empty xyz text."
            )

        raw_lines = text.splitlines()
        natoms: int | None = None
        header_lines: list[str] = []
        atom_lines: list[str] = raw_lines

        first = raw_lines[0].strip()
        if first.isdigit():
            natoms = int(first)
            header_lines = raw_lines[:2]
            atom_lines = raw_lines[2:]

        normalised_atoms: list[str] = []
        for line in atom_lines:
            stripped = line.strip()
            if not stripped:
                continue
            match = _XYZ_LINE.match(stripped)
            if match is None:
                raise TCKDBBuilderValidationError(
                    f"unrecognised XYZ line: {stripped!r}"
                )
            parts = stripped.split()
            parts[0] = _normalise_symbol(parts[0])
            normalised_atoms.append(" ".join(parts))

        if not normalised_atoms:
            raise TCKDBBuilderValidationError(
                "Geometry.from_xyz produced zero atom lines."
            )

        if natoms is not None and natoms != len(normalised_atoms):
            raise TCKDBBuilderValidationError(
                f"XYZ header declared natoms={natoms} but "
                f"{len(normalised_atoms)} atom lines were parsed."
            )
        if natoms is None:
            natoms = len(normalised_atoms)

        rebuilt = "\n".join([*header_lines, *normalised_atoms]).rstrip()
        if not header_lines:
            # Bare body input — synthesise a standard header so the
            # server side does not have to special-case.
            rebuilt = f"{natoms}\ngeometry\n" + "\n".join(normalised_atoms)

        return cls(xyz_text=rebuilt, label=label, natoms=natoms)

    def to_payload(self) -> dict[str, Any]:
        """Return the ``GeometryPayload`` fragment dict."""
        return {"xyz_text": self.xyz_text}


def _normalise_symbol(symbol: str) -> str:
    """Capitalise an atomic symbol; reject empty/digits-only input.

    The capitalisation rule is intentionally simple: first character
    upper, remainder lower. ``"h"`` → ``"H"``, ``"na"`` → ``"Na"``,
    ``"CL"`` → ``"Cl"``. The server still validates against the
    periodic table.
    """
    if not symbol or not symbol[0].isalpha():
        raise TCKDBBuilderValidationError(
            f"invalid atomic symbol {symbol!r}."
        )
    return symbol[0].upper() + symbol[1:].lower()
