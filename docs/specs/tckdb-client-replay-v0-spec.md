# tckdb-client replay engine v0 spec

## Goal

Implement the offline replay engine and CLI in `tckdb-client` per [DR-0027](../decisions/0027-offline-payload-bundle-format-and-replay-engine-location.md). The engine walks a TCKDB Offline Payload Bundle directory on disk, posts the saved payloads to a TCKDB instance via the existing HTTP client, and updates each sidecar's status atomically.

The engine is chemistry-blind: it does not parse payload content, does not validate ESS signatures, does not import ARC. It dispatches on `payload_kind`, hands JSON or bytes to `tckdb-client`'s existing `request_json` / `upload`, and records the response.

> **Invariant for the implementer.** Every sidecar carries a `bundle_format_version`. The engine refuses sidecars whose version it does not understand. Sidecar status updates are atomic. The engine never inspects payload contents.

## Non-goals

- **No new TCKDB-side endpoints.** Replay calls only the same endpoints Track A uses (`/uploads/conformers`, `/calculations/{id}/artifacts`, etc.).
- **No new chemistry knowledge in `tckdb-client`.** The engine learns the bundle layout and the dispatch table; it never learns what a conformer or a calculation *means*.
- **No ARC import.** The CLI must be runnable in an environment where ARC is not installed, against bundles that ARC produced elsewhere.
- **No retry policy / scheduling / rate limiting** in the engine itself. A user re-running replay against transient errors re-runs every `failed` sidecar; idempotency makes this safe but possibly redundant. Smarter retry behaviour layers on top of the engine, not inside it.
- **No bundle archive format.** v0 operates on directories on disk. Tarballs / zip-based bundle distribution is a future concern.
- **No concurrency.** The engine processes sidecars sequentially in v0. Concurrent uploads against the same calc would be safe via idempotency but introduce lock-management complexity that's not warranted yet.
- **No new dependencies.** `tckdb-client`'s only runtime dependency today is `httpx`. Replay should not add `click`, `rich`, or any other CLI/UX library — `argparse` from stdlib is sufficient.

## Background — what's already built

| Component | Location | Status |
|---|---|---|
| `TCKDBClient` (HTTP transport) | `clients/python/tckdb-client/src/tckdb_client/client.py` | ✅ has `request_json(method, path, *, json, idempotency_key)` |
| `IdempotencyInputs` / key derivation | `tckdb_client.idempotency` | ✅ producers reuse stored keys via the sidecar; engine doesn't compute keys |
| Error classes (`TCKDBHTTPError`, etc.) | `tckdb_client.errors` | ✅ replay reuses for HTTP failures |
| ARC adapter (Track A producer) | `arc/tckdb/adapter.py` (ARC repo, branch `tckdb-imp`) | ✅ writes payloads + sidecars to disk during live runs |
| Sidecar shape (chemistry payloads) | `arc/tckdb/payload_writer.py:SidecarMetadata` | ✅ stable fields used in production |
| Sidecar shape (artifacts) | `arc/tckdb/payload_writer.py:ArtifactSidecarMetadata` | ✅ stable fields used in production |
| `bundle_format_version` field | — | ❌ does not exist yet; ARC must start emitting it |
| Replay engine | — | ❌ this spec |
| CLI entrypoint | — | ❌ this spec |
| `[project.scripts]` table in `pyproject.toml` | `clients/python/tckdb-client/pyproject.toml` | ❌ this spec adds `tckdb-replay = "tckdb_client.cli:main"` |

## Bundle format v0 — sidecar contract

Two sidecar variants in v0, both identifiable by `payload_kind`. Both must include `bundle_format_version`; the engine rejects sidecars where this field is missing or its major version is unknown.

### Common fields (all sidecars)

| field | type | required | meaning |
|---|---|---|---|
| `bundle_format_version` | string | ✅ | `"0"` for v0. Engine rejects mismatches. |
| `payload_kind` | string | ✅ | Dispatch key. v0 values: `conformer_calculation`, `calculation_artifact`. |
| `endpoint` | string | ✅ | Path the engine POSTs to (e.g. `/uploads/conformers`, `/calculations/6/artifacts`). |
| `idempotency_key` | string | ✅ | Stored at producer time per [DR-0025](../decisions/0025-arc-idempotency-key-derivation.md); replay reuses unchanged. |
| `base_url` | string | ✅ | The base URL the producer originally targeted. May be overridden by `--base-url` at replay time. |
| `status` | enum | ✅ | `pending` \| `uploaded` \| `failed` \| `skipped`. State machine below. |
| `last_error` | string \| null | optional | Most recent failure detail. Cleared on successful upload. |
| `uploaded_at` | ISO-8601 \| null | optional | Set on successful upload. |
| `response_status_code` | int \| null | optional | HTTP status on the successful response. |
| `response_body` | object \| null | optional | Truncated server response body for traceability. |
| `idempotency_replayed` | bool \| null | optional | Whether the server returned a cached response. |
| `created_at` | ISO-8601 | optional | Producer-side write time. |

### `payload_kind = conformer_calculation`

Adds:

| field | type | meaning |
|---|---|---|
| `payload_file` | string | Path to the on-disk JSON payload to POST. Engine reads this verbatim and posts it as the request body. |

### `payload_kind = calculation_artifact`

Adds:

| field | type | meaning |
|---|---|---|
| `source_path` | string | Path to the on-disk artifact file (a Gaussian log, an input deck, etc.). Engine re-reads, base64-encodes, and wraps in an `ArtifactsUploadRequest`. |
| `kind` | string | One of `ArtifactKind` (`output_log`, `input`, `checkpoint`, `formatted_checkpoint`, `ancillary`). Carried into the upload payload's `kind` field. |
| `filename` | string | Original filename. Carried into the upload payload's `filename` field. |
| `sha256` | string | Producer-computed SHA-256 of the source file at write time. At replay, the engine recomputes from `source_path`. If the producer's value is present and disagrees with the recomputed value, the engine fails locally with a "file drift" error and does **not** POST. The value sent in the request body is always the freshly-computed value. |
| `bytes` | int | Producer-computed file size at write time. Same drift-detection treatment as `sha256`. |
| `calculation_id` | int | Embedded in the URL via `endpoint` (`/calculations/{id}/artifacts`); duplicated here for diagnostics. |

### Sidecar `status` state machine

```
pending ──upload-attempt──> uploaded
   │                  │
   │                  └─> failed (last_error set, status stays available for retry)
   │
   └─upload-attempt──> uploaded | failed
```

`skipped` is set by the producer (e.g. when `tckdb_config.upload=false` in ARC), not the engine. The engine treats `skipped` and `uploaded` identically: ignore.

Replay reads anything `pending` or `failed`. After a successful upload, status becomes `uploaded`; the next replay run skips it.

## Required changes — `tckdb-client`

### Change 1 — `tckdb_client/replay.py` — the engine

New module. Public surface:

```python
ClientFactory = Callable[[str], TCKDBClient]
"""A factory that produces a TCKDBClient bound to the given base URL.

The CLI builds a factory closure capturing api_key, timeout, etc. The
engine calls the factory with the *effective* base URL for each
sidecar — never with a placeholder, never with a default. Base URL
precedence is therefore explicit at the engine boundary, not buried
inside a long-lived client instance."""


@dataclass(frozen=True)
class ReplaySummary:
    total: int
    uploaded: int
    skipped_already_uploaded: int
    failed: int
    by_kind: dict[str, dict[str, int]]   # kind -> {uploaded, failed, ...}


def replay_bundle(
    bundle_dir: str | Path,
    *,
    client_factory: ClientFactory,
    base_url_override: str | None = None,
    only_pending: bool = False,
    dry_run: bool = False,
    supported_format_versions: tuple[str, ...] = ("0",),
) -> ReplaySummary:
    """Walk bundle_dir, dispatch by payload_kind, post via a per-sidecar client, update sidecars."""
```

Core flow:

1. Walk `bundle_dir/` recursively for `*.meta.json` files.
2. Load each sidecar (JSON parse). On parse error: log `last_error="sidecar parse failure: <exc>"`, mark `failed` (atomically), continue.
3. Verify `bundle_format_version` is in `supported_format_versions`. On mismatch (including missing): mark `failed` with `last_error="unsupported bundle_format_version: <value>"`, continue.
4. Skip sidecars with `status` in `{"uploaded", "skipped"}`. If `only_pending=True`, also skip `failed`.
5. Resolve effective base URL: `base_url_override or sidecar["base_url"]`. If neither is present, mark `failed` with a clear `last_error` and continue.
6. Build a client for this sidecar: `client = client_factory(effective_base_url)`. The factory is called per-sidecar so different sidecars in the same bundle can target different `base_url`s if no override is given. The factory is also called when `dry_run=False`; under `dry_run=True` the engine skips this step.
7. Dispatch on `payload_kind` (see below). On unknown kind: mark `failed` with `last_error="unknown payload_kind: <kind>"`, continue.
8. For chosen handler, build the request and call `client.request_json(...)`.
9. On 2xx: update sidecar atomically with `status="uploaded"`, `uploaded_at`, `response_status_code`, `response_body`, `idempotency_replayed`, `last_error=None`.
10. On HTTP error: update sidecar atomically with `status="failed"`, `last_error=<error string>`, leave other fields.
11. On `dry_run=True`: skip the actual POST and the sidecar write; record what *would* have been done in the summary.

The engine must not raise on per-sidecar errors. Each sidecar is independent; one failure does not abort the whole bundle. If `bundle_dir` itself doesn't exist, that's a CLI-layer error (caught and exited cleanly).

### Change 2 — Dispatch handlers

Two handlers for v0. Each receives the parsed sidecar dict, the effective base URL, and the client; returns a `(success: bool, response_or_error)` tuple.

```python
def _replay_conformer_calculation(
    sidecar: dict, *, client: TCKDBClient, effective_base_url: str
) -> tuple[bool, Any]:
    payload_path = Path(sidecar["payload_file"])
    if not payload_path.exists():
        return (False, f"payload_file does not exist: {payload_path}")
    payload = json.loads(payload_path.read_text())
    response = client.request_json(
        "POST",
        sidecar["endpoint"],
        json=payload,
        idempotency_key=sidecar["idempotency_key"],
    )
    return (True, response)


def _replay_calculation_artifact(
    sidecar: dict, *, client: TCKDBClient, effective_base_url: str
) -> tuple[bool, Any]:
    source_path = Path(sidecar["source_path"])
    if not source_path.exists():
        return (False, f"source_path does not exist: {source_path}")
    content = source_path.read_bytes()
    fresh_sha256 = hashlib.sha256(content).hexdigest()
    fresh_bytes = len(content)
    # File-drift detection: if the producer recorded sha256 / bytes in
    # the sidecar, they must match the file's current state. A mismatch
    # means the bytes on disk are not the bytes the producer originally
    # uploaded — either the file was modified, swapped, or the sidecar
    # is wrong. Fail loudly *before* posting so the operator sees
    # "file drift" rather than a confusing server-side 422 about sha
    # mismatch.
    declared_sha = sidecar.get("sha256")
    declared_bytes = sidecar.get("bytes")
    if declared_sha is not None and declared_sha != fresh_sha256:
        return (False, (
            f"file drift: sidecar.sha256={declared_sha} does not match "
            f"current source_path.sha256={fresh_sha256}"
        ))
    if declared_bytes is not None and declared_bytes != fresh_bytes:
        return (False, (
            f"file drift: sidecar.bytes={declared_bytes} does not match "
            f"current source_path.bytes={fresh_bytes}"
        ))
    body = {
        "artifacts": [{
            "kind": sidecar["kind"],
            "filename": sidecar["filename"],
            "content_base64": base64.b64encode(content).decode("ascii"),
            "sha256": fresh_sha256,
            "bytes": fresh_bytes,
        }]
    }
    response = client.request_json(
        "POST",
        sidecar["endpoint"],
        json=body,
        idempotency_key=sidecar["idempotency_key"],
    )
    return (True, response)


_DISPATCH = {
    "conformer_calculation": _replay_conformer_calculation,
    "calculation_artifact": _replay_calculation_artifact,
}
```

The engine looks up `_DISPATCH[sidecar["payload_kind"]]`. Unknown kinds produce a `KeyError` that the engine catches and converts to a `failed` sidecar with a clear message.

### Change 3 — Atomic sidecar writes

A small helper used wherever the engine updates a sidecar:

```python
def _atomic_write_sidecar(sidecar_path: Path, sidecar: dict) -> None:
    """Write sidecar JSON via tempfile + os.replace.

    Targets POSIX atomic-rename semantics. On non-POSIX filesystems, the
    rename may not be atomic; the rename is still attempted and any
    leftover .tmp file should be reported (warning) by the caller, not
    treated as an error.
    """
    import json
    import os
    import tempfile

    sidecar_dir = sidecar_path.parent
    fd, tmp_path = tempfile.mkstemp(
        prefix=sidecar_path.name + ".",
        suffix=".tmp",
        dir=sidecar_dir,
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(sidecar, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())  # best-effort; ignore on platforms that don't support it
        os.replace(tmp_path, sidecar_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

Mid-write SIGKILL leaves a `.tmp` file the next replay run should clean up. Recommendation: at the start of `replay_bundle`, sweep `*.tmp` files older than (say) 1 hour as a janitorial step. Out of scope for v0; flag as known limitation.

### Change 4 — `tckdb_client/cli.py` — the CLI

Stdlib `argparse`, no new dependencies. The CLI builds a `client_factory` closure and hands it to the engine — never constructs a long-lived client with a placeholder URL.

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tckdb-replay",
        description="Replay a TCKDB Offline Payload Bundle to a TCKDB instance.",
    )
    parser.add_argument("bundle_dir", help="Path to the bundle directory (e.g. tckdb_payloads/)")
    parser.add_argument("--base-url", default=None, help="Override the base_url recorded in sidecars.")
    parser.add_argument(
        "--api-key-env", default="TCKDB_API_KEY",
        help="Env var holding the API key. Default: TCKDB_API_KEY",
    )
    parser.add_argument("--only-pending", action="store_true", help="Skip sidecars marked failed.")
    parser.add_argument("--dry-run", action="store_true", help="Walk the bundle without making HTTP calls.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    args = parser.parse_args(argv)

    api_key = os.environ.get(args.api_key_env)
    if api_key is None and not args.dry_run:
        parser.error(f"API key env var {args.api_key_env} is not set")

    # The factory binds api_key + timeout once; the engine calls it
    # with the effective base URL per sidecar. Base URL precedence
    # (--base-url > sidecar.base_url > error) is enforced inside the
    # engine before the factory is invoked, so the URL passed in here
    # is always non-placeholder.
    def _client_factory(base_url: str) -> TCKDBClient:
        return TCKDBClient(base_url=base_url, api_key=api_key, timeout=args.timeout)

    summary = replay_bundle(
        args.bundle_dir,
        client_factory=_client_factory,
        base_url_override=args.base_url,
        only_pending=args.only_pending,
        dry_run=args.dry_run,
    )

    print(_format_summary(summary))
    return 0 if summary.failed == 0 else 1
```

The factory is closed over `api_key` and `timeout`; the engine calls `_client_factory(effective_base_url)` for each sidecar. A bundle whose sidecars have heterogeneous `base_url` values (rare today, but legal under the contract) gets multiple short-lived clients pointed at different servers; the engine doesn't have to know or care.

Under `--dry-run`, the engine never invokes the factory, so even an unset `api_key` (e.g. for someone validating bundle shape without credentials) produces a usable validation pass — the factory is only as strict as the engine demands at the moment it's called.

Exit codes:

| code | meaning |
|---|---|
| 0 | All sidecars uploaded successfully (or all skipped). |
| 1 | One or more sidecars failed; user should inspect `last_error` in failing sidecars and re-run. |
| 2 | CLI argument error (handled by argparse). |
| 3 | Bundle directory does not exist or is not a directory. |

### Change 5 — `pyproject.toml` — register the CLI

Add to `clients/python/tckdb-client/pyproject.toml`:

```toml
[project.scripts]
tckdb-replay = "tckdb_client.cli:main"
```

After `pip install tckdb-client`, the user has a `tckdb-replay` command on their PATH.

## Required changes — ARC (companion)

These are out of scope for the `tckdb-client` PR but must land before any v0 bundle can be replayed. Track them as a separate ARC-side task.

### ARC change 1 — emit `bundle_format_version`

`arc/tckdb/payload_writer.py`'s `SidecarMetadata` and `ArtifactSidecarMetadata` dataclasses gain a `bundle_format_version: str = "0"` field. ARC's adapter writes this on every sidecar from now on.

### ARC change 2 — atomic sidecar writes (audit)

Audit the existing payload writer's sidecar update path. If it mutates in-place, switch to the same `tempfile + os.replace` pattern that the replay engine uses. The atomic-write contract must hold on both Track A (live upload) and Track B (replay) — otherwise a killed ARC run leaves a corrupt sidecar that no engine can recover.

### ARC change 3 — backfill (operational, not code)

Existing on-disk bundles produced before this change have no `bundle_format_version` field. They cannot be replayed by a v0-strict engine.

**The v0 engine is strict by design.** Bundles without `bundle_format_version` must be upgraded by an operator/backfill script before replay. Do not add missing-version tolerance to the v0 engine.

The backfill is a small operator script that walks an old bundle directory and adds `"bundle_format_version": "0"` to each sidecar atomically (same `tempfile + os.replace` pattern as the engine). Document this in the ARC README as the "upgrade existing bundles" recipe. If a tolerant escape hatch ever becomes warranted (e.g. for a one-shot migration where backfill is impractical), it lands as an explicit `--unsafe-assume-version 0` flag in a later release; v0 ships strict.

## Test plan

All tests live under `clients/python/tckdb-client/tests/test_replay.py` (engine) and `test_cli.py` (CLI).

### Engine tests

- **Round-trip happy path**: a fixture bundle with one `pending` `conformer_calculation` sidecar and one `pending` `calculation_artifact` sidecar. Replay with a stub client that returns 201. Both sidecars become `uploaded`, `uploaded_at` is set, `response_status_code=201`. Status is byte-for-byte the same shape Track A would produce.
- **Idempotency replay**: `pending` sidecar is replayed; stub client returns a response with `idempotency_replayed=True`. Sidecar's `idempotency_replayed` field is `True`, status `uploaded`.
- **Failed sidecar retry**: a sidecar with `status=failed` and `last_error="HTTP 503 transient"`. Replay it; stub client returns 201. Sidecar transitions to `uploaded`, `last_error=None`.
- **Already uploaded skip**: a sidecar with `status=uploaded`. Replay does not call the client, sidecar is unchanged.
- **`only_pending=True`**: a bundle with one `pending` and one `failed` sidecar. Replay only attempts the `pending`; `failed` is left untouched.
- **Unknown payload_kind**: a sidecar with `payload_kind="future_kind_not_yet_implemented"`. Sidecar transitions to `failed` with a clear `last_error`. The engine continues to other sidecars.
- **Version mismatch**: a sidecar with `bundle_format_version="1"` (or missing). Sidecar transitions to `failed` with a clear `last_error`. The engine continues.
- **Missing payload file**: a `conformer_calculation` sidecar whose `payload_file` does not exist. Sidecar transitions to `failed`; no HTTP call attempted.
- **Missing artifact source**: a `calculation_artifact` sidecar whose `source_path` does not exist. Same as above.
- **File drift — sha256 mismatch**: a `calculation_artifact` sidecar with `sha256="aaaa…"` (64 chars) but `source_path` whose actual content hashes to a different value. Sidecar transitions to `failed` with `last_error` containing `"file drift"`; no HTTP call is attempted (assert via stub client call count == 0).
- **File drift — bytes mismatch**: same shape, but with `bytes` declared as 100 and the actual file is 200 bytes. Same expected behaviour.
- **Base URL precedence**: with `base_url_override="http://override.example"`, the factory is invoked with `"http://override.example"`; without an override, the factory is invoked with the sidecar's `base_url`; with neither, the engine marks the sidecar `failed` and continues.
- **Client factory called per sidecar**: a bundle with two sidecars at the same `base_url` and one at a different `base_url`. The factory is called three times with the corresponding URLs; assert the factory's call list. Each call produces a client; clients are not reused across sidecars.
- **Client factory NOT called under `dry_run`**: with `dry_run=True`, the factory is never invoked. The summary still reports each sidecar as a would-have-been-attempted upload.
- **Atomic write under simulated interruption**: write a sidecar, then run the engine; mid-way through `_atomic_write_sidecar`, simulate `os.replace` failure (monkeypatch). Assert the original sidecar JSON is intact and a `.tmp` file may exist alongside it.
- **`dry_run`**: the engine walks the bundle, returns a summary, but no HTTP calls fire and no sidecars are mutated.

### CLI tests

- `tckdb-replay <empty_dir>` → exit 0, summary `total=0`.
- `tckdb-replay <missing_dir>` → exit 3.
- `tckdb-replay <bundle> --base-url http://foo --api-key-env MY_KEY`, with `MY_KEY` unset → exit 2 (argparse error or explicit handling).
- `tckdb-replay <bundle>` with all-pending sidecars and a working stub server → exit 0.
- `tckdb-replay <bundle>` with one failing sidecar → exit 1.

### ARC-side tests (separate PR)

- ARC adapter emits `bundle_format_version="0"` on every sidecar. (`arc/tckdb/payload_writer_test.py` extended.)
- ARC adapter sidecar updates are atomic. (Add a test that simulates SIGKILL-equivalent mid-write — patch `os.replace` to raise — and assert the pre-write sidecar is intact.)

## Open questions

1. **Should the engine accept a glob pattern instead of a fixed `tckdb_payloads/` root?** Today the bundle layout is fixed (`<root>/conformer_calculation/`, `<root>/calculation_artifacts/`). A glob lets users replay subsets ("just CHO sidecars") without copying files. Recommend: defer. v0 is "all sidecars in this directory tree." A `--filter <glob>` flag is straightforward to add later.

2. **Should `dry_run` write the summary to a file, or only print it?** Today the CLI prints to stdout. For automation use cases (CI, scheduled replay) a `--summary-json <path>` would be valuable. Recommend: defer to v0.1.

3. **How does the engine handle a bundle written by a producer that emitted `bundle_format_version="0"` but with extra sidecar fields the engine doesn't recognize?** Today: the engine ignores unknown fields (Pydantic-style permissive). This forward-compatibility might bite if a producer accidentally renames a load-bearing field. Recommend: be permissive on unknown additions, strict on missing required fields. Document the contract.

4. **Should the engine support a "validate-only" mode (parse all sidecars, check shapes, no HTTP)?** This is `dry_run` today. The naming might be improved (`--validate` is clearer than `--dry-run` for "I just want to check the bundle is well-formed"). Recommend: alias for v0.1.

## Implementation order

The order intentionally puts the format spec first (so producers can update in parallel), then the engine, then the CLI, then the ARC companion changes.

1. **Add the bundle format spec section to this document or split it out.** The Bundle format v0 section above is the contract. Producers (ARC) and consumers (the engine) read this section. Until it's stable, do not start coding.
2. **Write the engine** — `tckdb_client/replay.py` with the public `replay_bundle()` function and the two dispatch handlers. No CLI yet. Run engine tests against fixture bundles.
3. **Add atomic write helper** — used in step 2; pulled into a small standalone module if useful.
4. **Write the CLI** — `tckdb_client/cli.py` calling into the engine. Run CLI tests with a `responses` or `httpx`-mock-style stub.
5. **Register the CLI in `pyproject.toml`** — `[project.scripts]` entry. Reinstall locally with `pip install -e .` to verify the entrypoint resolves.
6. **ARC companion PR (separate)** — emit `bundle_format_version`, audit ARC's sidecar writes for atomicity. Land before any v0 bundles need to be replayed in production.
7. **Operational backfill** — the one-liner that adds `bundle_format_version` to existing bundles. Document in ARC's README.

## ARC adapter follow-up (separate, out of scope here)

After this spec ships and is implemented:

- ARC's adapter starts emitting `bundle_format_version="0"` on every sidecar.
- ARC's `payload_writer.py` is audited for atomic-write semantics, fixed if needed.
- An optional `arc tckdb-replay` convenience wrapper may be added that subprocesses the `tckdb-replay` CLI. UX sugar, not architecture.

These are tracked as ARC-side work, not part of the `tckdb-client` PR.
