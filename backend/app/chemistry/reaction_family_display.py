"""Human-readable display names for RMG reaction-family identifiers.

``reaction_family`` stores 125 RMG identifiers verbatim — ``H_Abstraction``,
``R_Addition_MultipleBond``, ``Surface_Adsorption_Bidentate``. RMG publishes
no expanded names for them, so a readable name has to be *derived* here
rather than looked up somewhere authoritative.

The derivation is deliberately two layers plus one refusal:

1. **Mechanical.** Split the identifier on ``_`` and ``-``, split camelCase,
   join with single spaces. ``R_Addition_MultipleBond`` becomes
   "R Addition Multiple Bond". No chemistry knowledge is applied, so this
   layer cannot be wrong.
2. **Token expansion.** :data:`TOKEN_EXPANSIONS` maps a small set of
   confirmed abbreviations to words, turning "H Abstraction" into
   "Hydrogen Abstraction". **A token with no entry is left exactly as the
   mechanical layer produced it.** The table may hold one entry or seventy
   and nothing is ever wrong — it only gets better as entries land. No
   release waits on completing a chemistry vocabulary, and nobody has to
   guess an entry to fill a gap.
3. **Refusal.** A family that contains a token nobody could resolve
   (:data:`UNRESOLVED_TOKENS`), or that is ambiguous as a whole
   (:data:`UNRESOLVED_FAMILIES`), is returned as its raw identifier,
   untouched. A half-translated name reads as authoritative when it is not:
   "Surface Carbonate 2F Decomposition" looks like a human name while
   silently carrying an untranslated chemistry token, whereas
   ``Surface_Carbonate_2F_Decomposition`` is visibly a machine identifier
   and a reader knows at a glance that no translation was given. Either it
   is a human name or it is an identifier; the hybrid is neither.

.. warning:: **Two scoping traps. Read before adding an entry.**

   **RMG's atom-type table describes group definitions, not family names,
   and the same token means different things in the two contexts.** Do not
   import that table wholesale.

   ==========  ================================  ==============================
   token       as an RMG atom type               in a family name
   ==========  ================================  ==============================
   ``R``       *any* atom — a wildcard           a **radical**
                                                 (``Intra_R_Add_Endocyclic``)
   ``CO``      carbon double-bonded to oxygen    **carbon monoxide**, the
                                                 molecule (``1,2_Insertion_CO``)
   ==========  ================================  ==============================

   Only ``Val6`` and ``Cd`` were checked to be coherent in family-name
   scope; they are the only two entries here taken from that table.

   **A token's meaning can also be scoped to a single family, not just to a
   context — and a token-level table cannot express that.** ``F`` is
   unambiguously fluorine in ``F_Abstraction``, whose siblings are
   ``Br_Abstraction``, ``Cl_Abstraction``, ``H_Abstraction`` and
   ``Li_Abstraction``. The same ``F`` in
   ``Surface_Carbonate_F_CO_Decomposition`` has three plausible readings
   (fluorine, Faradays/electrons, or free surface sites) and is unresolved.
   That is why :data:`UNRESOLVED_FAMILIES` exists alongside
   :data:`UNRESOLVED_TOKENS`: the ambiguity there is a property of the
   family, not of the token everywhere. ``R``, ``CO`` and ``F`` are three
   instances of this same shape — expect a fourth.

Nothing here is stored. The display name is derived at read time so it
improves the moment a token lands in the table below, with no migration, no
125-row backfill, and no chance of a stored copy drifting from the code that
derives it.
"""

from __future__ import annotations

import re

# Word separators in an RMG family identifier. Both become a single space.
_SEPARATORS = re.compile(r"[_\-]+")

# camelCase boundary: an uppercase letter that both follows a lowercase
# letter and starts a lowercase word. Deliberately narrower than the usual
# camel split, which would mangle chemistry: ``vdW`` -> "vd W",
# ``LiR`` -> "Li R", ``COm`` -> "CO m". Under this rule those stay intact
# while ``MultipleBond`` -> "Multiple Bond" and ``PeroxyRadical`` ->
# "Peroxy Radical" still split.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z][a-z])")

# A comma-separated locant run: ``1,2``, ``1,3``, ``1,4``. Deliberately does
# NOT match ``2+2`` or ``1+2`` — those are cycloaddition component counts, a
# different reading, and "positions 2+2" would be wrong.
_LOCANTS = re.compile(r"^\d+(?:,\d+)+$")

#: Confirmed token expansions. An absent token falls through unchanged.
#: Every entry here is a chemistry claim that a chemist confirmed — never add
#: one to fill a gap. Leaving a token alone is always safe; guessing is not.
TOKEN_EXPANSIONS: dict[str, str] = {
    # Confirmed by the project owner (a chemist), in family-name scope.
    "H": "Hydrogen",
    "R": "Radical",  # a radical HERE; a wildcard in RMG's atom-type table
    "Birad": "Biradical",
    "HO2": "hydroperoxyl",
    # From RMG's published atom-type table; individually checked to be
    # coherent in family-name scope. The rest of that table is not.
    "Val6": "atom with six valence electrons",
    "Cd": "carbon with one double bond",
    # Comma-separated locants (``1,2``, ``1,3``, ``1,4``) are left BARE, not
    # prefixed with "positions". The notation is already unambiguous to the
    # audience -- ``1,3_sigmatropic_rearrangement`` is how a chemist writes a
    # [1,3] shift -- so a prefix explains something nobody needed explained and
    # makes every such name longer. _LOCANTS still exists to keep them out of
    # any other rule, and to hold the line against ``2+2``, which counts
    # cycloaddition components rather than positions and must never read as
    # one. The separator is the discriminator: comma is a locant, plus is a
    # count.
    # Element symbols, settled by their siblings: F/Br/Cl/Li each appear as
    # the abstracted species in an ``<X>_Abstraction`` family sitting directly
    # beside ``H_Abstraction``. NOTE the coupling: ``F`` is only safe here
    # because ``Surface_Carbonate_F_CO_Decomposition``, where ``F`` is
    # unresolved, is excluded by family below. Removing that exclusion would
    # silently assert fluorine there.
    "F": "Fluorine",
    "Br": "Bromine",
    "Cl": "Chlorine",
    "Li": "Lithium",
}

#: Tokens nobody could resolve. A family containing one is returned as its raw
#: identifier rather than half-translated.
#:
#: This is a *named list*, not "any token missing from
#: :data:`TOKEN_EXPANSIONS`" — most tokens (``Abstraction``, ``Surface``,
#: ``Dissociation``) are ordinary English and read correctly with no entry at
#: all. If "unknown" meant "unmapped", adding a legitimate expansion would
#: silently change which families get translated, and the rule would become
#: accidental.
#:
#: - ``COm`` / ``CSm`` — the trailing ``m`` is unexplained.
#: - ``2F`` — fluorine, Faradays/electrons, or free surface sites; the sibling
#:   surface families count them (``F``, ``2F``) like the
#:   ``Single``/``Double``/``Bidentate`` site-counting pattern, while that same
#:   set contains real electrochemistry
#:   (``Surface_Proton_Electron_Reduction_*``).
#: - ``ExoTetCyclic`` — probably Baldwin's rules (exo attack at a tetrahedral
#:   carbon), consistent with the sibling ``Exocyclic``/``Endocyclic``
#:   families, but unconfirmed.
#:
#: Matched against separator-split tokens, before the camelCase split — so
#: ``ExoTetCyclic`` is matched whole rather than as "Exo Tet Cyclic".
UNRESOLVED_TOKENS: frozenset[str] = frozenset(
    {
        "COm",
        "CSm",
        "2F",
        "ExoTetCyclic",
    }
)

#: Families that are ambiguous as a whole, listed by name because the
#: ambiguity belongs to the family rather than to any token everywhere.
#:
#: ``Surface_Carbonate_F_CO_Decomposition`` carries the unresolved reading of
#: ``F`` (see :data:`UNRESOLVED_TOKENS`) even though bare ``F`` is settled as
#: fluorine in ``F_Abstraction``. A token-level table cannot say "fluorine
#: there, unknown here"; this can.
UNRESOLVED_FAMILIES: frozenset[str] = frozenset(
    {
        "Surface_Carbonate_F_CO_Decomposition",
    }
)


def _separator_tokens(identifier: str) -> list[str]:
    """Split on ``_`` and ``-`` only — no camelCase split yet."""
    return [token for token in _SEPARATORS.split(identifier) if token]


def _expand(token: str) -> str:
    """Apply the confirmed expansions; otherwise return the token unchanged."""
    expansion = TOKEN_EXPANSIONS.get(token)
    if expansion is not None:
        return expansion
    if _LOCANTS.match(token):
        # Bare, deliberately. See the note beside _LOCANTS.
        return token
    return token


def is_unresolved_reaction_family(name: str) -> bool:
    """Whether ``name`` must be shown as its raw identifier.

    True when the family is listed in :data:`UNRESOLVED_FAMILIES`, or when any
    of its separator-split tokens is in :data:`UNRESOLVED_TOKENS`.
    """
    identifier = name.strip()
    if identifier in UNRESOLVED_FAMILIES:
        return True
    return any(token in UNRESOLVED_TOKENS for token in _separator_tokens(identifier))


def reaction_family_display_name(name: str) -> str:
    """Return a readable display name for an RMG reaction-family identifier.

    ``H_Abstraction`` -> "Hydrogen Abstraction";
    ``Surface_Adsorption_Bidentate`` -> "Surface Adsorption Bidentate";
    ``Surface_Carbonate_2F_Decomposition`` -> unchanged, because it carries a
    token nobody could resolve.

    :param name: the stored family identifier.
    :raises ValueError: if ``name`` is blank.
    """
    identifier = name.strip()
    if not identifier:
        raise ValueError("reaction family name must not be blank.")

    if is_unresolved_reaction_family(identifier):
        return identifier

    words: list[str] = []
    for token in _separator_tokens(identifier):
        for part in _CAMEL_BOUNDARY.split(token):
            if part:
                words.append(_expand(part))

    display = " ".join(words)
    if display[:1].islower():
        display = display[0].upper() + display[1:]
    return display
