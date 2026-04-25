"""Edge-case tests for the Gaussian route-line parser.

Focuses on:
  1. Nested option blocks with variety
  2. Repeated semantic words across sections (Rule 1: section+raw_key is the unit)
  3. Link0 directive inclusion/exclusion
  4. Route-line wrapping
  5. Bare route-line keywords
  6. Value preservation (Rule 4: no premature normalization)
"""

from __future__ import annotations

import pytest

from app.services.gaussian_parameter_parser import (
    _extract_link0,
    _extract_route_line,
    _parse_route_tokens,
    parse_gaussian_log,
    parse_method_basis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _params_by_section(params: list[dict], section: str) -> list[dict]:
    return [p for p in params if p.get("section") == section]


def _find_param(
    params: list[dict], raw_key: str, section: str | None = None
) -> dict | None:
    for p in params:
        if p["raw_key"] == raw_key:
            if section is None or p.get("section") == section:
                return p
    return None


# ---------------------------------------------------------------------------
# 1. Nested option blocks
# ---------------------------------------------------------------------------


class TestNestedOptionBlocks:
    """Complex option blocks like opt=(calcfc,ts,noeigentest,maxcycle=200)."""

    def test_opt_ts_noeigentest(self):
        route = "#P opt=(calcfc,ts,noeigentest,maxcycle=200) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        opt = _params_by_section(params, "opt")

        assert _find_param(opt, "calcfc") is not None
        assert _find_param(opt, "ts") is not None
        assert _find_param(opt, "noeigentest") is not None

        mc = _find_param(opt, "maxcycle")
        assert mc is not None
        assert mc["raw_value"] == "200"

        # ts should be a boolean flag
        ts = _find_param(opt, "ts")
        assert ts["raw_value"] == "true"
        assert ts["value_type"] == "bool"

    def test_scf_xqc_tight_maxcycle(self):
        route = "#P scf=(xqc,tight,maxcycle=512) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        scf = _params_by_section(params, "scf")

        xqc = _find_param(scf, "xqc")
        assert xqc is not None
        assert xqc["canonical_key"] == "scf_fallback"

        tight = _find_param(scf, "tight")
        assert tight is not None
        assert tight["canonical_key"] == "scf_convergence"
        assert tight["canonical_value"] == "tight"

        mc = _find_param(scf, "maxcycle")
        assert mc is not None
        assert mc["raw_value"] == "512"
        assert mc["canonical_key"] == "scf_max_cycles"

    def test_integral_variations(self):
        route = "#P integral=(ultrafinegrid,acc2e=14) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        integral = _params_by_section(params, "integral")

        grid = _find_param(integral, "ultrafinegrid")
        assert grid is not None
        assert grid["raw_value"] == "true"

        acc = _find_param(integral, "acc2e")
        assert acc is not None
        assert acc["raw_value"] == "14"

    def test_deeply_mixed_options(self):
        """Route with multiple complex option blocks simultaneously."""
        route = (
            "#P opt=(calcfc,ts,noeigentest,maxcycle=200,maxstep=10) "
            "scf=(xqc,tight,maxcycle=512) "
            "integral=(grid=ultrafine,acc2e=12) "
            "uwb97xd/def2tzvp"
        )
        params = _parse_route_tokens(route)

        opt = _params_by_section(params, "opt")
        scf = _params_by_section(params, "scf")
        integral = _params_by_section(params, "integral")

        assert len(opt) == 5
        assert len(scf) == 3
        assert len(integral) == 2


# ---------------------------------------------------------------------------
# 2. Repeated semantic words across sections
# ---------------------------------------------------------------------------


class TestRepeatedKeywords:
    """Same raw_key in different sections must get different canonical_keys."""

    def test_tight_in_opt_vs_scf(self):
        route = "#P opt=(tight) scf=(tight) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)

        opt_tight = _find_param(params, "tight", section="opt")
        scf_tight = _find_param(params, "tight", section="scf")

        assert opt_tight is not None
        assert scf_tight is not None
        assert opt_tight["canonical_key"] == "opt_convergence"
        assert scf_tight["canonical_key"] == "scf_convergence"

    def test_maxcycle_in_opt_vs_scf(self):
        route = "#P opt=(maxcycle=200) scf=(maxcycle=512) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)

        opt_mc = _find_param(params, "maxcycle", section="opt")
        scf_mc = _find_param(params, "maxcycle", section="scf")

        assert opt_mc is not None
        assert scf_mc is not None
        assert opt_mc["canonical_key"] == "opt_max_cycles"
        assert scf_mc["canonical_key"] == "scf_max_cycles"
        assert opt_mc["raw_value"] == "200"
        assert scf_mc["raw_value"] == "512"

    def test_verytight_in_opt_vs_scf(self):
        route = "#P opt=(verytight) scf=(verytight) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)

        opt_vt = _find_param(params, "verytight", section="opt")
        scf_vt = _find_param(params, "verytight", section="scf")

        assert opt_vt["canonical_key"] == "opt_convergence"
        assert opt_vt["canonical_value"] == "very_tight"
        assert scf_vt["canonical_key"] == "scf_convergence"
        assert scf_vt["canonical_value"] == "very_tight"


# ---------------------------------------------------------------------------
# 3. Link0 directives
# ---------------------------------------------------------------------------


class TestLink0Directives:
    """Test which % directives are included vs excluded."""

    LINK0_BLOCK = (
        " %chk=check.chk\n"
        " %oldchk=prev.chk\n"
        " %rwf=/scratch/tmp.rwf\n"
        " %mem=32768mb\n"
        " %NProcShared=8\n"
        " %nproc=16\n"
    )

    def test_mem_included(self):
        params = _extract_link0(self.LINK0_BLOCK)
        mem = _find_param(params, "%mem")
        assert mem is not None
        assert mem["raw_value"] == "32768mb"
        assert mem["canonical_key"] == "memory"

    def test_nprocshared_included(self):
        params = _extract_link0(self.LINK0_BLOCK)
        nproc = _find_param(params, "%NProcShared")
        assert nproc is not None
        assert nproc["raw_value"] == "8"
        assert nproc["canonical_key"] == "nproc_shared"

    def test_nproc_included(self):
        params = _extract_link0(self.LINK0_BLOCK)
        nproc = _find_param(params, "%nproc")
        assert nproc is not None
        assert nproc["raw_value"] == "16"

    def test_chk_excluded(self):
        """Checkpoint file paths are not execution parameters."""
        params = _extract_link0(self.LINK0_BLOCK)
        assert _find_param(params, "%chk") is None

    def test_oldchk_excluded(self):
        params = _extract_link0(self.LINK0_BLOCK)
        assert _find_param(params, "%oldchk") is None

    def test_rwf_excluded(self):
        """Read-write file paths are not execution parameters."""
        params = _extract_link0(self.LINK0_BLOCK)
        assert _find_param(params, "%rwf") is None


# ---------------------------------------------------------------------------
# 4. Route-line wrapping
# ---------------------------------------------------------------------------


class TestRouteLineWrapping:
    """Route lines that wrap across multiple lines in the log."""

    def _make_log_fragment(self, *route_lines: str) -> str:
        """Build a minimal log fragment with route between dashes."""
        dashes = "-" * 70
        header = (
            " Gaussian 09:  EM64L-G09RevD.01 24-Apr-2013\n"
            "               01-Jan-2026\n"
            " ******************************************\n"
        )
        route_block = "\n".join(f" {line}" for line in route_lines)
        return f"{header} {dashes}\n{route_block}\n {dashes}\n"

    def test_method_basis_split_across_lines(self):
        """uwb97xd/def2tzvp split as 'def2tz' + 'vp'."""
        log = self._make_log_fragment(
            "#P opt=(tight) guess=read uwb97xd/def2tz",
            "vp scf=(tight)",
        )
        route = _extract_route_line(log)
        mb = parse_method_basis(route)
        assert mb is not None
        assert mb["basis"].lower() == "def2tzvp"

    def test_iop_split_across_lines(self):
        """IOp block split across lines."""
        log = self._make_log_fragment(
            "#P opt=(tight) IOp(2/9=200",
            "0) uwb97xd/def2tzvp",
        )
        route = _extract_route_line(log)
        params = _parse_route_tokens(route)
        iop = _find_param(params, "IOp(2/9)", section="internal_option")
        assert iop is not None
        assert iop["raw_value"] == "2000"

    def test_nested_option_split_across_lines(self):
        """Option block parenthesized content split across lines."""
        log = self._make_log_fragment(
            "#P opt=(calcfc,maxcycle=100,maxstep=5,ti",
            "ght) uwb97xd/def2tzvp scf=(direct,tight)",
        )
        route = _extract_route_line(log)
        params = _parse_route_tokens(route)
        tight = _find_param(params, "tight", section="opt")
        assert tight is not None
        assert tight["canonical_key"] == "opt_convergence"

    def test_closing_paren_on_own_line(self):
        """Closing paren for scf=(...) on its own line (as in real input.log)."""
        log = self._make_log_fragment(
            "#P opt=(tight) uwb97xd/def2tzvp scf=(direct,tight",
            ")",
        )
        route = _extract_route_line(log)
        params = _parse_route_tokens(route)
        scf = _params_by_section(params, "scf")
        keys = {p["raw_key"] for p in scf}
        assert "direct" in keys
        assert "tight" in keys


# ---------------------------------------------------------------------------
# 5. Bare route-line keywords
# ---------------------------------------------------------------------------


class TestBareKeywords:
    """Standalone route keywords without = or ()."""

    def test_nosymm(self):
        route = "#P opt=(tight) nosymm uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        nosymm = _find_param(params, "nosymm")
        assert nosymm is not None
        assert nosymm["raw_value"] == "true"
        assert nosymm["section"] == "general"

    def test_force(self):
        route = "#P opt=(tight) force uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        force = _find_param(params, "force")
        assert force is not None
        assert force["raw_value"] == "true"

    def test_empiricaldispersion_key_value(self):
        """empiricaldispersion=gd3bj is key=value, not a bare keyword."""
        route = "#P empiricaldispersion=gd3bj uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        ed = _find_param(params, "empiricaldispersion")
        assert ed is not None
        assert ed["raw_value"] == "gd3bj"
        assert ed["section"] == "general"

    def test_bare_freq_not_stored_as_parameter(self):
        """freq/opt/sp as bare keywords define calc type, not parameters."""
        route = "#P freq uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        # freq as a bare keyword should not appear as a parameter row;
        # it defines Calculation.type, not an execution setting.
        freq = _find_param(params, "freq")
        assert freq is None

    def test_bare_opt_not_stored_as_parameter(self):
        route = "#P opt uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        opt = _find_param(params, "opt")
        assert opt is None

    def test_opt_with_options_still_parses_suboptions(self):
        """opt=(tight) should parse sub-options but not store 'opt' itself."""
        route = "#P opt=(tight) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        # The sub-option 'tight' should be in section 'opt'
        tight = _find_param(params, "tight", section="opt")
        assert tight is not None
        # But there should be no standalone 'opt' parameter
        assert _find_param(params, "opt", section="general") is None


# ---------------------------------------------------------------------------
# 6. Value preservation (Rule 4)
# ---------------------------------------------------------------------------


class TestValuePreservation:
    """Raw values should be preserved as-is, not prematurely normalized."""

    def test_memory_value_preserved_with_unit(self):
        params = _extract_link0(" %mem=32768mb\n")
        mem = _find_param(params, "%mem")
        # Stored as "32768mb" not converted to bytes
        assert mem["raw_value"] == "32768mb"

    def test_grid_value_preserved(self):
        route = "#P integral=(grid=ultrafine) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        grid = _find_param(params, "grid", section="integral")
        assert grid["raw_value"] == "ultrafine"  # not "99302" or whatever

    def test_maxcycle_preserved_as_string(self):
        route = "#P opt=(maxcycle=200) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        mc = _find_param(params, "maxcycle", section="opt")
        assert mc["raw_value"] == "200"
        assert mc["value_type"] == "int"  # type hint, but value stays text

    def test_iop_value_preserved(self):
        route = "#P IOp(2/9=2000) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        iop = _find_param(params, "IOp(2/9)")
        assert iop["raw_value"] == "2000"

    def test_guess_value_preserved(self):
        route = "#P guess=read uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        guess = _find_param(params, "guess")
        assert guess["raw_value"] == "read"


# ---------------------------------------------------------------------------
# 7. Multiple IOp directives
# ---------------------------------------------------------------------------


class TestMultipleIOps:
    """Multiple IOp() blocks in one route line."""

    def test_two_separate_iops(self):
        route = "#P IOp(2/9=2000) IOp(3/76=200) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        iops = _params_by_section(params, "internal_option")
        assert len(iops) == 2
        keys = {p["raw_key"] for p in iops}
        assert "IOp(2/9)" in keys
        assert "IOp(3/76)" in keys

    def test_comma_separated_iops(self):
        """IOp(2/9=2000,3/76=200) — multiple options in one IOp block."""
        route = "#P IOp(2/9=2000,3/76=200) uwb97xd/def2tzvp"
        params = _parse_route_tokens(route)
        iops = _params_by_section(params, "internal_option")
        assert len(iops) == 2
