"""D8: a wide conformer basin reaches a curator through the Phase D advisory mechanism.

A chain of optimised structures can be linked end to end while its ends are further apart than the tolerance. The
basin is reported, never split. This is the check that reports it: explicit invocation only, one append-only
``record_machine_review`` row, no effect on status, selection, trust or approval.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.species import ConformerGroup, ConformerObservation
from app.services.consistency.service import invoke
from app.services.consistency.wide_basin import (
    ALL_DEFINING_MEMBERS_WITHIN_TOLERANCE,
    NO_DEFINING_MEMBER_NOT_JUDGED,
    WIDE_BASIN,
)
from tests.services.scientific_read._factories import (
    make_conformer_group,
    make_species,
    make_species_entry,
    next_inchi_key,
)
from tests.services.test_conformer_basin_linkage import _BUTANE, butane, deposit_observations


def _reasons(result) -> list[str]:
    return [json.loads(f.message)["reason"] for f in result.findings]


def _group(db_session, **shape):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("WIDE")))
    group = make_conformer_group(db_session, entry)
    for key, value in shape.items():
        setattr(group, key, value)
    db_session.flush()
    return group


def test_a_group_that_is_not_a_clique_carries_a_wide_basin_finding_with_its_span(db_session) -> None:
    group = _group(db_session, basin_is_clique=False, basin_span_deg=36.0)
    result, row = invoke(db_session, check="wide-basin", target_ref=group.public_ref, commit=True)
    assert _reasons(result) == [WIDE_BASIN]
    assert json.loads(result.findings[0].message)["basin_span_deg"] == 36.0
    assert group.public_ref in result.findings[0].evidence_keys
    stored = db_session.get(RecordMachineReviewRow, row.id)
    assert stored.record_type.value == "conformer_group"
    assert stored.provider == "tckdb.scientific_checks"
    assert (stored.model, stored.rubric_name, stored.rubric_version) == (
        "wide_basin_consistency", "wide_basin_consistency", 1,
    )


def test_a_clique_is_silent_and_a_group_with_no_defining_member_is_not_judged(db_session) -> None:
    clique = _group(db_session, basin_is_clique=True, basin_span_deg=12.0)
    assert _reasons(invoke(db_session, check="wide-basin", target_ref=clique.public_ref)[0]) == [
        ALL_DEFINING_MEMBERS_WITHIN_TOLERANCE
    ]
    provisional = _group(db_session)
    assert _reasons(invoke(db_session, check="wide-basin", target_ref=provisional.public_ref)[0]) == [
        NO_DEFINING_MEMBER_NOT_JUDGED
    ]


def test_the_check_refuses_a_reference_that_is_not_a_group(db_session) -> None:
    with pytest.raises(ValueError, match="cg_"):
        invoke(db_session, check="wide-basin", target_ref="thm_nope")
    with pytest.raises(ValueError, match="alone"):
        invoke(db_session, check="wide-basin", target_ref="cg_x", temperature_grid=(300.0,))


def test_a_chain_deposited_through_the_upload_path_is_reported_by_the_check(db_conn) -> None:
    with Session(db_conn) as session:
        session.begin()
        try:
            deposit_observations(session, _BUTANE, [butane("A", 0.0), butane("B", 18.0), butane("C", 36.0)])
            group = session.scalars(
                select(ConformerGroup).join(
                    ConformerObservation, ConformerObservation.conformer_group_id == ConformerGroup.id
                )
            ).first()
            result, _ = invoke(session, check="wide-basin", target_ref=group.public_ref)
            assert _reasons(result) == [WIDE_BASIN]
            assert json.loads(result.findings[0].message)["basin_span_deg"] == pytest.approx(36.0, abs=1.0)
        finally:
            session.rollback()
