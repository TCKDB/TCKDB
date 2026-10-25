"""``evidence_summary.software`` on a transition-state search page is deterministic and follows the review floor.

Two defects this pins. The grouped query is ``SELECT DISTINCT`` without ``ORDER BY``, so the calculation-type keys of the
map were emitted in the order rows happened to arrive, and two identical requests could differ byte for byte. And the
query had no visibility predicate, so under the curated profile a calculation below the floor still put its type and its
software into the map (owner decision D5).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, CalculationType, RecordReviewStatus, SubmissionRecordType
from app.services.scientific_read import levels_of_theory
from app.services.scientific_read.transition_states_search import _software_index_for_entries
from tests.api.scientific.test_levels_of_theory_on_evidence_summary import _ts_entry
from tests.services.scientific_read._factories import make_calculation, make_software_release, set_review

SEARCH = "/api/v1/scientific/transition-states/search"


class _Rows:
    """A session whose ``execute`` returns the given rows in the given order, whatever the statement."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        return iter(self._rows)


def _row(owner, type_, release_id, name):
    return SimpleNamespace(
        owner_id=owner, type=type_, id=release_id, public_ref=f"swr_{release_id}", version="1", name=name
    )


def test_the_software_map_is_the_same_whatever_order_the_rows_arrive_in():
    """Kills: building the map in row order, so identical requests can differ byte for byte."""
    rows = [
        _row(1, CalculationType.sp, 3, "molpro"),
        _row(1, CalculationType.opt, 1, "gaussian"),
        _row(1, CalculationType.freq, 2, "orca"),
        _row(1, CalculationType.irc, 4, "gaussian"),
        _row(1, CalculationType.opt, 5, "orca"),
    ]
    forward = _software_index_for_entries(_Rows(rows), [1])
    backward = _software_index_for_entries(_Rows(list(reversed(rows))), [1])
    shuffled = _software_index_for_entries(_Rows([rows[3], rows[0], rows[4], rows[2], rows[1]]), [1])

    def wire(index):
        return json.dumps({k: [s.model_dump() for s in v] for k, v in index[1].items()})

    assert wire(forward) == wire(backward) == wire(shuffled)
    assert list(forward[1]) == levels_of_theory.ordered_calculation_types(forward[1])
    assert list(forward[1])[:2] == ["opt", "freq"], "declaration order: optimise, then frequencies"


def test_an_unknown_calculation_type_is_emitted_after_the_known_ones():
    rows = [_row(1, "zzz_unlisted", 7, "orca"), _row(1, CalculationType.sp, 3, "molpro")]
    assert list(_software_index_for_entries(_Rows(rows), [1])[1]) == ["sp", "zzz_unlisted"]


def _world(db_session, sp_status=None):
    _, (entry,) = _ts_entry(db_session)
    shown = make_software_release(db_session, name="gaussian", version="16")
    other = make_software_release(db_session, name="orca", version="6")
    opt = make_calculation(
        db_session, type=CalculationType.opt, transition_state_entry_id=entry.id, software_release_id=shown.id
    )
    sp = make_calculation(
        db_session, type=CalculationType.sp, transition_state_entry_id=entry.id, software_release_id=other.id
    )
    set_review(
        db_session, record_type=SubmissionRecordType.calculation, record_id=opt.id, status=RecordReviewStatus.approved
    )
    if sp_status is not None:
        set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=sp.id, status=sp_status)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entry.id,
        status=RecordReviewStatus.approved,
    )
    return entry, shown, other


def _software(client, entry, profile):
    body = client.get(SEARCH, params={"transition_state_entry_ref": entry.public_ref, "profile": profile}).json()
    assert body["records"], f"{profile}: the search returned nothing"
    return body["records"][0]["evidence_summary"]["software"]


@pytest.mark.parametrize(
    "sp_status,shown",
    [
        (None, True),
        (RecordReviewStatus.under_review, True),
        (RecordReviewStatus.rejected, False),
        (RecordReviewStatus.deprecated, False),
    ],
    ids=["not_reviewed", "under_review", "rejected", "deprecated"],
)
def test_the_software_map_follows_the_evidence_rule(client, db_session, login_as, sp_status, shown):
    """Kills: a rejected/deprecated calculation naming its type and software under curated; a not-reviewed one withheld."""
    entry, _, _ = _world(db_session, sp_status)
    curator = AppUser(username="ts-software-curator", role=AppUserRole.curator)
    db_session.add(curator)
    db_session.flush()
    login_as(curator.id)

    curated = _software(client, entry, "curated")
    assert [s["software"] for s in curated["opt"]] == ["gaussian"]
    if shown:
        assert set(curated) == {"opt", "sp"}
        assert [s["software"] for s in curated["sp"]] == ["orca"]
    else:
        assert set(curated) == {"opt"}
        assert "orca" not in json.dumps(curated) and "sp" not in curated

    exploratory = _software(client, entry, "exploratory")
    assert set(exploratory) == {"opt", "sp"}
    assert [s["software"] for s in exploratory["sp"]] == ["orca"]
