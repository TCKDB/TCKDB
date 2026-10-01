"""Identity key of the free-text level-of-theory components (issue #602).

``dispersion``, ``solvent`` and ``solvent_model`` are names, and every program
reads them case-insensitively (``EmpiricalDispersion=GD3BJ`` and ``gd3bj``,
``SCRF=(SMD,Solvent=Water)`` and ``water``, ORCA ``! D3BJ`` and ``d3bj``).
ARC lower-cases them (``arc/level.py``, ``Level.lower``), so a stored ``D3BJ``
and an uploaded ``d3bj`` were two levels of theory, as were ``Water`` and
``water``.

:func:`component_identity_key` is the string each is hashed as: stripped and
lower-cased, and a blank value has no key (``None``). The verbatim spelling is
still what the row stores and what reads show.

This is a case rule only. Solvent synonyms (``h2o`` for ``water``) and
dispersion synonyms (``d3(bj)`` for ``d3bj``) would need a curated table with
citations, as method names have (``method_names.py``); they are out of scope
for #602.

``keywords`` is the one hashed text field that is not keyed. It is free-form
route or input text, not a name: quoted strings inside it (file names, custom
parameter blocks) can be case-sensitive, so a case rule could merge two
different levels of theory. It stays hashed as written.

The Alembic revision that re-keys existing rows (``d0a7c3b91e4f``) carries a
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
