"""The deposit tests publish a real release, so they reuse the release fixtures."""

from __future__ import annotations

from tests.services.release.conftest import (  # re-exported fixtures
    attested_submission,
    curator,
    draft_release,
    policy,
    second_curator,
    species_entry,
    thermo_candidates,
)
