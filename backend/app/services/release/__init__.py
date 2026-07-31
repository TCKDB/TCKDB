"""Curated product selection and citable dataset releases.

Submodules:

``versions``   what a manifest binds itself to (Alembic revision, package
               versions, policy versions, contract tags).
``records``    serializing a selected record and enumerating its candidates.
``artifacts``  the four deterministic, checksummable NDJSON files a release
               ships.
``manifest``   rendering, freezing and verifying the immutable manifest.
``curation``   the write paths: policies, releases, and append-only selections.

See ``backend/docs/specs/dataset_release_and_profiles.md``.
"""
