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
from app.services.consistency.gibbs import compare_gibbs
from app.services.consistency.hess import compare_hess
from app.services.consistency.kinetics import compare_kinetics
from app.services.consistency.thermo import compare_thermo
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
        "(rubric ``external_cp_comparison``, version 2) that a curator may "
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
                "the upload path. Scope is gas phase only "
                "(app.services.consistency.engine.SUPPORTED_PHASES, decided "
                "2026-09-23): a record whose phase is recorded as something "
                "else, or was never recorded at all, is reported unavailable, "
                "not refused and not assumed gas."
            ),
        ),
    ),
    escape_hatch=None,
)

__all__ = ["CHECK_EXTERNAL_CP_COMPARISON", "CHECK_THERMO_CONSISTENCY", "CHECK_THERMO_KINETICS_CONSISTENCY"]

# Explicit Phase D triggers share the review-only registry, never the upload path.

CHECK_THERMO_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=1, code=None,
    asserts="Compare every supplied NASA Cp/entropy fit with exact points, s298 and explicitly named neighbours.",
    tier=CheckTier.review, channel=CodeChannel.none,
    tier_rationale="Explicit advisory comparison records residuals or unavailable reasons; no threshold or approval effect.",
    adr="0008", enforced_by=(PythonCheck(
        compare_thermo,
        note=(
            "Explicit Phase D CLI/service invocation only. Scope is gas "
            "phase only (app.services.consistency.engine.SUPPORTED_PHASES, "
            "decided 2026-09-23): a record whose phase is recorded as "
            "something else, or was never recorded at all, is reported "
            "unavailable, not refused and not assumed gas."
        ),
    ),),
    escape_hatch=None,
)
CHECK_THERMO_KINETICS_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=2, code=None,
    asserts="Compare explicitly supplied opposite elementary rates with equilibrium from explicitly mapped NASA thermo.",
    tier=CheckTier.review, channel=CodeChannel.none,
    tier_rationale="Explicit advisory comparison records residuals or unavailable reasons; no threshold or approval effect.",
    adr="0008", enforced_by=(PythonCheck(
        compare_kinetics,
        note=(
            "Explicit Phase D CLI/service invocation only. Scope is gas "
            "phase only (app.services.consistency.engine.SUPPORTED_PHASES, "
            "decided 2026-09-23): a thermo record whose phase is recorded as "
            "something else, or was never recorded at all, is reported "
            "unavailable, not refused and not assumed gas."
        ),
    ),),
    escape_hatch=None,
)

__all__ += ["CHECK_GIBBS_SELF_CONSISTENCY"]

CHECK_GIBBS_SELF_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=3, code=None,
    asserts=(
        "Compare every stored Gibbs value of one thermo record with H(T) - T*S(T) taken from each of "
        "that record's own representations, H and S always from the same one."
    ),
    tier=CheckTier.review, channel=CodeChannel.none,
    tier_rationale=(
        "Explicit advisory comparison records residuals or unavailable reasons; the only flag is "
        "float precision on the same row's own identity, and there is no threshold or approval effect."
    ),
    adr="0008", enforced_by=(PythonCheck(
        compare_gibbs,
        note=(
            "Explicit Phase D CLI/service invocation only (--check gibbs-self). A record whose "
            "enthalpy_reference_kind was never recorded is excluded, never assumed formation_298k; "
            "a G on a declared record that follows another convention is reported as its raw "
            "residual, never relabelled. Reference pressure does not gate this check (g, h and s "
            "of one record share one state); gas phase only, as the other Phase D checks."
        ),
    ),),
    escape_hatch=None,
)

__all__ += ["CHECK_HESS_CONSISTENCY"]

CHECK_HESS_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=5, code=None,
    asserts=(
        "Compare a kinetics record's stated reaction energy (its tunneling row, forward, separated-species "
        "zero) with the Hess sum of explicitly mapped formation enthalpies at the matching temperature."
    ),
    tier=CheckTier.review, channel=CodeChannel.none,
    tier_rationale="Explicit advisory comparison records residuals or unavailable reasons; no threshold or approval effect.",
    adr="0008", enforced_by=(PythonCheck(
        compare_hess,
        note=(
            "Explicit Phase D CLI/service invocation only (--check hess). "
            "thermal_enthalpy_298k pairs with formation enthalpies at "
            "298.15 K under one declared enthalpy_reference_kind; "
            "electronic_plus_zpe and atom_and_bond_corrected pair with "
            "enthalpy_formation_0k_kj_mol. The element-reference "
            "compilation is not recorded, so cancellation across terms is "
            "assumed and every finding says so. Gas phase only; ions and "
            "isotopologues out of scope; pressure never gates enthalpy."
        ),
    ),),
    escape_hatch=None,
)
