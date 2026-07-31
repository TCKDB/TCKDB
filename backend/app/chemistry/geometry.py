from __future__ import annotations

from dataclasses import dataclass

from app.chemistry.isotopes import normalize_isotope, validate_isotope
from app.schemas.fragments.geometry import GeometryPayload


@dataclass(frozen=True)
class ParsedXYZ:
    """Parsed canonical representation of an XYZ geometry block.

    :param natoms: Number of atoms declared in the XYZ payload.
    :param canonical_xyz_text: Canonicalized XYZ text used for hashing.
    :param atoms: Parsed atom records as ``(element, x, y, z)`` tuples.
    :param isotopes: Normalized non-standard isotope substitutions, as a
        sorted tuple of ``(atom_index, mass_number)`` pairs with 1-based atom
        indices matching ``atoms``. Atoms at their most abundant isotope are
        absent, so an ordinary geometry has an empty tuple.
    """

    natoms: int
    canonical_xyz_text: str
    atoms: tuple[tuple[str, float, float, float], ...]
    isotopes: tuple[tuple[int, int], ...] = ()

    @property
    def hash_text(self) -> str:
        """Return the canonical text that identifies this geometry.

        Isotopic substitution changes the physics the geometry stands for
        (masses, and therefore frequencies, rotational constants and ZPE)
        without moving a single nucleus, so two deposits with identical
        coordinates but different labelling are *different* geometries and
        must not dedupe onto one another.

        The isotope suffix is appended only when a substitution is present,
        which keeps ``geom_hash`` byte-for-byte identical for every geometry
        already stored — none of which carries isotope data.
        """

        if not self.isotopes:
            return self.canonical_xyz_text
        suffix = ",".join(
            f"{atom_index}:{mass_number}" for atom_index, mass_number in self.isotopes
        )
        return f"{self.canonical_xyz_text}\nISOTOPES {suffix}"

    def isotope_substitutions(self) -> dict[tuple[str, int], int]:
        """Count non-standard isotope substitutions by ``(element, mass_number)``.

        :returns: Mapping used to cross-check the geometry against the
            isotope labels declared in the species-entry SMILES.
        """

        counts: dict[tuple[str, int], int] = {}
        for atom_index, mass_number in self.isotopes:
            element = self.atoms[atom_index - 1][0].capitalize()
            key = (element, mass_number)
            counts[key] = counts.get(key, 0) + 1
        return counts


def parse_xyz(payload: GeometryPayload) -> ParsedXYZ:
    """Parse and canonicalize an uploaded XYZ payload.

    :param payload: Upload-facing geometry payload.
    :returns: Parsed XYZ representation with canonicalized coordinate text.
    :raises ValueError: If the XYZ text is malformed or internally inconsistent.
    """

    lines = [line.rstrip() for line in payload.xyz_text.strip().splitlines()]
    if len(lines) < 3:
        raise ValueError("geometry.xyz_text must contain an XYZ header and atom lines")

    try:
        natoms = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError(
            "geometry.xyz_text first line must be an integer atom count"
        ) from exc

    atom_lines = lines[2:]
    if len(atom_lines) != natoms:
        raise ValueError(
            "geometry.xyz_text atom count does not match the number of atom lines"
        )

    atoms: list[tuple[str, float, float, float]] = []
    for line in atom_lines:
        parts = line.split()
        if len(parts) != 4:
            raise ValueError("Each XYZ atom line must contain element x y z")
        element = parts[0]
        try:
            x = float(parts[1])
            y = float(parts[2])
            z = float(parts[3])
        except ValueError as exc:
            raise ValueError("XYZ coordinates must be numeric") from exc
        atoms.append((element, x, y, z))

    canonical_lines = [str(natoms), ""]
    for element, x, y, z in atoms:
        canonical_lines.append(f"{element} {x:.12f} {y:.12f} {z:.12f}")

    isotopes: list[tuple[int, int]] = []
    for atom_index, mass_number in sorted((payload.isotopes or {}).items()):
        if not 1 <= atom_index <= natoms:
            raise ValueError(
                f"geometry.isotopes atom index {atom_index} is outside "
                f"1..{natoms} for this geometry"
            )
        element = atoms[atom_index - 1][0]
        validate_isotope(
            element,
            mass_number,
            context=f"geometry.isotopes[{atom_index}]",
        )
        # An explicitly stated standard isotope carries no information and is
        # dropped, so `{1: 1}` on a hydrogen can never fork an identity away
        # from an unlabelled deposit of the same molecule.
        if normalize_isotope(element, mass_number) is not None:
            isotopes.append((atom_index, mass_number))

    return ParsedXYZ(
        natoms=natoms,
        canonical_xyz_text="\n".join(canonical_lines),
        atoms=tuple(atoms),
        isotopes=tuple(isotopes),
    )
