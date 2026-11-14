"""Identity key of the free-text level-of-theory components.

``dispersion``, ``solvent`` and ``solvent_model`` are names, and every program
reads them case-insensitively (``EmpiricalDispersion=GD3BJ`` and ``gd3bj``,
``SCRF=(SMD,Solvent=Water)`` and ``water``, ORCA ``! D3BJ`` and ``d3bj``).
A producer that lower-cases them (ARC does: ``arc/level.py``, ``Level.lower``)
uploads ``d3bj`` where another deposit stored ``D3BJ``, so they were two
levels of theory, as were ``Water`` and
``water``.

:func:`component_identity_key` is the string each is hashed as: stripped and
lower-cased, and a blank value has no key (``None``). The verbatim spelling is
still what the row stores and what reads show.

This is a case rule only. Solvent synonyms (``h2o`` for ``water``) would need
a curated table with citations, as method names have (``method_names.py``);
they are out of scope. Dispersion synonyms are not: the dispersion column is
hashed through ``dispersion_names.dispersion_identity_key``, which
applies this case rule first and then a cited alias table.

A level of theory carries no free-text ``keywords`` field: the settings a deck or log states are calculation
parameters, and the method qualifiers that change the level are typed (``calculation_resolution``).

The Alembic revision that re-keys existing rows carries a
frozen copy of this rule, and a test holds the two in agreement.
"""

from __future__ import annotations


def component_identity_key(name: str | None) -> str | None:
    """Return the identity key of a dispersion, solvent or solvent-model name.

    :param name: The name as a producer wrote it, or ``None``.
    :returns: The name stripped and lower-cased; ``None`` for ``None`` or a
        blank name. Idempotent.
    """

    if name is None:
        return None
    key = name.strip().lower()
    return key or None
