"""Experimental scientific-upload builder layer for ``tckdb-client``.

The builder layer constructs upload payloads from chemistry-shaped
Python objects (``Species``, ``Calculation.opt(...)``, …) and hands
them to the thin HTTP client at :class:`tckdb_client.TCKDBClient`.
It does not own HTTP, auth, or any backend state.

Phase 1 supports computed-species uploads only. See
``clients/python/docs/builder_api_mvp.md`` for the full spec and
phased roadmap.

Importing this subpackage must remain backend-free — no ``app.*``,
``backend.*``, SQLAlchemy, FastAPI, RDKit, or pydantic-settings
imports may sneak in (enforced by ``tests/test_builders_no_backend_imports``).
"""

from __future__ import annotations

from tckdb_client.builders.calculation import (
    Calculation,
    LevelOfTheory,
    SoftwareRelease,
)
from tckdb_client.builders.geometry import Geometry
from tckdb_client.builders.kinetics import Kinetics
from tckdb_client.builders.reaction import ChemReaction, TransitionState
from tckdb_client.builders.species import Species
from tckdb_client.builders.thermo import Thermo
from tckdb_client.builders.uploads import (
    ComputedReactionUpload,
    ComputedSpeciesUpload,
)
from tckdb_client.builders.validation import TCKDBBuilderValidationError

__all__ = [
    "Calculation",
    "ChemReaction",
    "ComputedReactionUpload",
    "ComputedSpeciesUpload",
    "Geometry",
    "Kinetics",
    "LevelOfTheory",
    "Species",
    "SoftwareRelease",
    "Thermo",
    "TransitionState",
    "TCKDBBuilderValidationError",
]
