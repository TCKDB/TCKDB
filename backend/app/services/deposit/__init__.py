"""Digest-bound publication deposit (``tckdb.deposit.v1``).

A *release* is the citable, frozen scientific product (four NDJSON files and
a manifest). An *archive* is the lossless ``tckdb.archive.v1`` database
snapshot that carries the evidence behind it. A *deposit* is the directory
that binds one release, its archive, the exact source commit, the scripts
that generate every manuscript number and those scripts' expected outputs
into a single ``MANIFEST.json`` of SHA-256 digests, so that a reader with the
deposit alone can rebuild the database, re-run the generators and check every
byte. See :mod:`app.services.deposit.build`.
"""

from app.services.deposit.build import (
    DEPOSIT_SCHEMA,
    AccountNotAllowlistedError,
    ActorOutsideAllowlistError,
    DepositError,
    DepositVerificationReport,
    DirtyTreeError,
    NoExactTagError,
    OutputNotEmptyError,
    ReleaseNotPublishableError,
    RevisionMismatchError,
    SourceBinding,
    SourcePinMissingError,
    UnknownVersionError,
    assert_publishable,
    collect_release_members,
    source_binding,
    verify_deposit,
    write_deposit,
)
from app.services.deposit.expected_outputs import (
    Generator,
    render_json,
    render_markdown,
    write_expected_outputs,
)

__all__ = [
    "DEPOSIT_SCHEMA",
    "AccountNotAllowlistedError",
    "ActorOutsideAllowlistError",
    "DepositError",
    "DepositVerificationReport",
    "DirtyTreeError",
    "Generator",
    "NoExactTagError",
    "OutputNotEmptyError",
    "ReleaseNotPublishableError",
    "RevisionMismatchError",
    "SourceBinding",
    "SourcePinMissingError",
    "UnknownVersionError",
    "assert_publishable",
    "collect_release_members",
    "render_json",
    "render_markdown",
    "source_binding",
    "verify_deposit",
    "write_deposit",
    "write_expected_outputs",
]
