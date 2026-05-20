"""CCCBDB → TCKDB payload builders (Phase 2a).

Builders consume :mod:`app.importers.cccbdb` parser records and emit
TCKDB-compatible upload payloads. They never touch the database.

Public entry points:

* :func:`build_experimental_species_payload` — top-level, returns a
  :class:`BuildResult` containing identity + thermo + statmech +
  geometry payloads plus structured ``external_source`` provenance
  and a list of ``warnings`` for parsed values that have no
  first-class destination.
"""

from app.importers.cccbdb.builders.common import (
    BuildResult,
    ExternalSourceMetadata,
)
from app.importers.cccbdb.builders.experimental_species_payload import (
    build_experimental_species_payload,
)
from app.importers.cccbdb.builders.geometry_payload import (
    build_geometry_payload,
)
from app.importers.cccbdb.builders.molecular_property_payload import (
    CCCBDBMolecularPropertyBuildResult,
    build_molecular_property_payloads_from_property_table,
)
from app.importers.cccbdb.builders.species_payload import (
    build_species_entry_identity_payload,
)
from app.importers.cccbdb.builders.statmech_payload import (
    build_statmech_payload,
)
from app.importers.cccbdb.builders.thermo_payload import (
    build_thermo_payload,
)

__all__ = [
    "BuildResult",
    "CCCBDBMolecularPropertyBuildResult",
    "ExternalSourceMetadata",
    "build_experimental_species_payload",
    "build_geometry_payload",
    "build_molecular_property_payloads_from_property_table",
    "build_species_entry_identity_payload",
    "build_statmech_payload",
    "build_thermo_payload",
]
