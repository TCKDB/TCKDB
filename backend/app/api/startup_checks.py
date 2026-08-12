"""Startup safety guard for hosted/public deployments.

The application supports three deployment postures, selected via the
``DEPLOYMENT_MODE`` env var on :class:`app.api.config.Settings`:

* ``local`` — developer laptop / CI. Developer-friendly defaults
  (open registration, exposed docs, plaintext cookies) are allowed.
* ``shared_private`` — lab or internal deployment behind private
  network controls. Production-safe auth/doc/internal-id settings
  are enforced; CORS is *not* required (curl/HPC-only is common).
* ``hosted_public`` — internet-facing deployment. Same enforced
  settings as ``shared_private`` plus stricter CORS requirements.

The guard is intentionally narrow: it checks the small set of flags
documented in ``docs/deployment/production_checklist.md``. It does not
attempt to verify operator concerns like "is TLS actually terminated
upstream" — those remain in the pre-flight checklist.
"""

from __future__ import annotations

import logging
import threading

from app.api.config import Settings

logger = logging.getLogger(__name__)


class UnsafeDeploymentConfigError(RuntimeError):
    """Raised when a hosted deployment is starting with unsafe settings.

    The ``violations`` list contains one human-readable line per
    unsafe setting so the operator sees every problem at once.
    """

    def __init__(self, mode: str, violations: list[str]) -> None:
        self.mode = mode
        self.violations = list(violations)
        message = "Unsafe deployment configuration for DEPLOYMENT_MODE={mode}:\n{lines}".format(
            mode=mode,
            lines="\n".join(f"- {v}" for v in violations),
        )
        super().__init__(message)


def _collect_violations(settings: Settings) -> list[str]:
    """Return the list of unsafe-setting messages for a hosted mode.

    Caller has already confirmed ``settings.deployment_mode`` is one of
    the hosted modes; ``local`` short-circuits before reaching here.
    """
    violations: list[str] = []

    if settings.auth_allow_open_registration:
        violations.append("AUTH_ALLOW_OPEN_REGISTRATION must be false")
    if settings.expose_api_docs:
        violations.append("EXPOSE_API_DOCS must be false")
    if not settings.legacy_reads_require_auth:
        violations.append("LEGACY_READS_REQUIRE_AUTH must be true")
    if not settings.session_cookie_secure:
        violations.append("SESSION_COOKIE_SECURE must be true")
    if settings.allow_public_internal_ids:
        violations.append("ALLOW_PUBLIC_INTERNAL_IDS must be false")
    if not settings.rate_limit_enabled:
        violations.append("RATE_LIMIT_ENABLED must be true")

    # CORS rules diverge between the two hosted modes. shared_private
    # may run curl/HPC-only with no browser clients (empty allow-list
    # disables the middleware entirely, which is fine). hosted_public
    # is allowed to omit CORS only if no browser app uses the API; the
    # "*" wildcard is never acceptable in either hosted mode because
    # it neutralizes the allow-list.
    if "*" in settings.cors_allow_origins:
        violations.append('CORS_ALLOW_ORIGINS must not contain "*"')

    # statement_timeout is recommended, not strictly required — operators
    # often set it at the role level instead (see the checklist). We
    # reject only the affirmatively-disabled value (<=0) here; ``None``
    # is treated as "rely on role-level setting".
    if settings.db_statement_timeout_ms is not None and settings.db_statement_timeout_ms <= 0:
        violations.append("DB_STATEMENT_TIMEOUT_MS must be > 0 when set")

    return violations


def validate_deployment_safety(settings: Settings) -> None:
    """Validate that hosted deployments boot with production-safe settings.

    ``local`` is a no-op. ``shared_private`` and ``hosted_public`` raise
    :class:`UnsafeDeploymentConfigError` with the full list of unsafe
    settings if any required check fails.
    """
    mode = settings.deployment_mode
    if mode == "local":
        return

    violations = _collect_violations(settings)
    if violations:
        raise UnsafeDeploymentConfigError(mode, violations)


#: The only encoding this deployment is designed for. Anything else stores
#: bytes without validating them (``SQL_ASCII``) or silently transcodes.
EXPECTED_SERVER_ENCODING = "UTF8"


def server_encoding(session) -> str | None:
    """Return the connected cluster's ``server_encoding``, or None."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        return session.execute(text("SHOW server_encoding")).scalar_one_or_none()
    except SQLAlchemyError:
        return None


#: The template every ``CREATE DATABASE`` on a cluster copies when the
#: statement names no other one. Its encoding is therefore the encoding a
#: new database gets by default, whatever the *current* database is.
TEMPLATE_DATABASE = "template1"


def template_encoding(session) -> str | None:
    """Return ``template1``'s encoding on the connected cluster, or None.

    :func:`server_encoding` answers "is the database I am connected to
    right", which is not the same question as "is the next database created
    here going to be right". ``CREATE DATABASE`` with no ``TEMPLATE`` clause
    copies ``template1``, so a cluster can hold a correct ``UTF8`` production
    database and still hand ``SQL_ASCII`` to everything created next to it.

    That is not hypothetical: the live deployment's ``tckdb`` was converted
    to ``UTF8`` by hand after the 2026-08-04 incident and its ``template1``
    was not, so on 2026-08-12 the two disagreed. The restore runbooks drop
    and recreate the database, which is exactly the moment the template's
    encoding becomes the production database's encoding -- silently, because
    a ``SQL_ASCII`` database accepts every byte a ``UTF8`` dump contains and
    only mis-counts them afterwards.

    Reported rather than judged, on the same reasoning as ``server_encoding``:
    a divergent template is a real hazard and no restart fixes it, so it is
    made answerable from outside rather than made to degrade ``/status``.

    Reads ``pg_database``, a catalog table, on a session whose statement
    timeout is already installed by :mod:`app.api.deps`, and only after
    connectivity has been proven -- so it needs no deadline of its own.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        return session.execute(
            text(
                "SELECT pg_encoding_to_char(encoding) FROM pg_database "
                "WHERE datname = :name"
            ),
            {"name": TEMPLATE_DATABASE},
        ).scalar_one_or_none()
    except SQLAlchemyError:
        return None


#: Hard wall-clock ceiling for the boot-time encoding probe, mirroring
#: ``_STORAGE_PROBE_DEADLINE_SECONDS`` in :mod:`app.api.routes.health` and
#: for the same reason. ``connect_timeout`` on the engine URL
#: (``Settings.db_connect_timeout_seconds``) bounds the socket connect,
#: which is the common case, but it does not bound everything: libpq
#: applies it per host attempt and around the connect poll loop, so a
#: blocking DNS lookup and a server that completes a handshake and then
#: never answers ``SHOW server_encoding`` both escape it. The deadline is
#: what makes "neither blocks startup" a property of this module rather
#: than of the network.
_ENCODING_PROBE_DEADLINE_SECONDS = 4.0


def _read_server_encoding() -> str | None:
    """Open a session on the ambient engine, ask, and close it."""
    from app.api.deps import SessionLocal

    session = SessionLocal()
    try:
        return server_encoding(session)
    finally:
        session.close()


def report_database_encoding_at_startup() -> str | None:
    """Check the cluster's encoding at boot and say so if it is wrong.

    The encoding a deployment gets is fixed at ``initdb`` and cannot be
    changed afterwards without a dump and restore, so it is not something
    the application can fix — but it is something it can refuse to be
    quiet about. ``docker-compose.yml`` now pins ``--encoding=UTF8``; a
    pin that nothing verifies is a wish, and clusters created before it,
    or by hand, or by a different compose file, are still out there.

    A ``SQL_ASCII`` cluster stores whatever bytes it is handed and
    validates nothing, so the damage is delayed and arbitrary: everything
    works until the first non-ASCII character arrives, which is how one
    em dash came to roll back an entire upload on 2026-08-04, months
    after the volume was created.

    Does not raise. The database being unreachable at boot is normal --
    the API may legitimately start before Postgres accepts connections --
    and is reported at ``debug``, not as a fault.

    **It also does not wait.** The query runs on a daemon thread and the
    lifespan stops waiting after
    :data:`_ENCODING_PROBE_DEADLINE_SECONDS`, which is what the storage
    probe already did and this one did not. A *refused* connection has
    always returned in milliseconds; a black-holed host -- packets
    dropped rather than rejected -- returned after the kernel exhausted
    its SYN retries, measured at 130 s, and the whole of that was spent
    inside the lifespan with the API accepting nothing. A diagnostic that
    can convert a degraded deployment into an outage is not worth its
    diagnosis.

    The abandoned thread is a daemon and holds only its own session, so
    it neither delays process exit nor is joined at interpreter shutdown;
    it unwinds on its own once ``connect_timeout`` fires.
    """
    outcome: dict[str, str | None] = {}
    finished = threading.Event()

    def _probe() -> None:
        try:
            outcome["encoding"] = _read_server_encoding()
        except BaseException:  # pragma: no cover - defensive
            # Same contract as the storage probe: a boot diagnostic that
            # raises must not become the fault it exists to report.
            logger.exception("startup: server_encoding probe raised; continuing")
        finally:
            finished.set()

    threading.Thread(
        target=_probe, name="db-encoding-probe", daemon=True
    ).start()

    if not finished.wait(_ENCODING_PROBE_DEADLINE_SECONDS):
        logger.warning(
            "startup: database did not answer within %ss; server_encoding "
            "unchecked and boot continuing. A refused connection returns "
            "at once, so this is the other shape: the host is accepting "
            "the packets and dropping them, or resolving and not "
            "answering. Check DB_HOST/DB_PORT and anything filtering "
            "between here and the cluster.",
            _ENCODING_PROBE_DEADLINE_SECONDS,
        )
        return None

    encoding = outcome.get("encoding")
    if encoding is None:
        logger.debug(
            "startup: database not reachable yet; server_encoding unchecked"
        )
        return None
    if encoding.upper() == EXPECTED_SERVER_ENCODING:
        logger.info("startup: database server_encoding=%s", encoding)
    else:
        logger.error(
            "startup: DATABASE SERVER_ENCODING IS %s, EXPECTED %s. This "
            "cluster does not validate the bytes it stores, and any "
            "non-ASCII character in any value -- including one in an error "
            "or warning message -- can abort the transaction that carries "
            "it. The encoding is fixed at initdb and cannot be altered in "
            "place: it needs a dump, a cluster created with "
            "--encoding=UTF8, and a restore. See "
            "docs/deployment/troubleshooting.md.",
            encoding,
            EXPECTED_SERVER_ENCODING,
        )
    return encoding


def report_artifact_storage_at_startup() -> dict:
    """Probe the object store once at boot and say so in the log.

    **Why at startup and not only on ``/status``.** On 2026-08-05 the
    containerised API ran with ``S3_ENDPOINT_URL=http://127.0.0.1:9000``
    inherited from the earlier host deployment, where inside a container
    that is the container's own loopback. Every artifact-bearing upload
    returned 503 for as long as it took someone to try one. The
    misconfiguration was present and detectable from the first second of
    the process's life, and nothing looked.

    The first place an operator looks after a deploy is ``docker logs
    tckdb-api`` — the deploy script's own failure message says exactly
    that — so this writes one unmissable line there, naming the endpoint
    and bucket that were tried. ``/status`` still answers the same
    question on demand; this answers it before anyone asks.

    **It does not refuse to boot.** With the object store down, reads,
    queries, and uploads without attached files all still work, so
    exiting would replace a partial outage with a total one — the same
    reasoning that keeps artifact storage out of ``/readyz``. The probe
    inherits ``/status``'s hard wall-clock deadline, so an unreachable
    endpoint delays startup by a bounded few seconds and never hangs it.

    Returns the same block ``/status`` reports, so a caller (and a test)
    can assert on it rather than scrape the log.
    """
    # Imported here rather than at module scope: this module is imported by
    # the application factory before the router exists, and the routes
    # package pulls in the whole dependency graph.
    from app.api.routes.health import _artifact_storage_status

    try:
        block = _artifact_storage_status()
    except Exception as exc:  # pragma: no cover - defensive
        # A probe that raises must not take the process with it. Boot-time
        # diagnostics exist to make failures visible, not to create one.
        logger.error(
            "startup: artifact storage probe raised %s; continuing to boot",
            type(exc).__name__,
        )
        return {"healthy": None, "reason": f"probe raised {type(exc).__name__}"}

    if block.get("healthy") is True:
        logger.info(
            "startup: artifact storage ok (endpoint=%s bucket=%s)",
            block.get("endpoint"),
            block.get("bucket"),
        )
    else:
        logger.error(
            "startup: ARTIFACT STORAGE UNAVAILABLE - endpoint=%s bucket=%s "
            "reachable=%s reason=%s. Artifact-bearing uploads will fail with "
            "503 until this is fixed; reads and queries are unaffected. This "
            "is usually S3_ENDPOINT_URL in the deployment's env file - note "
            "that 127.0.0.1 inside a container is the container's own "
            "loopback, not the host's.",
            block.get("endpoint"),
            block.get("bucket"),
            block.get("reachable"),
            block.get("reason"),
        )
    return block
