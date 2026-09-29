"""MolSSI QCSchema (QCElemental) import/export adapter for TCKDB.

Six independently-testable stages, mirroring ``tckdb_chemkin``'s layout:

1. :mod:`tckdb_qcschema.reader` -- family dispatch (v1 vs v2) and strict
   class validation, producing an internal :class:`~tckdb_qcschema.reader.QCRecord`.
   Also refuses re-import of a document this adapter itself exported
   (``provenance.creator=="TCKDB"``; C-Q3).
2. :mod:`tckdb_qcschema.molecule` -- QCSchema ``Molecule`` -> TCKDB geometry
   and identity (bohr->angstrom, isotopes, ghost/fragment refusal).
3. :mod:`tckdb_qcschema.hessian` -- full-matrix ``return_result`` <-> packed
   lower triangle, symmetry-checked; the export direction is the exact
   inverse of the import direction.
4. :mod:`tckdb_qcschema.mapping` -- ``QCRecord`` -> a
   ``ConformerUploadRequest``-shaped dict plus a :class:`~tckdb_qcschema.mapping.MappingReport`,
   which :mod:`tckdb_qcschema.report_coverage` holds to the whole document:
   every field in exactly one bucket.
5. :mod:`tckdb_qcschema.uploader` -- idempotent POST via ``tckdb-client``.
6. :mod:`tckdb_qcschema.exporter` (C-Q3) -- a stored ``sp``/``freq``
   calculation, read back through ``tckdb-client``, as a QCSchema v2
   ``AtomicResult`` document.

Scans: :mod:`tckdb_qcschema.scan` maps a ``TorsionDriveResult`` (QCSchema's
only scan model) plus the optimization it started from to a
computed-species bundle whose conformer carries the scan, and
:mod:`tckdb_qcschema.scan_export` exports a stored relaxed dihedral scan
back out as a v2 ``TorsionDriveResult``. QCSchema has no IRC model, so an
IRC is neither imported nor exported.

Stages 1-4 are pure (no network, no DB). ``uploader`` and ``exporter`` are
the two stages that touch the network, and only through a caller-supplied
``tckdb-client``.
"""

from __future__ import annotations

__version__ = "0.7.0"

__all__ = ["__version__"]
