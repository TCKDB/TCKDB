"""The identity key of a basis-set name (issue #574).

Electronic-structure programs spell one basis set in different ways. The
group knowledge base's "Basis set spelling" card
(``ess/capabilities.md``) records the cases met in practice:

==============  ============  ============  ============  ==============  ============
basis           Gaussian      ORCA          Q-Chem        Psi4 / PySCF    Molpro
==============  ============  ============  ============  ==============  ============
def2-TZVP       ``Def2TZVP``  ``def2-TZVP`` ``def2-TZVP`` ``def2-tzvp``   ``def2-TZVP``
cc-pVTZ         ``cc-pVTZ``   ``cc-pVTZ``   ``cc-pVTZ``   ``cc-pvtz``     ``cc-pVTZ``
aug-cc-pVTZ     ``aug-cc-pVTZ`` (all five, up to case)
==============  ============  ============  ============  ==============  ============

``level_of_theory.lot_hash`` hashed the basis byte-for-byte, so Psi4's
``def2-tzvp`` and ARC/Gaussian's ``def2tzvp`` became two levels of theory.

:func:`basis_identity_key` returns the string the hash is taken over. The
verbatim name is still what the row stores and what every read shows; only
the identity key is normalised.

The rules, and why each is safe
-------------------------------
The key only equates two spellings when no two real basis sets differ by
that spelling. There are exactly two rules, and :data:`HYPHEN_RULES` is
the whole of the second:

1. **Case.** Every program listed above reads basis names
   case-insensitively, and no two basis sets in the Basis Set Exchange
   differ only by case. ``cc-pVTZ`` and ``cc-pvtz`` are one basis.
2. **One hyphen, at two named places.** The hyphen after the family token
   ``def2`` (Karlsruhe/Weigend) and after ``cc`` in ``cc-p`` (Dunning's
   correlation-consistent family) separates a family name from a member
   name. No basis set exists whose name differs from a member of these
   families only by that hyphen, and Gaussian (``Def2TZVP``) and PySCF's
   own keys (``ccpvtz``) drop it. The key restores it, so the key is the
   lower-cased Basis Set Exchange name: ``def2-tzvp``, ``cc-pvtz``.

   The family token must start the name or follow a hyphen, so
   ``aug-cc-pVTZ`` and ``ma-def2-TZVP`` keep their prefixes and still gain
   the family hyphen, and nothing inside another word is rewritten.

What is deliberately not normalised
-----------------------------------
Every other character is kept, because it distinguishes real basis sets:

* ``*``, ``**``, ``+``, ``++`` and parenthesised polarisation --
  ``6-31G*`` / ``6-31G**`` / ``6-31+G`` / ``6-31G(d)`` / ``6-31G(d,p)``
  are five basis sets.
* Diffuse and calendar prefixes -- ``aug-``, ``d-aug-``, ``jun-``,
  ``may-``, ``apr-``, ``heavy-aug-``, ``ma-``. The hyphen after the prefix
  is kept too, so PySCF's ``augccpvtz`` stays apart from
  ``aug-cc-pvtz``: a false split is recoverable, a false merge is not.
* Suffixes -- ``-F12``, ``-PP``, ``-DK``, ``-X2C``, ``(-f)``, ``/C``,
  ``/J``, ``/JK``, ``-RI``, ``-JKFIT``, ``-MP2FIT``.
* Program shorthands -- Molpro's ``vtz`` for ``cc-pVTZ`` and ``avtz`` for
  ``aug-cc-pVTZ``. Equating them needs an alias table, not a spelling
  rule, and none exists here.
* Internal whitespace. Leading and trailing whitespace is already trimmed
  by the wire schema.

This module is pure. The Alembic revision that re-keys existing rows
(``38b06819f099``) carries a frozen copy of these rules, and a test holds
the two in agreement.
"""

from __future__ import annotations

import re

#: ``(pattern, replacement, family)`` applied in order after lower-casing.
#: Every pattern inserts a hyphen that the family's canonical name carries
#: and some program drops. The lookbehind keeps each rule to a family token
#: at the start of the name or after a hyphen (a prefix such as ``aug-``).
HYPHEN_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    # Karlsruhe def2 family: Gaussian "Def2TZVP", everyone else "def2-TZVP".
    (re.compile(r"(?<![^-])def2(?=[a-z])"), "def2-", "def2"),
    # Dunning correlation-consistent family: cc-pVnZ, cc-pCVnZ, cc-pwCVnZ.
    # PySCF's internal key is "ccpvtz"; every program's input spelling is
    # "cc-pVTZ".
    (re.compile(r"(?<![^-])ccp(?=(?:w?c)?v)"), "cc-p", "Dunning cc-p"),
)


def basis_identity_key(name: str | None) -> str | None:
    """Return the identity key of a basis-set name, or ``None`` for none.

    The key is only for hashing and comparison. It is never stored in place
    of the verbatim name and never shown.

    :param name: A basis-set name as a producer wrote it.
    :returns: The lower-cased name with the family hyphens in
        :data:`HYPHEN_RULES` restored. Idempotent.
    """

    if name is None:
        return None
    key = name.strip().lower()
    if not key:
        return None
    for pattern, replacement, _family in HYPHEN_RULES:
        key = pattern.sub(replacement, key)
    return key
