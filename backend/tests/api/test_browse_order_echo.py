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

from app.db.models.common import (
    CalculationType,
    EnergyCorrectionSchemeKind,
    FrequencyScaleKind,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkStateKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.schemas.reads import scientific_common
from app.schemas.reads.scientific_common import ORDER_KEY_CATALOG, REVIEW_RANK
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_network_state_participant,
    attach_sp_result,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_energy_correction_scheme,
    make_frequency_scale_factor,
    make_kinetics,
    make_literature,
    make_lot,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
    make_observation,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
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
#   2    under_review     3   NEWER than the unreviewed rows 3 and 4. It ranks equal to them (a curator opening a record
#                             is not a verdict), so it sorts by time among them; an order that ranked under_review
#                             below not_reviewed would push it last, and that is what this row detects
#   3    not_reviewed     5   same time as row 4: only the id breaks the tie
#   4    not_reviewed     5   largest id
#
# Display order is review rank, created_at DESC, id DESC: 1, 0, 2, 4, 3.
ROW_SPECS = [
    (RecordReviewStatus.not_reviewed, 1),
    (RecordReviewStatus.approved, 30),
    (RecordReviewStatus.under_review, 3),
    (RecordReviewStatus.not_reviewed, 5),
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
    #: Public ref of each row when the row has none of its own (an artifact is named by its calculation).
    row_refs: list[str] | None = None
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
    for row, (_status, age_days) in zip(rows, specs, strict=True):
        stamp = _T0 - timedelta(days=age_days)
        if set_created is not None:
            set_created(row, stamp)
        else:
            row.created_at = stamp
    db_session.flush()


def review(db_session, seeded: Seeded) -> None:
    for (record_type, record_id), (status, _age) in zip(seeded.review_targets, seeded.specs, strict=True):
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


def _order_by(seeded: Seeded, keys: list[tuple[str, str]], *, ties_wrong_way: str | None = None) -> list[int]:
    """Row indexes ordered by ``keys`` (name, direction), the way the echo says. A null sorts last.

    ``ties_wrong_way`` is the echo's own ``id`` direction: rows the keys leave tied are then left in the OPPOSITE
    order, so ordering by one key alone cannot coincide with the served order by luck of the creation order."""
    order = list(range(len(seeded.rows)))
    if ties_wrong_way == "desc":
        pass  # index ascending is the id-ascending order: the wrong way for a descending id
    elif ties_wrong_way == "asc":
        order.reverse()
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
    row_refs = seeded.row_refs or [row.public_ref for row in seeded.rows]
    refs = {ref: i for i, ref in enumerate(row_refs)}
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
    id_direction = dict(echoed).get("id")
    for key, direction in echoed:
        if key in ("review_rank", "created_at", "id"):
            assert _order_by(seeded, [(key, direction)], ties_wrong_way=id_direction) != served, (
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




def _entry(db_session, prefix):
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key(prefix))
    return species, make_species_entry(db_session, species)


def seed_calculations(db_session, *, route: str, ranking: str | None = None) -> Seeded:
    _species, entry = _entry(db_session, "OC")
    lot = make_lot(db_session) if ranking == "lowest_energy" else None
    calcs = [
        make_calculation(
            db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id if lot else None
        )
        for _ in range(5)
    ]
    skew(db_session, calcs, ROW_SPECS)
    extra: dict = {}
    if ranking == "lowest_energy":
        energies = [-2.0, -4.0, None, -1.0, -3.0]  # no energy on row 2: it sorts last
        for calc, energy in zip(calcs, energies, strict=True):
            if energy is not None:
                attach_sp_result(db_session, calculation=calc, electronic_energy_hartree=energy)
        extra["energy_hartree"] = lambda i: energies[i]
    if route == "calculations":
        url = f"{API}/calculations/search?species_entry_ref={entry.public_ref}"
    else:
        url = f"{API}/species-calculations/search?species_entry_ref={entry.public_ref}"
        if ranking:
            url += f"&ranking={ranking}"
        if ranking == "lowest_energy":
            url += f"&calculation_type=sp&level_of_theory_ref={lot.public_ref}"
    return Seeded(
        url, calcs, finder("calculation_ref"), [(SubmissionRecordType.calculation, c.id) for c in calcs], extra=extra
    )


def seed_conformers(db_session) -> Seeded:
    _species, entry = _entry(db_session, "OCG")
    groups = [make_conformer_group(db_session, entry, label=f"g{i}") for i in range(5)]
    skew(db_session, groups, ROW_SPECS)
    return Seeded(
        f"{API}/conformers/search?species_entry_ref={entry.public_ref}",
        groups,
        finder("conformer_group_ref"),
        [(SubmissionRecordType.conformer_group, g.id) for g in groups],
    )


def seed_transition_states(db_session, *, browse: bool) -> Seeded:
    _chem, reaction_entries = _reaction_entries(db_session, n=1, prefix="OTS")
    reaction_entry = reaction_entries[0]
    tses = [
        make_transition_state_entry(
            db_session, transition_state=make_transition_state(db_session, reaction_entry=reaction_entry, label=f"t{i}")
        )
        for i in range(5)
    ]
    skew(db_session, tses, ROW_SPECS)
    url = (
        f"{API}/transition-states/browse"
        if browse
        else f"{API}/transition-states/search?reaction_entry_ref={reaction_entry.public_ref}"
    )
    return Seeded(
        url,
        tses,
        finder("transition_state_entry_ref"),
        [(SubmissionRecordType.transition_state_entry, t.id) for t in tses],
    )


def seed_networks(db_session, *, browse: bool) -> Seeded:
    networks = [make_network(db_session, name=f"net-{i}-{next_inchi_key('ON')}") for i in range(5)]
    skew(db_session, networks, ROW_SPECS)
    url = f"{API}/networks/browse" if browse else f"{API}/networks/search?has_solves=false"
    return Seeded(url, networks, finder("network_ref"), [(SubmissionRecordType.network, n.id) for n in networks])


def seed_network_solves(db_session) -> Seeded:
    network = make_network(db_session, name=f"net-{next_inchi_key('ONS')}")
    solves = [make_network_solve(db_session, network=network) for _ in range(5)]
    skew(db_session, solves, ROW_SPECS)
    return Seeded(
        f"{API}/network-solves/search?network_ref={network.public_ref}",
        solves,
        finder("network_solve_ref"),
        [(SubmissionRecordType.network_solve, s.id) for s in solves],
    )


def seed_network_kinetics(db_session) -> Seeded:
    """A network kinetics row has no review row of its own: it inherits its solve's. Each row sits under its own solve,
    so the five solves carry the five statuses."""
    network = make_network(db_session, name=f"net-{next_inchi_key('ONK')}")
    species_entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("ONKS")))
    states = []
    for label in ("a", "b"):
        state = make_network_state(
            db_session, network=network, kind=NetworkStateKind.well, composition_hash=f"{label}{'0' * 63}"
        )
        attach_network_state_participant(db_session, state=state, species_entry=species_entry)
        states.append(state)
    channel = make_network_channel(
        db_session, network=network, source_state=states[0], sink_state=states[1], kind=NetworkChannelKind.isomerization
    )
    solves = [make_network_solve(db_session, network=network) for _ in range(5)]
    rows = [
        make_network_kinetics(db_session, channel=channel, solve=s, model_kind=NetworkKineticsModelKind.plog)
        for s in solves
    ]
    skew(db_session, rows, ROW_SPECS)
    return Seeded(
        f"{API}/network-kinetics/search?network_ref={network.public_ref}",
        rows,
        finder("network_kinetics_ref"),
        [(SubmissionRecordType.network_solve, s.id) for s in solves],
    )


def seed_observations(db_session) -> Seeded:
    _species, entry = _entry(db_session, "OO")
    rows = [make_observation(db_session, species_entry=entry, scalar_value=1.0 + i) for i in range(5)]
    skew(db_session, rows, ROW_SPECS)
    return Seeded(
        f"{API}/species-entries/{entry.public_ref}/observations",
        rows,
        finder("observation_ref"),
        [(SubmissionRecordType.molecular_property_observation, o.id) for o in rows],
    )


def seed_artifacts(db_session) -> Seeded:
    """One artifact per calculation. The artifact orders by its calculation's review, its own time and its own id."""
    _species, entry = _entry(db_session, "OA")
    calcs = [make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id) for _ in range(5)]
    artifacts = [
        attach_artifact(db_session, calculation=c, filename=f"o{i}.log", sha256=f"{i:x}" * 64)
        for i, c in enumerate(calcs)
    ]
    skew(db_session, artifacts, ROW_SPECS)
    return Seeded(
        f"{API}/artifacts/search?species_entry_ref={entry.public_ref}",
        artifacts,
        finder("calculation_ref"),
        [(SubmissionRecordType.calculation, c.id) for c in calcs],
        extra={
            "artifact_created_at": lambda i: artifacts[i].created_at.timestamp(),
            "artifact_id": lambda i: artifacts[i].id,
        },
        row_refs=[c.public_ref for c in calcs],
    )


EXTRA_CASES: dict[str, Callable[[Any], Seeded]] = {
    "calculations-search": lambda db: seed_calculations(db, route="calculations"),
    "species-calculations-default": lambda db: seed_calculations(db, route="species"),
    "species-calculations-latest": lambda db: seed_calculations(db, route="species", ranking="latest"),
    "species-calculations-earliest": lambda db: seed_calculations(db, route="species", ranking="earliest"),
    "species-calculations-lowest-energy": lambda db: seed_calculations(db, route="species", ranking="lowest_energy"),
    "conformers-search": seed_conformers,
    "transition-states-search": lambda db: seed_transition_states(db, browse=False),
    "transition-states-browse": lambda db: seed_transition_states(db, browse=True),
    "networks-search": lambda db: seed_networks(db, browse=False),
    "networks-browse": lambda db: seed_networks(db, browse=True),
    "network-solves-search": seed_network_solves,
    "network-kinetics-search": seed_network_kinetics,
    "observations": seed_observations,
    "artifacts-search": seed_artifacts,
}


# --- reference lists and structure search: keys that are values of the record, not review state ----------------------


def _used(db_session, lot) -> None:
    """A level of theory is listed once a calculation uses it (usage-derived gate)."""
    _species, entry = _entry(db_session, f"OU{lot.id}")
    make_calculation(db_session, species_entry_id=entry.id, lot_id=lot.id)


#: Reference lists have no review state: every row is not_reviewed and review decides nothing.
_UNREVIEWED = [(RecordReviewStatus.not_reviewed, 0)] * 5


def seed_levels_of_theory(db_session, *, browse: bool) -> Seeded:
    # Creation order disagrees with (method, basis); ties on both are separated by the id alone.
    if browse:
        methods = ["pbe", "b3lyp", "pbe", "b3lyp", "b3lyp"]
        solvents = [None, None, None, None, "water"]  # row 4 repeats row 1's (method, basis)
    else:
        methods = ["m"] * 5  # ``search`` needs a filter, so one method throughout
        solvents = [f"s{i}" for i in range(5)]
    bases = ["def2", "6-31g", "cc", "def2", "6-31g"]
    lots = [make_lot(db_session, method=m, basis=b, solvent=s) for m, b, s in zip(methods, bases, solvents, strict=True)]
    for lot in lots:
        _used(db_session, lot)
    url = f"{API}/level-of-theories/browse" if browse else f"{API}/level-of-theories/search?method=m"
    return Seeded(
        url,
        lots,
        finder("level_of_theory_ref"),
        [(SubmissionRecordType.calculation, 0)] * 5,
        extra={"method": lambda i: methods[i], "basis": lambda i: bases[i]},
        specs=_UNREVIEWED,
    )


def seed_frequency_scale_factors(db_session) -> Seeded:
    kinds = [FrequencyScaleKind.zpe, FrequencyScaleKind.fundamental, FrequencyScaleKind.zpe,
             FrequencyScaleKind.fundamental, FrequencyScaleKind.fundamental]
    values = [0.97, 0.99, 0.95, 0.98, 0.99]  # rows 1 and 4: same kind and value; only the id separates them
    rows = []
    for i, (kind, value) in enumerate(zip(kinds, values, strict=True)):
        lot = make_lot(db_session, method="fsf", basis=f"b{i}")
        rows.append(make_frequency_scale_factor(db_session, lot=lot, scale_kind=kind, value=value))
    return Seeded(
        f"{API}/frequency-scale-factors/search?method=fsf",
        rows,
        finder("frequency_scale_factor_ref"),
        [(SubmissionRecordType.calculation, 0)] * 5,
        extra={"scale_kind": lambda i: kinds[i].value, "value": lambda i: values[i]},
        specs=_UNREVIEWED,
    )


def seed_energy_correction_schemes(db_session) -> Seeded:
    kinds = [EnergyCorrectionSchemeKind.soc, EnergyCorrectionSchemeKind.atom_energy, EnergyCorrectionSchemeKind.soc,
             EnergyCorrectionSchemeKind.atom_energy, EnergyCorrectionSchemeKind.atom_energy]
    names = ["z", "y", "a", "x", "y"]  # rows 1 and 4 share kind and name: only the id separates them
    rows = [
        make_energy_correction_scheme(db_session, name=n, kind=k, lot=make_lot(db_session, method="ecs", basis=f"b{i}"))
        for i, (n, k) in enumerate(zip(names, kinds, strict=True))
    ]
    return Seeded(
        f"{API}/energy-correction-schemes/search?method=ecs",
        rows,
        finder("energy_correction_scheme_ref"),
        [(SubmissionRecordType.calculation, 0)] * 5,
        extra={"scheme_kind": lambda i: kinds[i].value, "name": lambda i: names[i]},
        specs=_UNREVIEWED,
    )


def seed_structure_search(db_session, *, mode: str) -> Seeded:
    """Five entries of one species (all equally similar to the query): the entry id and the review rank decide."""
    species = make_species(db_session, smiles="CCO", inchi_key="LFQSCWFLJHTTHZ-UHFFFAOYSA-N")  # ethanol's own key: exact matches on it
    entries = [make_species_entry(db_session, species, stereo_label=f"e{i}") for i in range(5)]
    query = {"exact": "query_smiles=CCO&mode=exact", "substructure": "query_smiles=CC&mode=substructure",
             "similarity": "query_smiles=CCO&mode=similarity"}[mode]
    return Seeded(
        f"{API}/species/structure-search?{query}",
        entries,
        finder("species_entry_ref"),
        [(SubmissionRecordType.species_entry, e.id) for e in entries],
        extra={"species_entry_id": lambda i: entries[i].id, "similarity_score": lambda i: 1.0},
    )


EXTRA_CASES.update(
    {
        "levels-of-theory-browse": lambda db: seed_levels_of_theory(db, browse=True),
        "levels-of-theory-search": lambda db: seed_levels_of_theory(db, browse=False),
        "frequency-scale-factors-search": seed_frequency_scale_factors,
        "energy-correction-schemes-search": seed_energy_correction_schemes,
        "structure-search-exact": lambda db: seed_structure_search(db, mode="exact"),
        "structure-search-substructure": lambda db: seed_structure_search(db, mode="substructure"),
        "structure-search-similarity": lambda db: seed_structure_search(db, mode="similarity"),
    }
)


def seed_literature_records(db_session) -> Seeded:
    """Records citing one paper: ordered by kind, then newest, then id. Two kinds, interleaved in creation order, with a
    same-time pair inside one kind so only the id can separate it."""
    literature = make_literature(db_session)
    _species, entry = _entry(db_session, "OL")
    kinds = ["statmech", "transport", "statmech", "transport", "statmech"]
    rows = [
        make_statmech(db_session, species_entry=entry, literature_id=literature.id)
        if kind == "statmech"
        else make_transport(db_session, species_entry=entry, literature_id=literature.id)
        for kind in kinds
    ]
    ages = [3, 9, 10, 1, 10]
    specs = [(RecordReviewStatus.not_reviewed, a) for a in ages]
    skew(db_session, rows, specs)
    return Seeded(
        f"{API}/literature/{literature.public_ref}/records",
        rows,
        finder("record_ref"),
        [(SubmissionRecordType.calculation, 0)] * 5,
        extra={"record_type": lambda i: kinds[i]},
        specs=specs,
    )


EXTRA_CASES["literature-records"] = seed_literature_records


def _analytics(seed: Callable[..., Seeded], path: str, **kwargs) -> Callable[[Any], Seeded]:
    """The analytics read lists the same records, over the same keys, behind a keyset cursor."""

    def build(db_session) -> Seeded:
        seeded = seed(db_session, **kwargs)
        seeded.url = f"{API}/analytics/{path}"
        return seeded

    return build


EXTRA_CASES.update(
    {
        "analytics-thermo": _analytics(seed_thermo, "thermo", search=True),
        "analytics-kinetics": _analytics(seed_kinetics, "kinetics", search=True),
        "analytics-statmech": _analytics(seed_statmech, "statmech", search=True),
        "analytics-calculations": _analytics(seed_calculations, "calculations", route="calculations"),
    }
)


# --- sub-lists ordered by what they list: path points and integrity records --------------------------------------------


def _echoed_point_order_is_served(body: dict, points_key: str = "points") -> None:
    echoed = _keys(body["request"]["sort"])
    assert echoed == [("point_index", "asc")], echoed
    served = [p["point_index"] for p in body[points_key]]
    assert served == sorted(served, reverse=False)
    assert len(served) >= 3


def test_scan_points_are_served_in_the_echoed_order(client, db_session):
    """Points are inserted out of order, so only a real ORDER BY on the echoed key can produce the served order."""
    from tests.api.scientific import test_api_scientific_calculation_paths as paths

    calc = paths._make_scan_calc(db_session)
    for idx in (3, 1, 2):
        paths._attach_scan_point(db_session, calculation=calc, point_index=idx, electronic_energy_hartree=-99.0 - idx)
    body = client.get(paths._scan_url(calc.public_ref)).json()
    _echoed_point_order_is_served(body)


def test_irc_points_are_served_in_the_echoed_order(client, db_session):
    from app.db.models.common import IRCDirection
    from tests.api.scientific import test_api_scientific_calculation_paths as paths

    calc = paths._make_irc_calc(db_session, direction=IRCDirection.forward)
    for idx in (3, 1, 2):
        paths._attach_irc_point(
            db_session, calculation=calc, point_index=idx, direction=IRCDirection.forward,
            electronic_energy_hartree=-99.0 - idx,
        )
    body = client.get(paths._irc_url(calc.public_ref)).json()
    _echoed_point_order_is_served(body)


def test_path_search_points_are_served_in_the_echoed_order(client, db_session):
    from tests.api.scientific import test_api_scientific_calculation_paths as paths

    calc = paths._make_path_search_calc(db_session)
    for idx, energy in [(2, -99.0), (0, -100.0), (1, -99.5)]:
        paths._attach_path_search_point(
            db_session, calculation=calc, point_index=idx, electronic_energy_hartree=energy
        )
    body = client.get(paths._path_search_url(calc.public_ref)).json()
    _echoed_point_order_is_served(body)


def test_artifact_integrity_records_are_served_in_the_echoed_order(client, db_session, login_as, _api_curator_user):
    """Most recently observed first, then digest. Observation times are set so that time, creation order and digest
    all disagree, with a tied pair that only the digest separates."""
    from sqlalchemy import update

    from app.db.models.calculation import ArtifactIntegrityEvent
    from app.db.models.common import ArtifactIntegrityDetectionContext, ArtifactIntegrityFinding
    from tests.api.scientific import test_api_scientific_artifact_integrity as integrity

    ages = [1, 30, 10, 10, 5]
    tokens = ["e", "a", "d", "b", "c"]
    shas = []
    for token, age in zip(tokens, ages, strict=True):
        _calc, artifact, sha = integrity._broken_artifact(db_session, token)
        integrity._record(
            db_session, sha, ArtifactIntegrityFinding.digest_mismatch, ArtifactIntegrityDetectionContext.download,
            artifact_id=artifact.id,
        )
        db_session.execute(
            update(ArtifactIntegrityEvent).where(ArtifactIntegrityEvent.sha256 == sha).values(created_at=_T0 - timedelta(days=age))
        )
        shas.append(sha)
    db_session.flush()
    login_as(_api_curator_user)

    body = client.get(integrity.LIST_URL).json()
    echoed = _keys(body["request"]["sort"])
    assert echoed == [("last_observed_at", "desc"), ("sha256", "asc")]
    served = [r["sha256"] for r in body["records"] if r["sha256"] in shas]
    by_age = dict(zip(shas, ages, strict=True))
    expected = sorted(shas, key=lambda s: (by_age[s], s))  # newest first (smaller age), then digest ascending
    assert served == expected
    # Time alone, with the tied pair left in the WRONG digest order, must not give the served order.
    assert served != sorted(sorted(shas, reverse=True), key=lambda s: by_age[s])
    assert served != sorted(shas)


# --- an order the request asked for says so --------------------------------------------------------------------------


def test_a_substructure_browse_leads_with_size_and_says_the_request_chose_it(client, db_session):
    """Kills: echoing the review-first order while the query leads with ``heavy_atom_count ASC``."""
    small = make_species(db_session, smiles="CCO", inchi_key=next_inchi_key("BOE"))  # created first
    mid = make_species(db_session, smiles="CCCO", inchi_key=next_inchi_key("BOE"))
    large = make_species(db_session, smiles="CCCCO", inchi_key=next_inchi_key("BOE"))
    for sp in (small, mid, large):
        make_species_entry(db_session, sp)

    body = client.get(f"{API}/species/browse?query_smiles=CCO&mode=substructure").json()

    assert [r["species_ref"] for r in body["records"]] == [small.public_ref, mid.public_ref, large.public_ref]
    assert [k for k, _ in _keys(body["request"]["sort"])] == ["heavy_atom_count", "review_rank", "created_at", "id"]
    assert body["request"]["sort"]["requested_by"] == "mode=substructure"


def test_a_browse_without_a_structure_query_has_no_size_key_and_no_requested_by(client, db_session):
    make_species_entry(db_session, make_species(db_session, smiles="CCO", inchi_key=next_inchi_key("BOE")))
    sort = client.get(f"{API}/species/browse").json()["request"]["sort"]
    assert "heavy_atom_count" not in [k for k, _ in _keys(sort)]
    assert sort["requested_by"] is None


@pytest.mark.parametrize("ranking", ["latest", "earliest", "lowest_energy"])
def test_a_ranking_the_request_names_is_labelled_in_the_echo(ranking, client, db_session):
    seeded = seed_calculations(db_session, route="species", ranking=ranking)
    sort = client.get(seeded.url).json()["request"]["sort"]
    assert sort["requested_by"] == f"ranking={ranking}"


def test_the_default_ranking_is_not_labelled(client, db_session):
    seeded = seed_calculations(db_session, route="species")
    assert client.get(seeded.url).json()["request"]["sort"]["requested_by"] is None


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
