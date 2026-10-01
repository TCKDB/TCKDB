"""The workflow half of ``scf_stability.source_calculation_key``.

The request schemas refuse an undeclared, self-referencing or cross-owner key
before any workflow runs, and answer with the same code the workflow would
(ADR 0017), so an HTTP test cannot tell the layers apart. The schema lives in
another package and has drifted from its workflows before, so this pins the
service on its own: each refusal below is provoked with no schema in front.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.fragments.calculation import SCFStabilityContent

from app.api.error_contract import CodedValueError
from app.db.models.calculation import CalculationSCFStability
from app.db.models.common import CalculationType, SCFStabilityStatus
from app.services.scf_stability_sources import link_scf_stability_sources
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _entry(db_session, prefix: str):
    species = make_species(db_session, inchi_key=next_inchi_key(prefix))
    return make_species_entry(db_session, species)


def _stability_row(db_session, calc_id: int) -> CalculationSCFStability:
    row = CalculationSCFStability(
        calculation_id=calc_id, status=SCFStabilityStatus.stable
    )
    db_session.add(row)
    db_session.flush()
    return row


def _block(key: str | None) -> SCFStabilityContent:
    return SCFStabilityContent(status="stable", source_calculation_key=key)


def test_links_the_named_job_when_both_belong_to_one_entry(db_session):
    entry = _entry(db_session, "SCFA")
    opt = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    sp = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    row = _stability_row(db_session, opt.id)

    linked = link_scf_stability_sources(
        db_session, [("opt", _block("sp"))], {"opt": opt, "sp": sp}
    )

    assert linked == 1
    assert row.source_calculation_id == sp.id


def test_a_block_without_a_key_is_left_alone(db_session):
    entry = _entry(db_session, "SCFB")
    opt = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    row = _stability_row(db_session, opt.id)

    linked = link_scf_stability_sources(
        db_session, [("opt", _block(None)), ("opt", None)], {"opt": opt}
    )

    assert linked == 0
    assert row.source_calculation_id is None


def test_an_undeclared_key_is_a_coded_refusal_not_a_keyerror(db_session):
    entry = _entry(db_session, "SCFC")
    opt = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    _stability_row(db_session, opt.id)

    with pytest.raises(CodedValueError) as err:
        link_scf_stability_sources(
            db_session, [("opt", _block("ghost"))], {"opt": opt}
        )
    assert err.value.code == "calculation_key_undeclared"
    assert "ghost" in str(err.value)


def test_a_job_owned_by_another_entry_is_refused(db_session):
    mine = _entry(db_session, "SCFD")
    other = _entry(db_session, "SCFE")
    opt = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=mine.id
    )
    foreign = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=other.id
    )
    row = _stability_row(db_session, opt.id)

    with pytest.raises(CodedValueError) as err:
        link_scf_stability_sources(
            db_session,
            [("opt", _block("foreign"))],
            {"opt": opt, "foreign": foreign},
        )
    assert err.value.code == "scf_stability_source_calculation_owner_mismatch"
    assert row.source_calculation_id is None


def test_a_missing_row_is_a_defect_not_a_silent_skip(db_session):
    """The block was declared, so its row must exist.

    Silence here would hide a dropped write behind a green upload.
    """
    entry = _entry(db_session, "SCFF")
    opt = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    sp = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    with pytest.raises(RuntimeError, match="no calc_scf_stability row"):
        link_scf_stability_sources(
            db_session, [("opt", _block("sp"))], {"opt": opt, "sp": sp}
        )
