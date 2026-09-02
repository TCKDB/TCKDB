"""Service implementation for /api/v1/scientific/species/search.

See docs/specs/read_api_mvp.md §Endpoint 1 for the contract.

Where the work happens
----------------------
A species record is ranked by its *best visible entry*, so review
visibility and ranking are properties of the ``species_entry`` set, not of
``species``. Both are expressed in SQL (see
:mod:`app.services.scientific_read.sql_review`) over one candidate-species
subquery, and only the page is materialized: the per-entry badges,
availability counts and ``include=`` section id lists are fetched for the
species on the page, never for every match.

The previous shape loaded every matching species, every one of their
entries, and a review badge for each, before it could sort or slice. That
is not just wasted work at catalog scale — the entry and badge loads render
one bind parameter per id, and PostgreSQL's wire protocol caps a statement
at 65,535 parameters, so a broad enough identifier match returned ``503
database_unavailable`` instead of a page. ``backend/docs/benchmarks/README.md``
has the measurement.
"""

from __future__ import annotations

import re
from typing import Any

from rdkit import Chem, RDLogger
from sqlalchemy import Text, and_, case, func, not_, or_, select, text
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError, reject_unsupported_filters
from app.db.models.calculation import Calculation
from app.db.models.common import (
    SubmissionRecordType,
)
from app.db.models.species import (
    ConformerGroup,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport
from app.schemas.reads._field_bounds import (
    MAX_ELEMENT_SYMBOLS as _MAX_ELEMENT_SYMBOLS,
)
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_species import (
    ElementMatchMode,
    RequestEcho,
    ScientificSpeciesBrowseResponse,
    ScientificSpeciesSearchResponse,
    SpeciesBrowseRequest,
    SpeciesEntryAvailability,
    SpeciesEntryScientificRecord,
    SpeciesEntrySectionIds,
    SpeciesFilterRequest,
    SpeciesScientificRecord,
    SpeciesSearchRequest,
)
from app.schemas.reads.scientific_structure_search import (
    DEFAULT_SIMILARITY_THRESHOLD,
    StructureQueryKind,
    StructureSearchMode,
)
from app.services.scientific_read.calculation_provenance_filters import (
    apply_calculation_provenance_filter,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.handles import (
    NO_MATCH,
    reconcile_species_entry_pair,
    reconcile_species_pair,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.species_identity import (
    species_entry_label_for,
)
from app.services.scientific_read.sql_review import (
    join_review,
    review_rank_expr,
    review_status_expr,
    summary_from_sql,
    visible_review_filter,
)
from app.services.scientific_read.structure_query import (
    enforce_mode_query_compatibility,
    inchi_key_from_query,
    parse_smarts,
    parse_smiles_to_canonical,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "thermo",
    "statmech",
    "transport",
    "conformers",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

#: /species/browse's own include vocabulary -- deliberately missing
#: ``thermo``/``statmech``/``transport``/``conformers``.
#:
#: Those four tokens gate a :class:`~app.schemas.reads.scientific_species.
#: SpeciesEntrySectionIds` block whose payload is a bare integer-id array
#: (``{"ids": [...]}``). ``is_internal_id_key()`` in
#: ``app/services/scientific_read/internal_ids.py`` only matches keys
#: ending in ``_id``/``_ids`` or on its literal deny-list, so a field
#: named plain ``ids`` sails through the Phase D strip untouched -- on
#: ``/species/search`` that is a pre-existing, unfixed gap (out of scope
#: here; a caller must already have an identifier to reach it), but on an
#: identifier-free, unauthenticated, whole-corpus listing the same gap
#: turns ``GET /species/browse?include=all&limit=200`` into a way to
#: harvest every thermo/statmech/transport/conformer-group primary key in
#: the archive anonymously, in one request. See
#: ``docs/specs/public_identifier_policy.md`` (sequential PKs rejected
#: because they "leak the total count of objects ... and roughly the
#: upload schedule") and ``internal_ids_visibility_policy.md`` (bare
#: integer-id arrays are meant to be hidden by default).
#:
#: Dropping the tokens rather than adding ``"ids"`` to the Phase D
#: deny-list: the deny-list is global, so widening it is a separate,
#: wider-blast-radius change this endpoint should not force. Availability
#: already tells a browse caller whether a section exists
#: (``SpeciesEntryAvailability.has_thermo`` etc., plus
#: ``calculation_count``); a caller who wants the actual thermo/statmech/
#: transport/conformer rows navigates by ``species_entry_ref``, which is
#: the handle this surface hands out, not by an id an anonymous listing
#: should never have handed them in the first place.
_BROWSE_LEGAL_INCLUDE_TOKENS: set[str] = {"review", "internal_ids", "all"}

_DEFAULT_SORT_ECHO = "review_rank,has_entries,created_at,id"

#: The rank a species with no *visible* entry sorts at. Deliberately worse
#: than every real review rank, so "has entries the caller may see" beats
#: "has none" without needing a second sort key ahead of it.
_NO_VISIBLE_ENTRY_RANK = max(REVIEW_RANK.values()) + 1


def _formula_expr():
    """The species' molecular formula, derived in SQL by the RDKit cartridge.

    ``species`` has no stored formula column. ``mol_formula()`` over
    ``mol_from_smiles(sp.smiles)`` yields Hill notation (e.g. ``H2O``,
    ``C3H6``), with a trailing charge suffix for ions (``HO-``, ``H4N+``,
    ``Fe+2``). Radicals carry no marker: ``[CH3]`` is ``CH3``. Isotopes are
    not distinguished — the cartridge's default ``mol_formula()`` ignores
    isotope labels, so heavy water reports as ``H2O``, the same as light
    water. ``mol_from_smiles()`` returns SQL NULL for any row whose SMILES
    fails to parse, so such a row yields a NULL formula rather than raising.

    **One expression, two uses, deliberately.** This is both the ``formula=``
    filter predicate and the ``formula`` field served on every record. A
    caller who searched by formula therefore reads back the very string that
    was matched, because it is the same expression over the same column — not
    a second formula implementation that happens to agree today. The
    functional index ``ix_species_formula_lookup`` is written over this
    expression too, which is what makes the filter cheap.

    Depending on the cartridge here costs nothing new: the base Alembic
    revision ``60b67e360daf`` runs ``CREATE EXTENSION IF NOT EXISTS rdkit``
    before any table exists, so a database this code can talk to has it.
    Deriving the string in Python instead would reach for the ``rdkit``
    wheel, which ``pyproject.toml`` declares as an opt-in ``[rdkit]`` extra.
    """
    return func.mol_formula(func.mol_from_smiles(Species.smiles)).cast(Text)


def _heavy_atom_count_expr():
    """The species' heavy (non-hydrogen) atom count, via the RDKit cartridge.

    ``mol_numheavyatoms(mol_from_smiles(sp.smiles))`` counts every atom of
    atomic number greater than 1 in the molecular graph RDKit parses from
    the identity SMILES. "Heavy atom" here means the conventional
    chemistry sense -- non-hydrogen -- and that holds regardless of *how*
    the SMILES spells its hydrogens:

    * Implicit hydrogens (``C`` for methane, ``[CH3]`` for the methyl
      radical) are never graph atoms at all, so they were never going to
      be counted.
    * Explicit hydrogens (``[H][H]`` for molecular hydrogen, ``[OH-]``
      for hydroxide) *are* graph atoms, but of atomic number 1, so
      ``mol_numheavyatoms`` excludes them the same as an implicit one --
      RDKit's underlying ``GetNumHeavyAtoms()`` counts by atomic number,
      not by which atoms the SMILES happened to spell out.
    * Isotope labels do not change the count: ``mol_numheavyatoms`` reads
      atomic number, not mass number, so ``[2H]O[2H]`` (heavy water)
      counts the same one heavy atom as ``O`` (light water) -- consistent
      with :func:`_formula_expr`, whose ``mol_formula()`` drops isotope
      labels for the same reason.

    Pinned against these exact shapes (bracket radical, explicit-H,
    charged, isotope-labelled) in
    ``tests/services/scientific_read/test_browse_species.py`` rather than
    assumed: RDKit's implicit/explicit-hydrogen bookkeeping is exactly
    the kind of thing that is "quietly ambiguous" until a real SMILES is
    run through it.

    Same source column, same NULL-on-unparseable behavior as
    :func:`_formula_expr` — an unparseable SMILES yields SQL NULL, which
    fails every ``<=``/``>=`` comparison and so drops the row from a
    ``max_heavy_atoms``/``min_heavy_atoms`` filter rather than raising.
    """
    return func.mol_numheavyatoms(func.mol_from_smiles(Species.smiles))


def _formula_has_element_expr(symbol: str):
    """Whether the species' Hill-notation formula names element ``symbol``.

    Built over :func:`_formula_expr` rather than a second, parallel
    derivation from the mol object — the same one-expression precedent
    that keeps ``formula=`` and the served ``formula`` field from
    disagreeing extends to composition: "does this species contain
    nitrogen" is answered from the identical string a caller reads back
    as ``formula``.

    Hill notation writes each element as exactly one token — an
    uppercase letter optionally followed by one lowercase letter (``H``,
    ``Cl``, ``Fe``) — with no separator between adjacent elements
    (``CH3``, not ``C H3``). A naive substring test (``formula LIKE
    '%N%'``) would match the ``n``-less token ``Mn`` has none of, and
    worse, would match a query for ``C`` against a formula that is only
    ``Cl``. The pattern ``symbol(?![a-z])`` (a negative lookahead,
    supported by PostgreSQL's ``~`` advanced-regex operator) rules that
    out: it requires the match not be followed by a lowercase letter, so
    ``C`` matches the ``C`` in ``CH3`` but not the ``C`` inside ``Cl2``.
    No lookbehind is needed on the other side — Hill tokens are never
    preceded by a lowercase letter belonging to a different element (each
    element appears at most once, and the trailing ionic charge suffix
    ``+``/``-`` and the digit counts are not letters), so there is no way
    for a two-letter symbol's second character to be mistaken for the
    start of the next token.

    :param symbol: An already-validated element symbol (see
        :func:`_validate_element_symbols`) — title case, e.g. ``"Cl"``.
    """
    pattern = re.escape(symbol) + "(?![a-z])"
    return _formula_expr().op("~")(pattern)


def _reject_unknown_element(raw: str) -> None:
    raise ValueError(
        f"unknown_element_symbol: {raw!r} supplied to elements= is "
        "not a recognised element symbol."
    )


def _validate_element_symbols(raw_symbols: list[str]) -> list[str]:
    """Resolve and validate caller-supplied element symbols.

    Case-insensitive on input (``"cl"``, ``"CL"`` and ``"Cl"`` all resolve
    to ``"Cl"``, matching the codebase's existing convention in
    :func:`app.chemistry.geometry.normalize_element_symbol`). An unknown
    symbol is refused loudly — 422 ``unknown_element_symbol`` — rather
    than silently matching nothing: a typo'd ``Xx`` must not read as "we
    hold nothing of that element" (an honest answer would be "you asked
    for something that is not an element").

    Two checks guard the lookup, not one, because RDKit's periodic table
    answers "is this a symbol" more loosely than the plain-English
    question implies:

    * ``symbol.isalpha()`` rejects anything that is not letters before
      RDKit ever sees it — in particular ``*``, RDKit's *dummy atom*
      wildcard. ``GetAtomicNumber("*")`` does not raise; it returns
      ``0``. Without this check ``elements=*`` would be accepted, match
      no Hill-notation token (a formula never contains ``*``), and come
      back ``200`` with an empty page — exactly the "typo reads as
      empty archive" failure this whole function exists to prevent, just
      reached through a symbol that ``.capitalize()`` cannot flag as
      malformed the way ``isalpha()`` can.
    * The atomic-number result is checked for ``0`` too, as defence in
      depth: ``isalpha()`` is what actually stops ``*`` (a non-letter),
      but a future non-letter dummy-atom spelling should not silently
      slip back in if that check is ever loosened.

    :class:`rdkit.RDLogger` is disabled around the lookup: an unknown
    symbol makes the RDKit C++ layer print an several-kilobyte
    "Post-condition Violation" stack trace to stderr on every call, and
    this is a public, unauthenticated endpoint — logging that once per
    rejected request is amplification an anonymous caller can trigger
    for free. The ``isalpha()`` pre-check already avoids the RDKit call
    (and therefore the trace) for most typos (``Xx``, ``*``, ``123``);
    disabling the logger covers the rest (a well-formed but unrecognised
    letters-only symbol, e.g. a two-letter combination that names no
    element).

    Deliberately does **not** resolve isotope tokens (``D``/``T``) to
    ``H`` the way
    :func:`app.chemistry.geometry.resolve_element_symbol` does for
    geometry composition counts. This filter answers an *elements*
    question, not an *isotopes* question — see :func:`_formula_expr`,
    whose ``mol_formula()`` already collapses every isotope of hydrogen
    into the same ``H`` token, so ``elements=H`` already matches a
    deuterated species (pinned in the test suite). RDKit's periodic
    table does not recognise ``D``/``T`` as element symbols at all, so
    they are refused here the same as any other unrecognised token —
    which is the correct refusal for a filter that cannot select for or
    against deuteration.

    :raises ValueError: 422 ``unknown_element_symbol`` for any symbol
        RDKit's periodic table does not recognise, or that is not a
        recognisable element symbol at all (``*`` and other non-letter
        tokens).
    """
    table = Chem.GetPeriodicTable()
    resolved: list[str] = []
    RDLogger.DisableLog("rdApp.*")
    try:
        for raw in raw_symbols:
            symbol = raw.strip().capitalize()
            if not symbol:
                continue
            if not symbol.isalpha():
                _reject_unknown_element(raw)
            try:
                atomic_number = table.GetAtomicNumber(symbol)
            except RuntimeError as exc:
                raise ValueError(
                    f"unknown_element_symbol: {raw!r} supplied to elements= "
                    "is not a recognised element symbol."
                ) from exc
            if atomic_number == 0:
                # Belt-and-suspenders for the dummy-atom case (see
                # docstring): isalpha() already stops "*" itself, this
                # guards any future non-letter dummy-atom spelling.
                _reject_unknown_element(raw)
            resolved.append(symbol)
    finally:
        RDLogger.EnableLog("rdApp.*")
    return resolved


def _parse_elements_filter(request: SpeciesBrowseRequest) -> list[str] | None:
    """Parse + validate ``request.elements`` into a symbol list, or ``None``.

    ``None`` means "no elements filter was supplied" (the field itself
    was absent). A supplied value that reduces to no symbols at all
    (``""``, ``","``, blank entries only) is treated the same as
    "not supplied" — there is nothing to filter on, and refusing an
    all-whitespace value would be a stricter contract than the rest of
    this endpoint's optional filters enforce elsewhere. Callers that need
    to tell "no filter" apart from "filter present but named nothing" —
    e.g. the request echo, which must not claim a filter that was never
    applied — check the return value against ``None``, not against
    ``request.elements``.

    :raises ValueError: 422 ``unknown_element_symbol``, propagated from
        :func:`_validate_element_symbols`; or 422
        ``too_many_element_symbols`` if the caller named more than
        :data:`app.schemas.reads._field_bounds.MAX_ELEMENT_SYMBOLS`
        distinct symbols.
    """
    if request.elements is None:
        return None
    raw_symbols = [s for s in (p.strip() for p in request.elements.split(",")) if s]
    if not raw_symbols:
        return None
    if len(raw_symbols) > _MAX_ELEMENT_SYMBOLS:
        raise CodedValueError(
            "too_many_element_symbols",
            f"elements= names {len(raw_symbols)} symbols; at most "
            f"{_MAX_ELEMENT_SYMBOLS} are accepted per request.",
            context={
                "max_elements": _MAX_ELEMENT_SYMBOLS,
                "elements_count": len(raw_symbols),
            },
        )
    return _validate_element_symbols(raw_symbols)


def _select_browse_structure_query(
    request: SpeciesBrowseRequest,
) -> tuple[StructureQueryKind, str] | None:
    """Pick the browse structure-filter query field, or ``None`` if absent.

    Narrower than ``structure_search.py``'s own ``_select_structure_query``:
    :class:`SpeciesBrowseRequest` exposes only ``query_smiles`` /
    ``query_smarts`` (see its docstring for why ``query_inchi`` /
    ``query_inchi_key`` are not browse fields), and supplying NEITHER is
    not an error here -- the structure filter is optional, unlike the
    standalone endpoint's ``missing_structure_query`` requirement.
    Supplying BOTH is still refused with the same
    ``multiple_structure_queries`` code the standalone endpoint uses for
    the identical ambiguity.
    """
    supplied: list[tuple[StructureQueryKind, str]] = []
    if request.query_smiles is not None:
        supplied.append((StructureQueryKind.smiles, request.query_smiles))
    if request.query_smarts is not None:
        supplied.append((StructureQueryKind.smarts, request.query_smarts))
    if not supplied:
        return None
    if len(supplied) > 1:
        names = sorted(k.value for k, _ in supplied)
        raise CodedValueError(
            "multiple_structure_queries",
            f"exactly one structure query field is allowed; got {names!r}.",
            context={"supplied": [f"query_{name}" for name in names]},
        )
    return supplied[0]


def _validate_browse_structure_query(
    request: SpeciesBrowseRequest,
) -> tuple[StructureQueryKind, str] | None:
    """Validate the browse structure filter up front, before any SQL runs.

    Mirrors the other up-front validators called from :func:`browse_species`
    (``_parse_elements_filter``, ``_validate_provenance_version_parents``):
    a 422 must surface before the candidate statement is built, not
    mid-query. Returns the selected ``(kind, value)`` pair (threaded into
    :func:`_browse_candidate_species_stmt` and the request echo) so
    validation happens exactly once.

    :raises ValueError/CodedValueError: 422 ``multiple_structure_queries``
        (both fields supplied), ``invalid_structure_query`` (mode does not
        accept the supplied field, or the SMARTS itself does not parse --
        checked eagerly here so a bad pattern 422s before any SQL runs;
        SMILES is parsed lazily in :func:`_apply_structure_filter`, same
        as ``elements``/``formula`` deferring their own SQL-facing work).
    """
    selected = _select_browse_structure_query(request)
    if selected is None:
        return None
    kind, value = selected
    enforce_mode_query_compatibility(request.mode, kind)
    if kind is StructureQueryKind.smarts:
        parse_smarts(value)
    return kind, value


def _apply_structure_filter(
    stmt,
    request: SpeciesBrowseRequest,
    structure_query: tuple[StructureQueryKind, str] | None,
):
    """Narrow browse candidates to species matching a structure query.

    Optional and additive -- a no-op when ``structure_query`` is ``None``,
    same as every other optional filter in this module -- and composes
    with every other browse filter in the SAME statement (AND-combined
    via ``.where()`` on ``stmt`` like ``charge``/``formula``/the
    provenance filters above), never a second query run separately and
    intersected in Python.

    Reuses the exact RDKit parsing (``structure_query.py``) and cartridge
    operators/functions (``@>``, ``tanimoto_sml``, ``morganbv_fp``,
    ``mol_from_smiles``, ``qmol_from_smarts``) that
    ``/scientific/species/structure-search`` (``structure_search.py``)
    uses -- expressed as SQLAlchemy here rather than raw parameterized SQL
    because this predicate has to compose inside an ORM ``select()``
    rather than drive its own standalone paginated response; see that
    module's docstring for what each operator means.

    ``exact`` mode needs no cartridge call at all: it matches the already-
    indexed ``species.inchi_key`` column directly, same as
    ``structure_search.py``'s own ``_run_exact_query``. ``substructure``
    and ``similarity`` match against ``species_entry.mol`` via a
    correlated ``EXISTS`` -- a species passes if *any* of its entries
    matches, the same "OR-across-entry" reading
    :func:`_apply_provenance_filters` already uses for calculations one
    level down.
    """
    if structure_query is None:
        return stmt
    kind, value = structure_query

    if request.mode is StructureSearchMode.exact:
        target_key = inchi_key_from_query(kind, value)
        return stmt.where(Species.inchi_key == target_key)

    if kind is StructureQueryKind.smarts:
        # The cartridge's ONLY qmol_from_smarts overload takes `cstring`
        # (confirmed against the live schema: `\df qmol_from_smarts` --
        # unlike mol_from_smiles below, which is also overloaded for
        # `text`). Postgres allows an explicit `::cstring` cast on a
        # regular bind parameter but not an implicit one, and
        # `func.qmol_from_smarts(...)` has no way to express that cast
        # (SQLAlchemy has no stock `cstring` type) -- so this one
        # function call is built as a `text()` fragment with an explicit
        # cast, then used as an ordinary ColumnElement via `.op("@>")`
        # below, same as every other operand here.
        query_mol_expr: Any = text(
            "qmol_from_smarts(CAST(:structure_query AS cstring))"
        ).bindparams(structure_query=value)
    else:
        query_mol_expr = func.mol_from_smiles(parse_smiles_to_canonical(value))

    entry_select = (
        select(SpeciesEntry.id)
        .select_from(SpeciesEntry)
        .where(SpeciesEntry.species_id == Species.id)
        .where(SpeciesEntry.mol.is_not(None))
    )
    if request.mode is StructureSearchMode.substructure:
        entry_select = entry_select.where(
            SpeciesEntry.mol.op("@>")(query_mol_expr)
        )
    else:  # similarity
        threshold = (
            request.similarity_threshold
            if request.similarity_threshold is not None
            else DEFAULT_SIMILARITY_THRESHOLD
        )
        entry_select = entry_select.where(
            func.tanimoto_sml(
                func.morganbv_fp(SpeciesEntry.mol),
                func.morganbv_fp(query_mol_expr),
            )
            >= threshold
        )
    return stmt.where(entry_select.exists())


def search_species(
    session: Session, request: SpeciesSearchRequest
) -> ScientificSpeciesSearchResponse:
    """Discover species by chemical identity, with per-entry trust + availability.

    Filters multiple identifiers with AND semantics; inconsistent identifiers
    return an empty result set rather than raising. Default sort is the L3
    ``review_rank ASC, has_entries DESC, created_at DESC, id DESC``. Client-
    supplied ``sort=`` is rejected (v0). ``rejected`` and ``deprecated``
    review states are excluded by default.

    :param session: SQLAlchemy session bound to the read DB.
    :param request: Parsed request model.
    :returns: ``ScientificSpeciesSearchResponse`` Pydantic model.
    :raises ValueError: 422 for sort/pagination/include validation failures.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/species/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    # InChI is not persisted, so accepting it would violate the advertised
    # AND semantics whenever another identifier is supplied.
    reject_unsupported_filters(
        {"inchi": request.inchi},
        endpoint="/scientific/species/search",
    )

    # Phase C: ref-by-handle filters are valid identifier sources. Resolve
    # them first so we know whether the caller supplied a real identifier
    # and, if so, what id(s) to scope by.
    species_pair = reconcile_species_pair(
        session,
        id_value=None,
        ref_value=request.species_ref,
    )
    species_entry_pair = reconcile_species_entry_pair(
        session,
        id_value=None,
        ref_value=request.species_entry_ref,
    )
    if species_pair is NO_MATCH or species_entry_pair is NO_MATCH:
        return _empty_response(request, includes, offset, limit)
    species_ref_id: int | None = species_pair  # type: ignore[assignment]
    species_entry_ref_id: int | None = species_entry_pair  # type: ignore[assignment]

    has_chem_identifier = any(
        v is not None
        for v in (
            request.smiles,
            request.inchi,
            request.inchi_key,
            request.formula,
        )
    )
    if not has_chem_identifier and species_ref_id is None and species_entry_ref_id is None:
        raise ValueError(
            "missing_identifier: at least one of {smiles, inchi, inchi_key, "
            "formula, species_ref, species_entry_ref} is required."
        )

    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )

    # The identity filter, as a subquery rather than a materialized id list —
    # the entry, count and ranking queries below all re-derive it in SQL, so
    # a match of any size stays a match rather than becoming bind parameters.
    candidates = _candidate_species_stmt(
        request,
        species_ref_id=species_ref_id,
        species_entry_ref_id=species_entry_ref_id,
    ).subquery("candidate_species")

    # Pre-collapse total is the number of *species* matched, whether or not
    # any of their entries survived the trust gate: a species with none is
    # still a record, with an empty ``entries`` list.
    pre_collapse_total = (
        session.scalar(select(func.count()).select_from(candidates)) or 0
    )
    if pre_collapse_total == 0:
        return _empty_response(request, includes, offset, limit)

    # Pre-collapse review summary across every surviving entry, not just the
    # page's — one aggregate rather than one badge per candidate.
    summary = summary_from_sql(
        session,
        _visible_entry_rows(
            candidates,
            request,
            visible,
            species_entry_ref_id=species_entry_ref_id,
        ),
    )

    collapse_first = request.collapse.value == "first"
    page_species_ids = _rank_and_slice_species(
        session,
        candidates,
        request,
        visible,
        species_entry_ref_id=species_entry_ref_id,
        offset=0 if collapse_first else offset,
        limit=1 if collapse_first else limit,
    )
    if collapse_first:
        # ``collapse=first`` keeps the single best record and *then* applies
        # offset/limit to that one-element list, so any offset past it is
        # an empty page. Mirrored here rather than folded into the SQL.
        page_species_ids = page_species_ids[offset : offset + limit]

    returned_records = _build_page_records(
        session,
        page_species_ids,
        request,
        visible,
        species_entry_ref_id=species_entry_ref_id,
        includes=includes,
    )

    pagination = build_pagination(
        offset=offset,
        limit=limit,
        returned=len(returned_records),
        total=pre_collapse_total,
        collapse_first=collapse_first,
    )

    request_filter = _filter_echo(request)
    return ScientificSpeciesSearchResponse(
        request=RequestEcho(
            filter=request_filter,
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=returned_records,
        pagination=pagination,
    )


def browse_species(
    session: Session, request: SpeciesBrowseRequest
) -> ScientificSpeciesBrowseResponse:
    """List species by secondary filter alone — no identifier required.

    See docs/specs/read_api_mvp.md §Endpoint 1 for the sibling
    ``/species/search`` contract this deliberately mirrors. The two
    surfaces exist because ``search_species`` rejects an identifier-free
    request (``missing_identifier``) by design: a caller who *knows* what
    they want should get an exact, ref-scoped answer, and relaxing that
    refusal for an ``?browse=true`` opt-in would make the same route mean
    two different things depending on a flag. A catalogue read is a
    different question — "what does this archive hold?" — with a
    different shape of answer (a bounded, paged listing of the whole
    corpus, not a lookup), so it gets its own path and its own request
    model (:class:`SpeciesBrowseRequest`, which structurally cannot carry
    an identifier).

    Everything downstream of "which candidate species" is shared with
    ``search_species`` verbatim: the same visibility gate
    (:func:`app.services.scientific_read.common.visible_statuses`), the
    same ``review_rank ASC, has_entries DESC, created_at DESC, id DESC``
    order (:func:`_rank_and_slice_species`, whose ``id DESC`` tiebreak is
    what keeps pagination stable — see its docstring) *unless* a
    substructure/similarity structure filter is active, in which case
    ``heavy_atom_count ASC`` (smallest match first) leads that same order
    — see the ``order_by_size`` derivation a few lines below and
    :func:`_rank_and_slice_species`'s own ``order_by_size`` parameter doc
    for the full reasoning, including why ``exact`` mode is excluded and
    what happens to a species whose SMILES the cartridge can't parse — the
    same bounded page size (:func:`app.services.scientific_read.common.validate_pagination`,
    default 50 / hard cap 200), and the same per-page record builder
    (:func:`_build_page_records`) — so a browse record and a search record
    are the same shape, metadata only: identity, refs, per-entry
    availability *counts* and review badges, never an artifact, geometry
    or calculation payload.

    Only the candidate set differs: :func:`_browse_candidate_species_stmt`
    has no identifier predicate at all (there is no identifier field on
    :class:`SpeciesBrowseRequest` to read one from), so with every
    optional filter absent it is every species in the corpus.

    **#277 (and its follow-up).** A candidate species that has *some*
    ``species_entry`` rows but none currently visible is not a useful
    catalogue listing under any circumstance, so both ``pagination.total``
    (:func:`_browse_visible_species_total`) and the page itself
    (:func:`_rank_and_slice_species` with ``require_visible_entries=True``)
    drop it unconditionally. This used to be conditional on whether the
    caller had typed one of five specific request fields
    (min_review_status / include_rejected / include_deprecated /
    electronic_state_kind / species_entry_kind) — a check that missed the
    read-profile floor :func:`app.services.scientific_read.common.visible_statuses`
    also applies (``profile=curated`` narrows visibility to ``approved``
    with no request field set at all, so ``?profile=curated`` reproduced
    the exact bug verbatim), and that inverted itself for
    ``include_rejected``/``include_deprecated``: those only ever *widen*
    visibility, so treating them as a trigger could drop an entry-less
    species that a plainer request kept. Applying the rule unconditionally
    removes the whole class rather than patching the two known instances —
    a third way to narrow visibility reintroduces the bug with a
    field-name-keyed trigger in place, and cannot with a structural one.
    :func:`_has_any_entry_expr` is what keeps this safe for a species with
    *zero* ``species_entry`` rows: such a species is never dropped, under
    any filter, narrowed or not — it is still a valid (empty) catalogue
    entry, and dropping it would break the endpoint's other headline
    feature (see ``test_browse_with_no_filters_lists_every_created_species``).

    :param session: SQLAlchemy session bound to the read DB.
    :param request: Parsed request model.
    :returns: ``ScientificSpeciesBrowseResponse`` Pydantic model.
    :raises ValueError: 422 for sort/pagination/include validation failures.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _BROWSE_LEGAL_INCLUDE_TOKENS,
        "/scientific/species/browse",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    # Validated (and case-normalised) up front so an unknown symbol 422s
    # before any SQL runs, the same way reject_client_sort / validate_pagination
    # / validate_includes fail closed before the candidate query is built.
    element_symbols = _parse_elements_filter(request)
    _validate_provenance_version_parents(request)
    structure_query = _validate_browse_structure_query(request)

    # Smallest-first sizing only makes sense when the candidate set is
    # actually a structure match, and only under the two modes where more
    # than one *size* of match is expected -- substructure ("contains this
    # fragment") and similarity ("close to this") both routinely return
    # molecules of different sizes, which is exactly the incremental-typing
    # case the owner described (see _rank_and_slice_species's
    # ``order_by_size`` docstring for why ``exact`` is excluded).
    order_by_size = structure_query is not None and request.mode in (
        StructureSearchMode.substructure,
        StructureSearchMode.similarity,
    )

    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )

    candidates = _browse_candidate_species_stmt(
        request,
        element_symbols,
        structure_query,
        include_heavy_atom_count=order_by_size,
    ).subquery("candidate_species")

    # #277 (see the docstring above): always drop a candidate species that
    # has entries but none currently visible, unconditionally -- not just
    # when a specific request field triggered it.
    pre_collapse_total = _browse_visible_species_total(
        session, candidates, request, visible
    )
    if pre_collapse_total == 0:
        return _empty_browse_response(
            request, includes, offset, limit, element_symbols, structure_query
        )

    summary = summary_from_sql(
        session,
        _visible_entry_rows(candidates, request, visible, species_entry_ref_id=None),
    )

    collapse_first = request.collapse.value == "first"
    page_species_ids = _rank_and_slice_species(
        session,
        candidates,
        request,
        visible,
        species_entry_ref_id=None,
        offset=0 if collapse_first else offset,
        limit=1 if collapse_first else limit,
        require_visible_entries=True,
        order_by_size=order_by_size,
    )
    if collapse_first:
        page_species_ids = page_species_ids[offset : offset + limit]

    returned_records = _build_page_records(
        session,
        page_species_ids,
        request,
        visible,
        species_entry_ref_id=None,
        includes=includes,
    )

    pagination = build_pagination(
        offset=offset,
        limit=limit,
        returned=len(returned_records),
        total=pre_collapse_total,
        collapse_first=collapse_first,
    )

    return ScientificSpeciesBrowseResponse(
        request=RequestEcho(
            filter=_browse_filter_echo(request, element_symbols, structure_query),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=returned_records,
        pagination=pagination,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _candidate_species_stmt(
    request: SpeciesSearchRequest,
    *,
    species_ref_id: int | None = None,
    species_entry_ref_id: int | None = None,
):
    """The identity filter as a ``SELECT id, created_at`` over ``species``.

    Returns a statement, not rows: it is used as a subquery by the count,
    the summary and the ranking, so the caller never holds the matching id
    set in Python.
    """
    stmt = select(Species.id, Species.created_at)
    if request.smiles is not None:
        stmt = stmt.where(Species.smiles == request.smiles)
    if request.inchi_key is not None:
        stmt = stmt.where(Species.inchi_key == request.inchi_key)
    if request.charge is not None:
        stmt = stmt.where(Species.charge == request.charge)
    if request.multiplicity is not None:
        stmt = stmt.where(Species.multiplicity == request.multiplicity)
    if species_ref_id is not None:
        stmt = stmt.where(Species.id == species_ref_id)
    if species_entry_ref_id is not None:
        # Constrain the parent species to the one owning this entry.
        stmt = stmt.where(
            Species.id
            == select(SpeciesEntry.species_id)
            .where(SpeciesEntry.id == species_entry_ref_id)
            .scalar_subquery()
        )
    if request.formula is not None:
        # Derived per-query by the cartridge rather than stored: see
        # _formula_expr() for the notation, the isotope caveat and the
        # NULL-on-unparseable behavior (which excludes such a row here
        # rather than raising). Match is case-sensitive and exact; we only
        # strip incidental surrounding whitespace from client input.
        #
        # _build_page_records() serves the same expression as the record's
        # `formula`, so what a caller filtered on is what they read back.
        stmt = stmt.where(_formula_expr() == request.formula.strip())
    return stmt


def _browse_candidate_species_stmt(
    request: SpeciesBrowseRequest,
    element_symbols: list[str] | None,
    structure_query: tuple[StructureQueryKind, str] | None = None,
    *,
    include_heavy_atom_count: bool = False,
):
    """The browse candidate set as a ``SELECT id, created_at`` over ``species``.

    Same shape and same use (a subquery for the count, the summary and the
    ranking) as :func:`_candidate_species_stmt`, and deliberately not a
    call to it with ``None`` identifiers threaded through: that would let
    a future identifier field added to ``SpeciesSearchRequest`` silently
    reach browse through the shared function unless every call site were
    re-audited. Keeping the statement builders separate means the only
    way ``/species/browse`` gains an identifier predicate is a change
    written here, on a request type that has no identifier fields to read
    one from.

    With ``charge``, ``multiplicity``, ``formula`` and every composition
    filter absent (the default, empty browse request) this is every
    species in the corpus — which is the whole point of the endpoint.

    :param element_symbols: Already-validated, title-case element symbols
        parsed from ``request.elements`` by :func:`_parse_elements_filter`
        (``None`` when the caller supplied none). Threaded in rather than
        re-parsed here so validation happens exactly once, before any SQL
        runs, in :func:`browse_species`.
    :param structure_query: Already-validated ``(kind, value)`` pair from
        :func:`_validate_browse_structure_query` (``None`` when neither
        ``query_smiles`` nor ``query_smarts`` was supplied). Applied by
        :func:`_apply_structure_filter` below, alongside every other
        optional filter in this statement -- one candidate query, not a
        second one intersected afterward.
    :param include_heavy_atom_count: Adds a third selected column,
        :func:`_heavy_atom_count_expr` labelled ``heavy_atom_count``, so
        :func:`_rank_and_slice_species` can order by it (the smallest-
        match-first sort, see :func:`browse_species`'s ``order_by_size``).
        ``False`` by default and for every filter that is not a
        substructure/similarity structure search -- the cartridge call is
        not free, and an ordinary browse with no structure filter should
        not pay for a sort key it never uses.
    """
    columns: list[Any] = [Species.id, Species.created_at]
    if include_heavy_atom_count:
        columns.append(_heavy_atom_count_expr().label("heavy_atom_count"))
    stmt = select(*columns)
    if request.charge is not None:
        stmt = stmt.where(Species.charge == request.charge)
    if request.multiplicity is not None:
        stmt = stmt.where(Species.multiplicity == request.multiplicity)
    if request.formula is not None:
        # Same expression, same case-sensitive exact match, same NULL-on-
        # unparseable behavior as _candidate_species_stmt(); see
        # _formula_expr() for the full rationale.
        stmt = stmt.where(_formula_expr() == request.formula.strip())
    if element_symbols:
        element_predicates = [
            _formula_has_element_expr(symbol) for symbol in element_symbols
        ]
        if request.elem_mode is ElementMatchMode.any:
            stmt = stmt.where(or_(*element_predicates))
        else:
            stmt = stmt.where(and_(*element_predicates))
    if request.max_heavy_atoms is not None or request.min_heavy_atoms is not None:
        heavy_atoms = _heavy_atom_count_expr()
        if request.max_heavy_atoms is not None:
            stmt = stmt.where(heavy_atoms <= request.max_heavy_atoms)
        if request.min_heavy_atoms is not None:
            stmt = stmt.where(heavy_atoms >= request.min_heavy_atoms)
    stmt = _apply_provenance_filters(stmt, request)
    stmt = _apply_structure_filter(stmt, request, structure_query)
    return stmt


def _validate_provenance_version_parents(request: SpeciesBrowseRequest) -> None:
    """Refuse a version filter given without its named parent.

    ``software_version=16`` without ``software=Gaussian`` is ambiguous:
    more than one software package can have a release called "16", so an
    unscoped version filter would match calculations run with any of
    them, not the one package the caller has in mind. Mirrors
    ``/meta/software-versions`` / ``/meta/workflow-tool-versions``
    (``missing_version_parent``, #304) rather than inventing a second
    way to say the same thing — see
    :class:`~app.schemas.reads.scientific_species.SpeciesBrowseRequest`
    for the full rationale, including why this is stricter than
    ``TransitionStatesBrowseRequest`` today.

    Raises ``ValueError("missing_version_parent: ...")`` (-> HTTP 422,
    coded) when a ``*_version`` field is set and its parent is not.
    Called before any SQL runs, alongside the other up-front validators
    in :func:`browse_species`.
    """
    if request.software_version is not None and request.software is None:
        raise ValueError(
            "missing_version_parent: software is required when "
            "software_version is supplied for /species/browse."
        )
    if (
        request.workflow_tool_version is not None
        and request.workflow_tool is None
    ):
        raise ValueError(
            "missing_version_parent: workflow_tool is required when "
            "workflow_tool_version is supplied for /species/browse."
        )


def _apply_provenance_filters(stmt, request: SpeciesBrowseRequest):
    """Narrow browse candidates to species with >=1 matching calculation.

    OR-across-calculation, at the *species* grain: a candidate species
    passes if any ``calculation`` row belonging to *any* of its
    ``species_entry`` rows matches every supplied
    method/basis/software/workflow_tool constraint. Delegates the join
    logic to :func:`app.services.scientific_read.calculation_provenance_filters.apply_calculation_provenance_filter`,
    the same function ``transition_states_search.py`` uses for the
    identical TS-entry-grained filter — see that module's docstring for
    the semantics in full and
    :class:`~app.schemas.reads.scientific_species.SpeciesBrowseRequest`
    for why species browse chose the same "any" reading one level up
    (species -> entries -> calculations, vs. TS entry -> calculations
    directly).

    A no-op (returns ``stmt`` unchanged) when none of the six fields is
    set, same as every other optional filter in this module.
    """
    return apply_calculation_provenance_filter(
        stmt,
        request,
        select(Calculation.id)
        .select_from(Calculation)
        .join(SpeciesEntry, SpeciesEntry.id == Calculation.species_entry_id)
        .where(SpeciesEntry.species_id == Species.id),
    )


def _entry_filter_predicates(
    request: SpeciesFilterRequest, species_entry_ref_id: int | None
) -> list:
    """The per-entry ``where`` terms, in one place for all three uses."""
    predicates = []
    if request.electronic_state_kind is not None:
        predicates.append(
            SpeciesEntry.electronic_state_kind == request.electronic_state_kind
        )
    if request.species_entry_kind is not None:
        predicates.append(SpeciesEntry.kind == request.species_entry_kind)
    if species_entry_ref_id is not None:
        predicates.append(SpeciesEntry.id == species_entry_ref_id)
    return predicates


def _join_entries_and_reviews(
    stmt,
    candidates,
    request: SpeciesFilterRequest,
    *,
    species_entry_ref_id: int | None,
    outer: bool,
):
    """Join candidate species → their entries → each entry's review row.

    The chain is deliberately **flat**: ``species`` joined to
    ``species_entry`` joined to ``record_review``, with the per-entry filters
    as join conditions, rather than a pre-filtered entry *subquery* joined to
    the species set.

    That is not a cosmetic preference. With the entry filters and the review
    LEFT JOIN wrapped in a subquery, the planner is free to compile
    ``species_entry.species_id = species.id`` into a join *filter* rather
    than an index condition — the review outer join and its ``status IS
    NULL`` branch block the pushdown — and then the inner side is re-scanned
    in full once per candidate species. That plan was observed on a
    statistics-free database (65,599 candidate species: over 15 minutes,
    ``Join Filter: (species_entry.species_id = species.id)`` above a
    full ``ix_species_entry_species_id`` scan). Keeping the chain flat leaves
    the join key on a plain indexed column where the planner can reach it.

    Statistics still matter more than shape — no formulation of this query
    survives a table that has tens of thousands of rows and no ``ANALYZE``.
    What the flat chain buys unconditionally is that the identity predicate
    is evaluated once per statement instead of twice, which is what the
    RDKit-backed ``formula=`` filter cares about: on the Stage 4 benchmark
    corpus the broad formula search is 21.7 ms flat against 28.3 ms wrapped.

    :param outer: ``True`` keeps species with no matching entry (the ranking
        needs them — they are still records); ``False`` drops them (the
        summary counts entries, not species).
    :returns: ``(statement, review_alias)``.
    """
    entry_on = and_(
        SpeciesEntry.species_id == candidates.c.id,
        *_entry_filter_predicates(request, species_entry_ref_id),
    )
    from_clause = (
        candidates.outerjoin(SpeciesEntry, entry_on)
        if outer
        else candidates.join(SpeciesEntry, entry_on)
    )
    return join_review(
        stmt.select_from(from_clause),
        SpeciesEntry.id,
        SubmissionRecordType.species_entry,
    )


def _visible_rank_expr(review, visible: set):
    """The per-row ``CASE`` that decides whether a joined entry row is visible.

    One definition, two consumers: :func:`_rank_and_slice_species` (which
    ``MIN``/``COUNT``s it for ranking) and :func:`_browse_visible_species_total`
    (which ``COUNT``s it to decide whether a candidate species survives an
    entry-level ``/species/browse`` filter). Factored out precisely so those
    two can never silently disagree about which entries are visible — the
    same "one expression, used everywhere" reasoning as :func:`_formula_expr`.

    The ``SpeciesEntry.id IS NOT NULL`` guard is load-bearing: without it the
    outer join's all-NULL row for an entry-less species would satisfy the
    "no review row means not_reviewed" branch of the visibility predicate and
    the species would rank (or count) as if it had a never-reviewed entry.
    """
    return case(
        (
            and_(
                SpeciesEntry.id.is_not(None),
                visible_review_filter(review, visible),
            ),
            review_rank_expr(review),
        ),
        else_=None,
    )


def _has_any_entry_expr(candidates):
    """Whether a candidate species has at least one ``species_entry`` row
    *at all* — structurally, independent of every request filter.

    Deliberately **not** derived from the outer join
    :func:`_join_entries_and_reviews` builds. That join's ``ON`` condition
    already applies :func:`_entry_filter_predicates` (``electronic_state_kind``
    / ``species_entry_kind``), so *within* that join, "no entry matched the
    filter" and "no entry exists" produce the identical all-NULL row and
    cannot be told apart. They must stay distinguishable: a species whose
    only entry fails ``electronic_state_kind=excited`` must still be
    dropped by that filter (:func:`_visible_rank_expr` already makes that
    entry invisible), while a species with zero ``species_entry`` rows must
    never be dropped by *any* filter — it is still a valid catalogue
    listing, just an empty one. This is a second, independent correlated
    subquery over ``species_entry`` naming only ``species_id``, so no
    request filter can touch it.
    """
    return (
        select(func.count())
        .select_from(SpeciesEntry)
        .where(SpeciesEntry.species_id == candidates.c.id)
        .correlate(candidates)
        .scalar_subquery()
    ) > 0


def _rank_and_slice_species(
    session: Session,
    candidates,
    request: SpeciesFilterRequest,
    visible: set,
    *,
    species_entry_ref_id: int | None,
    offset: int,
    limit: int,
    require_visible_entries: bool = False,
    order_by_size: bool = False,
) -> list[int]:
    """Order the candidate species and return one page of ids.

    ``review_rank ASC, has_entries DESC, created_at DESC, id DESC``, where
    ``review_rank`` is the best rank among a species' *visible* entries and
    :data:`_NO_VISIBLE_ENTRY_RANK` when it has none.

    :param order_by_size: When ``True``, ``heavy_atom_count ASC`` (NULLS
        LAST) leads every other key -- smallest match first, largest last,
        the behaviour :func:`browse_species` turns on for a substructure
        or similarity structure search (see its own ``order_by_size``
        derivation). ``candidates`` must have been built by
        :func:`_browse_candidate_species_stmt` with
        ``include_heavy_atom_count=True`` so ``candidates.c.heavy_atom_count``
        exists -- the two flags are set from the same condition in
        :func:`browse_species`, so they can't disagree.

        Size leads; review status does not disappear, it becomes the
        tiebreak among same-sized species (still ``best_rank ASC`` next in
        line) rather than the other way around. The owner's request was
        explicit -- "smallest first ... ending with the largest at the
        end" -- and putting ``best_rank`` ahead of size would not deliver
        that: a large *approved* species would still sort before a small
        *unreviewed* one. Demoting review status to a tiebreak keeps it
        meaningful (it still decides ties) without breaking the ordering
        the owner asked for.

        A species whose SMILES the cartridge cannot parse gets a NULL
        ``heavy_atom_count`` (:func:`_heavy_atom_count_expr`'s documented
        behaviour) rather than being dropped -- ``NULLS LAST`` sorts it
        after every species with a known size, not out of the listing, and
        the remaining keys (``best_rank``, ``has_entries``, ``created_at``,
        ``id``) still total-order the unparseable rows among themselves.

        ``exact`` mode never sets this: :func:`_apply_structure_filter`
        matches ``exact`` on ``species.inchi_key`` alone, and that column
        is deliberately non-unique (``uq_species_identity`` is
        ``(smiles, charge, multiplicity)``, not ``inchi_key`` --
        see ``Species.__table_args__``), so an exact match CAN return more
        than one species row (distinct tautomers/charge/multiplicity/spin
        states that collapse to the same InChIKey). That is handled --
        those rows still page under the existing
        ``best_rank/has_entries/created_at/id`` order -- but a size sort
        was not added for them: rows sharing one InChIKey are, chemically,
        the same skeleton, so they are the same heavy-atom count in every
        case this schema can produce, and a sort key that can only ever
        compare equal is not a sort a reader asked for.

    Visibility is a ``CASE`` inside the aggregate (:func:`_visible_rank_expr`)
    rather than a ``WHERE``. A ``WHERE`` would drop species whose every entry
    is invisible, and by default those species are still records — with an
    empty ``entries`` list. ``MIN`` ignores NULLs, so an invisible entry
    contributes nothing and a species with no visible entry aggregates to
    NULL, which the ``COALESCE`` turns into the sorts-last rank.

    ``id`` desc is the tiebreak that makes this a total order; without it,
    species sharing a ``created_at`` (every row inserted in one transaction
    does) would page non-deterministically.

    :param require_visible_entries: When ``True``, a ``HAVING`` clause drops
        a candidate species from the page once it has *some* entries but
        none of them are visible — see :func:`_has_any_entry_expr`, which is
        what makes that "some entries, none visible" condition distinct from
        "zero entries, structurally": a species with no ``species_entry``
        rows at all is **never** dropped by this flag, only ranked last
        (unchanged from the ``False`` default).

        Default ``False`` preserves the original behaviour exactly (every
        call from :func:`search_species` relies on this default: a species
        named by identifier is still a record even with no visible entries
        -- "CH3 exists but has no approved entries" is a defensible answer
        when the caller named CH3 by identifier). ``/species/browse``
        (:func:`browse_species`) always passes ``True`` unconditionally --
        see its docstring for why an earlier version of this gated on which
        request fields were set, and why that was wrong (issue #277
        follow-up: review-profile-driven narrowing carries no request field
        to key off of).
    """
    stmt, review = _join_entries_and_reviews(
        select(candidates.c.id),
        candidates,
        request,
        species_entry_ref_id=species_entry_ref_id,
        outer=True,
    )
    visible_rank = _visible_rank_expr(review, visible)
    best_rank = func.coalesce(func.min(visible_rank), _NO_VISIBLE_ENTRY_RANK)
    has_entries = func.count(visible_rank) > 0
    group_by_cols = [candidates.c.id, candidates.c.created_at]
    order_by_cols = []
    if order_by_size:
        # heavy_atom_count is functionally dependent on candidates.c.id
        # (one row per candidate species), but Postgres doesn't infer that
        # dependency through a derived subquery the way it does for a real
        # table's primary key -- candidates.c.created_at is already
        # grouped explicitly for the same reason (see the module-level
        # note above), so heavy_atom_count follows the same pattern.
        group_by_cols.append(candidates.c.heavy_atom_count)
        order_by_cols.append(candidates.c.heavy_atom_count.asc().nulls_last())
    stmt = stmt.group_by(*group_by_cols)
    if require_visible_entries:
        stmt = stmt.having(or_(not_(_has_any_entry_expr(candidates)), has_entries))
    order_by_cols.extend(
        [
            best_rank.asc(),
            has_entries.desc(),
            candidates.c.created_at.desc(),
            candidates.c.id.desc(),
        ]
    )
    stmt = stmt.order_by(*order_by_cols).offset(offset).limit(limit)
    return list(session.scalars(stmt))


def _browse_visible_species_total(
    session: Session,
    candidates,
    request: SpeciesBrowseRequest,
    visible: set,
) -> int:
    """Count of candidate species browse's paging step will actually keep.

    Used by :func:`browse_species` unconditionally instead of a bare
    ``count(*) from candidates``, so ``pagination.total`` reports the
    species that survive the same "some entries but none visible get
    dropped; zero entries stays" rule :func:`_rank_and_slice_species`
    applies with ``require_visible_entries=True`` — the defect reported as
    issue #277 (``min_review_status=approved`` reporting the full
    unfiltered species count as ``total`` while every returned record
    carried an empty ``entries`` list; a later follow-up found the same
    defect reachable through ``profile=curated``, which narrows visibility
    with no request field to key a trigger on at all).

    Built over :func:`_visible_rank_expr` and :func:`_has_any_entry_expr`,
    the same two expressions :func:`_rank_and_slice_species` groups,
    orders and (when asked) filters by — one definition of "visible" and
    one of "has any entry", so the count returned here and the set of
    species that survive paging can never disagree about which species
    that is.
    """
    stmt, review = _join_entries_and_reviews(
        select(candidates.c.id),
        candidates,
        request,
        species_entry_ref_id=None,
        outer=True,
    )
    visible_rank = _visible_rank_expr(review, visible)
    has_entries = func.count(visible_rank) > 0
    keep = or_(not_(_has_any_entry_expr(candidates)), has_entries)
    counted = stmt.group_by(candidates.c.id).having(keep).subquery("kept_species")
    return session.scalar(select(func.count()).select_from(counted)) or 0


def _visible_entry_rows(
    candidates,
    request: SpeciesFilterRequest,
    visible: set,
    *,
    species_entry_ref_id: int | None,
):
    """Subquery of every visible entry of every candidate species.

    Feeds the pre-pagination :func:`summary_from_sql` aggregate. Referenced
    exactly once, so PostgreSQL inlines it into the aggregate rather than
    treating it as an optimization fence.
    """
    stmt, review = _join_entries_and_reviews(
        select(SpeciesEntry.id),
        candidates,
        request,
        species_entry_ref_id=species_entry_ref_id,
        outer=False,
    )
    return (
        stmt.add_columns(review_status_expr(review).label("review_status"))
        .where(visible_review_filter(review, visible))
        .subquery("visible_entries")
    )


def _load_page_entries(
    session: Session,
    page_species_ids: list[int],
    request: SpeciesFilterRequest,
    visible: set,
    *,
    species_entry_ref_id: int | None,
) -> list[SpeciesEntry]:
    """The visible entries of the page's species, in a stable order.

    Scoped by the page's species ids — at most ``limit`` of them — so this is
    the one place an ``IN`` list of ids is safe.
    """
    stmt = select(SpeciesEntry).where(
        SpeciesEntry.species_id.in_(page_species_ids),
        *_entry_filter_predicates(request, species_entry_ref_id),
    )
    stmt, review = join_review(
        stmt, SpeciesEntry.id, SubmissionRecordType.species_entry
    )
    return list(
        session.scalars(
            stmt.where(visible_review_filter(review, visible)).order_by(
                SpeciesEntry.id
            )
        )
    )


def _build_page_records(
    session: Session,
    page_species_ids: list[int],
    request: SpeciesFilterRequest,
    visible: set,
    *,
    species_entry_ref_id: int | None,
    includes: set[str],
) -> list[SpeciesScientificRecord]:
    """Materialize one page: species, their visible entries, and the extras.

    Every load below is bounded by the page — at most ``limit`` species and
    their entries — which is the whole point of ranking in SQL first.
    """
    if not page_species_ids:
        return []

    # The formula rides along with the species row rather than being fetched
    # separately: it is derived from a column of that same row, and the page
    # is already bounded, so the cartridge evaluates it at most `limit` times.
    species_rows = session.execute(
        select(Species, _formula_expr()).where(Species.id.in_(page_species_ids))
    ).all()
    species_by_id = {species.id: species for species, _ in species_rows}
    formula_by_id = {species.id: formula for species, formula in species_rows}
    entries = _load_page_entries(
        session,
        page_species_ids,
        request,
        visible,
        species_entry_ref_id=species_entry_ref_id,
    )
    entries_by_species: dict[int, list[SpeciesEntry]] = {
        species_id: [] for species_id in page_species_ids
    }
    for entry in entries:
        entries_by_species[entry.species_id].append(entry)

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_ids=[entry.id for entry in entries],
    )
    availability_per_entry = _compute_availability(session, entries)
    section_ids = _compute_section_ids(session, entries, includes)

    records: list[SpeciesScientificRecord] = []
    for species_id in page_species_ids:
        species = species_by_id[species_id]
        records.append(
            SpeciesScientificRecord(
                species_id=species.id,
                species_ref=species.public_ref,
                canonical_smiles=species.smiles,
                inchi_key=species.inchi_key,
                formula=formula_by_id[species_id],
                charge=species.charge,
                multiplicity=species.multiplicity,
                stereo_kind=species.stereo_kind,
                entries=[
                    _build_entry_record(
                        entry,
                        badges[entry.id],
                        availability_per_entry[entry.id],
                        section_ids.get(entry.id, {}),
                    )
                    for entry in entries_by_species[species_id]
                ],
            )
        )
    return records


def _compute_availability(
    session: Session, entries: list[SpeciesEntry]
) -> dict[int, SpeciesEntryAvailability]:
    """Bulk-compute availability flags + counts for a list of species entries."""
    if not entries:
        return {}
    entry_ids = [e.id for e in entries]

    thermo_ids = set(
        session.scalars(
            select(Thermo.species_entry_id).where(
                Thermo.species_entry_id.in_(entry_ids)
            )
        ).all()
    )
    statmech_ids = set(
        session.scalars(
            select(Statmech.species_entry_id).where(
                Statmech.species_entry_id.in_(entry_ids)
            )
        ).all()
    )
    transport_ids = set(
        session.scalars(
            select(Transport.species_entry_id).where(
                Transport.species_entry_id.in_(entry_ids)
            )
        ).all()
    )
    conformer_ids = set(
        session.scalars(
            select(ConformerGroup.species_entry_id).where(
                ConformerGroup.species_entry_id.in_(entry_ids)
            )
        ).all()
    )

    calc_counts: dict[int, int] = dict.fromkeys(entry_ids, 0)
    for entry_id, count in session.execute(
        select(Calculation.species_entry_id, func.count(Calculation.id))
        .where(Calculation.species_entry_id.in_(entry_ids))
        .group_by(Calculation.species_entry_id)
    ).all():
        calc_counts[entry_id] = count

    return {
        e.id: SpeciesEntryAvailability(
            has_thermo=e.id in thermo_ids,
            has_statmech=e.id in statmech_ids,
            has_transport=e.id in transport_ids,
            has_conformers=e.id in conformer_ids,
            calculation_count=calc_counts.get(e.id, 0),
        )
        for e in entries
    }


def _compute_section_ids(
    session: Session,
    entries: list[SpeciesEntry],
    includes: set[str],
) -> dict[int, dict[str, SpeciesEntrySectionIds]]:
    """Bulk-fetch ID lists per entry for any include= sections requested."""
    if not entries or not includes:
        return {}
    entry_ids = [e.id for e in entries]
    result: dict[int, dict[str, SpeciesEntrySectionIds]] = {
        eid: {} for eid in entry_ids
    }

    if "thermo" in includes:
        thermo_pairs = session.execute(
            select(Thermo.species_entry_id, Thermo.id).where(
                Thermo.species_entry_id.in_(entry_ids)
            )
        ).all()
        per_entry: dict[int, list[int]] = {eid: [] for eid in entry_ids}
        for eid, tid in thermo_pairs:
            per_entry[eid].append(tid)
        for eid, ids in per_entry.items():
            result[eid]["thermo"] = SpeciesEntrySectionIds(ids=sorted(ids))

    if "statmech" in includes:
        pairs = session.execute(
            select(Statmech.species_entry_id, Statmech.id).where(
                Statmech.species_entry_id.in_(entry_ids)
            )
        ).all()
        per_entry = {eid: [] for eid in entry_ids}
        for eid, sid in pairs:
            per_entry[eid].append(sid)
        for eid, ids in per_entry.items():
            result[eid]["statmech"] = SpeciesEntrySectionIds(ids=sorted(ids))

    if "transport" in includes:
        pairs = session.execute(
            select(Transport.species_entry_id, Transport.id).where(
                Transport.species_entry_id.in_(entry_ids)
            )
        ).all()
        per_entry = {eid: [] for eid in entry_ids}
        for eid, tid in pairs:
            per_entry[eid].append(tid)
        for eid, ids in per_entry.items():
            result[eid]["transport"] = SpeciesEntrySectionIds(ids=sorted(ids))

    if "conformers" in includes:
        pairs = session.execute(
            select(ConformerGroup.species_entry_id, ConformerGroup.id).where(
                ConformerGroup.species_entry_id.in_(entry_ids)
            )
        ).all()
        per_entry = {eid: [] for eid in entry_ids}
        for eid, cid in pairs:
            per_entry[eid].append(cid)
        for eid, ids in per_entry.items():
            result[eid]["conformers"] = SpeciesEntrySectionIds(ids=sorted(ids))

    # include=review is a no-op at the data-shape level here — every entry
    # already carries a RecordReviewBadge by default. The token is accepted
    # for consistency with the L4 vocabulary.
    return result


def _build_entry_record(
    entry: SpeciesEntry,
    badge: RecordReviewBadge,
    availability: SpeciesEntryAvailability,
    section_ids: dict[str, SpeciesEntrySectionIds],
) -> SpeciesEntryScientificRecord:
    return SpeciesEntryScientificRecord(
        species_entry_id=entry.id,
        species_entry_ref=entry.public_ref,
        species_entry_kind=entry.kind,
        electronic_state_kind=entry.electronic_state_kind,
        # The identity columns are read straight off the already-loaded
        # entity: no extra query, and no substitution for a NULL. See
        # SpeciesEntryScientificRecord for why they are in the default
        # projection rather than behind an include token.
        stereo_label=entry.stereo_label,
        electronic_state_label=entry.electronic_state_label,
        term_symbol=entry.term_symbol,
        isotope_key=entry.isotope_key,
        species_entry_label=species_entry_label_for(entry),
        review=badge,
        availability=availability,
        thermo_summary=section_ids.get("thermo"),
        statmech_summary=section_ids.get("statmech"),
        transport_summary=section_ids.get("transport"),
        conformers_summary=section_ids.get("conformers"),
    )


def _filter_echo(request: SpeciesSearchRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    for field in (
        "smiles",
        "inchi",
        "inchi_key",
        "formula",
        "charge",
        "multiplicity",
        "species_ref",
        "species_entry_ref",
    ):
        value = getattr(request, field)
        if value is not None:
            echo[field] = value
    if request.electronic_state_kind is not None:
        echo["electronic_state_kind"] = request.electronic_state_kind.value
    if request.species_entry_kind is not None:
        echo["species_entry_kind"] = request.species_entry_kind.value
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_response(
    request: SpeciesSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificSpeciesSearchResponse:
    return ScientificSpeciesSearchResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


def _browse_filter_echo(
    request: SpeciesBrowseRequest,
    element_symbols: list[str] | None,
    structure_query: tuple[StructureQueryKind, str] | None = None,
) -> dict[str, object]:
    """:func:`_filter_echo`'s counterpart for browse.

    Not a call to ``_filter_echo`` with the identifier fields absent:
    :class:`SpeciesBrowseRequest` has no ``smiles`` / ``inchi`` /
    ``inchi_key`` / ``species_ref`` / ``species_entry_ref`` attributes to
    read, so ``getattr(request, "smiles")`` would raise rather than
    quietly return ``None``. Kept as its own small function — duplicating
    the shared-field half rather than threading a field list through a
    common helper — so a change to one endpoint's echo can never silently
    move the other's.

    :param element_symbols: The *parsed* result of
        :func:`_parse_elements_filter`, not ``request.elements`` itself.
        ``elements=" , "`` is a non-``None`` string that parses to no
        symbols at all and applies no filter (see
        :func:`_parse_elements_filter`) — echoing on
        ``request.elements is not None`` would claim a filter that was
        never applied to a single row. Gating on the parsed list instead
        means the echo can only ever report a filter that ran.
    """
    echo: dict[str, object] = {}
    for field in ("formula", "charge", "multiplicity"):
        value = getattr(request, field)
        if value is not None:
            echo[field] = value
    if request.electronic_state_kind is not None:
        echo["electronic_state_kind"] = request.electronic_state_kind.value
    if request.species_entry_kind is not None:
        echo["species_entry_kind"] = request.species_entry_kind.value
    if element_symbols:
        echo["elements"] = request.elements
        echo["elem_mode"] = request.elem_mode.value
    if request.max_heavy_atoms is not None:
        echo["max_heavy_atoms"] = request.max_heavy_atoms
    if request.min_heavy_atoms is not None:
        echo["min_heavy_atoms"] = request.min_heavy_atoms
    for field in (
        "method",
        "basis",
        "software",
        "software_version",
        "workflow_tool",
        "workflow_tool_version",
    ):
        value = getattr(request, field)
        if value is not None:
            echo[field] = value
    if structure_query is not None:
        kind, value = structure_query
        echo[f"query_{kind.value}"] = value
        echo["mode"] = request.mode.value
        if request.mode is StructureSearchMode.similarity:
            # Always echo the effective threshold, same as
            # structure_search.py's own _request_filter_echo -- a caller
            # who did not set one still sees the default they ran with.
            echo["similarity_threshold"] = (
                request.similarity_threshold
                if request.similarity_threshold is not None
                else DEFAULT_SIMILARITY_THRESHOLD
            )
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_browse_response(
    request: SpeciesBrowseRequest,
    includes: set[str],
    offset: int,
    limit: int,
    element_symbols: list[str] | None = None,
    structure_query: tuple[StructureQueryKind, str] | None = None,
) -> ScientificSpeciesBrowseResponse:
    return ScientificSpeciesBrowseResponse(
        request=RequestEcho(
            filter=_browse_filter_echo(request, element_symbols, structure_query),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )
