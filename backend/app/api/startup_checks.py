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
