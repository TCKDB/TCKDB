"""What license a release carries when nobody said, and when somebody did.

``dataset_release.data_license`` is a required, non-blank column, so every
release has always had to name a license — but nothing said *which*, and no
release had ever been cut, so the question stayed open. It is now answered:
CC BY 4.0 for the corpus (attribution required, reuse otherwise unrestricted),
MIT for the code, and the pair is the house default rather than something a
curator restates on every request.

Three claims, and all three have to hold together:

* a caller who says nothing gets ``CC-BY-4.0`` — otherwise the decision is a
  document rather than a behaviour;
* a caller who says something gets **theirs** — a default that overrode an
  explicit value would publish an operator's corpus under terms they did not
  choose, which is worse than having no default at all;
* a blank is still refused — "I did not say" and "there is no license" are
  different claims, and only the first one a default may answer.

The middle claim is the one worth a test that fails loudly. It is the failure
mode that looks like success: every release carries a license, every release
carries the *right-looking* license, and the operator's own choice is silently
gone.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.release.curation import (
    DEFAULT_CODE_LICENSE,
    DEFAULT_DATA_LICENSE,
    create_release,
)


def _release(db_session, policy, curator, *, tag, **licenses):
    return create_release(
        db_session,
        tag=tag,
        title="TCKDB curated thermochemistry",
        curation_policy=policy,
        citation_text=f"TCKDB curated dataset release {tag}.",
        contact="tckdb-maintainers@example.org",
        created_by=curator.id,
        **licenses,
    )


def test_the_house_defaults_are_the_licenses_the_project_actually_uses():
    """Pin the values, not just the mechanism.

    A default that silently became ``"TBD"`` would satisfy every other test in
    this file, because they all compare against the constant.
    """
    assert DEFAULT_DATA_LICENSE == "CC-BY-4.0"
    assert DEFAULT_CODE_LICENSE == "MIT"


def test_a_release_created_without_licenses_is_cc_by_4_0(db_session, policy, curator):
    release = _release(db_session, policy, curator, tag="2026.08.0")

    assert release.data_license == "CC-BY-4.0"
    assert release.code_license == "MIT"


def test_an_explicit_license_is_stored_verbatim_and_the_default_does_not_win(
    db_session, policy, curator
):
    """The mutation this file exists to catch.

    An operator publishing a corpus they hold under other terms — a public-
    domain dedication, a lab's own agreement — passes it, and must get it
    back. If the default ever overrides an explicit value, this is the test
    that goes red rather than a release going out under the wrong terms.
    """
    release = _release(
        db_session,
        policy,
        curator,
        tag="2026.08.1",
        data_license="CC0-1.0",
        code_license="Apache-2.0",
    )

    assert release.data_license == "CC0-1.0"
    assert release.code_license == "Apache-2.0"
    assert release.data_license != DEFAULT_DATA_LICENSE
    assert release.code_license != DEFAULT_CODE_LICENSE


def test_one_release_defaulting_does_not_disturb_another_that_chose(
    db_session, policy, curator
):
    """Both branches in one transaction, since both are reached in one deployment.

    A per-call default and a shared mutable default look identical until two
    releases exist at once.
    """
    chose = _release(
        db_session, policy, curator, tag="2026.08.2", data_license="CC0-1.0"
    )
    defaulted = _release(db_session, policy, curator, tag="2026.08.3")

    assert chose.data_license == "CC0-1.0"
    assert defaulted.data_license == "CC-BY-4.0"
    assert chose.code_license == "MIT", "code license defaults independently"


@pytest.mark.parametrize("blank", ["", " ", "     "])
def test_a_blank_data_license_is_still_refused_by_the_database(
    db_session, policy, curator, blank
):
    """``data_license_nonblank`` has to survive the arrival of a default.

    The default answers an *absent* license. A caller who explicitly passes a
    blank is asserting there is none, and the check constraint refuses that at
    the storage layer regardless of what any Python default says.
    """
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError, match="data_license_nonblank"):
        _release(
            db_session, policy, curator, tag="2026.08.4", data_license=blank
        )
    savepoint.rollback()


def test_the_nonblank_constraint_only_knows_about_spaces(db_session, policy, curator):
    """A measured boundary, recorded rather than assumed.

    ``length(btrim(data_license)) > 0`` is what the column carries, and
    PostgreSQL's one-argument ``btrim`` strips **spaces only** — so a tab or a
    newline is, to that constraint, a character like any other and a
    tab-only license is stored. Found by writing the blank test with ``"\\t\\n"``
    in it and watching it not raise.

    This is neither endorsed nor fixed here: every ``*_nonblank`` constraint in
    the schema (tag, title, citation, contact, both licenses) shares the
    wording, so tightening it is a migration across deployed tables and its own
    change. It is pinned so the gap is a known, tested boundary instead of a
    surprise the next reader mistakes for a defect in their own code — and so
    the day someone does tighten it, this test says exactly what changed.
    """
    savepoint = db_session.begin_nested()
    release = _release(
        db_session, policy, curator, tag="2026.08.6", data_license="\t\n"
    )
    assert release.data_license == "\t\n", (
        "if this now raises IntegrityError the constraint was tightened; "
        "update this test and the blank test above together"
    )
    savepoint.rollback()


def test_a_blank_code_license_is_still_refused_by_the_database(
    db_session, policy, curator
):
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError, match="code_license_nonblank"):
        _release(db_session, policy, curator, tag="2026.08.5", code_license="  ")
    savepoint.rollback()
