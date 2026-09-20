"""Register the first ``review``-tier check: computed Cp against external data.

ADR 0008 puts every cross-check against an external reference dataset in the
``review`` tier: it runs only from an explicit admin/CLI trigger, never from
the upload path, produces no error-envelope code, and has no approval effect
-- its output is one ``record_machine_review`` row a curator may read. See
``app.services.external_comparison.cp`` for the runner (the Cantera-based
Cp(T) evaluation and the finding it writes) and
``docs/research/tckdb-phase-c-implementation-plan.md`` C4 for the design.
"""

from __future__ import annotations

from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)
from app.services.external_comparison.cp import compare_thermo_with_cp_observations

CHECK_EXTERNAL_CP_COMPARISON = ScientificCheck(
    group="External reference comparison",
    sort_key=1,
    code=None,
    asserts=(
        "A computed thermo record's heat capacity, evaluated at each "
        "temperature an independent external observation reports, is "
        "compared against that observation and the residual is recorded -- "
        "never judged against a threshold and never fed back into the "
        "record's trust or review state."
    ),
    tier=CheckTier.review,
    channel=CodeChannel.none,
    tier_rationale=(
        "ADR 0008 reserves the review tier for exactly this shape of claim: a "
        "comparison against reference data TCKDB did not itself compute and "
        "has no authority to grade. It cannot block -- the upload that "
        "produced the computed thermo record is long since accepted, and an "
        "external observation may itself be wrong, superseded, or reported on "
        "a real-gas basis the computed record was never fit to reproduce. It "
        "cannot warn either, because a warning annotates the depositor's "
        "payload at upload time and this check runs later, against data the "
        "depositor never supplied. So the consequence is the review tier: one "
        "append-only, rubric-versioned ``record_machine_review`` row "
        "(rubric ``external_cp_comparison``, version 1) that a curator may "
        "read and act on, with no accuracy threshold authorized anywhere in "
        "this check -- picking one is out of scope for this change and is "
        "recorded as a hold point in the C4 plan."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            compare_thermo_with_cp_observations,
            note=(
                "Runs only from the admin/CLI trigger "
                "(app.services.external_comparison.cp.run_and_record via "
                "backend/scripts/run_external_cp_comparison.py), never from "
                "the upload path."
            ),
        ),
    ),
    escape_hatch=None,
)

__all__ = ["CHECK_EXTERNAL_CP_COMPARISON"]
