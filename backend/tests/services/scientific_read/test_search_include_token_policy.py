"""Structural policy guard: where ``trust`` is a legal search token, and where it is not.

The policy this file guards has changed, and the change is the point of it
rather than a relaxation of it.

**Before.** ``trust`` was legal only on detail / subresource surfaces and
never on a broad search / list surface, because only the detail paths
eager-load the graph the evidence evaluator walks. The drift this test was
written for (fixed in b8f1429) was ``statmech_search`` importing a *detail*
include-token set: that made ``/scientific/statmech/search`` accept
``include=trust`` and leak it through ``include=all``.

**Now.** Five search surfaces — thermo, kinetics, transition-states,
statmech and transport — each declared a ``trust`` field their own
vocabulary made unfillable. Three shipped it as a permanent ``null``; two
stripped it unconditionally, so a consumer could not learn from the
response that a verdict existed at all. It did: the same verdict was one
token away under ``include=assessments``. Those five now accept the token,
and their services load the evidence graph over the page rather than per
record.

**What did not change is the half that costs.** ``trust`` is
*internal-tokenized* on every one of the five, so ``include=all`` still
never expands to it — a caller must name it. That is the assertion below
that applies to all fifteen search modules without exception, and it is the
one whose omission would be discovered last: an ``include=all`` that
silently bought a 23-entry eager-load chain per page would break nothing
until it met a real page size on the hosted instance.

So the legal-set assertion is now two-sided rather than one-sided. Naming
both sides is deliberate: a one-sided "not legal here" test cannot fail when
somebody drops the token from a surface that is supposed to have it, and
half of this file's subject matter is exactly that failure.

We assert on the constants **as bound in the search module's namespace**,
because those are the exact objects each search function passes to
``validate_includes``. This is intentionally a structural, no-DB test; the
runtime API tests cover the behavioural surfaces.
"""

from __future__ import annotations

import importlib

import pytest

from app.services.scientific_read.common import validate_includes

# Each broad search/list scientific-read module, with the names of the
# include-token constants it actually passes to ``validate_includes``, and
# whether ``trust`` is legal on that surface.
# (module path, legal-tokens const, internal-tokens const, trust_is_legal)
#
# Subresource / detail modules (e.g. ``statmech``, ``transport``,
# ``species_statmech``, ``calculations`` detail) are deliberately excluded:
# this file is about the *search* half of each vocabulary.
SEARCH_MODULES: list[tuple[str, str, str, bool]] = [
    ("app.services.scientific_read.calculations_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", False),
    ("app.services.scientific_read.kinetics_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", True),
    ("app.services.scientific_read.thermo_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", True),
    ("app.services.scientific_read.statmech_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", True),
    ("app.services.scientific_read.transport_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", True),
    ("app.services.scientific_read.species_calculations_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", False),
    ("app.services.scientific_read.transition_states_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", True),
    ("app.services.scientific_read.conformers_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", False),
    ("app.services.scientific_read.networks_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", False),
    ("app.services.scientific_read.network_kinetics_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", False),
    ("app.services.scientific_read.network_solves_search",
     "_SOLVE_LEGAL_INCLUDE_TOKENS", "_SOLVE_INTERNAL_INCLUDE_TOKENS", False),
    ("app.services.scientific_read.artifacts_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", False),
    ("app.services.scientific_read.structure_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", False),
    ("app.services.scientific_read.energy_correction_schemes_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", False),
    ("app.services.scientific_read.frequency_scale_factors_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS", False),
]

#: The five search surfaces whose records declare a ``trust`` field. Named
#: here as well as flagged in the table so that a case silently dropping out
#: of the parametrisation is caught: an empty or shrunken parametrisation is
#: the vacuous green this repository keeps finding.
TRUST_BEARING_SEARCH_MODULES = {
    "app.services.scientific_read.kinetics_search",
    "app.services.scientific_read.thermo_search",
    "app.services.scientific_read.statmech_search",
    "app.services.scientific_read.transport_search",
    "app.services.scientific_read.transition_states_search",
}


def _ids(case: tuple[str, str, str, bool]) -> str:
    return case[0].rsplit(".", 1)[-1]


def test_the_parametrisation_covers_every_trust_bearing_search_surface() -> None:
    """The table below must name all five, and must not quietly lose one.

    Without this, deleting a row from ``SEARCH_MODULES`` removes a case and
    the file still reports green — which is precisely how a surface loses
    its guard unnoticed.
    """
    flagged = {path for path, _, _, legal in SEARCH_MODULES if legal}
    assert flagged == TRUST_BEARING_SEARCH_MODULES
    assert len(SEARCH_MODULES) == 15


@pytest.mark.parametrize(
    "module_path,legal_name,internal_name,trust_is_legal",
    SEARCH_MODULES,
    ids=[_ids(c) for c in SEARCH_MODULES],
)
def test_search_legal_includes_match_the_declared_policy(
    module_path: str, legal_name: str, internal_name: str, trust_is_legal: bool
) -> None:
    """``trust`` is legal on exactly the five surfaces whose records declare it."""
    module = importlib.import_module(module_path)
    # getattr (not .get) so a renamed/removed constant fails loudly — that is
    # itself a signal the search surface changed shape and needs re-review.
    legal: set[str] = getattr(module, legal_name)
    if trust_is_legal:
        assert "trust" in legal, (
            f"{module_path}.{legal_name} no longer accepts 'trust'. This "
            f"surface's record declares a trust field; without the token the "
            f"field is one no request can fill, which is the defect PR 2 "
            f"existed to remove. If the eager-load cost forced a rollback, "
            f"cap or paginate — do not un-legalise the token."
        )
    else:
        assert "trust" not in legal, (
            f"{module_path}.{legal_name} exposes 'trust' as a legal include "
            f"token. Its record shape does not declare one, so the token "
            f"would be accepted and produce nothing. Did an import get "
            f"re-pointed at a detail _DETAIL_LEGAL_INCLUDE_TOKENS set?"
        )


@pytest.mark.parametrize(
    "module_path,legal_name,internal_name,trust_is_legal",
    SEARCH_MODULES,
    ids=[_ids(c) for c in SEARCH_MODULES],
)
def test_search_include_all_does_not_expand_to_trust(
    module_path: str, legal_name: str, internal_name: str, trust_is_legal: bool
) -> None:
    """``include=all`` resolution on a search surface must never yield ``trust``.

    Applies to all fifteen, including the five where the token is legal:
    legal is not the same as free. The evaluator's eager-load chain runs
    from 9 entries on transport to 23 on transition-states, and
    ``include=all`` is the token a client sends when it does not want to
    think about which sections it needs. Buying that chain on a page of 34
    records, on that request, is the exact cost the old vocabulary avoided
    by refusing the token — re-entered through the door marked convenience.

    This exercises the real ``validate_includes`` resolver with each
    module's actual legal + internal token sets, so it also guards against a
    future change to ``all``-expansion semantics that would surface an
    internal trust token.
    """
    module = importlib.import_module(module_path)
    legal: set[str] = getattr(module, legal_name)
    internal: set[str] = getattr(module, internal_name)
    resolved = validate_includes(
        ["all"], legal, f"{module_path}::all-expansion", internal_tokens=internal
    )
    assert "trust" not in resolved, (
        f"{module_path}: include=all expanded to include 'trust' "
        f"({sorted(resolved)!r}). Broad search/list must never expose trust."
    )
