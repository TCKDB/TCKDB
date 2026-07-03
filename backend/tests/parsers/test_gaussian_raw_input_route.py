"""Tests for Gaussian raw .gjf/.com input route extraction.

The Gaussian parser was originally written against log echoes whose
route line is bracketed by dashed delimiter rows. Raw input files have
no such delimiters; instead the route starts at the first ``#`` line
and ends at the next blank line. These tests cover that path.

Coverage:

1. Route extraction from a raw input layout.
2. The blank line before the title terminates the route.
3. Wrapped raw route lines are reassembled with spaces between them.
4. Method/basis (``uwb97xd/def2tzvp``) is NOT inserted into
   ``calculation_parameter`` rows.
5. ``opt=(calcfc,maxcycle=100,maxstep=5,tight)`` emits ``calcfc``,
   ``maxcycle``, ``maxstep``, and ``tight`` rows with the correct
   canonical keys.
6. ``integral=(grid=ultrafine, Acc2E=12)`` emits ``grid.quality`` and
   ``integral.accuracy``.
7. ``IOp(2/9=2000)`` is captured under ``internal_option.iop``.
8. ``scf=(direct,tight)`` emits ``scf.direct`` and ``scf.convergence``.
"""

from __future__ import annotations

from app.services.gaussian_parameter_parser import (
    _parse_route_tokens,
    extract_gaussian_route_text,
)

# A real-world raw Gaussian input as supplied by the bug report.
RAW_INPUT_SINGLE_LINE_ROUTE = (
    "%chk=check.chk\n"
    "%mem=43008mb\n"
    "%NProcShared=12\n"
    "\n"
    "#P opt=(calcfc,maxcycle=100,maxstep=5,tight)  guess=read "
    "uwb97xd/def2tzvp  integral=(grid=ultrafine, Acc2E=12) "
    "IOp(2/9=2000)    scf=(direct,tight)\n"
    "\n"
    "rmg_rxn_1142_p1_sbr-OH-sbr\n"
    "\n"
    "0 2\n"
    "C 0.0 0.0 0.0\n"
    "\n"
)


# Same route content but wrapped across multiple raw lines.
RAW_INPUT_WRAPPED_ROUTE = (
    "%chk=check.chk\n"
    "%mem=43008mb\n"
    "%NProcShared=12\n"
    "\n"
    "#P opt=(calcfc,maxcycle=100,maxstep=5,tight)\n"
    "   guess=read uwb97xd/def2tzvp\n"
    "   integral=(grid=ultrafine, Acc2E=12)\n"
    "   IOp(2/9=2000) scf=(direct,tight)\n"
    "\n"
    "rmg_rxn_1142_p1_sbr-OH-sbr\n"
    "\n"
    "0 2\n"
    "C 0.0 0.0 0.0\n"
    "\n"
)


def _params_by_section(params: list[dict], section: str) -> list[dict]:
    return [p for p in params if p.get("section") == section]


def _find(params: list[dict], raw_key: str, section: str | None = None) -> dict | None:
    for p in params:
        if p["raw_key"] == raw_key and (section is None or p["section"] == section):
            return p
    return None


# ---------------------------------------------------------------------------
# 1-3. Route-text extraction
# ---------------------------------------------------------------------------


class TestRawRouteExtraction:
    def test_single_line_raw_route(self):
        route = extract_gaussian_route_text(RAW_INPUT_SINGLE_LINE_ROUTE)
        assert route is not None
        assert route.startswith("#P")
        assert "opt=(calcfc,maxcycle=100,maxstep=5,tight)" in route
        assert "scf=(direct,tight)" in route

    def test_blank_line_before_title_terminates_route(self):
        route = extract_gaussian_route_text(RAW_INPUT_SINGLE_LINE_ROUTE)
        # The title line must NOT bleed into the route.
        assert "rmg_rxn_1142_p1_sbr-OH-sbr" not in route
        # And neither should the charge/multiplicity line.
        assert "0 2" not in route

    def test_wrapped_route_reassembled_with_spaces(self):
        route = extract_gaussian_route_text(RAW_INPUT_WRAPPED_ROUTE)
        assert route is not None
        # Joining with spaces preserves token boundaries: tokens that
        # ended a wrapped line stay intact rather than being glued to
        # the next line's first token.
        assert "tight) guess=read" in route or "tight)  guess=read" in route
        assert "uwb97xd/def2tzvp integral=" in route or "def2tzvp  integral=" in route
        # Same downstream tokens as the single-line case must be present.
        assert "IOp(2/9=2000)" in route
        assert "scf=(direct,tight)" in route

    def test_returns_none_when_no_route_line(self):
        assert extract_gaussian_route_text("just some unrelated text\n") is None


# ---------------------------------------------------------------------------
# 4-8. Parameter parsing on the raw-input route
# ---------------------------------------------------------------------------


class TestRawRouteParameterParsing:
    def setup_method(self) -> None:
        route = extract_gaussian_route_text(RAW_INPUT_SINGLE_LINE_ROUTE)
        assert route is not None
        self.params = _parse_route_tokens(route)

    def test_method_basis_not_stored_as_parameter(self):
        # uwb97xd/def2tzvp is the level of theory, not an execution
        # parameter — it must NOT appear as a calculation_parameter row.
        for p in self.params:
            assert p["raw_key"].lower() != "uwb97xd"
            assert "def2tzvp" not in (p.get("raw_value") or "").lower()
            assert (p.get("canonical_key") or "").lower() != "method"

    def test_verbosity_captured(self):
        v = _find(self.params, "verbosity", section="general")
        assert v is not None
        assert v["canonical_key"] == "output.verbosity"
        assert v["raw_value"] == "P"

    def test_opt_block_emits_calcfc_maxcycle_maxstep_tight(self):
        opt = _params_by_section(self.params, "opt")

        calcfc = _find(opt, "calcfc")
        assert calcfc is not None
        assert calcfc["canonical_key"] == "opt.initial_hessian"

        mc = _find(opt, "maxcycle")
        assert mc is not None
        assert mc["canonical_key"] == "opt.max_cycles"
        assert mc["raw_value"] == "100"

        ms = _find(opt, "maxstep")
        assert ms is not None
        assert ms["canonical_key"] == "opt.max_step"
        assert ms["raw_value"] == "5"

        tight = _find(opt, "tight")
        assert tight is not None
        assert tight["canonical_key"] == "opt.convergence"
        assert tight["canonical_value"] == "tight"

    def test_integral_block_emits_grid_quality_and_integral_accuracy(self):
        integral = _params_by_section(self.params, "integral")

        grid = _find(integral, "grid")
        assert grid is not None
        assert grid["canonical_key"] == "grid.quality"
        assert grid["raw_value"] == "ultrafine"

        acc = _find(integral, "Acc2E")
        assert acc is not None
        assert acc["canonical_key"] == "integral.accuracy"
        assert acc["raw_value"] == "12"

    def test_iop_captured_under_internal_option_iop(self):
        iop_rows = _params_by_section(self.params, "internal_option")
        assert len(iop_rows) == 1
        iop = iop_rows[0]
        assert iop["canonical_key"] == "internal_option.iop"
        assert iop["raw_key"] == "IOp(2/9)"
        assert iop["raw_value"] == "2000"

    def test_scf_block_emits_direct_and_convergence(self):
        scf = _params_by_section(self.params, "scf")

        direct = _find(scf, "direct")
        assert direct is not None
        assert direct["canonical_key"] == "scf.direct"

        tight = _find(scf, "tight")
        assert tight is not None
        assert tight["canonical_key"] == "scf.convergence"
        assert tight["canonical_value"] == "tight"

    def test_guess_strategy_captured(self):
        guess = _find(self.params, "guess", section="general")
        assert guess is not None
        assert guess["canonical_key"] == "guess.strategy"
        assert guess["raw_value"] == "read"


# ---------------------------------------------------------------------------
# Wrapped-route equivalence: parameter set must match the single-line case.
# ---------------------------------------------------------------------------


class TestWrappedRouteEquivalence:
    def test_wrapped_route_yields_same_canonical_keys_as_single_line(self):
        single = extract_gaussian_route_text(RAW_INPUT_SINGLE_LINE_ROUTE)
        wrapped = extract_gaussian_route_text(RAW_INPUT_WRAPPED_ROUTE)
        single_params = _parse_route_tokens(single)
        wrapped_params = _parse_route_tokens(wrapped)

        # Compare the multiset of (section, raw_key) pairs — these are
        # the units of identity for a calculation_parameter row.
        single_keys = sorted((p["section"], p["raw_key"]) for p in single_params)
        wrapped_keys = sorted((p["section"], p["raw_key"]) for p in wrapped_params)
        assert single_keys == wrapped_keys
