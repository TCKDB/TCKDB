"""The DR-0028 body sweep must catch the shape it exists to catch.

``tests/error_body_observer.py`` is a passive observer: it inspects every
4xx/5xx body the suite produces and fails the test that produced a
leaking one. Nothing in the suite calls it, so if a refactor quietly
stopped it matching anything, every gate would stay green and the file
would be decoration. That is the failure mode this repository produces
more often than any other, and ``all([])`` being ``True`` is how it
usually looks.

Three separate things have to be pinned, and the first two are the ones
a passing suite cannot demonstrate on its own:

* the extractor recognises a leak and does not invent one (below);
* the plumbing is live -- a real refusal, over the wire, is seen by the
  observer, and blinding the observer makes that assertion red;
* the numeric ``context`` facts the API publishes today are *not* leaks,
  stated as the measurement they came from, so a future tightening that
  would break ``freq_mode_index_not_unique`` fails here first.

The floor over the whole run lives in ``tests/conftest.py``
(``BODY_SWEEP_FLOORS``); it is the coarse net for a blinding that leaves
the extractor's entry intact. The per-test plumbing check is in
``_check_error_bodies`` in the same file.
"""

from __future__ import annotations

import pytest

from tests import error_body_observer
from tests.error_body_observer import Observation, inspect


def _leaks(status: int, content: dict) -> list[str]:
    leaks, _fields = inspect(status, content)
    return [leak.where for leak in leaks]


def _fields(status: int, content: dict) -> int:
    _leaks_found, fields = inspect(status, content)
    return fields


# ---------------------------------------------------------------------------
# The extractor
# ---------------------------------------------------------------------------


def test_a_row_id_under_its_natural_name_is_a_leak() -> None:
    """The shape the mistake has actually taken every time so far."""
    body = {
        "code": "calculation_not_owned",
        "detail": "calculation is owned by another species entry",
        "context": {"calculation_id": 412},
    }
    assert _leaks(422, body) == ["context.calculation_id"]


@pytest.mark.parametrize(
    "context, where",
    [
        ({"id": 7}, "context.id"),
        ({"species_pk": 7}, "context.species_pk"),
        # A list of ids, which is how a bulk refusal would carry them.
        ({"calculation_ids": [3, 4]}, "context.calculation_ids"),
        # A map keyed by the depositor's own local key, whose *values* are
        # row ids. ``test_local_key_resolution.py`` and
        # ``test_api_pdep_undeclared_local_keys.py`` each assert against
        # this by hand; here it is caught wherever the map is id-named.
        ({"resolved_ids": {"my_opt": 91}}, "context.resolved_ids"),
        # Nested one level down, because ``context`` is not always flat.
        ({"owner": {"species_entry_id": 3}}, "context.owner.species_entry_id"),
    ],
)
def test_every_id_shaped_key_carrying_an_integer_is_a_leak(context, where) -> None:
    body = {"code": "x", "detail": "refused", "context": context}
    assert _leaks(422, body) == [where]


@pytest.mark.parametrize(
    "detail",
    [
        # The single worst leak the AST scan ever found, as a client sees it.
        "ref resolves elsewhere (ref -> id=884)",
        # The shape the AST scan catches only because ``calc_id`` is an
        # id-shaped *name*. Here there is no name left, only the label.
        "calculation not found (calculation_id=412)",
        "species_entry_id: 9 is not owned by you",
        "row was written (id: 7)",
    ],
)
def test_prose_that_announces_an_id_and_then_gives_one_is_a_leak(detail) -> None:
    """The rendered-string half of the AST scan's text rules.

    ``test_no_row_ids_in_user_facing_text.py`` catches these at the raise
    site when they are written as an f-string literal, and says in its own
    docstring that it cannot when the message is ``%``-formatted, built
    with ``str.format``, or bound to a local first. By the time the body
    exists all three look the same, which is the whole reason to check
    here as well as there.
    """
    body = {"code": "handle_conflict", "detail": detail, "context": {}}
    assert _leaks(422, body) == ["detail"], detail


@pytest.mark.parametrize(
    "context",
    [
        # The refusal named in DR-0028's own discussion. It must keep
        # publishing both of these.
        {"duplicate_mode_indices": [1], "mode_count": 2},
        {"n_imag": 2, "imaginary_mode_count": 2},
        {"molecularity": 3, "participant_index": 0},
        {"charge": -1, "multiplicity": 2},
        {"offset": 5000, "candidate_count": 12},
        {"retry_after_seconds": 30},
        {"min": 0.0, "max": 1.5},
        # A formula map. Its keys are element symbols, which is why an
        # allowlist of legitimate numeric key names cannot be written:
        # the key space is the periodic table.
        {"reactants": {"C": 2, "H": 6}, "products": {"C": 2, "H": 6}},
        # An id-shaped key whose value is a public ref, not a row id. A
        # misleading name is a naming problem; refusing it would teach
        # authors to rename the key rather than to stop leaking.
        {"species_entry_id": "spe_abcdefghijklmnopqrstuvwx"},
        # ``isinstance(True, int)`` is True in Python.
        {"is_valid_id": True},
    ],
)
def test_the_numeric_context_the_api_publishes_today_is_not_a_leak(context) -> None:
    """Measured, not assumed.

    Every entry here was observed in a real 4xx body during a full run of
    all three gates. The union across those gates was 29 distinct
    numeric-valued ``context`` key names and **zero** id-shaped ones,
    which is the measurement that chose a key-name rule over an
    allowlist. If a future tightening makes one of these red, it is
    taking a published scientific fact off the wire.
    """
    body = {"code": "x", "detail": "refused", "context": context}
    assert _leaks(422, body) == []


def test_prose_that_merely_contains_a_number_is_not_a_leak() -> None:
    for detail in (
        "grid=10 is too coarse",
        "uuid=1 is not a uuid",
        "valid=1 is not a boolean",
        # Cantera really writes this into a warning during the rest gate.
        "discontinuity in s/R detected at Tmid = 1000",
        "matched 4 records, narrow the query",
        "species_entry not found",
        "invalid handle 'spe_zzz'",
        # A quantity whose name merely ends in a digit-bearing suffix.
        "pressure_bar=3 is out of range",
    ):
        body = {"code": "x", "detail": detail, "context": {}}
        assert _leaks(422, body) == [], detail


def test_a_clean_body_still_counts_as_examined() -> None:
    """Why the floor counts fields and not only bodies.

    Most error bodies carry ``context == {}``, so a walk of ``context``
    alone visits nothing and a field floor would be near zero however
    healthy the run. Counting the ``detail`` string too puts a hard
    minimum of one field on every body, which is what makes a blinded
    walk visibly different from a clean one.
    """
    assert _fields(404, {"code": "x", "detail": "not found", "context": {}}) == 1
    assert _fields(422, {"code": "x", "detail": "no", "context": {"a": 1, "b": 2}}) == 3


# ---------------------------------------------------------------------------
# The plumbing
# ---------------------------------------------------------------------------


def test_a_real_refusal_over_the_wire_reaches_the_observer(client) -> None:
    """Both patches are live, proved against a request rather than a mock.

    This is the assertion the per-test check in ``conftest.py`` makes for
    every test in the suite; making it once, explicitly, is what turns
    "no test ever complained" into evidence. ``drain()`` is called first
    so the counts belong to this request and not to whatever the fixtures
    did on the way in.
    """
    error_body_observer.drain()
    response = client.get("/api/v1/scientific/reaction-entries/99999999/full")
    assert response.status_code == 404, response.text

    observed = error_body_observer.drain()
    assert observed.bodies >= 1, "JSONResponse.__init__ is no longer patched"
    assert observed.client_errors >= 1, "TestClient.request is no longer patched"
    assert observed.leaks == [], observed.leaks
    # The teardown hook must not then fail this test on a stale count.
    assert error_body_observer.drain() == Observation()


def test_the_session_totals_only_ever_grow(client) -> None:
    """The floor reads these, and a drain must not reset them.

    An early version cleared both counters together, which made the floor
    a measure of the last test rather than of the run.
    """
    before_bodies, before_fields, before_client = error_body_observer.session_totals()
    client.get("/api/v1/scientific/geometries/99999999")
    error_body_observer.drain()
    after_bodies, after_fields, after_client = error_body_observer.session_totals()

    assert after_bodies > before_bodies
    assert after_fields > before_fields
    assert after_client > before_client
