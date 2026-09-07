"""Tests for the ``tckdb`` read-only CLI (``tckdb get reaction <ref>``).

Mirrors ``test_cli.py``'s style: stub ``cli.TCKDBClient`` so no network call
is ever made, and drive ``cli.main_tckdb`` the way a shell would invoke the
``tckdb`` console script.
"""

from __future__ import annotations

import json

import pytest

from tckdb_client import cli
from tckdb_client.errors import TCKDBConnectionError, TCKDBHTTPError

# ---------------------------------------------------------------------------
# Fixture payloads
# ---------------------------------------------------------------------------

FULL_ENVELOPE = {
    "request": {
        "include": ["species", "kinetics", "transition_states", "networks"],
        "include_review": "summary",
    },
    "reaction_entry": {
        "reaction_entry_ref": "rxe_ed66mj3ohtyien5rm2x3sb3rdu",
        "reaction_ref": "rxn_zicx5swji2nqkg2v263rnwxn4m",
        "equation": "O + [CH3] <=> C + [OH]",
        "reversible": True,
        "family": "H_Abstraction",
        "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
        "atom_maps": [],
    },
    "review_summary": {
        "approved": 0,
        "under_review": 0,
        "not_reviewed": 7,
        "deprecated": 0,
        "rejected": 0,
        "total": 7,
    },
    "species": {
        "reactants": [
            {
                "species_entry_ref": "spe_evwhah63tpbcfopksky6ji2diu",
                "species_entry_label": None,
                "smiles": "O",
                "formula": "H2O",
                "stoichiometry": 1,
                "participant_index": 1,
                "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
            },
            {
                "species_entry_ref": "spe_bcbdjwkip75yoziblpntwzblzu",
                "species_entry_label": None,
                "smiles": "[CH3]",
                "formula": "CH3",
                "stoichiometry": 1,
                "participant_index": 2,
                "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
            },
        ],
        "products": [
            {
                "species_entry_ref": "spe_bg76km6tw34gcbraa75vkwkyli",
                "species_entry_label": None,
                "smiles": "C",
                "formula": "CH4",
                "stoichiometry": 1,
                "participant_index": 1,
                "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
            },
            {
                "species_entry_ref": "spe_2lq3jupc22nwanghcqtshffoxu",
                "species_entry_label": None,
                "smiles": "[OH]",
                "formula": "OH",
                "stoichiometry": 1,
                "participant_index": 2,
                "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
            },
        ],
    },
    "kinetics": [
        {
            "kinetics_ref": "kin_spkzatwjlvmmnja3i5im4fl7hq",
            "scientific_origin": "computed",
            "model_kind": "modified_arrhenius",
            "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
            "parameters": {
                "A": 3025.44,
                "A_units": "cm3_mol_s",
                "n": 3.11242,
                "Ea_kj_mol": 39.9711,
            },
            "temperature_coverage": {
                "record_min_k": 300.0,
                "record_max_k": 3000.0,
            },
            "levels": {
                "geometry": {"display": "b3lyp/def2tzvp"},
                "frequency": {"display": "b3lyp/def2tzvp"},
                "energy": {"display": "b3lyp/def2tzvp"},
                "energy_source": "sp",
            },
        }
    ],
    "transition_states": [
        {
            "transition_state_ref": "ts_quylbcnqsbrrbem7dgg7p53ln4",
            "transition_state_entry_ref": "tse_yeccffkfo6p6d5exzv5iujxvuu",
            "status": "optimized",
            "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
            "evidence_summary": {
                "levels_of_theory": {
                    "opt": [{"display": "b3lyp/def2tzvp"}],
                    "freq": [{"display": "b3lyp/def2tzvp"}],
                    "sp": [{"display": "b3lyp/def2tzvp"}],
                    "irc": [{"display": "b3lyp/def2tzvp"}],
                },
            },
            "calculations": {
                "ts_opt": {"calculation_ref": "calc_asiuefa76hbbwoigp4cyzqepeu", "type": "opt"},
                "ts_freq": {"calculation_ref": "calc_rkr2yga5s5nycthaiqme4hta7a", "type": "freq"},
                "ts_sp": {"calculation_ref": "calc_sy73meqxsieuscaynnvxjzsqoi", "type": "sp"},
                "ts_irc": {"calculation_ref": "calc_6ebj6taluvremds7vyy75buh7q", "type": "irc"},
            },
            "dependencies": [],
        }
    ],
    "networks": [
        {
            "network_ref": "net_o6bt63kjeyvhvxx26w6kdi433a",
            "name": "hydrazine",
            "solve_temperature_min_k": 300.0,
            "solve_temperature_max_k": 2000.0,
            "solve_pressure_min_bar": 0.01,
            "solve_pressure_max_bar": 100.0,
            "channel_count": 21,
            "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
        }
    ],
    "review_records": None,
}

NETWORK_ONLY_ENVELOPE = {
    "request": {"include": ["species", "kinetics", "transition_states", "networks"]},
    "reaction_entry": {
        "reaction_entry_ref": "rxe_gw4unjmagt7lzc6dmpjfm5t6xu",
        "reaction_ref": "rxn_ss2j4rfyavzwq7oyapbb32oote",
        "equation": "NN <=> [H][H] + [N-]=[NH2+]",
        "reversible": True,
        "family": None,
        "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
        "atom_maps": [],
    },
    "review_summary": {"total": 5},
    "species": {
        "reactants": [
            {"species_entry_ref": "spe_a", "smiles": "NN", "participant_index": 1}
        ],
        "products": [
            {"species_entry_ref": "spe_b", "smiles": "[H][H]", "participant_index": 1},
            {"species_entry_ref": "spe_c", "smiles": "[N-]=[NH2+]", "participant_index": 2},
        ],
    },
    "kinetics": [],
    "transition_states": [
        {
            "transition_state_ref": "ts_x",
            "transition_state_entry_ref": "tse_x",
            "status": "optimized",
            "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
            "evidence_summary": {"levels_of_theory": {}},
            "calculations": {},
            "dependencies": [],
        }
    ],
    "networks": [
        {
            "network_ref": "net_o6bt63kjeyvhvxx26w6kdi433a",
            "name": "hydrazine",
            "solve_temperature_min_k": 300.0,
            "solve_temperature_max_k": 2000.0,
            "solve_pressure_min_bar": 0.01,
            "solve_pressure_max_bar": 100.0,
            "channel_count": 21,
            "review": {"status": "not_reviewed", "reviewed_at": None, "reviewer_kind": None},
        }
    ],
}

EMPTY_SECTIONS_ENVELOPE = {
    "reaction_entry": {
        "reaction_entry_ref": "rxe_empty",
        "reaction_ref": "rxn_empty",
        "equation": "A <=> B",
        "reversible": True,
        "family": None,
        "review": {"status": "not_reviewed"},
        "atom_maps": [],
    },
    "species": {
        "reactants": [{"species_entry_ref": "spe_a", "smiles": "A", "participant_index": 1}],
        "products": [{"species_entry_ref": "spe_b", "smiles": "B", "participant_index": 1}],
    },
    "kinetics": [],
    "transition_states": [],
    "networks": [],
}

CHOOSER_ENVELOPE = {
    "records": [
        {
            "reaction_ref": "rxn_naeqmg4l5wyqex5cl5tir2vt2y",
            "reaction_entry_ref": "rxe_tku6xu2lt3girf2rsiwl5uds4e",
            "equation": "NN <=> [H][H] + N=N",
            "availability": {"has_kinetics": False, "has_transition_state": True, "kinetics_count": 0},
        },
        {
            "reaction_ref": "rxn_naeqmg4l5wyqex5cl5tir2vt2y",
            "reaction_entry_ref": "rxe_kftcgjn7zalusaouojwi23z3gy",
            "equation": "NN <=> [H][H] + N=N",
            "availability": {"has_kinetics": False, "has_transition_state": True, "kinetics_count": 0},
        },
    ]
}


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------


class _StubReactionClient:
    """Stands in for TCKDBClient; records calls, never touches the network."""

    def __init__(self, base_url: str, timeout: float = 30.0, **kwargs):
        self.base_url = base_url
        self.timeout = timeout
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def get_reaction_full(self, ref, *, include=None, **kwargs):
        self.calls.append(("get_reaction_full", {"ref": ref, "include": include}))
        return self._get_reaction_full_impl(ref, include)

    def _get_reaction_full_impl(self, ref, include):
        raise NotImplementedError

    def search_reactions(self, *, reaction_ref=None, **kwargs):
        self.calls.append(("search_reactions", {"reaction_ref": reaction_ref}))
        return self._search_reactions_impl(reaction_ref)

    def _search_reactions_impl(self, reaction_ref):
        raise NotImplementedError

    def close(self):
        self.closed = True


def _make_stub(*, full=None, chooser=None, on_full=None, on_search=None):
    class Stub(_StubReactionClient):
        def _get_reaction_full_impl(self, ref, include):
            if on_full is not None:
                return on_full(ref, include)
            return full

        def _search_reactions_impl(self, reaction_ref):
            if on_search is not None:
                return on_search(reaction_ref)
            return chooser

    return Stub


# ---------------------------------------------------------------------------
# ref-prefix routing
# ---------------------------------------------------------------------------


def test_rxe_ref_calls_get_reaction_full(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cls = _make_stub(full=EMPTY_SECTIONS_ENVELOPE)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_ed66mj3ohtyien5rm2x3sb3rdu"])
    assert rc == cli.EXIT_OK


def test_rxn_ref_calls_search_reactions(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def on_search(reaction_ref):
        seen["ref"] = reaction_ref
        return CHOOSER_ENVELOPE

    stub_cls = _make_stub(on_search=on_search)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxn_naeqmg4l5wyqex5cl5tir2vt2y"])
    assert rc == cli.EXIT_OK
    assert seen["ref"] == "rxn_naeqmg4l5wyqex5cl5tir2vt2y"


def test_chooser_handles_null_entry_ref_without_crashing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A search record whose ``reaction_entry_ref`` is null must render a
    message, not raise ``TypeError`` out of ``", ".join(...)``."""
    envelope = {
        "records": [
            {
                "reaction_ref": "rxn_x",
                "reaction_entry_ref": None,
                "equation": "A <=> B",
                "availability": {"has_transition_state": True, "kinetics_count": 0},
            }
        ]
    }

    def on_search(reaction_ref):
        return envelope

    stub_cls = _make_stub(on_search=on_search)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxn_x"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "not recorded" in out
    assert "?" in out  # the hint line's placeholder for the missing ref


def test_int_ref_calls_get_reaction_full(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def on_full(ref, include):
        seen["ref"] = ref
        return EMPTY_SECTIONS_ENVELOPE

    stub_cls = _make_stub(on_full=on_full)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "123"])
    assert rc == cli.EXIT_OK
    assert seen["ref"] == "123"


def test_garbage_ref_exits_argparse(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main_tckdb(["get", "reaction", "not-a-valid-ref"])
    assert exc_info.value.code == cli.EXIT_ARGPARSE
    err = capsys.readouterr().err
    assert "invalid reaction reference" in err


# ---------------------------------------------------------------------------
# --json round-trip
# ---------------------------------------------------------------------------


def test_json_flag_round_trips_full_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    stub_cls = _make_stub(full=FULL_ENVELOPE)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_ed66mj3ohtyien5rm2x3sb3rdu", "--json"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.rstrip("\n") == json.dumps(FULL_ENVELOPE, indent=2)
    assert json.loads(out) == FULL_ENVELOPE


def test_json_flag_round_trips_chooser_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    stub_cls = _make_stub(chooser=CHOOSER_ENVELOPE)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxn_naeqmg4l5wyqex5cl5tir2vt2y", "--json"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert json.loads(out) == CHOOSER_ENVELOPE


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def test_table_renders_every_served_field(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    stub_cls = _make_stub(full=FULL_ENVELOPE)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_ed66mj3ohtyien5rm2x3sb3rdu"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out

    # Equation: formulas, not SMILES, since formula is served.
    assert "H2O + CH3 <=> CH4 + OH" in out
    # Kinetics row.
    assert "kin_spkzatwjlvmmnja3i5im4fl7hq" in out
    assert "modified_arrhenius" in out
    assert "3025.44 cm3_mol_s" in out
    assert "3.11242" in out
    assert "39.9711 kJ/mol" in out
    assert "300-3000 K" in out
    assert "b3lyp/def2tzvp" in out
    assert "sp" in out  # energy_source
    # Transition-state row.
    assert "tse_yeccffkfo6p6d5exzv5iujxvuu" in out
    assert "optimized" in out
    assert "calc_asiuefa76hbbwoigp4cyzqepeu" in out
    assert "calc_rkr2yga5s5nycthaiqme4hta7a" in out
    assert "calc_sy73meqxsieuscaynnvxjzsqoi" in out
    assert "calc_6ebj6taluvremds7vyy75buh7q" in out
    # Networks row.
    assert "net_o6bt63kjeyvhvxx26w6kdi433a" in out
    assert "hydrazine" in out
    assert "300-2000 K" in out
    assert "0.01-100 bar" in out
    assert "21" in out
    # Kinetics columns name a level of theory, not a value.
    assert "geometry_level" in out
    assert "frequency_level" in out
    assert "energy_level" in out


def test_null_fields_print_not_recorded_not_the_string_none(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A present-but-``null`` ref must never render as the literal ``None``."""
    envelope = {
        "reaction_entry": {
            "reaction_entry_ref": None,
            "reaction_ref": None,
            "equation": "A <=> B",
            "reversible": True,
            "family": None,
            "review": {"status": "not_reviewed"},
            "atom_maps": [],
        },
        "species": {
            "reactants": [{"species_entry_ref": "spe_a", "smiles": "A", "participant_index": 1}],
            "products": [{"species_entry_ref": "spe_b", "smiles": "B", "participant_index": 1}],
        },
        "kinetics": [
            {
                "kinetics_ref": None,
                "model_kind": None,
                "parameters": {},
                "temperature_coverage": {},
            }
        ],
        "transition_states": [
            {
                "transition_state_entry_ref": None,
                "status": None,
                "evidence_summary": {"levels_of_theory": {}},
                "calculations": {"ts_opt": {"calculation_ref": None}},
                "dependencies": [],
            }
        ],
        "networks": [
            {
                "network_ref": None,
                "name": None,
                "channel_count": None,
            }
        ],
    }
    stub_cls = _make_stub(full=envelope)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_x"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "None" not in out
    assert "entry not recorded  reaction not recorded" in out
    assert "not recorded" in out  # present throughout the null rows


def test_network_only_sentence_when_kinetics_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    stub_cls = _make_stub(full=NETWORK_ONLY_ENVELOPE)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_gw4unjmagt7lzc6dmpjfm5t6xu"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert (
        "No rate coefficient deposited on this entry; phenomenological k(T,P) "
        "for this system is served by network net_o6bt63kjeyvhvxx26w6kdi433a"
    ) in out


def test_empty_sections_print_none_deposited(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    stub_cls = _make_stub(full=EMPTY_SECTIONS_ENVELOPE)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_empty"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert "none deposited" in out
    # No bare blank line stands in for an empty section.
    kinetics_idx = next(i for i, line in enumerate(lines) if line == "Kinetics:")
    assert lines[kinetics_idx + 1].strip() == "none deposited"
    ts_idx = next(i for i, line in enumerate(lines) if line == "Transition states:")
    assert lines[ts_idx + 1].strip() == "none deposited"
    networks_idx = next(i for i, line in enumerate(lines) if line == "Networks:")
    assert lines[networks_idx + 1].strip() == "none deposited"


def test_absent_section_is_not_rendered(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    envelope = dict(EMPTY_SECTIONS_ENVELOPE)
    envelope.pop("networks")  # simulate include= without "networks"
    stub_cls = _make_stub(full=envelope)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_empty", "--include", "species"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Networks:" not in out


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_404_exits_4(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    class _FailingStub(_StubReactionClient):
        def _get_reaction_full_impl(self, ref, include):
            raise TCKDBHTTPError(
                "not found",
                status_code=404,
                code="handle_not_found",
                detail="reaction_entry not found",
                response_json={"code": "handle_not_found", "detail": "not found"},
                response_text=None,
                headers={},
            )

    monkeypatch.setattr(cli, "TCKDBClient", _FailingStub)
    rc = cli.main_tckdb(["get", "reaction", "rxe_doesnotexist"])
    assert rc == cli.EXIT_NOT_FOUND
    err = capsys.readouterr().err
    assert "not found" in err


def test_argparse_misuse_exits_2() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main_tckdb(["get", "reaction"])  # missing required ref
    assert exc_info.value.code == cli.EXIT_ARGPARSE


def test_transport_error_exits_1(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    class _FailingStub(_StubReactionClient):
        def _get_reaction_full_impl(self, ref, include):
            raise TCKDBConnectionError("Network error: boom")

    monkeypatch.setattr(cli, "TCKDBClient", _FailingStub)
    rc = cli.main_tckdb(["get", "reaction", "rxe_x"])
    assert rc == cli.EXIT_FAILURES
    err = capsys.readouterr().err
    assert "Network error" in err


# ---------------------------------------------------------------------------
# --include defaults and override
# ---------------------------------------------------------------------------


def test_default_include_is_exactly_four_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def on_full(ref, include):
        seen["include"] = include
        return EMPTY_SECTIONS_ENVELOPE

    stub_cls = _make_stub(on_full=on_full)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    cli.main_tckdb(["get", "reaction", "rxe_x"])
    assert seen["include"] == ["species", "kinetics", "transition_states", "networks"]
    assert cli.DEFAULT_INCLUDE == ["species", "kinetics", "transition_states", "networks"]


def test_include_override_replaces_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def on_full(ref, include):
        seen["include"] = include
        return EMPTY_SECTIONS_ENVELOPE

    stub_cls = _make_stub(on_full=on_full)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    cli.main_tckdb(
        ["get", "reaction", "rxe_x", "--include", "species", "--include", "kinetics"]
    )
    assert seen["include"] == ["species", "kinetics"]


def test_comma_joined_include_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """The form advertised by --help (and the plan) must actually work.

    ``--help`` shows the default as the comma-joined string
    ``species,kinetics,transition_states,networks``; a user pasting that
    exact form as one ``--include`` value must not be silently treated as
    a single bogus token.
    """
    seen = {}

    def on_full(ref, include):
        seen["include"] = include
        return EMPTY_SECTIONS_ENVELOPE

    stub_cls = _make_stub(on_full=on_full)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    cli.main_tckdb(
        ["get", "reaction", "rxe_x", "--include", "species,kinetics,transition_states,networks"]
    )
    assert seen["include"] == ["species", "kinetics", "transition_states", "networks"]


def test_comma_joined_include_mixes_with_repeated_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = {}

    def on_full(ref, include):
        seen["include"] = include
        return EMPTY_SECTIONS_ENVELOPE

    stub_cls = _make_stub(on_full=on_full)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    cli.main_tckdb(
        ["get", "reaction", "rxe_x", "--include", "species,kinetics", "--include", "networks"]
    )
    assert seen["include"] == ["species", "kinetics", "networks"]


# ---------------------------------------------------------------------------
# unknown_include_token fallback (pre-deploy API without `networks`)
# ---------------------------------------------------------------------------


def test_unknown_include_token_retries_once_without_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    call_count = {"n": 0}

    def on_full(ref, include):
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert "networks" in include
            raise TCKDBHTTPError(
                "unknown_include_token",
                status_code=422,
                code="unknown_include_token",
                detail=(
                    "unknown_include_token: token(s) ['networks'] not legal for "
                    "/scientific/reaction-entries/{id}/full. Legal tokens: "
                    "['species', 'kinetics', 'transition_states']"
                ),
                response_json={},
                response_text=None,
                headers={},
            )
        assert "networks" not in include
        envelope = dict(EMPTY_SECTIONS_ENVELOPE)
        envelope.pop("networks")
        return envelope

    stub_cls = _make_stub(on_full=on_full)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_x"])
    assert rc == cli.EXIT_OK
    assert call_count["n"] == 2
    err = capsys.readouterr().err
    assert "does not support include=networks" in err


def test_unknown_include_token_does_not_retry_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second 422 after the retry must propagate, not loop."""
    call_count = {"n": 0}

    def on_full(ref, include):
        call_count["n"] += 1
        raise TCKDBHTTPError(
            "unknown_include_token",
            status_code=422,
            code="unknown_include_token",
            detail="unknown_include_token: token(s) ['networks'] not legal for X.",
            response_json={},
            response_text=None,
            headers={},
        )

    stub_cls = _make_stub(on_full=on_full)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_x"])
    assert rc == cli.EXIT_FAILURES
    assert call_count["n"] == 2  # first attempt + exactly one retry, then give up


def test_unknown_token_outside_forward_compat_set_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A typo like ``speceis`` must surface as an error, not vanish.

    Only tokens in ``_FORWARD_COMPAT_INCLUDE_TOKENS`` (today: ``networks``)
    are ever silently dropped and retried. A rejected token outside that
    set is a real usage mistake: retrying without it would silently fall
    back toward the server's default include set and exit 0 with data the
    caller never asked for, which is worse than failing loudly.
    """
    call_count = {"n": 0}

    def on_full(ref, include):
        call_count["n"] += 1
        detail = (
            "unknown_include_token: token(s) ['speceis'] not legal for "
            "/scientific/reaction-entries/{id}/full. Legal tokens: "
            "['species', 'kinetics', 'transition_states', 'networks']"
        )
        raise TCKDBHTTPError(
            detail,
            status_code=422,
            code="unknown_include_token",
            detail=detail,
            response_json={},
            response_text=None,
            headers={},
        )

    stub_cls = _make_stub(on_full=on_full)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_x", "--include", "speceis"])
    assert rc == cli.EXIT_FAILURES
    assert call_count["n"] == 1  # no retry -- not a known forward-compat token
    err = capsys.readouterr().err
    assert "speceis" in err


def test_unknown_token_mixed_with_networks_is_not_silently_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected typo riding alongside the known ``networks`` token must
    also block the retry -- dropping only ``networks`` would silently
    resend a request that still contains the typo, hiding it behind a
    second failure the caller has no reason to expect."""
    call_count = {"n": 0}

    def on_full(ref, include):
        call_count["n"] += 1
        raise TCKDBHTTPError(
            "unknown_include_token",
            status_code=422,
            code="unknown_include_token",
            detail=(
                "unknown_include_token: token(s) ['networks', 'speceis'] not "
                "legal for /scientific/reaction-entries/{id}/full."
            ),
            response_json={},
            response_text=None,
            headers={},
        )

    stub_cls = _make_stub(on_full=on_full)
    monkeypatch.setattr(cli, "TCKDBClient", stub_cls)
    rc = cli.main_tckdb(["get", "reaction", "rxe_x", "--include", "networks,speceis"])
    assert rc == cli.EXIT_FAILURES
    assert call_count["n"] == 1
