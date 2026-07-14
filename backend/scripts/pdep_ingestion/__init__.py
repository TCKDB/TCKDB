"""Parse a real Arkane pressure-dependence run into a TCKDB
``NetworkPDepUploadRequest``.

Sibling to ``scripts/arc_ingestion`` (which handles single-reaction ARC runs).
This package targets full pressure-dependent networks: an Arkane
``input.py`` + ``output.py`` + ``supporting_information.csv`` + per-species
``Data/*.py`` describing the ab-initio evidence, plus the fitted
phenomenological Chebyshev k(T,P) blocks.

Public entrypoint::

    from scripts.pdep_ingestion.builder import build_network_pdep_request
    request = build_network_pdep_request(run_dir)  # -> NetworkPDepUploadRequest
"""
