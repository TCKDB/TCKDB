from __future__ import annotations

from dataclasses import dataclass

from app.chemistry.isotopes import (
    HYDROGEN_ISOTOPE_SYMBOLS,
    normalize_isotope,
    validate_isotope,
)
from app.schemas.fragments.geometry import GeometryPayload


def normalize_element_symbol(symbol: str) -> str:
    """Return an element symbol in the one case every comparison agrees on.

    Electronic-structure codes are not consistent about capitalisation: ``Cl``,
    ``CL`` and ``cl`` are one element written three ways. Comparing those
    strings raw refuses correct chemistry over a capital letter, which ADR 0008
    disqualifies a blocking check from doing.

    ``str.capitalize`` is the rule: it upper-cases the first character and
    lower-cases the rest, which is exactly the wire schema's
    ``symbol[:1].upper() + symbol[1:].lower()`` for every element symbol, and
    the same rule :mod:`app.chemistry.isotopes` already applies before handing
    a symbol to RDKit's periodic table.

    Where this is applied — in two places, on purpose
    -------------------------------------------------
    **At ingestion**, since Alembic revision ``b4e7c1d20f83``.
    :func:`parse_xyz` runs every parsed symbol through this function before it
    becomes a ``geometry_atom.element`` row, and that revision brought the rows
    written before it into the same form. That is what makes the column one
    spelling per element in the ordinary case.

    **And still at comparison time**, everywhere it already was. Ingestion
    canonicalisation is a convention held by this module, not an invariant the
    schema enforces: no CHECK constraint requires
    ``geometry_atom.element`` to be canonical, so a restore from an older
    backup, a bulk import, or any future write path that does not call
    :func:`parse_xyz` can put ``CL`` back. Anything **blocking** has to be
    correct on rows the running process never wrote, so it normalises both
    sides rather than trusting the convention — see the element-conservation
    check in :mod:`app.services.reaction_atom_map`. On canonical input that is
    a no-op, so the correctness costs nothing.

    It is required outright wherever a symbol arrives from *outside* that
    column and has to be lined up against it: RDKit's title-case
    ``GetSymbol()``, a raw XYZ string that has not been through
    :func:`parse_xyz`, the wire schema's
    :func:`tckdb_schemas.fragments.reaction_atom_map.parse_xyz_elements`. Those
    sides are canonicalised by their own rules, or not at all, and this
    function is what makes the two rules one rule.

    Note what it does **not** do: it does not touch ``geometry.xyz_text``. That
    column keeps the symbol the depositor's file wrote, and :func:`parse_xyz`
    explains why.
    """

    return symbol.strip().capitalize()


def resolve_element_symbol(symbol: str) -> str:
    """Return the *element* an XYZ symbol names, not the nuclide it names.

    :func:`normalize_element_symbol` settles capitalisation. This settles the
    other way an XYZ can spell an element that a comparison must not trip over:
    ``D`` and ``T`` are hydrogen. Both are legal, common tokens — Gaussian,
    ORCA, Molpro and CFOUR all emit or accept them — and ingestion
    canonicalisation deliberately leaves them alone, so
    ``geometry_atom.element`` stores them as ``D`` and ``T``, and a check that
    compares raw symbols reads a perfectly ordinary deuterated geometry as
    containing an element its SMILES never mentions. That refuses correct
    chemistry, which ADR 0008 disqualifies a blocking check from doing.

    Use this wherever elements are **counted or matched** — composition checks,
    graph-isomorphism element groups. Do not use it where the deposited symbol
    itself is the subject (round-tripping ``geometry_atom.element``, rendering
    an XYZ back to a depositor): ``D`` is what they wrote and what they should
    read back.

    Isotope *identity* is unaffected: it is carried atom-resolved by
    ``geometry.isotopes`` and by SMILES isotope notation, and compared by
    :func:`app.services.species_resolution.assert_geometry_isotopes_match_identity`.
    Writing ``D`` in the element column is therefore composition-neutral and
    isotope-silent, exactly as it was before any composition check existed.

    :param symbol: Element symbol as deposited.
    :returns: The normalised symbol of the element, with ``D`` and ``T``
        resolved to ``H``.
    """

    normalized = normalize_element_symbol(symbol)
    return "H" if normalized in HYDROGEN_ISOTOPE_SYMBOLS else normalized


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

        The element is read straight out of :attr:`atoms` of *this* object,
        which only :func:`parse_xyz` constructs and which it canonicalises on
        the way in — so unlike a symbol read back out of ``geometry_atom``,
        this one is canonical by construction and the key lines up with the
        title-case symbols RDKit produces for the SMILES side without a second
        normalisation step.

        :returns: Mapping used to cross-check the geometry against the
            isotope labels declared in the species-entry SMILES.
        """

        counts: dict[tuple[str, int], int] = {}
        for atom_index, mass_number in self.isotopes:
            element = self.atoms[atom_index - 1][0]
            key = (element, mass_number)
            counts[key] = counts.get(key, 0) + 1
        return counts


def parse_xyz(payload: GeometryPayload) -> ParsedXYZ:
    """Parse and canonicalize an uploaded XYZ payload.

    Two products, two different rules
    ---------------------------------
    This function produces two things from one set of parsed atom lines, and
    they deliberately do **not** spell the element the same way.

    ``atoms`` becomes the ``geometry_atom`` rows. Element symbols there are
    canonicalised through :func:`normalize_element_symbol`, so a file that
    writes ``CL`` and a file that writes ``Cl`` both store ``Cl``. That column
    is the *parsed index* the database computes on: it is the target of
    ``reaction_atom_map_pair``'s two composite foreign keys and the
    ``character(2)`` value every element comparison in the service layer reads,
    and one spelling per element is what makes those reads say what they mean.

    This is a *convention*, not an invariant: nothing in the schema requires the
    column to be canonical, so the comparisons still normalise both sides and
    stay correct on rows that arrived some other way. Canonicalising here makes
    the common case clean; it does not license anything downstream to assume it.

    ``canonical_xyz_text`` becomes ``geometry.xyz_text`` and, hashed, becomes
    ``geom_hash``. Element symbols there are left **exactly as deposited**. Two
    reasons, in order of weight:

    * ``geom_hash`` is a public ref. :func:`app.services.public_refs` mints
      ``geometry:geom_hash=<hash>`` as a citable identifier, and it is also the
      dedupe key in :mod:`app.services.geometry_resolution`. Canonicalising the
      symbol inside the hashed text would re-key every geometry already stored
      whose XYZ shouted an element: published refs would dangle, and
      re-uploading the very same file would fail to dedupe onto its own row.
      This is the same constraint that keeps the isotope suffix off
      :attr:`ParsedXYZ.hash_text` for unlabelled geometries.
    * ``xyz_text`` is the deposited evidence. It is the block a depositor reads
      back, and the symbol is the one part of an atom line this function does
      not already reformat. ``D`` is what they wrote and what they should read
      back, for the reason :func:`resolve_element_symbol` gives; the same is
      true of ``CL``.

    So ``geometry.xyz_text`` may read ``CL`` while ``geometry_atom.element``
    reads ``Cl`` for the same atom. That is not drift: one is the record of what
    was deposited, the other is the index the database joins and compares on,
    and only the second one has to be canonical for the first one to stay
    citable.

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
    #: The element token exactly as the file wrote it, kept alongside the
    #: canonicalised one so ``canonical_xyz_text`` — and therefore
    #: ``geom_hash`` — is byte-for-byte what it was before this function
    #: canonicalised anything. See the docstring.
    deposited_symbols: list[str] = []
    for line in atom_lines:
        parts = line.split()
        if len(parts) != 4:
            raise ValueError("Each XYZ atom line must contain element x y z")
        deposited = parts[0]
        # `normalize_element_symbol`, not `resolve_element_symbol`: `D` and `T`
        # must stay `D` and `T` in the stored column. Collapsing them to `H`
        # here would destroy deposited isotope labelling, which is a fact about
        # the deposit and not a spelling of one.
        element = normalize_element_symbol(deposited)
        try:
            x = float(parts[1])
            y = float(parts[2])
            z = float(parts[3])
        except ValueError as exc:
            raise ValueError("XYZ coordinates must be numeric") from exc
        deposited_symbols.append(deposited)
        atoms.append((element, x, y, z))

    canonical_lines = [str(natoms), ""]
    for deposited, (_element, x, y, z) in zip(deposited_symbols, atoms, strict=True):
        canonical_lines.append(f"{deposited} {x:.12f} {y:.12f} {z:.12f}")

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
