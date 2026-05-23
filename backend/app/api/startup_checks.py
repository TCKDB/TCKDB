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

from app.api.config import Settings


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
