"""The two reaction-carrying upload routes agree on what an omitted
``reversible`` means (issue #583)."""

from __future__ import annotations

from tckdb_schemas.workflows.computed_reaction_upload import (
    ComputedReactionUploadRequest,
)
from tckdb_schemas.workflows.transition_state_upload import TSReactionUpload

_SIDES = {
    "reactants": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}],
    "products": [{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}],
}


def test_ts_reaction_accepts_omitted_reversible_as_true() -> None:
    assert TSReactionUpload(**_SIDES).reversible is True


def test_ts_reaction_keeps_an_explicit_false() -> None:
    assert TSReactionUpload(reversible=False, **_SIDES).reversible is False


def test_both_routes_declare_the_same_default() -> None:
    ts = TSReactionUpload.model_fields["reversible"]
    computed = ComputedReactionUploadRequest.model_fields["reversible"]
    assert not ts.is_required()
    assert not computed.is_required()
    assert ts.default is computed.default is True
