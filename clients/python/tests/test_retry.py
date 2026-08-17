"""Retry safety rules for TCKDBClient.

The dangerous case this module exists to prevent: replaying a write that
the server already committed. A ``POST`` without an ``Idempotency-Key``
must therefore take exactly one attempt no matter what goes wrong —
connection reset, timeout, 503, 429. Those cases are asserted
individually below, because "we never retry unsafe writes" is the whole
point of the feature.

Nothing here sleeps: ``RetryPolicy.sleep`` is injected, so the recorded
delays are asserted directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import make_client
from tckdb_client import TCKDBClient
from tckdb_client.errors import TCKDBConnectionError, TCKDBHTTPError
from tckdb_client.retry import (
    DEFAULT_NON_RETRYABLE_CODES,
    DEFAULT_RETRY_STATUS_CODES,
    RetryPolicy,
)

VALID_KEY = "idem-key-0000000001"


class SleepRecorder:
    """Stands in for ``time.sleep`` and remembers what it was asked to wait."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)

    @property
    def count(self) -> int:
        return len(self.delays)


def policy(**overrides) -> tuple[RetryPolicy, SleepRecorder]:
    recorder = SleepRecorder()
    kwargs = {
        "max_attempts": 3,
        "backoff_base": 1.0,
        "jitter": 0.0,
        "sleep": recorder,
        "rand": lambda: 0.0,
        **overrides,
    }
    return RetryPolicy(**kwargs), recorder


def client_with(handler, *, retry, api_key="tck_test_key_value_1234"):
    transport = httpx.MockTransport(handler)
    return TCKDBClient(
        "http://test.local/api/v1",
        api_key=api_key,
        timeout=5.0,
        transport=transport,
        retry=retry,
    )


# ---------------------------------------------------------------------------
# Default is OFF
# ---------------------------------------------------------------------------


class TestDefaultOff:
    def test_client_does_not_retry_unless_a_policy_is_supplied(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(503, json={"detail": "unavailable"})

        client, _ = make_client(handler)
        with pytest.raises(TCKDBHTTPError):
            client.get_json("/health")
        assert len(attempts) == 1

    def test_connection_errors_are_not_retried_by_default(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            raise httpx.ConnectError("refused", request=request)

        client, _ = make_client(handler)
        with pytest.raises(TCKDBConnectionError):
            client.get_json("/health")
        assert len(attempts) == 1

    def test_retry_argument_is_type_checked(self):
        with pytest.raises(TypeError, match="RetryPolicy"):
            TCKDBClient("http://test.local/api/v1", retry="aggressive")


# ---------------------------------------------------------------------------
# Method eligibility — the safety rule
# ---------------------------------------------------------------------------


class TestMethodEligibility:
    def test_get_and_head_are_retryable(self):
        pol, _ = policy()
        assert pol.method_is_retryable("GET", has_idempotency_key=False)
        assert pol.method_is_retryable("HEAD", has_idempotency_key=False)
        assert pol.method_is_retryable("get", has_idempotency_key=False)

    def test_post_is_retryable_only_with_an_idempotency_key(self):
        pol, _ = policy()
        assert pol.method_is_retryable("POST", has_idempotency_key=True)
        assert not pol.method_is_retryable("POST", has_idempotency_key=False)

    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
    def test_other_writes_are_never_retryable(self, method):
        pol, _ = policy()
        assert not pol.method_is_retryable(method, has_idempotency_key=True)
        assert not pol.method_is_retryable(method, has_idempotency_key=False)


class TestPostWithoutKeyIsNeverRetried:
    """The rule that must never regress, asserted failure mode by failure mode."""

    @pytest.mark.parametrize("status", sorted(DEFAULT_RETRY_STATUS_CODES))
    def test_transient_status_does_not_trigger_a_retry(self, status):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(status, json={"detail": "later"})

        pol, recorder = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.post_json("/uploads/thermo", {"payload": True})

        assert len(attempts) == 1
        assert recorder.count == 0

    def test_connection_error_does_not_trigger_a_retry(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            raise httpx.ConnectError("connection reset", request=request)

        pol, recorder = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBConnectionError):
            client.post_json("/uploads/thermo", {"payload": True})

        assert len(attempts) == 1
        assert recorder.count == 0

    def test_timeout_does_not_trigger_a_retry(self):
        """The scariest case: the server may have committed the upload."""
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            raise httpx.ReadTimeout("timed out", request=request)

        pol, recorder = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBConnectionError):
            client.post_json("/uploads/thermo", {"payload": True})

        assert len(attempts) == 1
        assert recorder.count == 0

    def test_retry_after_header_does_not_override_the_rule(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(
                429, headers={"Retry-After": "1"}, json={"detail": "slow down"}
            )

        pol, recorder = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.post_json("/uploads/thermo", {"payload": True})

        assert len(attempts) == 1
        assert recorder.count == 0

    def test_bundle_submit_without_a_key_is_not_retried(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(503, json={"detail": "unavailable"})

        pol, _ = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.bundle_submit({"records": []})

        assert len(attempts) == 1


class TestPostWithKeyIsRetried:
    def test_idempotent_post_retries_on_503_then_succeeds(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) < 3:
                return httpx.Response(503, json={"detail": "unavailable"})
            return httpx.Response(200, json={"ok": True})

        pol, recorder = policy()
        client = client_with(handler, retry=pol)

        result = client.post_json(
            "/uploads/thermo", {"payload": True}, idempotency_key=VALID_KEY
        )

        assert result == {"ok": True}
        assert len(attempts) == 3
        assert recorder.delays == [1.0, 2.0]
        assert all(r.headers["Idempotency-Key"] == VALID_KEY for r in attempts)

    def test_idempotent_post_retries_on_connection_error(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) == 1:
                raise httpx.ConnectError("reset", request=request)
            return httpx.Response(200, json={"ok": True})

        pol, recorder = policy()
        client = client_with(handler, retry=pol)

        assert client.upload(
            "thermo", {"payload": True}, idempotency_key=VALID_KEY
        ) == {"ok": True}
        assert len(attempts) == 2
        assert recorder.count == 1

    def test_a_key_supplied_through_extra_headers_also_enables_retry(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(502, json={"detail": "bad gateway"})
            return httpx.Response(200, json={"ok": True})

        pol, _ = policy()
        client = client_with(handler, retry=pol)

        client.request_json(
            "POST",
            "/uploads/thermo",
            json={"payload": True},
            extra_headers={"Idempotency-Key": VALID_KEY},
        )

        assert len(attempts) == 2

    def test_a_blank_key_header_does_not_enable_retry(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(503, json={"detail": "unavailable"})

        pol, _ = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.request_json(
                "POST",
                "/uploads/thermo",
                json={"payload": True},
                extra_headers={"Idempotency-Key": "   "},
            )

        assert len(attempts) == 1


# ---------------------------------------------------------------------------
# Status eligibility
# ---------------------------------------------------------------------------


class TestStatusEligibility:
    @pytest.mark.parametrize("status", [429, 502, 503, 504])
    def test_transient_statuses_are_retried_for_get(self, status):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(status, json={"detail": "later"})
            return httpx.Response(200, json={"ok": True})

        pol, _ = policy()
        client = client_with(handler, retry=pol)

        assert client.get_json("/health") == {"ok": True}
        assert len(attempts) == 2

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 410, 418, 422])
    def test_other_4xx_are_never_retried(self, status):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(status, json={"detail": "no"})

        pol, recorder = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.get_json("/species/1")

        assert len(attempts) == 1
        assert recorder.count == 0

    def test_500_is_not_retried_by_default(self):
        """A 500 is an unhandled server bug, not a documented transient."""
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(500, json={"detail": "boom"})

        pol, _ = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.get_json("/health")
        assert len(attempts) == 1

    def test_507_insufficient_storage_is_not_retried(self):
        """A full object store, and the reason it is 507 and not 503.

        TCKDB answers ``507 artifact_storage_full`` when the object store
        refuses a write for want of room. That condition *will* clear, but
        only when an operator frees space -- never on a client's backoff
        schedule -- so replaying it is a loop with no exit that the caller
        cannot shorten.

        The whole point of putting it at 507 rather than adding a second
        code at 503 is that this test needs nothing else to pass: 507 is
        absent from :data:`DEFAULT_RETRY_STATUS_CODES`, so a client stops
        after one attempt on the status alone. A pinned client that has
        never heard of ``artifact_storage_full`` behaves correctly, and so
        does a caller in another language with no deny list at all. At 503
        the correct behaviour would have depended on the deny list, and so
        on the caller having upgraded.
        """
        assert 507 not in DEFAULT_RETRY_STATUS_CODES

        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(
                507,
                json={
                    "code": "artifact_storage_full",
                    "detail": "no room; an operator must free space",
                },
            )

        # No deny list at all: the status must carry this on its own.
        pol, recorder = policy(max_attempts=3, non_retryable_codes=frozenset())
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.get_json("/health")
        assert len(attempts) == 1
        assert recorder.count == 0

    def test_retry_status_codes_are_configurable(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(500, json={"detail": "boom"})
            return httpx.Response(200, json={"ok": True})

        pol, _ = policy(retry_status_codes=frozenset({500}))
        client = client_with(handler, retry=pol)

        assert client.get_json("/health") == {"ok": True}
        assert len(attempts) == 2


# ---------------------------------------------------------------------------
# Code eligibility: when the body contradicts the status
# ---------------------------------------------------------------------------


class TestNonRetryableCodes:
    """A transient status carrying a deterministic code is attempted once.

    Every test here asserts an **attempt count**, not merely that the call
    raised. ``pytest.raises(TCKDBHTTPError)`` passes identically against a
    client that gave up immediately and one that replayed for two minutes
    first, so it says nothing at all about the behaviour under test — and
    the behaviour under test *is* the number of attempts.

    The two halves are deliberately in one class. Before #234 the client
    replayed ``artifact_object_missing`` on a backoff schedule forever for
    a condition guaranteed never to clear; the fix must not overshoot into
    abandoning the 503 beside it, which is a genuine outage where waiting
    is the correct response. Asserting only the first half would leave a
    client that stopped retrying everything looking fixed.
    """

    def test_the_deny_list_is_not_empty(self):
        """Everything below is vacuous if the generated set is empty.

        ``code_is_retryable`` answers ``True`` for anything it does not
        recognise, so an empty ``NON_RETRYABLE_CODES`` would make every
        once-only assertion below pass for the wrong reason — the status
        rule alone would be back in charge and nothing would say so.
        """
        assert DEFAULT_NON_RETRYABLE_CODES, (
            "NON_RETRYABLE_CODES is empty, so the retry layer has no code "
            "it will refuse to replay and every test in this class is "
            "asserting the pre-#234 behaviour"
        )
        assert "artifact_object_missing" in DEFAULT_NON_RETRYABLE_CODES
        assert "artifact_integrity_failed" in DEFAULT_NON_RETRYABLE_CODES

    @pytest.mark.parametrize(
        "code", ["artifact_object_missing", "artifact_integrity_failed"]
    )
    def test_a_custody_break_is_attempted_exactly_once(self, code):
        """502 is in the default retry set; the code overrules it."""
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(
                502,
                json={
                    "detail": "retrying will not clear it",
                    "code": code,
                    "context": {},
                },
            )

        pol, recorder = policy(max_attempts=4)
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError) as excinfo:
            client.get_json("/scientific/artifacts/abc/download")

        assert len(attempts) == 1, (
            f"{code} was attempted {len(attempts)} times; the condition is "
            "deterministic, so every attempt after the first is guaranteed "
            "to meet the same answer"
        )
        assert recorder.count == 0, "the client slept before giving up"
        assert excinfo.value.status_code == 502
        assert excinfo.value.code == code

    def test_a_storage_outage_is_still_retried(self):
        """The other half of the pair, at the status that means "wait".

        ``artifact_storage_unavailable`` leaves the same handler, from the
        same exception type, as the 502 above. It is not in the deny list
        and must keep its full backoff schedule — a client that stopped
        retrying this would abandon every transient object-store blip.
        """
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(
                503,
                json={
                    "detail": "Artifact storage is temporarily unavailable.",
                    "code": "artifact_storage_unavailable",
                    "context": {},
                },
            )

        pol, recorder = policy(max_attempts=4)
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.get_json("/scientific/artifacts/abc/download")

        assert len(attempts) == 4, (
            "a store that did not answer was attempted "
            f"{len(attempts)} times instead of the budgeted 4"
        )
        assert recorder.delays == [1.0, 2.0, 4.0]

    def test_a_recovering_store_still_succeeds_on_a_later_attempt(self):
        """Retrying is not merely permitted, it works.

        Exhausting the budget above proves the attempts happened; this
        proves the replay is a real request whose success is returned,
        which an assertion counting failures cannot distinguish.
        """
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) < 3:
                return httpx.Response(
                    503,
                    json={"code": "artifact_storage_unavailable", "detail": "wait"},
                )
            return httpx.Response(200, json={"ok": True})

        pol, _ = policy()
        client = client_with(handler, retry=pol)

        assert client.get_json("/health") == {"ok": True}
        assert len(attempts) == 3

    def test_download_artifact_stops_after_one_attempt(self):
        """Through the real call, not a synthetic path.

        ``download_artifact`` returns raw bytes and therefore takes
        ``_request_raw`` rather than ``request_json``. Both funnel into
        ``_send``, but the retry loop lives in ``_send`` and a test that
        only ever went through the JSON path would not have noticed if the
        bytes path skipped it — and the bytes path is the one this whole
        fix is about.
        """
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(
                502,
                json={
                    "detail": "The stored bytes for this artifact are not "
                    "in the object store.",
                    "code": "artifact_object_missing",
                    "context": {},
                },
            )

        pol, recorder = policy(max_attempts=5)
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError) as excinfo:
            client.download_artifact("a" * 64)

        assert len(attempts) == 1
        assert recorder.count == 0
        assert excinfo.value.code == "artifact_object_missing"
        assert attempts[0].url.path.endswith("/download")

    def test_an_unrecognised_code_is_still_retried(self):
        """The deny list fails safe: unknown means keep trying.

        A pinned client routinely talks to a newer server. If an unknown
        code stopped a retry, adding any transient failure server-side
        would silently break backoff for every client in the field —
        worse than the bug this list fixes, because it turns a recoverable
        outage into a failure.
        """
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(
                503,
                json={"code": "some_transient_thing_invented_later", "detail": "x"},
            )

        pol, _ = policy(max_attempts=3)
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.get_json("/health")
        assert len(attempts) == 3

    @pytest.mark.parametrize(
        "response_kwargs",
        [
            pytest.param({"json": {"detail": "no code field"}}, id="no-code-key"),
            pytest.param({"json": {"code": None}}, id="null-code"),
            pytest.param({"json": {"code": 502}}, id="non-string-code"),
            pytest.param({"json": ["not", "an", "object"]}, id="json-array"),
            pytest.param({"content": b"\x89PNG\r\n\x1a\n\xff"}, id="binary-body"),
            pytest.param({"text": "<html>502 Bad Gateway</html>"}, id="html-body"),
            pytest.param({"content": b""}, id="empty-body"),
        ],
    )
    def test_a_body_with_no_readable_code_is_retried(self, response_kwargs):
        """An unreadable body must never be the reason a retry stopped.

        A proxy's HTML 502, a truncated body, a response with no ``code``
        at all: each yields "no opinion", and no opinion leaves the status
        rule in charge. The alternative — treating an unparseable body as
        non-retryable — would silently disable backoff behind any
        misbehaving intermediary.
        """
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(502, **response_kwargs)

        pol, _ = policy(max_attempts=3)
        client = client_with(handler, retry=pol)

        with pytest.raises((TCKDBHTTPError, TCKDBConnectionError)):
            client.get_json("/health")
        assert len(attempts) == 3

    def test_the_code_rule_cannot_start_a_retry_the_status_refused(self):
        """The veto is one-directional.

        ``code_is_retryable`` answers ``True`` for every 4xx code, since
        none of them are in the deny list. That must not make a 422
        retryable: the status rule runs first and its "no" is final.
        """
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(
                422, json={"code": "reaction_mass_balance_failed", "detail": "no"}
            )

        pol, recorder = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.get_json("/species/1")
        assert len(attempts) == 1
        assert recorder.count == 0

    def test_the_deny_list_is_configurable_and_load_bearing(self):
        """Emptying it restores the old behaviour, which proves it acts.

        The same mutation the PR ran by hand, pinned as a test: with no
        deny list the identical 502 is replayed to the attempt cap. If
        this passed *and* the once-only test above passed, the field would
        not be consulted at all.
        """
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(
                502, json={"code": "artifact_object_missing", "detail": "gone"}
            )

        pol, _ = policy(max_attempts=3, non_retryable_codes=frozenset())
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.get_json("/health")
        assert len(attempts) == 3

    @pytest.mark.parametrize("value", [None, 502, b"artifact_object_missing", object()])
    def test_a_non_string_code_is_retryable(self, value):
        """The unit-level fail-safe, stated separately from the wire tests."""
        pol, _ = policy()
        assert pol.code_is_retryable(value)

    def test_the_policy_reports_the_two_codes_directly(self):
        pol, _ = policy()
        assert not pol.code_is_retryable("artifact_object_missing")
        assert not pol.code_is_retryable("artifact_integrity_failed")
        assert pol.code_is_retryable("artifact_storage_unavailable")
        assert pol.code_is_retryable("rate_limit_exceeded")


# ---------------------------------------------------------------------------
# Bounded attempts and backoff shape
# ---------------------------------------------------------------------------


class TestBoundedAttempts:
    def test_attempts_are_capped_and_the_last_error_surfaces(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(503, json={"detail": "unavailable"})

        pol, recorder = policy(max_attempts=4)
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError) as excinfo:
            client.get_json("/health")

        assert len(attempts) == 4
        assert recorder.count == 3
        assert excinfo.value.status_code == 503

    def test_max_attempts_one_disables_retrying(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(503, json={"detail": "unavailable"})

        pol, recorder = policy(max_attempts=1)
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.get_json("/health")
        assert len(attempts) == 1
        assert recorder.count == 0

    def test_connection_failures_exhaust_attempts_then_raise(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            raise httpx.ConnectTimeout("timed out", request=request)

        pol, recorder = policy(max_attempts=3)
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBConnectionError, match="timed out"):
            client.get_json("/health")

        assert len(attempts) == 3
        assert recorder.delays == [1.0, 2.0]

    def test_max_attempts_must_be_positive(self):
        with pytest.raises(ValueError, match="max_attempts"):
            RetryPolicy(max_attempts=0)

    @pytest.mark.parametrize("jitter", [-0.1, 1.1])
    def test_jitter_must_be_a_fraction(self, jitter):
        with pytest.raises(ValueError, match="jitter"):
            RetryPolicy(jitter=jitter)


class TestBackoffShape:
    def test_delays_grow_exponentially_and_respect_the_ceiling(self):
        pol, _ = policy(backoff_base=1.0, max_backoff=4.0, jitter=0.0)
        assert [pol.backoff_delay(n) for n in range(1, 6)] == [
            1.0,
            2.0,
            4.0,
            4.0,
            4.0,
        ]

    def test_jitter_spreads_the_delay_below_the_nominal_value(self):
        pol, _ = policy(backoff_base=4.0, jitter=0.5, rand=lambda: 0.0)
        assert pol.backoff_delay(1) == pytest.approx(2.0)

        pol_high, _ = policy(backoff_base=4.0, jitter=0.5, rand=lambda: 1.0)
        assert pol_high.backoff_delay(1) == pytest.approx(4.0)

    def test_jittered_delays_stay_within_bounds_across_the_range(self):
        values = iter([0.0, 0.25, 0.5, 0.75, 0.999])
        pol, _ = policy(backoff_base=2.0, jitter=0.5, rand=lambda: next(values))
        for _ in range(5):
            delay = pol.backoff_delay(1)
            assert 1.0 <= delay <= 2.0

    def test_attempt_must_be_positive(self):
        pol, _ = policy()
        with pytest.raises(ValueError, match="attempt"):
            pol.backoff_delay(0)


# ---------------------------------------------------------------------------
# Retry-After
# ---------------------------------------------------------------------------


class TestRetryAfter:
    def test_delta_seconds_form_is_honoured(self):
        pol, _ = policy()
        assert pol.parse_retry_after({"Retry-After": "17"}) == 17.0

    def test_header_lookup_is_case_insensitive(self):
        pol, _ = policy()
        assert pol.parse_retry_after({"retry-after": "3"}) == 3.0

    def test_http_date_form_is_honoured(self):
        now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
        pol, _ = policy(now=lambda: now)
        when = now + timedelta(seconds=45)
        header = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
        assert pol.parse_retry_after({"Retry-After": header}) == pytest.approx(45.0)

    def test_http_date_in_the_past_yields_zero(self):
        now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
        pol, _ = policy(now=lambda: now)
        header = (now - timedelta(minutes=5)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        assert pol.parse_retry_after({"Retry-After": header}) == 0.0

    def test_negative_delta_seconds_clamp_to_zero(self):
        pol, _ = policy()
        assert pol.parse_retry_after({"Retry-After": "-5"}) == 0.0

    def test_unparseable_value_is_ignored_rather_than_guessed(self):
        pol, _ = policy()
        assert pol.parse_retry_after({"Retry-After": "soon"}) is None
        assert pol.parse_retry_after({"Retry-After": ""}) is None
        assert pol.parse_retry_after({}) is None

    def test_respect_retry_after_can_be_disabled(self):
        pol, _ = policy(respect_retry_after=False)
        assert pol.parse_retry_after({"Retry-After": "30"}) is None

    def test_client_never_sleeps_less_than_retry_after_asks(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(
                    429, headers={"Retry-After": "12"}, json={"detail": "slow"}
                )
            return httpx.Response(200, json={"ok": True})

        # Backoff alone would have waited 0.01s; Retry-After wins.
        pol, recorder = policy(backoff_base=0.01)
        client = client_with(handler, retry=pol)

        assert client.get_json("/health") == {"ok": True}
        assert recorder.delays == [12.0]

    def test_longer_backoff_wins_over_a_shorter_retry_after(self):
        pol, _ = policy(backoff_base=30.0, max_backoff=30.0)
        assert pol.delay_for(1, {"Retry-After": "2"}) == 30.0

    def test_http_date_retry_after_is_honoured_end_to_end(self):
        now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
        header = (now + timedelta(seconds=20)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(
                    503, headers={"Retry-After": header}, json={"detail": "later"}
                )
            return httpx.Response(200, json={"ok": True})

        pol, recorder = policy(backoff_base=0.5, now=lambda: now)
        client = client_with(handler, retry=pol)

        assert client.get_json("/health") == {"ok": True}
        assert recorder.delays == [pytest.approx(20.0)]

    def test_a_retry_after_beyond_the_budget_gives_up_immediately(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(
                503, headers={"Retry-After": "3600"}, json={"detail": "maintenance"}
            )

        pol, recorder = policy(max_retry_after=60.0)
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError) as excinfo:
            client.get_json("/health")

        assert len(attempts) == 1
        assert recorder.count == 0
        assert excinfo.value.status_code == 503


# ---------------------------------------------------------------------------
# Interaction with the rest of the client
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_retries_apply_to_typed_read_methods(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(503, json={"detail": "unavailable"})
            return httpx.Response(
                200,
                json={
                    "request": {},
                    "review_summary": {},
                    "records": [],
                    "pagination": {
                        "offset": 0, "limit": 50, "returned": 0, "total": 0,
                    },
                },
            )

        pol, _ = policy()
        client = client_with(handler, retry=pol)

        client.search_species(smiles="CCO")
        assert len(attempts) == 2

    def test_retries_apply_to_raw_binary_reads(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(504, json={"detail": "gateway timeout"})
            return httpx.Response(200, content=b"bytes")

        pol, _ = policy()
        client = client_with(handler, retry=pol)

        assert client.download_artifact("d" * 64) == b"bytes"
        assert len(attempts) == 2

    def test_chemkin_export_post_is_not_retried_without_a_key(self):
        attempts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(503, json={"detail": "unavailable"})

        pol, _ = policy()
        client = client_with(handler, retry=pol)

        with pytest.raises(TCKDBHTTPError):
            client.export_chemkin({"all_reactions": True})

        assert len(attempts) == 1

    def test_successful_first_attempt_never_sleeps(self):
        pol, recorder = policy()
        client = client_with(
            lambda request: httpx.Response(200, json={"ok": True}), retry=pol
        )

        assert client.get_json("/health") == {"ok": True}
        assert recorder.count == 0

    def test_policy_is_reusable_across_clients(self):
        pol, recorder = policy()
        for _ in range(2):
            attempts: list[httpx.Request] = []

            def handler(request: httpx.Request) -> httpx.Response:
                attempts.append(request)
                if len(attempts) == 1:
                    return httpx.Response(503, json={"detail": "x"})
                return httpx.Response(200, json={"ok": True})

            client = client_with(handler, retry=pol)
            assert client.get_json("/health") == {"ok": True}
        assert recorder.count == 2
