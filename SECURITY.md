# Security policy and contact

## Reporting a vulnerability

Report suspected vulnerabilities **privately**. Do not open a public issue,
and do not include a working exploit in any public channel.

- Preferred: GitHub private vulnerability reporting on
  <https://github.com/TCKDB/TCKDB/security/advisories/new>.
- Alternative: email the maintainers listed in [`CITATION.cff`](CITATION.cff).

Please include the affected version or Alembic revision (visible at
`GET /api/v1/readyz`), the deployment mode (`DEPLOYMENT_MODE`), and the
smallest reproduction you can manage.

We aim to acknowledge a report within 5 working days and to agree a
disclosure timeline with the reporter. TCKDB is a research project maintained
by a small team; we will tell you honestly if a fix will take a while rather
than let a report go quiet.

## Scientific-data contact

Security reports and scientific corrections go to different places.

If a **record is wrong** — a bad number, a mis-attributed calculation, a
selection you disagree with — that is not a security issue. Open a public
issue, or contact the address in the affected dataset release's `contact`
field (`GET /api/v1/scientific/releases/{tag}`). Scientific disputes are
resolved in the open: the review history and the full candidate set behind
every curated selection are published with each release precisely so that
disagreement is possible without private appeal.

## Supported versions

TCKDB is pre-1.0. Only the current `main` branch and the currently deployed
hosted instance receive security fixes. There are no long-term support
branches yet; see [`CHANGELOG.md`](CHANGELOG.md) for the maturity policy.

## Scope

In scope:

- authentication and authorization bypass (API keys, sessions, role gates);
- unauthenticated access to raw artifact bytes, which may embed producer-side
  paths, usernames, and hostnames (see `docs/adr/0004-*`);
- injection, deserialization, and path-traversal issues in the API or the
  ingestion workflows;
- integrity failures in the release layer — anything that lets a **published**
  dataset release, its manifest, or its checksums be altered, or lets an
  append-only table (`release_selection`, `release_manifest`,
  `release_artifact`, `record_reproducibility_assessment`,
  `record_review_event`) be mutated;
- resource-exhaustion vectors reachable without authentication.

Out of scope:

- findings against a self-hosted deployment that has disabled the startup
  safety checks or is running with development settings;
- missing hardening headers on the local development server;
- scientific disagreement about a stored value (see above).

## Deployment hardening

Operators should read `docs/deployment/production_checklist.md`. The
application refuses to boot in a hosted deployment mode with unsafe settings
(`app/api/startup_checks.py`); do not work around that check.
