"""Application configuration via environment variables.

Reuses the same ``DB_*`` variables consumed by ``alembic/env.py`` and
``tests/conftest.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings


DeploymentMode = Literal["local", "shared_private", "hosted_public"]
AIReviewAssistantMode = Literal["off", "cloud", "local", "test"]
LLMPrecheckProviderName = Literal[
    "disabled",
    "fake_test",
    "online_api",
    "local_http",
]


class Settings(BaseSettings):
    # Deployment posture. Drives the startup safety guard in
    # :mod:`app.api.startup_checks` — ``local`` permits developer-friendly
    # defaults (open registration, exposed docs, no TLS cookie), while
    # ``shared_private`` and ``hosted_public`` refuse to boot when any
    # production-required setting is unsafe. See
    # ``docs/deployment/production_checklist.md``.
    deployment_mode: DeploymentMode = "local"

    db_user: str = "tckdb"
    db_password: str = "tckdb"
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "tckdb_dev"
    db_client_encoding: str = "utf8"

    # Registration policy. Local/dev defaults to open self-service so the
    # API stays usable out of the box; hosted deployments set
    # ``AUTH_ALLOW_OPEN_REGISTRATION=false`` and seed accounts via admin
    # tooling.
    auth_allow_open_registration: bool = True

    # Phase D internal-ID visibility policy. When ``False`` (the
    # default, intended for hosted production), the scientific read
    # API hides internal integer primary keys (``*_id`` fields,
    # ``*_ids`` bare arrays, and a small allow-list of non-suffix PK
    # keys like ``LiteratureSummary.id``) from every response and
    # silently drops ``include=internal_ids`` from the resolved
    # include set. Set to ``True`` in local/dev/test environments
    # that need the legacy id-bearing shape for compatibility or
    # debugging. See ``docs/specs/internal_ids_visibility_policy.md``.
    allow_public_internal_ids: bool = False

    # -----------------------------------------------------------------
    # Hosted abuse-control settings (security phase 1).
    # See ``docs/specs/public_read_abuse_controls.md``.
    # -----------------------------------------------------------------

    # Master switch for the application-level rate limiter. The test
    # suite turns it off via env var so the fixed budgets don't
    # interfere with bulk test runs.
    rate_limit_enabled: bool = True

    # Per-minute budgets, evaluated as a fixed window. Anonymous
    # callers are keyed by client IP; authenticated callers are keyed
    # by API-key fingerprint or session cookie so concurrent users on
    # one IP don't share a bucket.
    #
    # The buckets are split by route class so a noisy uploader cannot
    # starve the public read surface and an anonymous scraper does not
    # accidentally inherit the generous read budget for writes:
    #
    # - ``anon_read``  — anonymous GET/POST-search against the public
    #   scientific surface. Reasonably generous; IP-keyed.
    # - ``auth_read``  — same routes but with a credential present.
    #   The largest budget; credential-fingerprint-keyed.
    # - ``auth_write`` — authenticated mutations (uploads, admin,
    #   non-login POST/PUT/PATCH/DELETE). Tight on purpose; one
    #   misbehaving uploader should not exhaust an entire deployment.
    # - ``anon_other`` — anonymous everything-else, including stray
    #   mutating requests. Smallest budget so anonymous abuse cannot
    #   ride the read bucket.
    rate_limit_anon_read_per_minute: int = 60
    rate_limit_auth_read_per_minute: int = 300
    rate_limit_auth_write_per_minute: int = 30
    rate_limit_anon_other_per_minute: int = 20

    # Auth-surface throttles. These are deliberately tight: login is
    # the credential-stuffing target, and register is the account-spam
    # target. Both are keyed by client IP.
    rate_limit_auth_login_per_minute: int = 10
    rate_limit_register_per_hour: int = 10

    # When set, the middleware reads the client IP from this header.
    # Only enable when the deployment terminates TLS behind a trusted
    # reverse proxy that overwrites the header. With this unset the
    # middleware falls back to the ASGI transport peer, which a
    # spoofer cannot influence.
    trusted_proxy_header: str | None = None

    # Per-public-read query caps. These guard against unbounded
    # responses regardless of rate-limit budget.
    public_max_limit: int = 200
    public_max_offset: int = 10_000
    max_geometry_atoms_public: int = 500
    max_full_calculations_public: int = 100
    max_full_geometries_public: int = 100
    max_full_artifacts_public: int = 100
    max_full_conformer_groups_public: int = 100

    # OpenAPI / Swagger / ReDoc exposure. Default on for local
    # development so the docs stay one URL away. Hosted deployments
    # set ``EXPOSE_API_DOCS=false`` to gate the docs behind a private
    # network or an authenticated path. When false, FastAPI never
    # registers ``/docs``, ``/redoc``, or ``/openapi.json``.
    expose_api_docs: bool = True

    # Legacy entity-read auth gate. The public scientific surface
    # lives under ``/api/v1/scientific/*``; the older
    # ``/api/v1/{thermo,kinetics,...}`` routes pre-date the
    # internal-IDs visibility policy and have a flatter shape that
    # leaks integer PKs. Hosted deployments leave this on so those
    # routes require credentials; local/dev sets it to false to keep
    # them open.
    legacy_reads_require_auth: bool = True

    # Session cookie posture. The defaults assume the API is behind a
    # TLS-terminating proxy in production; local/dev sets
    # ``SESSION_COOKIE_SECURE=false`` so plain-HTTP login works.
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "lax"
    session_cookie_httponly: bool = True

    # F13: PostgreSQL ``statement_timeout`` for application sessions.
    # Set as a positive integer (milliseconds) to apply the timeout
    # on every new DBAPI connection — a safety net so one expensive
    # query cannot consume a pool slot indefinitely. ``0`` or
    # ``None`` disables the app-level setting (production deployments
    # are encouraged to set ``ALTER ROLE tckdb SET
    # statement_timeout = '30s'`` at the role level instead — see
    # ``docs/specs/public_read_abuse_controls.md``).
    db_statement_timeout_ms: int | None = 30_000

    # Minimum ``tckdb-client`` version accepted on write/upload routes.
    # Requests that identify themselves via ``X-TCKDB-Client-Name:
    # tckdb-client`` and a lower ``X-TCKDB-Client-Version`` are rejected
    # with ``426 Upgrade Required``. Raw HTTP callers that omit the
    # client-name header are not blocked. Bump this when a coordinated
    # client/server change makes older clients incompatible.
    min_supported_tckdb_client_version: str = "0.11.0"
    enforce_tckdb_client_version_on_writes: bool = True

    # Optional AI Review Assistant / LLM precheck. Default is fully off:
    # no API key, no local model, no network call, no Docker service, and
    # no dependency for uploads, reads, validation, or deterministic trust.
    ai_review_assistant_mode: AIReviewAssistantMode = "off"
    llm_precheck_provider: LLMPrecheckProviderName = "disabled"
    llm_precheck_model: str | None = None
    llm_precheck_api_key_env: str | None = None
    llm_precheck_base_url: str | None = None
    llm_precheck_timeout_seconds: int = 30
    llm_precheck_max_input_tokens: int = 6000
    llm_precheck_max_output_tokens: int = 1200
    llm_precheck_include_artifact_text: bool = False
    llm_precheck_include_coordinates: bool = False
    llm_precheck_store_full_context: bool = False

    # Observability: log output format and minimum level. ``text`` is
    # the developer-friendly default; hosted deployments set
    # ``LOG_FORMAT=json`` so logs land in their structured backend
    # with ``request_id``, ``level``, ``logger``, ``message`` fields.
    # Configured by :mod:`app.api.logging_config`.
    log_format: str = "text"
    log_level: str = "INFO"

    # CORS — empty allow-list means "no CORS middleware registered",
    # which is the correct hosted default. Production deployments set
    # ``CORS_ALLOW_ORIGINS="https://app.tckdb.org"`` (one or more
    # comma-separated origins).
    cors_allow_origins: list[str] = []
    cors_allow_credentials: bool = False
    cors_allow_methods: list[str] = ["GET", "POST", "OPTIONS"]
    cors_allow_headers: list[str] = [
        "Authorization",
        "Content-Type",
        "X-API-Key",
    ]

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?client_encoding={self.db_client_encoding}"
        )


settings = Settings()
