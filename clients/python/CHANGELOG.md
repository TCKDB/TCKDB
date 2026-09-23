# Changelog

## 0.91.0 - 2026-09-23

**Breaking:** `tckdb` (the `get reaction`/`download artifact` CLI) no longer
defaults `--base-url` to a hardcoded deployment. Pass `--base-url`, or set
`TCKDB_BASE_URL`; omitting both is now a clear error naming both ways to
supply it, instead of a silent request to that host. If you relied on the
default, add `--base-url` or `TCKDB_BASE_URL` to your invocation. See issue
#521.

## 0.89.0 - 2026-09-23

Explicit enthalpy reference declarations for thermo deposits and grouped reference reads.
No inferred defaults or legacy backfill.
