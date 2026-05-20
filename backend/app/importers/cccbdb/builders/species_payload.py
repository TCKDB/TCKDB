"""Builder: CCCBDB identity → ``SpeciesEntryIdentityPayload`` dict.

The existing TCKDB identity payload (``tckdb_schemas.fragments.identity``)
requires ``smiles``, ``charge``, and ``multiplicity``. CCCBDB
experimental pages do not always expose SMILES — when it is missing,
this builder returns a *partial* dict (still useful for downstream
matching on InChIKey) and flags it as not-yet-valid so callers do not
treat it as ready for the workflow layer.
"""

from __future__ import annotations

from typing import Any

from app.importers.cccbdb.models import CCCBDBSpeciesIdentity


def build_species_entry_identity_payload(
    identity: CCCBDBSpeciesIdentity,
    warnings: list[str],
) -> tuple[dict[str, Any] | None, bool]:
    """Build a :class:`SpeciesEntryIdentityPayload`-compatible dict.

    :param identity: Parsed CCCBDB identity fragment.
    :param warnings: List that receives any builder-level warnings
        (e.g. missing SMILES, oversize state label).
    :returns: ``(payload_dict_or_None, is_valid)``.

        * ``payload_dict_or_None`` is ``None`` when there is nothing
          to build at all (empty identity).
        * ``is_valid`` is ``True`` only when the dict contains
          ``smiles``, ``charge``, ``multiplicity`` — i.e. the minimum
          required by ``SpeciesEntryIdentityPayload``.
    """

    if (
        identity.name is None
        and identity.formula is None
        and identity.inchi is None
        and identity.inchikey is None
        and identity.smiles is None
    ):
        return None, False

    payload: dict[str, Any] = {}

    if identity.smiles is not None:
        payload["smiles"] = identity.smiles
    if identity.charge is not None:
        payload["charge"] = identity.charge
    if identity.multiplicity is not None:
        payload["multiplicity"] = identity.multiplicity

    # ``term_symbol_raw`` is capped at 64 chars in the schema; the
    # 8-char ``electronic_state_label`` is reserved for canonicalized
    # labels. CCCBDB raw labels (e.g. ``"X 1A1g (planar, D6h)"``)
    # always go into ``term_symbol_raw`` and only fit there.
    if identity.state_label is not None:
        if len(identity.state_label) <= 64:
            payload["term_symbol_raw"] = identity.state_label
        else:
            warnings.append(
                "identity: state_label exceeds 64 chars; preserved in "
                "external_source.unparsed instead"
            )

    is_valid = all(
        k in payload for k in ("smiles", "charge", "multiplicity")
    )

    if not is_valid:
        missing = [
            k
            for k in ("smiles", "charge", "multiplicity")
            if k not in payload
        ]
        warnings.append(
            "identity: payload not valid for "
            f"SpeciesEntryIdentityPayload (missing: {', '.join(missing)})"
        )

    return payload, is_valid
