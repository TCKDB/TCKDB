"""Level-of-theory method and basis filters, compared by identity key (#585).

``level_of_theory`` stores the method, basis, dispersion and solvent names
verbatim, and its ``lot_hash`` is taken over their identity keys
(:func:`~app.chemistry.method_names.method_identity_key`,
:func:`~app.chemistry.basis_set_names.basis_identity_key`,
:func:`~app.chemistry.lot_component_names.component_identity_key`, #602). A search that
compared the stored spelling instead answered a question the database no
longer asks: ``?method=wb97xd&basis=def2-tzvp`` found nothing against a row
stored as ``wb97xd`` / ``def2tzvp``, though identity treats both as one level.

These helpers build the predicate every ``method=`` / ``basis=`` filter uses.
The requested value is keyed in Python; the stored column is keyed in SQL by
the same rules, so a row matches exactly when its identity key equals the
request's. The stored spelling is never rewritten, and reads still show it.

There is no index on the key expression: ``level_of_theory`` is one row per
distinct level (thousands at the most), and the filter is always applied
beside far more selective predicates.
"""

from __future__ import annotations

from sqlalchemy import and_, case, false, func, literal, or_
from sqlalchemy.sql.elements import ColumnElement

from app.chemistry.basis_set_names import HYPHEN_RULES, basis_identity_key
from app.chemistry.dispersion_names import (
    DISPERSION_RULES,
    FOLDED_PATTERN,
    dispersion_identity_key,
    level_identity_keys,
)
from app.chemistry.lot_component_names import component_identity_key
from app.chemistry.method_names import NAME_ALIASES, SUFFIX_RULES
from app.db.models.level_of_theory import LevelOfTheory

#: Edge whitespace, stripped in SQL to mirror ``str.strip`` in Python.
#:
#: Known divergences between the SQL key and the Python key. No real method
#: or basis name is affected, but they are not all harmless misses: a divergence
#: can also match spuriously. A stored name with a capital I with a dot above
#: is keyed in SQL to a plain ``i`` (so it matches a request spelled with ``i``),
#: and a stored double capital sigma matches the request of two small sigmas,
#: where Python's key would treat them as different.
#:
#: * Whitespace. ``LevelOfTheoryRef`` strips values on the wire, so every row
#:   written through the API has none at its edges and the two agree. Rows that
#:   bypass it (archive restore, ``seed_scientific_demo_data.py``) may not, and
#:   Python's ``strip`` removes some characters (NBSP, U+2003, U+3000, U+0085,
#:   U+001C..U+001F) that Postgres ``\s`` may not.
#: * Case. ``lower`` in Postgres follows the database's ctype (the Pi reports
#:   ``server_encoding`` UTF8); Python's follows Unicode. They differ on a few
#:   characters, such as ``I`` with a dot above and Greek final sigma.
_EDGE_WHITESPACE = r"^\s+|\s+$"


def _stripped_lower(column: ColumnElement) -> ColumnElement[str]:
    return func.lower(func.regexp_replace(column, _EDGE_WHITESPACE, "", "g"))


def _name_aliased(key: ColumnElement[str]) -> ColumnElement[str]:
    """Whole-name aliases (#618), decided on the stripped, lower-cased name."""
    return case(
        *[(key == alias.alias, alias.canonical) for alias in NAME_ALIASES],
        else_=key,
    )


def method_key_sql(column: ColumnElement) -> ColumnElement[str]:
    """SQL twin of :func:`method_identity_key` over a stored method column.

    The curated aliases (#618) come from the same tables the Python key reads
    (``NAME_ALIASES`` and ``SUFFIX_RULES``), so the two cannot list different
    aliases. This is the method *alone*: the key a level of theory is hashed
    under also moves a folded dispersion out (#630), see
    :func:`level_keys_sql`.
    """
    key = _stripped_lower(column)
    for pattern, replacement in SUFFIX_RULES:
        key = func.regexp_replace(key, pattern, replacement)
    # A whole-name alias is decided on the stripped name; no suffix rule
    # touches a name alias's spelling, so applying the suffix rules first
    # cannot change which one matches.
    return _name_aliased(key)


def component_key_sql(column: ColumnElement) -> ColumnElement[str | None]:
    """SQL twin of :func:`component_identity_key` over a stored text column.

    ``NULLIF`` makes a blank name key to ``NULL``, as the Python key does.
    """
    return func.nullif(_stripped_lower(column), "")


def dispersion_key_sql(column: ColumnElement) -> ColumnElement[str | None]:
    """SQL twin of :func:`dispersion_identity_key` over a stored dispersion column (#630).

    Built from the same ``DISPERSION_RULES`` the Python key uses.
    """
    key = component_key_sql(column)
    for pattern, replacement in DISPERSION_RULES:
        key = func.regexp_replace(key, pattern, replacement)
    return key


def level_keys_sql(
    method_column: ColumnElement, dispersion_column: ColumnElement
) -> tuple[ColumnElement[str], ColumnElement[str | None]]:
    """SQL twin of :func:`level_identity_keys`: ``(method key, dispersion key)`` (#630).

    A recognised dispersion folded into the method moves into the dispersion
    key, under the same pattern (``FOLDED_PATTERN``) and the same condition
    as the Python key: the column is blank or states that same dispersion.
    """
    method = method_key_sql(method_column)
    dispersion = dispersion_key_sql(dispersion_column)
    folds = and_(
        method.op("~")(FOLDED_PATTERN),
        or_(
            dispersion.is_(None),
            dispersion == func.regexp_replace(method, FOLDED_PATTERN, "\\2"),
        ),
    )
    stem = _name_aliased(func.regexp_replace(method, FOLDED_PATTERN, "\\1"))
    suffix = func.regexp_replace(method, FOLDED_PATTERN, "\\2")
    return case((folds, stem), else_=method), case((folds, suffix), else_=dispersion)


def basis_key_sql(column: ColumnElement) -> ColumnElement[str | None]:
    """SQL twin of :func:`basis_identity_key` over a stored basis column.

    Built from the same ``HYPHEN_RULES`` patterns the Python key uses, so the
    two cannot list different rules. ``NULLIF`` makes a blank name key to
    ``NULL``, as ``basis_identity_key`` returns ``None`` for one.
    """
    key = _stripped_lower(column)
    for pattern, replacement, _family in HYPHEN_RULES:
        key = func.regexp_replace(key, pattern.pattern, replacement, "g")
    return func.nullif(key, "")


def method_matches(value: str) -> ColumnElement[bool]:
    """``LevelOfTheory.method`` names the same method as ``value``, up to case and aliases.

    The comparison is on the level's identity key (#630): a stored
    ``b3lyp-d3bj`` has method key ``b3lyp``, as ``b3lyp`` with
    ``dispersion=d3bj`` does. A request that folds a dispersion in
    (``b3lyp-d3bj``) also requires the level to have it.
    """
    want_method, want_dispersion = level_identity_keys(value, None)
    method, dispersion = level_keys_sql(LevelOfTheory.method, LevelOfTheory.dispersion)
    condition = method == literal(want_method)
    if want_dispersion is not None:
        condition = and_(condition, dispersion == literal(want_dispersion))
    return condition


def basis_matches(value: str) -> ColumnElement[bool]:
    """``LevelOfTheory.basis`` names the same basis set as ``value``, by key.

    A blank ``value`` has no key and matches nothing, as it matched nothing
    when the comparison was on the spelling.
    """
    key = basis_identity_key(value)
    if key is None:
        return false()
    return basis_key_sql(LevelOfTheory.basis) == literal(key)


def _component_matches(column: ColumnElement, value: str) -> ColumnElement[bool]:
    key = component_identity_key(value)
    if key is None:
        return false()
    return component_key_sql(column) == literal(key)


def dispersion_matches(value: str) -> ColumnElement[bool]:
    """``LevelOfTheory`` has the same dispersion as ``value``, by identity key (#602, #630).

    Includes a dispersion folded into the method (``b3lyp-d3bj`` has ``d3bj``).
    """
    key = dispersion_identity_key(value)
    if key is None:
        return false()
    _method, dispersion = level_keys_sql(LevelOfTheory.method, LevelOfTheory.dispersion)
    return dispersion == literal(key)


def solvent_matches(value: str) -> ColumnElement[bool]:
    """``LevelOfTheory.solvent`` names the same solvent as ``value``, up to case (#602)."""
    return _component_matches(LevelOfTheory.solvent, value)


__all__ = [
    "basis_key_sql",
    "basis_matches",
    "component_key_sql",
    "dispersion_key_sql",
    "dispersion_matches",
    "level_keys_sql",
    "method_key_sql",
    "method_matches",
    "solvent_matches",
]
