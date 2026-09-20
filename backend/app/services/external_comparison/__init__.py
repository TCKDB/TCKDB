"""Review-tier cross-checks against external reference data (Phase C-E4).

ADR 0008 puts every comparison against an external reference dataset in the
``review`` tier: it may never block an upload, never carries an error-envelope
code, and has no approval effect. :mod:`.cp` is the first member — a
deterministic comparison of a computed thermo record's heat capacity against
independently observed ``molecular_property_observation`` rows.
"""

from __future__ import annotations
