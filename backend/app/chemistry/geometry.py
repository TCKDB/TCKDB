from __future__ import annotations

from dataclasses import dataclass

from app.api.error_contract import CodedValueError
from app.chemistry.isotopes import (
    HYDROGEN_ISOTOPE_SYMBOLS,
    normalize_isotope,
    validate_isotope,
)
from app.schemas.fragments.geometry import GeometryPayload

#: Raised when a geometry's ``isotopes`` entry contradicts the nuclide its own
#: element spelling names: ``D`` with mass number 3, ``T`` with 2, ``D`` with 1.
#: An input-format refusal ("D is 2H" is a definition), so it is in the code
#: catalogue and not in the scientific check register; see ``docs/adr/0022``.
W_GEOMETRY_ISOTOPE_SYMBOL_CONFLICT = "geometry_isotope_symbol_conflict"


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

    **But no longer at comparison time on that column.** Ingestion
    canonicalisation used to be a convention held by this module rather than an
    invariant the schema enforced, so anything **blocking** normalised both
    sides rather than trusting it: a restore from an older backup, a bulk
    import, or a write path that did not call :func:`parse_xyz` could put
    ``CL`` back, and a blocking check has to be correct on rows the running
    process never wrote. ``ck_geometry_atom_element_canonical``
    (``c5a1f8e3d074``) makes it an invariant, and a CHECK — unlike a foreign
    key or a trigger — is not suspended by ``session_replication_role =
    replica``, so it holds on precisely those paths. The element-conservation
    check in :mod:`app.services.reaction_atom_map` therefore compares
    ``geometry_atom.element`` values as stored.

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
    other spelling a comparison must not trip over: ``D`` and ``T`` are
    hydrogen. They are also nuclides -- ``D`` is deuterium (mass number 2) and
    ``T`` is tritium (mass number 3), the only meaning either symbol has in
    chemistry (IUPAC Red Book IR-3.3.2) -- and that half of the meaning is
    carried elsewhere (decided 2026-10-03, ``docs/adr/0022``): a ``D``/``T``
    element token is read as hydrogen **plus** an implied mass number by
    :func:`parse_xyz`, so every *new* ``geometry_atom`` row holds ``H`` and an
    ``isotope_mass_number``.

    This function is therefore the answer to "which element is it", and only
    that. It stays in the codebase for two jobs:

    * counting and matching elements on symbols that did not come through
      :func:`parse_xyz` -- an RDKit symbol, a raw XYZ string, a wire payload;
    * reading the ``geometry_atom`` rows deposited **before** that decision,
      which store ``D``/``T`` in the element column with a NULL mass number.
      Nothing may rewrite them (``trg_as_geometry_atom`` forbids it), so a
      check that counts elements still resolves them to ``H`` rather than
      reading an element its SMILES never mentions. Their isotope meaning is
      read from the symbol at read time: see
      :func:`app.chemistry.isotopes.implied_isotope_mass_number`.

    Do not use it where the deposited symbol itself is the subject
    (round-tripping ``geometry.xyz_text``): ``D`` is what the depositor wrote
    and what they should read back.

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
        absent, so an ordinary geometry has an empty tuple. An atom spelled
        ``D`` or ``T`` is present with its implied mass number (2 or 3), exactly
        as if ``geometry.isotopes`` had said so; its ``atoms`` entry is ``H``.
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

        A ``D`` or ``T`` spelling counts as a substitution (decided
        2026-10-03, ``docs/adr/0022``): its implied mass number is in
        :attr:`isotopes` and so in this suffix, exactly as an explicit
        ``geometry.isotopes`` entry would be. A redundant explicit entry that
        equals the implied one adds nothing, so ``D`` alone and ``D`` with a
        redundant entry hash identically; ``D`` and ``H`` plus ``isotopes`` give
        two rows. A geometry that spells ``D`` and was stored
        before that decision keeps its old hash and its old row (no suffix, a
        ``D``/NULL atom); a new deposit of the same file hashes differently and
        gets its own correctly indexed row rather than deduping onto it.
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

    This is an *invariant* rather than a convention, and only since
    ``c5a1f8e3d074``: ``ck_geometry_atom_element_canonical`` requires the
    column to hold exactly what this function produces. Canonicalising here is
    what keeps the ingestion path inside that constraint; the constraint is
    what lets a reader compare the column without normalising first.

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
      back; the same is true of ``CL``.

    ``D`` and ``T`` are the one place the parsed index differs from case alone.
    An element token ``D`` or ``T`` declares a nuclide (deuterium, tritium), so
    the parsed atom is ``H`` with an implied mass number of 2 or 3: ``atoms``
    holds ``H`` (``geometry_atom.element`` stays an element, which is what
    ``reaction_atom_map``'s pair constraint compares), :attr:`ParsedXYZ.isotopes`
    holds the mass number (so the isotope identity check and ``hash_text`` see
    it), and ``xyz_text`` still reads ``D``. An explicit ``geometry.isotopes``
    entry for that atom must equal the implied number, and is then redundant;
    anything else contradicts the spelling and is refused with
    ``geometry_isotope_symbol_conflict``. See ``docs/adr/0022``.

    So ``geometry.xyz_text`` may read ``CL`` while ``geometry_atom.element``
    reads ``Cl`` for the same atom. That is not drift: one is the record of what
    was deposited, the other is the index the database joins and compares on,
    and only the second one has to be canonical for the first one to stay
    citable.

    :param payload: Upload-facing geometry payload.
    :returns: Parsed XYZ representation with canonicalized coordinate text.
    :raises ValueError: If the XYZ text is malformed or internally inconsistent.
    :raises CodedValueError: ``geometry_isotope_symbol_conflict`` when an
        explicit isotope contradicts a ``D``/``T`` element spelling.
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
    #: 1-based atom index -> mass number implied by a ``D``/``T`` spelling.
    implied_isotopes: dict[int, int] = {}
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
        element = normalize_element_symbol(deposited)
        # `D`/`T` name a nuclide, not an element: store the element (`H`, which
        # is what `ck_reaction_atom_map_pair_element_matches` compares) and
        # carry the nuclide as the implied mass number. `xyz_text` keeps `D`.
        if element in HYDROGEN_ISOTOPE_SYMBOLS:
            implied_isotopes[len(atoms) + 1] = HYDROGEN_ISOTOPE_SYMBOLS[element]
            element = "H"
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

    isotope_by_index: dict[int, int] = dict(implied_isotopes)
    for atom_index, mass_number in sorted((payload.isotopes or {}).items()):
        if not 1 <= atom_index <= natoms:
            raise ValueError(
                f"geometry.isotopes atom index {atom_index} is outside "
                f"1..{natoms} for this geometry"
            )
        element = atoms[atom_index - 1][0]
        implied = implied_isotopes.get(atom_index)
        if implied is not None and mass_number != implied:
            spelling = deposited_symbols[atom_index - 1]
            raise CodedValueError(
                W_GEOMETRY_ISOTOPE_SYMBOL_CONFLICT,
                f"isotopes[{atom_index}]={mass_number} contradicts the spelling "
                f"{spelling!r} (mass {implied}). Write H with isotope {mass_number} "
                "or drop the conflicting entry.",
                context={
                    "atom_index": atom_index,
                    "element_symbol": spelling,
                    "implied_mass_number": implied,
                    "declared_mass_number": mass_number,
                },
                message_prefix=False,
            )
        validate_isotope(
            element,
            mass_number,
            context=f"geometry.isotopes[{atom_index}]",
        )
        # An explicitly stated standard isotope carries no information and is
        # dropped, so `{1: 1}` on a hydrogen can never fork an identity away
        # from an unlabelled deposit of the same molecule. (An explicit entry
        # equal to a `D`/`T` implied one is the same dict key, so it is the
        # implied entry, once.)
        if normalize_isotope(element, mass_number) is not None:
            isotope_by_index[atom_index] = mass_number

    return ParsedXYZ(
        natoms=natoms,
        canonical_xyz_text="\n".join(canonical_lines),
        atoms=tuple(atoms),
        isotopes=tuple(sorted(isotope_by_index.items())),
    )

