"""Structural policy guard: broad scientific search/list services never expose ``trust``.

Policy (see ``backend/docs/specs/scientific_statmech_reads.md`` and
``read_query_api_audit.md``):

- ``trust`` is legal only on selected **detail / subresource** surfaces
  (calculation detail, reaction-entry kinetics, species-entry thermo /
  statmech / transport, statmech detail, transport detail, TS-entry detail,
  reaction-entry ``/full`` embedded trust).
- ``trust`` is **never** legal on broad **search / list** surfaces, and
  ``include=all`` must never expand to ``trust`` on those surfaces.

The original drift (fixed in b8f1429) was ``statmech_search`` importing a
detail include-token set that carried ``trust``: that made
``/scientific/statmech/search`` accept ``include=trust`` and leak trust via
``include=all``. This test fails fast if any broad search module's *bound*
legal-include constant ever carries ``trust`` again — e.g. by re-pointing an
import at a detail module's ``_DETAIL_LEGAL_INCLUDE_TOKENS``.

We assert on the constant **as bound in the search module's namespace**,
because that is the exact object each search function passes to
``validate_includes``. This is intentionally a structural, no-DB test; the
runtime API tests already cover the behavioural surfaces.
"""

from __future__ import annotations

import importlib

import pytest

from app.services.scientific_read.common import validate_includes

# Each broad search/list scientific-read module, with the names of the
# include-token constants it actually passes to ``validate_includes``.
# (module path, legal-tokens const, internal-tokens const)
#
# Subresource / detail modules (e.g. ``statmech``, ``transport``,
# ``species_statmech``, ``calculations`` detail) are deliberately excluded:
# those are the surfaces where ``trust`` IS legal.
SEARCH_MODULES: list[tuple[str, str, str]] = [
    ("app.services.scientific_read.calculations_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.kinetics_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.thermo_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.statmech_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.transport_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.species_calculations_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.transition_states_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.conformers_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.networks_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.network_kinetics_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.network_solves_search",
     "_SOLVE_LEGAL_INCLUDE_TOKENS", "_SOLVE_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.artifacts_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.structure_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.energy_correction_schemes_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
    ("app.services.scientific_read.frequency_scale_factors_search",
     "_LEGAL_INCLUDE_TOKENS", "_INTERNAL_INCLUDE_TOKENS"),
]


def _ids(case: tuple[str, str, str]) -> str:
    return case[0].rsplit(".", 1)[-1]


@pytest.mark.parametrize("module_path,legal_name,internal_name", SEARCH_MODULES,
                         ids=[_ids(c) for c in SEARCH_MODULES])
def test_search_legal_includes_exclude_trust(
    module_path: str, legal_name: str, internal_name: str
) -> None:
    """A broad search/list module's legal include set must not contain ``trust``."""
    module = importlib.import_module(module_path)
    # getattr (not .get) so a renamed/removed constant fails loudly — that is
    # itself a signal the search surface changed shape and needs re-review.
    legal: set[str] = getattr(module, legal_name)
    assert "trust" not in legal, (
        f"{module_path}.{legal_name} exposes 'trust' as a legal include token. "
        f"trust is legal only on detail/subresource surfaces, never on broad "
        f"search/list. Did an import get re-pointed at a detail "
        f"_DETAIL_LEGAL_INCLUDE_TOKENS set?"
    )


@pytest.mark.parametrize("module_path,legal_name,internal_name", SEARCH_MODULES,
                         ids=[_ids(c) for c in SEARCH_MODULES])
def test_search_include_all_does_not_expand_to_trust(
    module_path: str, legal_name: str, internal_name: str
) -> None:
    """``include=all`` resolution on a search surface must never yield ``trust``.

    This exercises the real ``validate_includes`` resolver with each module's
    actual legal + internal token sets, so it also guards against a future
    change to ``all``-expansion semantics that would surface an internal
    trust token.
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
