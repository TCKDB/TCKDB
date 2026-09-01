"""Tests for the frequency-scale-factor / freq-calculation software cross-check.

A harmonic frequency scale factor is specific to a level of theory AND to
the electronic-structure software the factor was fit against -- the same
level of theory in Gaussian vs ORCA can legitimately need a different
factor (``frequency_scale_factor.software_id``'s column comment has said so
since the initial schema). Nothing previously checked that the software a
factor was DERIVED FOR is the software that actually produced the
frequencies it was APPLIED TO. Measured against the deployed archive: 95
statmech rows compare cleanly today (Gaussian/Gaussian, ORCA/ORCA) and 6
carry a scale factor with no ``freq``-role source calculation to compare
against at all -- agreement so far is a fact about who happened to deposit,
not something anything verified.

Exercised here directly against
:func:`app.services.statmech_resolution.evaluate_frequency_scale_factor_software`
(the per-statmech classifier) and
:func:`app.services.statmech_resolution.collect_frequency_scale_factor_software_mismatch_warnings`
(the thin warning-producing wrapper over it):

  1. match       -- both softwares known, and they agree -> no warning.
  2. mismatch    -- both softwares known, and they differ -> warns, naming
                     both codes.
  3. not_comparable -- no ``freq``-role source calculation, or either side's
                     software is unresolved -> no warning, but this is
                     NEVER the same outcome as "match": the classifier
                     reports the state explicitly so a caller (and this
                     test) can tell the two apart, rather than inferring
                     "matched" from "nothing was said".
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.services import statmech_resolution
from app.services.statmech_resolution import (
    W_STATMECH_FSF_SOFTWARE_MISMATCH,
    FSFSoftwareComparisonState,
    collect_frequency_scale_factor_software_mismatch_warnings,
    evaluate_frequency_scale_factor_software,
)
from app.workflows.statmech import persist_statmech_upload

_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
_FSF_LOT = {"method": "wB97X-D", "basis": "def2-TZVP"}


def _freq_calc_payload(software_name: str) -> dict:
    return {
        "type": "freq",
        "software_release": {"name": software_name, "version": "1.0"},
        "level_of_theory": _LOT,
        "freq_result": {"n_imag": 0, "zpe_hartree": 0.021},
    }


def _request(
    *,
    smiles: str,
    fsf_software_name: str | None,
    freq_software_name: str | None,
    include_freq_source: bool = True,
) -> StatmechUploadRequest:
    """Build a standalone statmech upload with a frequency scale factor and
    (optionally) one ``freq``-role inline calculation.

    ``freq_software_name=None`` with ``include_freq_source=True`` is not
    representable (an inline calculation always carries a software
    reference) -- callers wanting "freq calc present but its software
    unresolved" are out of scope for this fixture builder; the "no freq
    source calculation at all" not-comparable case is covered by
    ``include_freq_source=False`` instead, which is the shape the 6
    non-comparable rows on the deployed archive actually have.
    """
    fsf: dict = {
        "level_of_theory": dict(_FSF_LOT),
        "scale_kind": "fundamental",
        "value": 0.988,
    }
    if fsf_software_name is not None:
        fsf["software"] = {"name": fsf_software_name}

    base: dict = {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "point_group": "C2v",
        "is_linear": False,
        "freq_scale_factor": fsf,
        "calculations": [],
        "source_calculations": [],
    }
    if include_freq_source:
        assert freq_software_name is not None
        base["calculations"] = [
            {"key": "freq0", "calculation": _freq_calc_payload(freq_software_name)}
        ]
        base["source_calculations"] = [
            {"calculation_key": "freq0", "role": "freq"}
        ]
    return StatmechUploadRequest(**base)


# ---------------------------------------------------------------------------
# 1. Mismatch -- warns, naming both codes.
# ---------------------------------------------------------------------------


def test_mismatch_warns(db_conn) -> None:
    """A Gaussian-derived scale factor applied to ORCA-run frequencies
    produces exactly one warning naming both codes."""
    with Session(db_conn) as session, session.begin():
        request = _request(
            smiles="C",
            fsf_software_name="Gaussian",
            freq_software_name="ORCA",
        )
        statmech = persist_statmech_upload(session, request)
        session.flush()

        comparisons = evaluate_frequency_scale_factor_software(
            session, [statmech.id]
        )
        assert comparisons[statmech.id].state is FSFSoftwareComparisonState.mismatch
        assert comparisons[statmech.id].fsf_software == "Gaussian"
        assert comparisons[statmech.id].freq_software == "ORCA"

        warnings = collect_frequency_scale_factor_software_mismatch_warnings(
            session, [statmech.id]
        )
        assert len(warnings) == 1
        assert warnings[0].code == W_STATMECH_FSF_SOFTWARE_MISMATCH
        assert warnings[0].field == "frequency_scale_factor"
        assert "Gaussian" in warnings[0].message
        assert "ORCA" in warnings[0].message
        # No-ID-leak rule: the message names software, never a row id.
        assert str(statmech.id) not in warnings[0].message


# ---------------------------------------------------------------------------
# 2. Match -- a genuine, non-empty comparison -- does not warn.
# ---------------------------------------------------------------------------


def test_match_does_not_warn(db_conn) -> None:
    """A Gaussian-derived scale factor applied to Gaussian-run frequencies
    is a real comparison that agrees, not an empty one -- both sides are
    populated and equal, and the classifier reports ``match`` explicitly
    rather than merely staying silent."""
    with Session(db_conn) as session, session.begin():
        request = _request(
            smiles="CC",
            fsf_software_name="Gaussian",
            freq_software_name="Gaussian",
        )
        statmech = persist_statmech_upload(session, request)
        session.flush()

        comparisons = evaluate_frequency_scale_factor_software(
            session, [statmech.id]
        )
        comparison = comparisons[statmech.id]
        assert comparison.state is FSFSoftwareComparisonState.match
        # A genuine comparison: both names are present and equal, not None.
        assert comparison.fsf_software == "Gaussian"
        assert comparison.freq_software == "Gaussian"

        warnings = collect_frequency_scale_factor_software_mismatch_warnings(
            session, [statmech.id]
        )
        assert warnings == []


# ---------------------------------------------------------------------------
# 3. Not comparable -- no freq source calculation at all.
# ---------------------------------------------------------------------------


def test_not_comparable_no_freq_source_calculation(db_conn) -> None:
    """A scale factor with known software but no ``freq``-role source
    calculation is NOT COMPARABLE -- distinct from ``match``, which this
    test asserts positively rather than settling for "no warning"."""
    with Session(db_conn) as session, session.begin():
        request = _request(
            smiles="CCC",
            fsf_software_name="Gaussian",
            freq_software_name=None,
            include_freq_source=False,
        )
        statmech = persist_statmech_upload(session, request)
        session.flush()
        assert statmech.source_calculations == []

        comparisons = evaluate_frequency_scale_factor_software(
            session, [statmech.id]
        )
        comparison = comparisons[statmech.id]
        assert comparison.state is FSFSoftwareComparisonState.not_comparable
        assert comparison.state is not FSFSoftwareComparisonState.match

        warnings = collect_frequency_scale_factor_software_mismatch_warnings(
            session, [statmech.id]
        )
        assert warnings == []


# ---------------------------------------------------------------------------
# 4. Not comparable -- factor has no recorded software.
# ---------------------------------------------------------------------------


def test_not_comparable_fsf_has_no_software(db_conn) -> None:
    """A software-agnostic scale factor (``software_id`` null) is NOT
    COMPARABLE even though its freq source calculation's software is known
    -- distinct from ``match``, asserted positively."""
    with Session(db_conn) as session, session.begin():
        request = _request(
            smiles="CCCC",
            fsf_software_name=None,
            freq_software_name="Gaussian",
        )
        statmech = persist_statmech_upload(session, request)
        session.flush()

        comparisons = evaluate_frequency_scale_factor_software(
            session, [statmech.id]
        )
        comparison = comparisons[statmech.id]
        assert comparison.state is FSFSoftwareComparisonState.not_comparable
        assert comparison.state is not FSFSoftwareComparisonState.match
        assert comparison.fsf_software is None

        warnings = collect_frequency_scale_factor_software_mismatch_warnings(
            session, [statmech.id]
        )
        assert warnings == []


def test_empty_input_returns_empty(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        assert evaluate_frequency_scale_factor_software(session, []) == {}
        assert collect_frequency_scale_factor_software_mismatch_warnings(
            session, []
        ) == []


# ---------------------------------------------------------------------------
# Mutation-landing check (not a permanent test): kept as documentation of
# what was proven while developing this suite --
# ``test_not_comparable_no_freq_source_calculation`` and
# ``test_not_comparable_fsf_has_no_software`` were confirmed to FAIL when
# ``evaluate_frequency_scale_factor_software`` was mutated to treat an
# empty ``known_freq`` list as a match instead of `continue`-ing past it
# (i.e. collapsing state 3 into state 1). See the PR description for the
# observed failure output; the mutation was reverted before landing.
# ---------------------------------------------------------------------------


def test_module_exposes_the_state_enum_used_by_the_classifier() -> None:
    """Guards the public seam these tests depend on -- if
    ``FSFSoftwareComparisonState`` or ``evaluate_frequency_scale_factor_software``
    is renamed without updating this suite, this fails loudly instead of
    the suite silently testing nothing."""
    assert statmech_resolution.FSFSoftwareComparisonState is FSFSoftwareComparisonState
    assert (
        statmech_resolution.evaluate_frequency_scale_factor_software
        is evaluate_frequency_scale_factor_software
    )
