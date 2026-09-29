"""The identity key of a level-of-theory method name (issue #585).

A method name is written in whatever case a producer prefers. ARC lower-cases
every method it emits (``arc/level.py``, ``Level.lower``); Gaussian, ORCA and
Molpro inputs are written in mixed or upper case:

====================  =======================  =======================
method                ARC                      Gaussian / ORCA / Molpro
====================  =======================  =======================
DLPNO-CCSD(T)-F12     ``dlpno-ccsd(t)-f12``    ``DLPNO-CCSD(T)-F12``
CCSD(T)-F12a          ``ccsd(t)-f12a``         ``CCSD(T)-F12a``
B3LYP                 ``b3lyp``                ``B3LYP``
====================  =======================  =======================

``level_of_theory.lot_hash`` hashed the method as written, so an ARC upload
of ``ccsd(t)-f12/cc-pvtz-f12`` and a stored ``CCSD(T)-F12/cc-pVTZ-F12`` were
two levels of theory.

:func:`method_identity_key` is the string the hash is taken over. The verbatim
name is still what the row stores and what every read shows.

The one rule, and why it is safe
--------------------------------
The key is the name with surrounding whitespace removed, lower-cased (as the
basis key is, and as Postgres's ``lower`` does over the stored column).
Every program above reads method names case-insensitively, and no two
methods differ only by case: ``F12a`` and ``F12b`` are different
approximations, but they differ by a letter, not by its case.

What is deliberately not normalised
-----------------------------------
Punctuation. ``wb97xd`` (Gaussian ``wB97XD``, ARC) and ``wB97X-D`` (ORCA)
usually name the same functional, but which functional a spelling means is
program-dependent, so equating them takes an alias table, not a spelling
rule. The same goes for ``HF`` / ``RHF``, and ``UB3LYP`` / ``B3LYP`` (the
spin treatment lives in its own column). Each pair keeps two keys: a split is
recoverable, because the merge script joins it later; a false merge is not.

The SQL twin used by the search filters
(``scientific_read/lot_identity_filters.py``) agrees with this key on every
real method name; its documented divergences are exotic Unicode and edge
whitespace that the wire schema already removes.

This module is pure. The Alembic revision that re-keys existing rows
(``c8424fe82997``) carries a frozen copy of this rule, and a test holds the
two in agreement.
"""

from __future__ import annotations


def method_identity_key(name: str) -> str:
    """Return the identity key of a method name.

    The key is only for hashing and comparison. It is never stored in place
    of the verbatim name and never shown.

    :param name: A method name as a producer wrote it.
    :returns: The name, stripped and lower-cased. Idempotent.
    """

    return name.strip().lower()
