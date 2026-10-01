"""Identity keys of the dispersion column, and of a dispersion folded into the method (issue #630).

#627 made the *case* of the dispersion column part of identity and joined
D3(BJ) spellings inside the **method** string. Two splits were left:

* the dispersion **column** kept ``d3bj``, ``gd3bj`` and
  ``empiricaldispersion=gd3bj`` apart, though ARC's Gaussian adapter writes
  ``level.dispersion`` into the route unchanged (``arc/job/adapters/gaussian.py``)
  and ARC's own matcher treats all three as one; and
* ``b3lyp-d3bj`` (dispersion folded into the method) was a different level of
  theory from ``b3lyp`` with ``dispersion=d3bj``.

Dispersion column aliases
-------------------------
:data:`DISPERSION_ALIASES` is a curated table, each entry with its citations,
under the same rules as the method table (``method_names.py``): an entry is
admitted only if every program checked means the same correction by the
spelling. Scope is the same as there: the hash cannot see a program.

The table, with the canonical spelling ARC and ORCA write:

==================  ===========  =========================================
spelling            key          source of the claim
==================  ===========  =========================================
``gd3bj``           ``d3bj``     Gaussian EmpiricalDispersion=GD3BJ
``d3(bj)``          ``d3bj``     Psi4 dftd3 table, ``-d3bj`` row
``gd3``             ``d3zero``   Gaussian EmpiricalDispersion=GD3
``gd2``             ``d2``       Gaussian EmpiricalDispersion=GD2
``d30``             ``d3zero``   ORCA 6.1 Table 3.13 (``D30`` = ``D3ZERO``)
==================  ===========  =========================================

Not in the table, because no citation was found: ``d3(0)`` and ``d3bj2b``
(Psi4 row spellings), the folded ``-gd3``, ``-gd2`` and ``-d3(0)``, and the
Gaussian route abbreviation ``em=``. Leaving them split is recoverable (the
merge script joins them later once an entry is added with a revision).

Gaussian writes the dispersion as a route option, and ARC stores the whole
option. Each alias is therefore also recognised wrapped as
``EmpiricalDispersion=X``, ``EmpiricalDispersion=(X)`` and
``EmpiricalDispersion(X)``, in any case and with spaces around the tokens. A
wrapped value whose inside is *not* in the table (``empiricaldispersion=pfd``)
is left as written: only what the table recognises is joined.

**Bare ``d3`` stays its own key.** ORCA's ``D3`` is BJ-damped while Psi4's
``-d3`` is zero-damped, so neither ``d3bj`` nor ``d3zero`` can claim it. (ARC's
own matcher folds ``gd3`` into ``d3``; that is a divergence from this key, on
purpose: ``gd3`` is zero damping and is keyed so.) ``pfd`` (Petersson-Frisch),
``d4``, ``d3mbj`` and the rest are not in the table.

Folded dispersion
-----------------
``b3lyp-d3bj`` and ``b3lyp`` + ``d3bj`` are the same calculation in every
program checked (Psi4 ``b3lyp-d3bj``; ORCA ``! B3LYP D3BJ``; Gaussian
``b3lyp EmpiricalDispersion=GD3BJ``), and TCKDB's own rule that the hash holds
one identity per calculation says they are one level. The key therefore moves
a recognised trailing dispersion **off the method and into the dispersion
key**: ``b3lyp-d3bj`` keys as ``("b3lyp", "d3bj")``, which is exactly what
``("b3lyp", "d3bj")`` keys as, so rows already stored in column form keep
their hash.

It is conservative on three counts, because a false merge cannot be undone
(the merge script is the only way back, and it is one-way):

* only the suffixes :data:`FOLDED_SUFFIXES` (``d3bj``, ``d3zero``, ``d2``,
  after the method key has applied its own ``-d3(bj)`` and ``-gd3bj``
  aliases) are split;
* only off a stem in :data:`FOLDED_STEMS`: a functional that is published
  *without* a dispersion-specific refit, so that the dispersion really is an
  add-on (``b2plyp`` is on the list: B2PLYP-D3BJ is standard, Grimme 2011).
  ``wb97x-d3bj``, ``wb97m-d3bj``, ``b97-d3bj`` and the refit double hybrids
  (``dsd-*``, ``pwpb95``) are separate functionals with their own
  parametrisation and stay folded in
  their method key, as does everything else not listed (a split is
  recoverable by adding a stem, with a revision; a false merge is not); and
* if the column also states a dispersion and it is a different one
  (``b3lyp-d3bj`` with ``dispersion=d3zero``), the level contradicts itself and
  nothing is split.

Some stem and dispersion combinations have no parameter set in some programs:
M06-2X with D3BJ is absent in Psi4, ORCA offers M06 with zero damping only and
Gaussian refuses it; HF with D3ZERO is BJ-only in ORCA; Psi4 has no D2 for
``hf``, ``m06-2x``, ``b3pw91``, ``cam-b3lyp`` or ``bhlyp``. Both spellings of
such a combination still name the same (nonexistent) calculation, so identity
is unaffected: the key joins spellings, it does not assert the combination runs.

The SQL twin used by the search filters is built from the rules here
(``scientific_read/lot_identity_filters.py``). The Alembic revision that
re-keys existing rows (``f3b8d5a1c702``) carries a frozen copy of these
rules, and a test holds the two to each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.chemistry.lot_component_names import component_identity_key
from app.chemistry.method_names import method_identity_key


@dataclass(frozen=True)
class DispersionAlias:
    """One curated dispersion spelling and the key it is hashed as.

    :param alias: The lower-cased spelling.
    :param canonical: The key.
    :param programs: ``None``: true for every program checked. A scoped entry
        is refused (the hash cannot see a program).
    :param citations: Manual sections or papers that justify it.
    """

    alias: str
    canonical: str
    programs: tuple[str, ...] | None
    citations: tuple[str, ...]


_GRIMME_D3 = "S. Grimme, J. Antony, S. Ehrlich, H. Krieg, J. Chem. Phys. 132, 154104 (2010): D3."
_GRIMME_BJ = "S. Grimme, S. Ehrlich, L. Goerigk, J. Comput. Chem. 32, 1456 (2011): Becke-Johnson damping."

DISPERSION_ALIASES: tuple[DispersionAlias, ...] = (
    DispersionAlias(
        alias="gd3bj",
        canonical="d3bj",
        programs=None,
        citations=(
            "Gaussian 16 manual, EmpiricalDispersion=GD3BJ: Grimme's D3 with "
            "Becke-Johnson damping.",
            "ORCA 6.1 manual 3.4 (Dispersion Corrections): keyword D3BJ.",
            "Psi4 manual, DFTD3 interface: '-D3BJ'.",
            _GRIMME_BJ,
        ),
    ),
    DispersionAlias(
        alias="d3(bj)",
        canonical="d3bj",
        programs=None,
        citations=(
            "Psi4 manual, DFTD3 interface: '-D3BJ2B, -D3BJ, -D3(BJ)' are one row, "
            "D3 with Becke-Johnson rational damping.",
            "ORCA 6.1 manual 3.4: keyword D3BJ.",
            "Gaussian 16 manual, EmpiricalDispersion=GD3BJ.",
            _GRIMME_BJ,
        ),
    ),
    DispersionAlias(
        alias="gd3",
        canonical="d3zero",
        programs=None,
        citations=(
            "Gaussian 16 manual, EmpiricalDispersion=GD3: Grimme's D3 version "
            "(zero damping; the BJ variant is the separate GD3BJ).",
            "ORCA 6.1 manual 3.4: keyword D3ZERO is D3 with zero damping.",
            "Psi4 manual, DFTD3 interface: '-D3ZERO' is D3 with zero damping.",
            _GRIMME_D3,
        ),
    ),
    DispersionAlias(
        alias="gd2",
        canonical="d2",
        programs=None,
        citations=(
            "Gaussian 16 manual, EmpiricalDispersion=GD2: Grimme's D2 version.",
            "ORCA 6.1 manual 3.4: keyword D2; Psi4 manual, DFTD3 interface: '-D2'.",
            "S. Grimme, J. Comput. Chem. 27, 1787 (2006): D2.",
        ),
    ),
    DispersionAlias(
        alias="d30",
        canonical="d3zero",
        programs=None,
        citations=(
            "ORCA 6.1 manual, Table 3.13 (simple input keywords for the DFT-D "
            "corrections): 'D30 activates D3 correction with zero damping, "
            "equivalent to D3ZERO'.",
            "Psi4 manual, DFTD3 interface: '-D3ZERO' is D3 with zero damping.",
            _GRIMME_D3,
        ),
    ),
)

#: One ``(pattern, replacement)`` per alias, read by the Python key and its
#: SQL twin so they cannot list different aliases. The pattern accepts the
#: bare spelling and the three Gaussian route spellings, over the stripped,
#: lower-cased value. No canonical spelling is itself an alias, so the rules
#: are order-independent and the key is idempotent. ``.`` is never used, so
#: Python and Postgres agree on newlines.
DISPERSION_RULES: tuple[tuple[str, str], ...] = tuple(
    (
        "^(?:"
        + re.escape(a.alias)
        + r"|empiricaldispersion\s*(?:=\s*"
        + re.escape(a.alias)
        + r"|=\s*\(\s*"
        + re.escape(a.alias)
        + r"\s*\)|\(\s*"
        + re.escape(a.alias)
        + r"\s*\)))$",
        a.canonical,
    )
    for a in DISPERSION_ALIASES
)
_DISPERSION_COMPILED = tuple((re.compile(p), r) for p, r in DISPERSION_RULES)

#: Dispersion keys a method may carry as a trailing ``-<key>``.
FOLDED_SUFFIXES: tuple[str, ...] = ("d3bj", "d3zero", "d2")

#: Functionals published without a dispersion-specific refit. Spelled as the
#: method key spells them; ``m06-2x`` and ``m062x`` are both listed because the
#: method key only aliases a whole name.
#:
#: Citations: the D3 parameter tables of Grimme et al. (2010, 2011) are
#: published for these functionals unchanged; Psi4's dftd3 interface accepts
#: ``<functional>-d3bj`` / ``-d3zero`` / ``-d2`` for them and ORCA reads
#: ``! <functional> D3BJ`` as the same two keywords. Deliberately absent:
#: ``wb97x``/``wb97m`` (``-d3bj`` is a refit functional), ``b97`` (B97-D is a
#: refit), ``dsd-*``/``pwpb95`` (double hybrids with refit), and ``ub3lyp``
#: or ``pbepbe`` (spellings the method table does not equate to these).
FOLDED_STEMS: tuple[str, ...] = (
    "b3lyp",
    "cam-b3lyp",
    "pbe",
    "pbe0",
    "tpss",
    "tpss0",
    "bp86",
    "blyp",
    "b2plyp",
    "revpbe",
    "b3pw91",
    "bhlyp",
    "hf",
    "m06-2x",
    "m062x",
)

#: ``(stem)-(suffix)`` over a method key. Group 1 is the stem, 2 the suffix.
FOLDED_PATTERN: str = (
    "^("
    + "|".join(re.escape(s) for s in sorted(FOLDED_STEMS, key=len, reverse=True))
    + ")-("
    + "|".join(re.escape(s) for s in FOLDED_SUFFIXES)
    + ")$"
)
_FOLDED_COMPILED = re.compile(FOLDED_PATTERN)


def dispersion_identity_key(name: str | None) -> str | None:
    """Return the identity key of the dispersion column.

    The case rule of :func:`component_identity_key`, then the curated aliases
    (:data:`DISPERSION_ALIASES`) including their Gaussian route spellings.

    :param name: The dispersion as a producer wrote it, or ``None``.
    :returns: The key; ``None`` for ``None`` or a blank name. Idempotent.
    """

    key = component_identity_key(name)
    if key is None:
        return None
    for pattern, replacement in _DISPERSION_COMPILED:
        key = pattern.sub(replacement, key)
    return key


def level_identity_keys(method: str, dispersion: str | None) -> tuple[str, str | None]:
    """Return the ``(method, dispersion)`` identity keys of a level of theory.

    A recognised dispersion folded into the method moves into the dispersion
    key (see the module docstring for what is recognised and why).

    :param method: The method as written.
    :param dispersion: The dispersion column as written, or ``None``.
    :returns: The pair the hash is taken over. Idempotent on its own output.
    """

    method_key = method_identity_key(method)
    dispersion_key = dispersion_identity_key(dispersion)
    hit = _FOLDED_COMPILED.match(method_key)
    if hit is not None and dispersion_key in (None, hit.group(2)):
        return method_identity_key(hit.group(1)), hit.group(2)
    return method_key, dispersion_key


def method_matches_level(
    stored_method: str, stored_dispersion: str | None, requested_method: str
) -> bool:
    """Whether a stored level answers a ``method=`` request, in memory.

    Python twin of ``lot_identity_filters.method_matches``: the request is
    keyed alone, and a dispersion it folds in (``b3lyp-d3bj``) must also be
    the stored level's.
    """

    want_method, want_dispersion = level_identity_keys(requested_method, None)
    have_method, have_dispersion = level_identity_keys(stored_method, stored_dispersion)
    return have_method == want_method and (
        want_dispersion is None or have_dispersion == want_dispersion
    )
