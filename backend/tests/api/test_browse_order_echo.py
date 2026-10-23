"""The order a response echoes is the order it served (``request.sort``).

Three layers. The static ones read the source: every key a ``browse_order(...)`` call names is explained by the
catalog, and every catalog key is used by something. The behavioural ones sort real rows and compare the echoed keys
with the positions the endpoint returned:

* three reads whose echo had drifted from their query (kept as named regressions), and
* **every list endpoint that returns an ``OrderEcho``**, parametrized in ``CASES``. Each case seeds rows whose review
  status, deposition time and id disagree with one another, calls the endpoint, and requires the served order to equal
  the order the echo describes. A case also requires that no single key alone reproduces the served order, so a seed
  that cannot tell the keys apart fails instead of passing vacuously.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads import scientific_common
from app.schemas.reads.scientific_common import ORDER_KEY_CATALOG, REVIEW_RANK
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transport,
    next_inchi_key,
    set_review,
)

APP = Path(scientific_common.__file__).resolve().parents[2]

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Five rows whose review status, deposition time and id disagree. Index is creation order, so a larger index is a
# larger id. ``age_days`` is how long before ``_T0`` the row was deposited (larger = older).
#
#   row  status         age   why
#   0    not_reviewed     1   newest deposit, smallest id: newest-first and id-descending disagree
#   1    approved        30   oldest deposit: only the review rank can put it first
#   2    under_review    10   ties with row 3 on time and ranks equal to the unreviewed rows (a curator opening a record
#                             is not a verdict)
#   3    not_reviewed    10   same time as row 2: only the id breaks the tie
#   4    not_reviewed     5   largest id, middle age
#
# Display order is review rank, created_at DESC, id DESC: 1, 0, 4, 3, 2.
ROW_SPECS = [
    (RecordReviewStatus.not_reviewed, 1),
    (RecordReviewStatus.approved, 30),
    (RecordReviewStatus.under_review, 10),
    (RecordReviewStatus.not_reviewed, 10),
    (RecordReviewStatus.not_reviewed, 5),
]


def _keys(order) -> list[tuple[str, str]]:
    return [(k["key"], k["direction"]) for k in order["keys"]]


# --- static: the catalog and the calls agree -------------------------------------------------------------------------


def _named_keys() -> set[str]:
    """Every string literal passed to ``browse_order(...)``."""
    named: set[str] = set()
    calls = 0
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "browse_order":
                calls += 1
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        named.add(arg.value)
                    elif isinstance(arg, ast.Tuple) and arg.elts and isinstance(arg.elts[0], ast.Constant):
                        named.add(arg.elts[0].value)
    assert calls > 20, f"only {calls} browse_order calls found: the scan reached too little to prove anything"
    return named


def test_every_key_a_call_names_is_explained_by_the_catalog():
    assert _named_keys() <= set(ORDER_KEY_CATALOG)


def test_every_catalog_key_is_used_by_some_order():
    unused = set(ORDER_KEY_CATALOG) - _named_keys()
    assert not unused, f"catalog keys no response echoes: {sorted(unused)}"


def test_no_evidence_key_is_left_in_the_catalog():
    """Whether a record has evidence is a filter, never a browse key."""
    evidence = {"has_entries", "has_kinetics", "has_transition_state", "quality_rank"}
    assert not evidence & set(ORDER_KEY_CATALOG)


# --- behavioural: every list endpoint ---------------------------------------------------------------------------------


@dataclass
class Seeded:
    """Rows an endpoint lists, in creation order, and how to read them back off a response.

    ``ref_of`` takes one response record and returns the public ref of the seeded row it stands for.
    ``review_targets`` gives, per row, the ``(record_type, id)`` a review status is set on (the row itself unless the
    endpoint ranks by something else, e.g. a species by its entries). ``extra`` supplies the value of a key outside
    ``review_rank`` / ``created_at`` / ``id`` for a row (``energy_hartree``...).
    """

    url: str
    rows: list[Any]
    ref_of: Callable[[dict], str]
    review_targets: list[tuple[SubmissionRecordType, int]]
    extra: dict[str, Callable[[int], Any]] = field(default_factory=dict)
    post_body: dict | None = None
    specs: list[tuple[RecordReviewStatus, int]] = field(default_factory=lambda: list(ROW_SPECS))
    #: Rows beyond this count are listed but not part of the comparison (an endpoint that lists a parent as well).
    created_attr: Callable[[Any], datetime] = lambda row: row.created_at


def find(obj: Any, key: str) -> Any:
    """First value stored under ``key`` anywhere in a response record."""
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], str):
            return obj[key]
        for v in obj.values():
            hit = find(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find(v, key)
            if hit is not None:
                return hit
    return None


def finder(key: str) -> Callable[[dict], str]:
    def ref_of(record: dict) -> str:
        ref = find(record, key)
        assert ref is not None, f"no {key} in record {list(record)}"
        return ref

    return ref_of


def skew(db_session, rows, specs, *, set_created: Callable[[Any, datetime], None] | None = None) -> None:
    """Give each row its deposition time (rows in one transaction would otherwise share ``now()``)."""
    for row, (_status, age_days) in zip(rows, specs):
        stamp = _T0 - timedelta(days=age_days)
        if set_created is not None:
            set_created(row, stamp)
        else:
            row.created_at = stamp
    db_session.flush()


def review(db_session, seeded: Seeded) -> None:
    for (record_type, record_id), (status, _age) in zip(seeded.review_targets, seeded.specs):
        if status is not RecordReviewStatus.not_reviewed:
            set_review(db_session, record_type=record_type, record_id=record_id, status=status)


def _value(seeded: Seeded, index: int, key: str) -> Any:
    status = seeded.specs[index][0]
    row = seeded.rows[index]
    if key == "review_rank":
        return REVIEW_RANK[status]
    if key == "created_at":
        return seeded.created_attr(row).timestamp()
    if key == "id":
        return row.id
    if key in seeded.extra:
        return seeded.extra[key](index)
    raise AssertionError(f"echoed key {key!r} has no value for this case: add it to Seeded.extra")


def _order_by(seeded: Seeded, keys: list[tuple[str, str]]) -> list[int]:
    """Row indexes ordered by ``keys`` (name, direction), the way the echo says. A null sorts last."""
    order = list(range(len(seeded.rows)))
    for key, direction in reversed(keys):  # stable sorts, least significant key first
        def sort_key(i, key=key):
            v = _value(seeded, i, key)
            return (v is None, 0 if v is None else v)

        present = [i for i in order if _value(seeded, i, key) is not None]
        absent = [i for i in order if _value(seeded, i, key) is None]
        present.sort(key=sort_key, reverse=(direction == "desc"))
        order = present + absent
    return order


def served_indexes(client, seeded: Seeded) -> tuple[list[int], dict]:
    if seeded.post_body is not None:
        body = client.post(seeded.url, json=seeded.post_body).json()
    else:
        body = client.get(seeded.url).json()
    assert "records" in body, body
    refs = {row.public_ref: i for i, row in enumerate(seeded.rows)}
    served = [refs[seeded.ref_of(rec)] for rec in body["records"] if seeded.ref_of(rec) in refs]
    return served, body


def check_echo_matches_served_order(client, seeded: Seeded) -> None:
    served, body = served_indexes(client, seeded)
    assert len(served) == len(seeded.rows), f"endpoint returned {len(served)} of {len(seeded.rows)} seeded rows"
    sort = body["request"]["sort"]
    echoed = _keys(sort)
    expected = _order_by(seeded, echoed)
    assert served == expected, (
        f"the response says it is ordered by {echoed}, which orders the rows {expected}; it served {served}"
    )
    # The seed must be able to tell the keys apart: no single echoed key may reproduce the served order on its own.
    for key, direction in echoed:
        if key in ("review_rank", "created_at", "id"):
            assert _order_by(seeded, [(key, direction)]) != served, (
                f"the seed cannot distinguish {key!r}: ordering by it alone gives the served order"
            )


# --- case builders: one per list endpoint ---------------------------------------------------------------------------

API = "/api/v1/scientific"
_ISOMERS = ["CCCCO", "CCC(C)O", "CC(C)CO", "CC(C)(C)O", "CCOCC"]  # C4H10O: one formula, five species


def _entries(db_session, n=2, smiles="C=C", prefix="OE"):
    species = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key(prefix))
    return species, [make_species_entry(db_session, species, stereo_label=chr(ord("a") + i)) for i in range(n)]


def seed_thermo(db_session, *, search: bool) -> Seeded:
    # The search spans two entries of one species, so a parent-first order would show; the entry read has one entry.
    species, entries = _entries(db_session)
    n_entries = 2 if search else 1
    rows = [
        make_thermo_scalar(db_session, species_entry=entries[i % n_entries], h298_kj_mol=-10.0 - i) for i in range(5)
    ]
    skew(db_session, rows, ROW_SPECS)
    url = f"{API}/thermo/search?smiles=C=C" if search else f"{API}/species-entries/{entries[0].public_ref}/thermo"
    return Seeded(url, rows, finder("thermo_ref"), [(SubmissionRecordType.thermo, r.id) for r in rows])


def seed_transport(db_session, *, search: bool) -> Seeded:
    species, entries = _entries(db_session, prefix="OT")
    n_entries = 2 if search else 1
    rows = [make_transport(db_session, species_entry=entries[i % n_entries], sigma_angstrom=3.0 + i) for i in range(5)]
    skew(db_session, rows, ROW_SPECS)
    url = (
        f"{API}/transport/search?species_ref={species.public_ref}"
        if search
        else f"{API}/species-entries/{entries[0].public_ref}/transport"
    )
    return Seeded(url, rows, finder("transport_ref"), [(SubmissionRecordType.transport, r.id) for r in rows])


def seed_statmech(db_session, *, search: bool) -> Seeded:
    species, entries = _entries(db_session, prefix="OS")
    n_entries = 2 if search else 1
    rows = [make_statmech(db_session, species_entry=entries[i % n_entries]) for i in range(5)]
    skew(db_session, rows, ROW_SPECS)
    url = (
        f"{API}/statmech/search?species_ref={species.public_ref}"
        if search
        else f"{API}/species-entries/{entries[0].public_ref}/statmech"
    )
    return Seeded(url, rows, finder("statmech_ref"), [(SubmissionRecordType.statmech, r.id) for r in rows])


def _reaction_entries(db_session, n=5, prefix="OK"):
    """``n`` reaction entries of one reaction, each with its own reactant entry."""
    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key(prefix + "R"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key(prefix + "P"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    product_entry = make_species_entry(db_session, ps)
    entries = [
        make_reaction_entry(
            db_session,
            reaction=chem,
            reactant_entries=[make_species_entry(db_session, rs, stereo_label=f"r{i}")],
            product_entries=[product_entry],
        )
        for i in range(n)
    ]
    return chem, entries


def seed_kinetics(db_session, *, search: bool) -> Seeded:
    _chem, entries = _reaction_entries(db_session, n=2, prefix="OKN")
    n_entries = 2 if search else 1
    rows = [make_kinetics(db_session, reaction_entry=entries[i % n_entries], a=1e-12 * (i + 1)) for i in range(5)]
    skew(db_session, rows, ROW_SPECS)
    url = (
        f"{API}/kinetics/search?reactants=CC"
        if search
        else f"{API}/reaction-entries/{entries[0].public_ref}/kinetics"
    )
    return Seeded(url, rows, finder("kinetics_ref"), [(SubmissionRecordType.kinetics, r.id) for r in rows])


def seed_reactions(db_session, *, browse: bool) -> Seeded:
    _chem, entries = _reaction_entries(db_session, n=5, prefix="ORX")
    skew(db_session, entries, ROW_SPECS)
    url = f"{API}/reactions/browse" if browse else f"{API}/reactions/search?reactants=CC"
    return Seeded(
        url, entries, finder("reaction_entry_ref"), [(SubmissionRecordType.reaction_entry, e.id) for e in entries]
    )


def seed_species(db_session, *, browse: bool) -> Seeded:
    """Five species of one formula, one entry each. A species ranks by its best visible entry, so the review goes on
    the entry; its time and id are the species' own."""
    species = [make_species(db_session, smiles=s, inchi_key=next_inchi_key("OSP")) for s in _ISOMERS]
    entries = [make_species_entry(db_session, sp) for sp in species]
    skew(db_session, species, ROW_SPECS)
    url = f"{API}/species/browse?formula=C4H10O" if browse else f"{API}/species/search?formula=C4H10O"
    return Seeded(url, species, finder("species_ref"), [(SubmissionRecordType.species_entry, e.id) for e in entries])




EXTRA_CASES: dict[str, Callable[[Any], Seeded]] = {}


CASES: dict[str, Callable[[Any], Seeded]] = {
    "thermo-entry": lambda db: seed_thermo(db, search=False),
    "thermo-search": lambda db: seed_thermo(db, search=True),
    "transport-entry": lambda db: seed_transport(db, search=False),
    "transport-search": lambda db: seed_transport(db, search=True),
    "statmech-entry": lambda db: seed_statmech(db, search=False),
    "statmech-search": lambda db: seed_statmech(db, search=True),
    "kinetics-entry": lambda db: seed_kinetics(db, search=False),
    "kinetics-search": lambda db: seed_kinetics(db, search=True),
    "reactions-search": lambda db: seed_reactions(db, browse=False),
    "reactions-browse": lambda db: seed_reactions(db, browse=True),
    "species-search": lambda db: seed_species(db, browse=False),
    "species-browse": lambda db: seed_species(db, browse=True),
    **EXTRA_CASES,
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_echoed_order_is_the_served_order(case, client, db_session):
    """Kills: an echo that names keys (or an order of keys) the query does not apply."""
    seeded = CASES[case](db_session)
    review(db_session, seeded)
    check_echo_matches_served_order(client, seeded)
