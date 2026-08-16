"""The constraint names registration classifies on must be real names.

``/auth/register`` tells a client *which* field it refused --
``username_taken`` or ``email_taken`` -- and the only thing in a unique
violation that distinguishes the two is the constraint name PostgreSQL
reports. Both raise SQLSTATE 23505; nothing else in the error says which
rule fired.

That makes the mapping in
:data:`app.api.routes.auth.REGISTRATION_CONFLICTS` a set of string keys
that have to match a name in the live catalog, and a lookup miss is
*silent*: the route falls back to the sentence it had before, both
refusals collapse back into one, and every test asserting "a 409 arrived"
still passes. So the keys are measured against ``pg_constraint`` here
rather than trusted, and a migration that renames either uniqueness rule
fails on this file instead of quietly demoting a published code.

The check is deliberately in both directions. Missing the name is the
failure above. Having ``app_user`` grow a uniqueness rule that a
registration payload *can* trip and the route does not classify is the
other one -- it would answer the generic sentence, which is honest but is
a gap somebody should be told about rather than discover from a support
ticket.

``app_user`` already carries a third uniqueness rule, ``uq_app_user_orcid``,
and it is deliberately unclassified: ``RegisterRequest`` has no ``orcid``
field, so no registration payload can name the column and no registration
can collide on it. Reachability is derived from the request schema rather
than asserted here, so the day someone adds ``orcid`` to registration the
gap fails this file instead of shipping.
"""

from __future__ import annotations

from sqlalchemy import text

import app.db.models  # noqa: F401  (populates Base.metadata)
from app.api.routes.auth import REGISTRATION_CONFLICTS, RegisterRequest
from app.db.models.app_user import AppUser


def _live_unique_constraints(db_session, table: str) -> dict[str, set[str]]:
    """``{constraint name: {column, ...}}`` for every UNIQUE on *table*."""
    rows = db_session.execute(
        text(
            "SELECT con.conname, att.attname"
            "  FROM pg_constraint con"
            "  JOIN unnest(con.conkey) AS k(attnum) ON TRUE"
            "  JOIN pg_attribute att"
            "    ON att.attrelid = con.conrelid AND att.attnum = k.attnum"
            " WHERE con.contype = 'u'"
            "   AND con.connamespace = current_schema()::regnamespace"
            "   AND con.conrelid = :table ::regclass"
        ),
        {"table": table},
    ).all()
    out: dict[str, set[str]] = {}
    for name, column in rows:
        out.setdefault(name, set()).add(column)
    return out


def _live_unique_constraint_names(db_session, table: str) -> set[str]:
    return set(_live_unique_constraints(db_session, table))


def _registration_writable_columns() -> set[str]:
    """``app_user`` columns a registration payload can put a value in.

    Derived from the request schema intersected with the table, so it
    tracks a new field automatically. ``password`` is excluded by the
    intersection -- it is stored as ``password_hash`` and is not unique
    anyway.
    """
    return set(RegisterRequest.model_fields) & set(
        AppUser.__table__.columns.keys()
    )


def test_every_classified_constraint_exists_in_the_live_schema(db_session):
    live = _live_unique_constraint_names(db_session, AppUser.__tablename__)
    missing = sorted(set(REGISTRATION_CONFLICTS) - live)
    assert not missing, (
        "app.api.routes.auth.REGISTRATION_CONFLICTS is keyed on constraint "
        f"names the database does not have: {missing}. The lookup fails "
        "silently -- registration keeps answering 409 with the generic "
        "sentence and username_taken/email_taken stop being emitted at "
        "all. If a migration renamed one of these, update the mapping; if "
        "it dropped one, drop the catalogue entry and regenerate the "
        f"client enum. Live names on {AppUser.__tablename__}: "
        f"{sorted(live)}."
    )


def test_every_reachable_uniqueness_rule_is_classified(db_session):
    """A rule registration can trip and cannot name is a silent gap.

    "Reachable" is computed, not listed: a rule is reachable if any of
    its columns is one a ``RegisterRequest`` can fill. That is what makes
    ``uq_app_user_orcid``'s exclusion a derivation rather than an
    exception -- adding ``orcid`` to the request schema makes it reachable
    and fails this test on the same commit.
    """
    writable = _registration_writable_columns()
    assert writable, (
        "no RegisterRequest field maps to an app_user column, which cannot "
        "be right and would make this test vacuous"
    )
    reachable = {
        name
        for name, columns in _live_unique_constraints(
            db_session, AppUser.__tablename__
        ).items()
        if columns & writable
    }
    unclassified = sorted(reachable - set(REGISTRATION_CONFLICTS))
    assert not unclassified, (
        f"{AppUser.__tablename__} has uniqueness rules a registration "
        f"payload can trip and the route cannot name: {unclassified}. "
        "Tripping one answers 409 with 'Username or email already in "
        "use.', which is wrong about which field and gives a client "
        "nothing to branch on. Add a code for it to "
        "REGISTRATION_CONFLICTS and to the code catalogue, then "
        "regenerate the client enum."
    )


def test_the_unreachable_rule_is_unreachable_for_the_stated_reason(db_session):
    """Pin why ``uq_app_user_orcid`` is left out, so the reason can rot loudly.

    Without this, the derivation above quietly covers a shrinking set: if
    ``RegisterRequest`` lost ``email``, ``uq_app_user_email`` would drop
    out of ``reachable`` and the test would still pass. Naming the
    columns registration can fill is what stops "reachable" from becoming
    "empty".
    """
    assert _registration_writable_columns() == {"username", "email", "full_name"}
    live = _live_unique_constraints(db_session, AppUser.__tablename__)
    assert live.get("uq_app_user_orcid") == {"orcid"}, live
    assert "orcid" not in RegisterRequest.model_fields


def test_the_mapping_covers_the_two_columns_the_model_declares_unique(db_session):
    """Tie the names back to the columns, not just to the catalog.

    The two tests above would both stay green if ``uq_app_user_username``
    were quietly redefined over a different column: the name would still
    exist and still be classified, while ``username_taken`` started
    reporting something else. Reading the column list out of the catalog
    is what makes the code's claim about *which field* checkable.
    """
    columns = _live_unique_constraints(db_session, AppUser.__tablename__)
    assert columns.get("uq_app_user_username") == {"username"}, columns
    assert columns.get("uq_app_user_email") == {"email"}, columns
    assert "username_taken" in REGISTRATION_CONFLICTS["uq_app_user_username"]
    assert "email_taken" in REGISTRATION_CONFLICTS["uq_app_user_email"]
