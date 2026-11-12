"""The level-of-theory hash as it was before the keywords decision (Stage 2), kept for history tests only.

Stage 2 removed free-text ``keywords`` from a level of theory's identity: the live
``calculation_resolution._level_of_theory_hash`` no longer puts a ``"keywords"`` key in its payload. Tests
that need the hash a level had while keywords were part of its identity compare against this frozen copy.

This is a copy of the old formula and must not be kept in step with the application: it is the formula the
historical revisions implemented. It lives in the tests, never in application code, because nothing in the
application may produce a ``keywords`` hash again.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry.basis_set_names import basis_identity_key
from app.chemistry.dispersion_names import level_identity_keys
from app.chemistry.lot_component_names import component_identity_key


def frozen_payload(**fields: Any) -> dict[str, object]:
    """The payload of the pre-Stage-2 formula; ``keywords`` (default ``None``) is hashed as written."""
    keywords = fields.pop("keywords", None)
    ref = LevelOfTheoryRef(**fields)
    method_key, dispersion_key = level_identity_keys(ref.method, ref.dispersion)
    payload: dict[str, object] = {
        "method": method_key,
        "basis": basis_identity_key(ref.basis),
        "aux_basis": basis_identity_key(ref.aux_basis),
        "cabs_basis": basis_identity_key(ref.cabs_basis),
        "dispersion": dispersion_key,
        "solvent": component_identity_key(ref.solvent),
        "solvent_model": component_identity_key(ref.solvent_model),
        "keywords": keywords,
        "spin_treatment": (getattr(ref.spin_treatment, "value", ref.spin_treatment) or "unknown"),
    }
    core_treatment = getattr(ref.core_treatment, "value", ref.core_treatment)
    if core_treatment is not None:
        payload["core_treatment"] = core_treatment
    return payload


def frozen_level_of_theory_hash(**fields: Any) -> str:
    """The hash the application computed before Stage 2 for these fields (``keywords`` allowed)."""
    return hashlib.sha256(
        json.dumps(frozen_payload(**fields), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
