"""What the versioned-names guard allows, and what it still owes.

``ALLOWED`` is the permanent list: each entry says why a match is not a
TCKDB version fused into a name. It is short on purpose. An entry that
matches nothing fails the guard, so the list cannot rot into a blanket
exemption.

``DEBT`` is the list of what the guard still owes. It is **empty**: the versioning programme is finished (PR-2
deleted the legacy versions, PR-3 renamed the code identifiers, PR-4 split the stored and wire strings), so every match
is either fixed or carries a reasoned ``ALLOWED`` entry. The tuple stays so that ``scripts/check_versioned_debt_not_grown.py``
has a binding to read at the merge base and at the head, and so a future bulk change (a new generation of rule
manifests, say) has somewhere to say what it is deferring. Anything put here may only shrink, and the ceilings in
``tests/test_no_versioned_names.py`` are zero, so adding a row means raising a ceiling in the same change.

Regenerate candidate rows with ``python -m tests.versioned_names_scan``.
"""

from __future__ import annotations

#: PRs of the versioning programme that have landed. No debt row may name one;
#: each later PR adds itself here in the same change that empties its rows.
COMPLETED_PRS: frozenset[str] = frozenset({"PR-2", "PR-3", "PR-4"})

#: Allowances this change adds, as (path glob, kind, token glob). A new ``ALLOWED`` entry, or a
#: widened glob (a different key), that the merge base does not have fails
#: ``scripts/check_versioned_debt_not_grown.py`` unless it is named here; naming it makes the
#: script print the entry and its reason for the reviewer. Empty when nothing is being added.
#: Bound exactly once, as a literal tuple: the script refuses anything else.
NEW_ALLOWANCES: tuple[tuple[str, str, str], ...] = ()

#: (path glob, kind, token glob, reason). ``path`` is repo-relative.
ALLOWED: tuple[tuple[str, str, str, str], ...] = (
    (
        'backend/app/services/artifact_storage_*.py',
        'identifier',
        'SigV4Auth',
        "botocore's own class name for AWS Signature Version 4; not a TCKDB version.",
    ),
    (
        'backend/app/services/artifact_storage_*.py',
        'identifier',
        'S3SigV4Auth',
        "botocore's own class name for the S3 flavour of AWS Signature Version 4; not a TCKDB version.",
    ),
    (
        'backend/app/services/gaussian_parameter_parser.py',
        'identifier',
        'iop_2_9_2000',
        'Names the Gaussian route keyword IOp(2/9=2000); the digits are a keyword, not a version.',
    ),
    (
        'backend/app/importers/thermoml/*.py',
        'string',
        '*ThermoML.v2020*',
        "NIST's name for the ThermoML archive release (ThermoML.v2020-09-30); a third party's version, recorded as given.",
    ),
    (
        'backend/app/api/config.py',
        'fstring',
        "f'postgresql+psycopg://*",
        "A database URL: the '@' separates credentials from host, it does not append a version.",
    ),
    (
        'backend/app/schemas/workflows/thermoml_upload.py',
        'string',
        'PD94bWwg*',
        "A base64 payload (an XML example document); the 'V1' is an accident of the encoding, not a name.",
    ),
    (
        'schemas/python/tckdb-schemas/tckdb_schemas/fragments/execution_environment.py',
        'fstring',
        "f'{module.name}/{module.version}'",
        "Formats an HPC module as name/version (gaussian/16); the module and its version are the site's, recorded as given.",
    ),
)

#: (path, kind, token, occurrences, removed by). Empty: see the module docstring.
DEBT: tuple[tuple[str, str, str, int, str], ...] = ()
