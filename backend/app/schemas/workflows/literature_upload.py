"""Re-export shim — ``LiteratureUploadRequest`` now lives in
``tckdb_schemas.literature``.

This module intentionally has **no standalone ``/uploads/literature`` route**.
``LiteratureUploadRequest`` is a nested payload consumed by the thermo,
kinetics, conformer, network, transport, transition-state, computed-reaction,
and energy-correction upload flows. Literature rows are created/resolved by
``app.services.literature_resolution`` when a parent upload embeds one.
"""

from tckdb_schemas.literature import LiteratureUploadRequest  # noqa: F401
