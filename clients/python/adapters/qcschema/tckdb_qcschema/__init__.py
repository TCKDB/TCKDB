"""MolSSI QCSchema (QCElemental) importer adapter for TCKDB.

Five independently-testable stages, mirroring ``tckdb_chemkin``'s layout:

1. :mod:`tckdb_qcschema.reader` -- family dispatch (v1 vs v2) and strict
   class validation, producing an internal :class:`~tckdb_qcschema.reader.QCRecord`.
2. :mod:`tckdb_qcschema.molecule` -- QCSchema ``Molecule`` -> TCKDB geometry
   and identity (bohr->angstrom, isotopes, ghost/fragment refusal).
3. :mod:`tckdb_qcschema.hessian` -- full-matrix ``return_result`` -> packed
   lower triangle, symmetry-checked.
4. :mod:`tckdb_qcschema.mapping` -- ``QCRecord`` -> a
   ``ConformerUploadRequest``-shaped dict plus a :class:`~tckdb_qcschema.mapping.MappingReport`.
5. :mod:`tckdb_qcschema.uploader` -- idempotent POST via ``tckdb-client``.

Stages 1-4 are pure (no network, no DB). ``tckdb_qcschema.uploader`` is the
only stage that touches the network.
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
