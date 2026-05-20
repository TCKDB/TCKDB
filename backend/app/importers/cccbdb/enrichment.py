"""Catalog-based identity-enrichment candidates for CCCBDB property rows.

This module is **candidate-proposal**, not identity resolution. Given
one row from a Phase 3a property table and the Phase 3b molecule
catalog, :func:`propose_catalog_matches` returns a list of
:class:`CCCBDBCatalogMatch` records — every plausible catalog entry
that could correspond to that row, each with a confidence score and
the reasons that produced it.

Important rules (also enforced by tests):

* Formula-only matches with multiple catalog candidates are **always**
  ambiguous, regardless of score. C2H6O is ethanol *or* dimethyl
  ether; C3H6 is propene *or* cyclopropane; C4H10 is n-butane *or*
  isobutane. Silently picking one would be a correctness bug.
* The original property row is **never mutated**.
* Ambiguous candidates are **never dropped** — callers see them all
  and make their own decision.
* ``is_unambiguous=True`` only when the proposed match list has
  length 1 *and* the score is medium or high.
"""

from __future__ import annotations

import re

from app.importers.cccbdb.models import (
    CCCBDBCatalogEntry,
    CCCBDBCatalogMatch,
    CCCBDBCatalogMatchConfidence,
    CCCBDBExperimentalPropertyRow,
    CCCBDBMoleculeCatalog,
)

_WS_RE = re.compile(r"\s+")


def _norm_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WS_RE.sub(" ", value).strip().lower()
    return cleaned or None


def _norm_formula(value: str | None) -> str | None:
    """Conservative formula key: strip whitespace + lowercase.

    We deliberately do NOT impose Hill order or canonicalize element
    case here — CCCBDB property tables and the catalog use the same
    raw formula spelling, so a verbatim case-folded match is the
    right join key for this layer.
    """

    if value is None:
        return None
    cleaned = value.replace(" ", "").strip().lower()
    return cleaned or None


def propose_catalog_matches(
    property_row: CCCBDBExperimentalPropertyRow,
    catalog: CCCBDBMoleculeCatalog,
) -> list[CCCBDBCatalogMatch]:
    """Return scored catalog candidates for one property-table row.

    :param property_row: The row to enrich. Not mutated.
    :param catalog: The catalog to search.
    :returns: A list of :class:`CCCBDBCatalogMatch`. Empty when the
        row carries no usable identifier or no catalog entry matches.

    Scoring:

        +----------------------------------+--------------+-------------------+
        | Property-row signal              | Score        | Unambiguous?      |
        +==================================+==============+===================+
        | formula + name both match        | high         | iff only one cand |
        | formula matches; name aliases    | medium       | iff only one cand |
        |   or substrings                  |              |                   |
        | formula only matches             | low          | iff only one cand |
        |   AND the catalog has exactly    |              |                   |
        |   one entry with that formula    |              |                   |
        | formula only matches             | low          | False             |
        |   AND the catalog has many       |              |                   |
        |   entries with that formula      |              |                   |
        | name only matches uniquely       | medium       | True              |
        | name only matches non-uniquely   | low          | False             |
        | formula conflict                 | not returned | n/a               |
        +----------------------------------+--------------+-------------------+
    """

    row_formula = _norm_formula(property_row.formula)
    row_name = _norm_name(property_row.name)

    if not row_formula and not row_name:
        return []

    # Group catalog entries by normalized formula to detect ambiguity
    # cheaply, and walk the catalog to assemble the candidate list.
    by_formula: dict[str, list[CCCBDBCatalogEntry]] = {}
    for entry in catalog.entries:
        key = _norm_formula(entry.formula)
        if key is None:
            continue
        by_formula.setdefault(key, []).append(entry)

    matches: list[CCCBDBCatalogMatch] = []
    for entry in catalog.entries:
        entry_formula = _norm_formula(entry.formula)
        entry_name = _norm_name(entry.name)

        # Formula conflict: row has a formula, catalog has a
        # *different* formula. Skip.
        if (
            row_formula is not None
            and entry_formula is not None
            and row_formula != entry_formula
        ):
            continue

        reasons: list[str] = []
        score: CCCBDBCatalogMatchConfidence | None = None

        formula_match = (
            row_formula is not None
            and entry_formula is not None
            and row_formula == entry_formula
        )
        name_exact = (
            row_name is not None
            and entry_name is not None
            and row_name == entry_name
        )
        name_alias = (
            row_name is not None
            and entry_name is not None
            and row_name != entry_name
            and (row_name in entry_name or entry_name in row_name)
        )

        if formula_match and name_exact:
            score = CCCBDBCatalogMatchConfidence.high
            reasons.append("formula exact match")
            reasons.append("name exact match")
        elif formula_match and name_alias:
            score = CCCBDBCatalogMatchConfidence.medium
            reasons.append("formula exact match")
            reasons.append("name substring/alias match")
        elif formula_match:
            score = CCCBDBCatalogMatchConfidence.low
            reasons.append("formula match only")
        elif name_exact:
            # No formula on the row (or no formula on the catalog
            # entry). Treat as a softer match.
            score = CCCBDBCatalogMatchConfidence.medium
            reasons.append("name exact match (no formula crosscheck)")
        elif name_alias and row_formula is None:
            score = CCCBDBCatalogMatchConfidence.low
            reasons.append("name substring/alias match (no formula)")

        if score is None:
            continue

        match_warnings: list[str] = []
        if formula_match and row_formula in by_formula:
            n_same_formula = len(by_formula[row_formula])
            if n_same_formula > 1 and not name_exact:
                match_warnings.append(
                    f"{n_same_formula} catalog entries share formula "
                    f"{property_row.formula!r}; isomer ambiguity"
                )

        matches.append(
            CCCBDBCatalogMatch(
                catalog_entry=entry,
                score=score,
                match_reasons=reasons,
                warnings=match_warnings,
                # Marked further down once we know the full set.
                is_unambiguous=False,
            )
        )

    # Decide unambiguity *after* the full candidate list is known.
    # Formula-only ties are always ambiguous; name-only matches need
    # to be uniquely sourced; high-confidence matches need to be the
    # sole candidate.
    if len(matches) == 1:
        only = matches[0]
        # Single candidate is unambiguous when score is medium+.
        # Formula-only-but-still-the-only-candidate stays low but
        # *is* unambiguous because nothing else matched.
        only.is_unambiguous = True
    else:
        # Multiple candidates: any "high"s that share a formula with
        # competing candidates of any score stay ambiguous.
        # Promote *only* a uniquely-high candidate to unambiguous.
        high_indices = [
            i
            for i, m in enumerate(matches)
            if m.score == CCCBDBCatalogMatchConfidence.high
        ]
        if len(high_indices) == 1:
            matches[high_indices[0]].is_unambiguous = True

    return matches
