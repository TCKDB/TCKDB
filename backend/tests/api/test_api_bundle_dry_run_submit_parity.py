"""``/bundles/dry-run`` must refuse exactly what ``/bundles/submit`` refuses.

What was wrong (#577)
---------------------
A dry run exists to answer one question: will this bundle submit? It
answered it from a read-only *preview* -- identity and provenance lookups
-- while submit ran that preview as a gate and then the real
``persist_thermo_upload`` / ``persist_kinetics_upload`` workflows, whose
own checks the preview never ran. So every check living in those workflows
was submit-only. The enthalpy-reference rule is the one the producer
contract review found; the cases below are the others, each of which
passed a dry run and was then refused on submit.

The fix does not copy those checks into the preview, because a copy is a
second place for the two routes to disagree and is how this drifted. The
dry run now *rehearses* ``submit_contribution_bundle`` -- the same function
the submit route calls -- inside a SAVEPOINT that is always rolled back,
and reports the refusal submit would give, rendered by the same exception
handler.

How these tests keep it from drifting
-------------------------------------
* ``test_dry_run_and_submit_refuse_identically`` runs every known-bad
  bundle through both routes and requires the same ``code`` and the same
  message. The accepted bundles run through it too, so a dry run that
  refused everything would fail rather than pass.
* ``test_dry_run_runs_every_step_submit_runs`` traces the backend
  functions each route calls and requires dry-run's set to contain
  submit's. A new check added anywhere under submit is covered by the
  dry run automatically, or this test names it.
* ``test_dry_run_verdict_comes_from_submit_itself`` proves the refusal is
  submit's own, not a lookalike.

Assertions are on exact codes, never on substrings of the message.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.common import CalculationType
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.db.models.thermo import Thermo
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"
DRY_RUN = "/api/v1/bundles/dry-run"
SUBMIT = "/api/v1/bundles/submit"


def _example(filename: str) -> dict:
    return json.loads((EXAMPLES_DIR / filename).read_text())


def _thermo(**changes: Any) -> dict:
    """The example thermo bundle with its one record edited.

    A value of ``None`` deletes the key rather than sending ``null``: an
    absent declaration is the case under test, and ``null`` would exercise
    the same path only by coincidence.
    """
    bundle = _example("thermo-bundle-v0.json")
    record = bundle["records"]["thermo_uploads"][0]
    for key, value in changes.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    return bundle


def _kinetics(**changes: Any) -> dict:
    bundle = _example("kinetics-bundle-v0.json")
    bundle["records"]["kinetics_uploads"][0].update(changes)
    return bundle


def _interpretations(statmech_ref: str) -> list[dict]:
    """A complete interpretation set for H + H -> H2, all naming one ref."""
    conventions = {
        "ensemble_policy": "lowest_energy_conformer",
        "standard_state_convention": "ideal_gas_1_bar",
        "degeneracy_interpretation": "external_symmetry_number",
    }
    return [
        {"role": "reactant", "participant_index": 1, "statmech_ref": statmech_ref, **conventions},
        {"role": "reactant", "participant_index": 2, "statmech_ref": statmech_ref, **conventions},
        {"role": "product", "participant_index": 1, "statmech_ref": statmech_ref, **conventions},
    ]


def _seed_foreign_calculation(session: Session) -> int:
    """A real calculation owned by a species entry no bundle here names."""
    species = make_species(session)
    entry = make_species_entry(session, species)
    return make_calculation(
        session, type=CalculationType.opt, species_entry_id=entry.id
    ).id


def _seed_foreign_ts_entry_ref(session: Session) -> str:
    """A real TS entry whose reaction entry is not H + H -> H2."""
    reactant = make_species(session)
    product = make_species(session)
    reactant_entry = make_species_entry(session, reactant)
    product_entry = make_species_entry(session, product)
    reaction = make_chem_reaction(
        session, reactants=[reactant], products=[product], reversible=False
    )
    reaction_entry = make_reaction_entry(
        session,
        reaction=reaction,
        reactant_entries=[reactant_entry],
        product_entries=[product_entry],
    )
    ts = make_transition_state(session, reaction_entry=reaction_entry)
    return make_transition_state_entry(session, transition_state=ts).public_ref


def _wigner(ts_ref: str) -> dict:
    return {
        "tunneling_model": "wigner",
        "tunneling_application": {
            "model": "wigner",
            "transition_state_entry_ref": ts_ref,
            "imaginary_frequency_cm1": -1500.0,
        },
    }


_LOT = {"method": "ccsd(t)", "basis": "cc-pvtz"}

#: Bundles submit refuses. Each is ``(bundle builder, submit's status,
#: submit's code)``; a builder receives the test session so it can seed the
#: rows a reference needs. Every one of them passed ``/bundles/dry-run``
#: before #577 except ``preview_blocking_smiles``, whose refusal the preview
#: already saw but reported without submit's code.
REFUSED: dict[str, tuple[Callable[[Session], dict], int, str]] = {
    # app/workflows/thermo.py assert_enthalpy_reference, called first in
    # persist_thermo_upload -- the gap #577 was filed for.
    "thermo_enthalpy_declaration_absent": (
        lambda _s: _thermo(enthalpy_reference_kind=None),
        422,
        "enthalpy_declaration_absent",
    ),
    # Same shared rule, the other direction: a declaration with nothing
    # to declare (entropy-only record).
    "thermo_enthalpy_declaration_without_content": (
        lambda _s: _thermo(h298_kj_mol=None, h298_uncertainty_kj_mol=None),
        422,
        "enthalpy_declaration_without_content",
    ),
    # Same shared rule: a near-miss of the one legal token. The request
    # schema accepts any string here, so only the workflow refuses it.
    "thermo_enthalpy_reference_kind_unrecognized": (
        lambda _s: _thermo(enthalpy_reference_kind="FORMATION_298K "),
        422,
        "enthalpy_reference_kind_unrecognized",
    ),
    # app/workflows/thermo.py _resolve_statmech_id: a cited statmech id
    # that names no row.
    "thermo_unknown_existing_statmech_id": (
        lambda _s: _thermo(existing_statmech_id=2_000_000_000),
        404,
        "unknown_statmech_ref",
    ),
    # app/workflows/thermo.py _resolve_source_calculation: a cited
    # calculation id that names no row.
    "thermo_unknown_existing_calculation_id": (
        lambda _s: _thermo(
            source_calculations=[
                {"existing_calculation_id": 2_000_000_000, "role": "sp"}
            ]
        ),
        404,
        "unknown_calculation_ref",
    ),
    # app/workflows/thermo.py _resolve_source_calculation ->
    # assert_calculation_owned_by: a real calculation, of another species.
    # Decided against the species entry the import resolves or creates, so
    # it can only be answered with the import's writes in hand.
    "thermo_source_calculation_owner_mismatch": (
        lambda s: _thermo(
            source_calculations=[
                {"existing_calculation_id": _seed_foreign_calculation(s), "role": "opt"}
            ]
        ),
        422,
        "thermo_source_calculation_owner_mismatch",
    ),
    # app/workflows/kinetics.py _resolve_interpretation_assignments: a
    # statmech_ref that names nothing.
    "kinetics_unknown_interpretation_statmech_ref": (
        lambda _s: _kinetics(
            interpretation_assignments=_interpretations("sm_" + "0" * 26)
        ),
        404,
        "unknown_statmech_ref",
    ),
    # app/workflows/kinetics.py _resolve_ts_anchored_reaction_entry: a TS
    # ref that names nothing.
    "kinetics_unknown_tunneling_ts_ref": (
        lambda _s: _kinetics(**_wigner("tse_" + "0" * 26)),
        404,
        "unknown_transition_state_entry_ref",
    ),
    # app/workflows/kinetics.py _resolve_ts_anchored_reaction_entry: a real
    # TS whose reaction is not the one submitted. A bare ValueError.
    "kinetics_ts_owned_by_another_reaction": (
        lambda s: _kinetics(**_wigner(_seed_foreign_ts_entry_ref(s))),
        422,
        "validation_error",
    ),
    # app/workflows/kinetics.py _find_sp_for_species: an energy level
    # whose single points were never deposited. A bare ValueError, so
    # submit answers with the generic code -- and the dry run must too.
    "kinetics_energy_level_without_sp": (
        lambda _s: _kinetics(energy_level_of_theory=_LOT),
        422,
        "validation_error",
    ),
    # The preview's own refusal. Submit's gate turns it into a
    # ``domain_error``; the dry run used to show the item error but never
    # the code a submit would carry.
    "preview_blocking_smiles": (
        lambda _s: _thermo(
            species_entry={"smiles": "not-a-smiles(", "charge": 0, "multiplicity": 1}
        ),
        400,
        "domain_error",
    ),
}

ACCEPTED: dict[str, Callable[[], dict]] = {
    "thermo_example": lambda: _example("thermo-bundle-v0.json"),
    "kinetics_example": lambda: _example("kinetics-bundle-v0.json"),
}


def _dry_run_errors(body: dict) -> list[dict]:
    return [m for m in body["messages"] if m["level"] == "error"]


def _counts(session) -> dict[str, int]:
    models = (
        Species,
        SpeciesEntry,
        ChemReaction,
        ReactionEntry,
        Thermo,
        Kinetics,
        Submission,
        SubmissionAuditEvent,
        SubmissionRecordLink,
    )
    return {
        m.__tablename__: session.scalar(select(func.count()).select_from(m)) or 0
        for m in models
    }


# ---------------------------------------------------------------------------
# The enthalpy case, stated on its own
# ---------------------------------------------------------------------------


def test_dry_run_refuses_an_undeclared_enthalpy_as_submit_does(client) -> None:
    bundle = _thermo(enthalpy_reference_kind=None)

    dry = client.post(DRY_RUN, json=bundle)
    assert dry.status_code == 200, dry.text
    errors = _dry_run_errors(dry.json())
    assert [e["code"] for e in errors] == ["enthalpy_declaration_absent"]
    assert errors[0]["field"] == "enthalpy_reference_kind"
    assert dry.json()["bundle_valid"] is False
    assert dry.json()["summary"]["errors"] >= 1

    sub = client.post(SUBMIT, json=bundle)
    assert sub.status_code == 422, sub.text
    assert sub.json()["code"] == "enthalpy_declaration_absent"
    assert errors[0]["message"] == sub.json()["detail"]


# ---------------------------------------------------------------------------
# Parity over every known-bad bundle, and over the accepted ones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_dry_run_and_submit_refuse_identically(client, case: str) -> None:
    build, status, code = REFUSED[case]
    bundle = build(client._db_session)

    # Dry run first: a refused submit may leave the shared test session
    # holding its partial writes, and the dry run must see the state
    # submit saw.
    before = _counts(client._db_session)
    dry = client.post(DRY_RUN, json=copy.deepcopy(bundle))
    after = _counts(client._db_session)
    assert dry.status_code == 200, dry.text
    assert before == after, "the rehearsal left rows behind"

    sub = client.post(SUBMIT, json=copy.deepcopy(bundle))
    assert (sub.status_code, sub.json()["code"]) == (status, code), sub.text

    body = dry.json()
    errors = _dry_run_errors(body)
    submit_refusals = [e for e in errors if e["code"] == code]
    assert len(submit_refusals) == 1, body["messages"]
    assert submit_refusals[0]["message"] == sub.json()["detail"]
    assert body["bundle_valid"] is False
    assert body["summary"]["errors"] >= 1


@pytest.mark.parametrize("case", sorted(ACCEPTED))
def test_dry_run_and_submit_accept_identically(client, case: str) -> None:
    bundle = ACCEPTED[case]()

    before = _counts(client._db_session)
    dry = client.post(DRY_RUN, json=copy.deepcopy(bundle))
    assert _counts(client._db_session) == before
    assert dry.status_code == 200, dry.text
    assert _dry_run_errors(dry.json()) == []
    assert dry.json()["bundle_valid"] is True

    sub = client.post(SUBMIT, json=copy.deepcopy(bundle))
    assert sub.status_code == 201, sub.text


def test_a_schema_refusal_is_the_same_422_on_both_routes(client) -> None:
    """Request validation runs before either route; pinned so it stays so."""
    bundle = _example("thermo-bundle-v0.json")
    del bundle["bundle_kind"]
    dry = client.post(DRY_RUN, json=copy.deepcopy(bundle))
    sub = client.post(SUBMIT, json=copy.deepcopy(bundle))
    assert dry.status_code == sub.status_code == 422
    assert dry.json()["code"] == sub.json()["code"]


# ---------------------------------------------------------------------------
# Structural guards: the dry run's verdict *is* submit's
# ---------------------------------------------------------------------------


def test_dry_run_verdict_comes_from_submit_itself(client, monkeypatch) -> None:
    """Replace submit's workflow with one that refuses with a sentence
    nothing else emits, under a catalogued code. If the dry run reports it,
    its verdict came from the function the submit route calls, not from a
    copy of its checks."""
    from app.api.routes import bundles as bundles_route
    from app.workflows import contribution_bundle_submit as submit_module

    # The function the submit route calls is the one the rehearsal calls.
    assert bundles_route.submit_contribution_bundle is submit_module.submit_contribution_bundle

    def _refuse(*_args, **_kwargs):
        raise CodedValueError(
            "enthalpy_declaration_absent",
            "sentinel 577: refused by the replaced submit workflow",
            message_prefix=False,
        )

    monkeypatch.setattr(submit_module, "submit_contribution_bundle", _refuse)

    # The example bundle is one both routes accept unpatched (see
    # test_dry_run_and_submit_accept_identically), so this refusal can only
    # have come from the replacement.
    dry = client.post(DRY_RUN, json=_example("thermo-bundle-v0.json"))
    assert dry.status_code == 200, dry.text
    assert [(e["code"], e["message"]) for e in _dry_run_errors(dry.json())] == [
        (
            "enthalpy_declaration_absent",
            "sentinel 577: refused by the replaced submit workflow",
        )
    ]
    assert dry.json()["bundle_valid"] is False


def test_a_rehearsal_that_tries_to_commit_fails_loudly_and_keeps_nothing(
    client, monkeypatch
) -> None:
    """A future service that commits mid-submit must not turn a dry run
    into a submit. The rehearsal refuses the commit; the route answers 500,
    and no row the rehearsal wrote survives."""
    from app.workflows import contribution_bundle_submit as submit_module

    real_submit = submit_module.submit_contribution_bundle

    def _submit_then_commit(session, bundle, *, actor):
        real_submit(session, bundle, actor=actor)
        session.commit()

    monkeypatch.setattr(submit_module, "submit_contribution_bundle", _submit_then_commit)

    session = client._db_session
    before = _counts(session)
    with pytest.raises(submit_module.DryRunCommitRefused):
        client.post(DRY_RUN, json=_example("thermo-bundle-v0.json"))
    assert _counts(session) == before


#: Functions only the submit route may call, each with its reason. Anything
#: else submit calls that the dry run does not is a submit-only check.
_SUBMIT_ONLY_BY_DESIGN: dict[str, str] = {}

_TRACED_PREFIXES = ("app.workflows.", "app.services.", "app.chemistry.", "tckdb_schemas.")


def _trace_calls(action: Callable[[], Any]) -> set[str]:
    """Every backend function ``action`` calls, on any thread.

    The route body runs on a threadpool worker, so the hook is installed
    on every thread, not just this one.
    """
    seen: set[str] = set()

    def hook(frame, event, _arg):
        if event == "call":
            module = frame.f_globals.get("__name__", "")
            if module.startswith(_TRACED_PREFIXES):
                seen.add(f"{module}.{frame.f_code.co_qualname}")
        return None

    threading.setprofile_all_threads(hook)
    sys.setprofile(hook)
    try:
        action()
    finally:
        sys.setprofile(None)
        threading.setprofile_all_threads(None)
    return seen


@pytest.mark.parametrize("case", sorted(ACCEPTED))
def test_dry_run_runs_every_step_submit_runs(client, case: str) -> None:
    bundle = ACCEPTED[case]()

    dry_calls = _trace_calls(
        lambda: client.post(DRY_RUN, json=copy.deepcopy(bundle)).raise_for_status()
    )
    submit_calls = _trace_calls(
        lambda: client.post(SUBMIT, json=copy.deepcopy(bundle)).raise_for_status()
    )

    # Not vacuous: submit's own workflow, and the rule #577 was filed for
    # on the thermo path, are in what was traced.
    assert "app.workflows.contribution_bundle_submit.submit_contribution_bundle" in submit_calls
    if case.startswith("thermo"):
        assert "tckdb_schemas.enthalpy_reference.enthalpy_reference_error" in submit_calls

    submit_only = submit_calls - dry_calls - set(_SUBMIT_ONLY_BY_DESIGN)
    assert submit_only == set(), (
        "submit ran these and the dry run did not; each is a check a dry "
        f"run cannot predict: {sorted(submit_only)}"
    )
