"""Fixtures shared by the integration tests.

The deposit round trip publishes a real release, so it reuses the release
service's corpus fixtures (one species entry, two approved thermo
candidates, a curator, a policy, a draft release) rather than building a
second, slightly different corpus.
"""

from __future__ import annotations

from tests.services.release.conftest import (  # re-exported fixtures
    curator,
    draft_release,
    policy,
    second_curator,
    species_entry,
    thermo_candidates,
)
