"""The statmech selection guide's request example validates, and the guide names every outcome and surfaced advisory."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.schemas.reads.scientific_statmech_selection import StatmechSelectionRequest
from app.services.statmech_selection.decision import SURFACED_ADVISORIES
from app.services.statmech_selection.vocabulary import Outcome

GUIDE = Path(__file__).resolve().parents[3] / "docs/guides/selecting_statmech_models.md"
TEXT = GUIDE.read_text()


def test_the_guide_has_exactly_the_one_request_example_this_test_checks():
    assert len(re.findall(r"```json\n", TEXT)) == 1  # a new example must be added here, or it is unchecked


def test_the_request_example_is_a_valid_request():
    (block,) = re.findall(r"```json\n(.*?)\n```", TEXT, flags=re.S)
    request = StatmechSelectionRequest.model_validate(json.loads(block)).service_request()
    assert request.task.value == "within_basin_thermal" and request.intent.value == "qualify"


def test_every_outcome_is_named_in_the_guide():
    missing = [o.value for o in Outcome if f"`{o.value}`" not in TEXT]
    assert missing == []


def test_every_refusal_code_the_surface_returns_is_named_in_the_guide():
    codes = (
        "statmech_selection_bound_exceeded",
        "statmech_selection_unsupported",
        "statmech_selection_scope_owner_mismatch",
        "unknown_statmech_candidate_ref",
        "handle_not_found",
    )
    assert [c for c in codes if c not in TEXT] == []
    assert "cannot be re-posted" in TEXT  # the echoed target is the normalised form


def test_every_surfaced_advisory_is_explained_in_the_guide():
    missing = [prefix for prefix in SURFACED_ADVISORIES if prefix not in TEXT]
    assert missing == []
