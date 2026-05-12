"""Phase C unit tests for the handle-resolution helpers.

Covers:
- ``parse_handle`` grammar (integer / ref / malformed)
- ``resolve_path_handle`` for integer and ref forms, including 404s and
  wrong-prefix rejections.
- ``resolve_filter_ref`` for query-style filters, including the
  "well-formed query, no match" path that should not raise.
- ``reconcile_id_ref`` for the id+ref pair semantics: both-supplied
  agree → ok, both-supplied disagree → 422, ref-only → resolved id,
  id-only → returned as-is, neither → ``None``, ref-only-unknown →
  ``NO_MATCH``.
"""

from __future__ import annotations

import pytest

from app.api.errors import NotFoundError
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.species import SpeciesEntry
from app.services.scientific_read.handles import (
    NO_MATCH,
    parse_handle,
    reconcile_level_of_theory_pair,
    reconcile_species_entry_pair,
    resolve_filter_ref,
    resolve_path_handle,
    resolve_species_entry_handle,
)
from tests.services.scientific_read._factories import (
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
)


# ---------------------------------------------------------------------------
# parse_handle
# ---------------------------------------------------------------------------


def test_parse_handle_integer():
    kind, value = parse_handle("42")
    assert kind == "id"
    assert value == 42


def test_parse_handle_ref():
    kind, value = parse_handle("spe_abcdef")
    assert kind == "ref"
    assert value == "spe_abcdef"


def test_parse_handle_empty_raises():
    with pytest.raises(ValueError, match="invalid_handle"):
        parse_handle("")


def test_parse_handle_malformed_raises():
    # No prefix separator, not all-digits.
    with pytest.raises(ValueError, match="invalid_handle"):
        parse_handle("notarealhandle")


def test_parse_handle_leading_zero_rejected():
    # Postgres ids are positive ints; "0" / "01" are not valid handles.
    with pytest.raises(ValueError, match="invalid_handle"):
        parse_handle("0")


# ---------------------------------------------------------------------------
# resolve_path_handle
# ---------------------------------------------------------------------------


def _species_entry(db_session):
    species = make_species(
        db_session, smiles="C#CCO", inchi_key=next_inchi_key("PCH")
    )
    return make_species_entry(db_session, species)


def test_resolve_path_handle_integer_ok(db_session):
    entry = _species_entry(db_session)
    assert (
        resolve_species_entry_handle(db_session, str(entry.id)) == entry.id
    )


def test_resolve_path_handle_ref_ok(db_session):
    entry = _species_entry(db_session)
    assert (
        resolve_species_entry_handle(db_session, entry.public_ref) == entry.id
    )


def test_resolve_path_handle_integer_missing_404(db_session):
    with pytest.raises(NotFoundError, match="species_entry not found"):
        resolve_species_entry_handle(db_session, "999999")


def test_resolve_path_handle_ref_unknown_404(db_session):
    # Well-formed ref with the right prefix but no row exists.
    with pytest.raises(NotFoundError, match="species_entry not found"):
        resolve_species_entry_handle(
            db_session, "spe_doesnotexistabcdef"
        )


def test_resolve_path_handle_wrong_prefix_422(db_session):
    with pytest.raises(ValueError, match="handle_type_mismatch"):
        resolve_species_entry_handle(db_session, "rxe_somebody")


def test_resolve_path_handle_malformed_422(db_session):
    with pytest.raises(ValueError, match="invalid_handle"):
        resolve_species_entry_handle(db_session, "garbage")


# ---------------------------------------------------------------------------
# resolve_filter_ref
# ---------------------------------------------------------------------------


def test_resolve_filter_ref_known(db_session):
    entry = _species_entry(db_session)
    resolved = resolve_filter_ref(
        db_session, SpeciesEntry, entry.public_ref, kind_label="species_entry"
    )
    assert resolved == entry.id


def test_resolve_filter_ref_unknown_returns_none(db_session):
    # Well-formed ref, right prefix, no matching row → None (NOT 404).
    resolved = resolve_filter_ref(
        db_session,
        SpeciesEntry,
        "spe_neverexistedabcdef",
        kind_label="species_entry",
    )
    assert resolved is None


def test_resolve_filter_ref_wrong_prefix_422(db_session):
    with pytest.raises(ValueError, match="handle_type_mismatch"):
        resolve_filter_ref(
            db_session, SpeciesEntry, "lot_abcdef", kind_label="species_entry"
        )


def test_resolve_filter_ref_malformed_422(db_session):
    with pytest.raises(ValueError, match="invalid_handle"):
        resolve_filter_ref(
            db_session, SpeciesEntry, "not-a-ref", kind_label="species_entry"
        )


# ---------------------------------------------------------------------------
# reconcile_id_ref
# ---------------------------------------------------------------------------


def test_reconcile_neither_returns_none(db_session):
    assert (
        reconcile_species_entry_pair(
            db_session, id_value=None, ref_value=None
        )
        is None
    )


def test_reconcile_id_only(db_session):
    # We don't hit the DB when only an id is supplied.
    assert (
        reconcile_species_entry_pair(
            db_session, id_value=42, ref_value=None
        )
        == 42
    )


def test_reconcile_ref_only_known(db_session):
    entry = _species_entry(db_session)
    assert (
        reconcile_species_entry_pair(
            db_session, id_value=None, ref_value=entry.public_ref
        )
        == entry.id
    )


def test_reconcile_ref_only_unknown_returns_no_match(db_session):
    sentinel = reconcile_species_entry_pair(
        db_session, id_value=None, ref_value="spe_neverexistedabcdef"
    )
    assert sentinel is NO_MATCH


def test_reconcile_both_agree(db_session):
    entry = _species_entry(db_session)
    assert (
        reconcile_species_entry_pair(
            db_session, id_value=entry.id, ref_value=entry.public_ref
        )
        == entry.id
    )


def test_reconcile_both_disagree_raises(db_session):
    a = _species_entry(db_session)
    b = _species_entry(db_session)
    with pytest.raises(ValueError, match="species_entry_handle_conflict"):
        reconcile_species_entry_pair(
            db_session, id_value=a.id, ref_value=b.public_ref
        )


def test_reconcile_ref_unknown_with_id_raises_conflict(db_session):
    # A supplied id with a ref that points at nothing must surface as a
    # 422 conflict, not a silent empty result — the caller's two
    # statements contradict each other.
    entry = _species_entry(db_session)
    with pytest.raises(ValueError, match="species_entry_handle_conflict"):
        reconcile_species_entry_pair(
            db_session,
            id_value=entry.id,
            ref_value="spe_neverexistedabcdef",
        )


def test_reconcile_level_of_theory_pair_round_trip(db_session):
    lot = make_lot(db_session, method="b3lyp", basis="6-31g")
    assert (
        reconcile_level_of_theory_pair(
            db_session, id_value=None, ref_value=lot.public_ref
        )
        == lot.id
    )
    with pytest.raises(ValueError, match="level_of_theory_handle_conflict"):
        reconcile_level_of_theory_pair(
            db_session,
            id_value=lot.id,
            ref_value="lot_neverexistedabcdef",
        )
