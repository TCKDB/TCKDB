"""Builder-emission diagnostics.

The builder layer accepts some fields for forward compatibility that
the current backend bundle schemas do not yet carry on the wire.
``upload.emission_diagnostics()`` lets producers see, before sending,
which of the values they supplied will actually round-trip through
the server.

Diagnostic codes are **stable strings**. Adding a new diagnostic is
backward-compatible; renaming an existing one is not. The current
catalog of warning codes is:

- ``transport_not_emitted_in_computed_species_bundle``
- ``transport_not_emitted_in_computed_reaction_bundle``
- ``thermo_source_calculations_not_emitted_in_computed_reaction_bundle``
- ``artifact_upload_requires_second_phase``

All three reflect known gaps in today's bundle schemas. When the
backend grows the matching fields, the assemblers will flip emission
on in one place and these warnings will stop appearing — no client
API change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["Diagnostic", "DIAG_CODES"]


# Frozen so producers can safely compare, hash, or aggregate
# diagnostics across uploads without worrying about mutation.
@dataclass(frozen=True)
class Diagnostic:
    """One emission-diagnostic record from a builder upload object.

    ``level`` is ``"info"`` (purely descriptive) or ``"warning"``
    (the builder accepted data it cannot ship to the server today).
    ``code`` is a stable token meant for machine matching; ``message``
    is the human-readable explanation; ``path`` is the logical
    builder path of the field that triggered the diagnostic — for
    example ``"species_transport[CH4]"``.
    """

    level: Literal["info", "warning"]
    code: str
    message: str
    path: str


# Canonical list of currently-defined codes. Exported so tests can pin
# the strings without importing the upload classes that emit them.
DIAG_CODES = type(
    "DiagCodes",
    (),
    {
        "TRANSPORT_NOT_EMITTED_IN_COMPUTED_SPECIES_BUNDLE":
            "transport_not_emitted_in_computed_species_bundle",
        "TRANSPORT_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE":
            "transport_not_emitted_in_computed_reaction_bundle",
        "THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE":
            "thermo_source_calculations_not_emitted_in_computed_reaction_bundle",
        "ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE":
            "artifact_upload_requires_second_phase",
    },
)()
