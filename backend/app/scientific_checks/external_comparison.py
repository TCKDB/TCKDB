"""Register the ``review``-tier checks: comparisons against external or independently derived data.

Each runs only from an explicit admin or CLI trigger, never from the upload path, produces no
error-envelope code, and has no approval effect.
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
from app.services.consistency.isotope_identity import compare_isotope_identity
from app.services.consistency.kinetics import compare_kinetics
from app.services.consistency.kirchhoff import compare_kirchhoff
from app.services.consistency.thermo import compare_thermo
from app.services.consistency.wide_basin import compare_wide_basin
from app.services.external_comparison.cp import compare_thermo_with_cp_observations

CHECK_EXTERNAL_CP_COMPARISON = ScientificCheck(
    group="External reference comparison",
    sort_key=1,
    code=None,
    asserts=(
        "A computed thermo record's heat capacity is compared at each temperature an "
        "independent external observation reports, and the residual is recorded."
    ),
    tier=CheckTier.review,
    channel=CodeChannel.none,
    enforced_by=(
        PythonCheck(compare_thermo_with_cp_observations),
    ),
    escape_hatch=None,
)

__all__ = ["CHECK_EXTERNAL_CP_COMPARISON", "CHECK_THERMO_CONSISTENCY", "CHECK_THERMO_KINETICS_CONSISTENCY"]

CHECK_THERMO_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=1, code=None,
    asserts=(
        "Compare every supplied NASA Cp/entropy fit with exact points, s298 and explicitly "
        "named neighbours."
    ),
    tier=CheckTier.review, channel=CodeChannel.none,
    enforced_by=(PythonCheck(compare_thermo),),
    escape_hatch=None,
)
CHECK_THERMO_KINETICS_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=2, code=None,
    asserts="Compare explicitly supplied opposite elementary rates with equilibrium from explicitly mapped NASA thermo.",
    tier=CheckTier.review, channel=CodeChannel.none,
    enforced_by=(PythonCheck(compare_kinetics),),
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
    enforced_by=(PythonCheck(compare_gibbs),),
    escape_hatch=None,
)

__all__ += ["CHECK_KIRCHHOFF_CONSISTENCY"]

CHECK_KIRCHHOFF_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=4, code=None,
    asserts=(
        "Compare a thermo record's supplied enthalpies at shared temperatures, each enthalpy "
        "change with the Cp integral of a fit, and each fit's own boundary jumps."
    ),
    tier=CheckTier.review, channel=CodeChannel.none,
    enforced_by=(PythonCheck(compare_kirchhoff),),
    escape_hatch=None,
)

__all__ += ["CHECK_HESS_CONSISTENCY"]

CHECK_HESS_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=5, code=None,
    asserts=(
        "Compare a kinetics record's stated reaction energy with the Hess sum of explicitly "
        "mapped formation enthalpies."
    ),
    tier=CheckTier.review, channel=CodeChannel.none,
    enforced_by=(PythonCheck(compare_hess),),
    escape_hatch=None,
)

__all__ += ["CHECK_ISOTOPE_IDENTITY_CONSISTENCY"]

CHECK_ISOTOPE_IDENTITY_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=6, code=None,
    asserts="Find, on a protium species entry, calculation geometries that declare a non-standard nuclide.",
    tier=CheckTier.review, channel=CodeChannel.none,
    enforced_by=(PythonCheck(compare_isotope_identity),),
    escape_hatch=None,
)

__all__ += ["CHECK_WIDE_BASIN_CONSISTENCY"]

CHECK_WIDE_BASIN_CONSISTENCY = ScientificCheck(
    group="Advisory consistency", sort_key=7, code=None,
    asserts=(
        "Report a conformer group whose converged structures are linked end to end but are "
        "not all within the matching tolerance of each other."
    ),
    tier=CheckTier.review, channel=CodeChannel.none,
    enforced_by=(PythonCheck(compare_wide_basin),),
    escape_hatch=None,
)
