"""What the versioned-names guard allows, and what it still owes.

``ALLOWED`` is the permanent list: each entry says why a match is not a
TCKDB version fused into a name. It is short on purpose. An entry that
matches nothing fails the guard, so the list cannot rot into a blanket
exemption.

``DEBT`` is the temporary list: every place that already violates the rule,
with the number of occurrences and the PR of the versioning programme that
removes it (``PR-2`` deletes the legacy versions, ``PR-3`` renames the code
identifiers, ``PR-4`` rewrites the stored and wire strings). It may only
shrink:

* an entry the scan no longer finds fails the guard (delete the entry);
* an entry whose count differs from the scan fails the guard (fix the count --
  a lower count after a partial clean-up is the only change that is allowed);
* a row naming a PR in ``COMPLETED_PRS`` fails the guard (that PR said it was done);
* the ceilings in ``tests/test_no_versioned_names.py`` fail any growth in
  entries or occurrences, so a new violation cannot be hidden by adding a row.

Regenerate the rows with ``python -m tests.versioned_names_scan`` and keep the
``PR`` column by hand. PR-4 deletes ``DEBT`` and asserts the scan is clean.
"""

from __future__ import annotations

#: PRs of the versioning programme that have landed. No debt row may name one;
#: each later PR adds itself here in the same change that empties its rows.
COMPLETED_PRS: frozenset[str] = frozenset({"PR-2", "PR-3"})

#: Allowances this change adds, as (path glob, kind, token glob). A new ``ALLOWED`` entry, or a
#: widened glob (a different key), that the merge base does not have fails
#: ``scripts/check_versioned_debt_not_grown.py`` unless it is named here; naming it makes the
#: script print the entry and its reason for the reviewer. Empty when nothing is being added.
#: Bound exactly once, as a literal tuple: the script refuses anything else.
NEW_ALLOWANCES: tuple[tuple[str, str, str], ...] = ()

#: Debt rows whose token text changed only because a rename (a PR-3 rename of an identifier the text mentions)
#: rewrote it: ``(path, kind, old token, new token)``. The violation, its file, its kind and its count are the
#: same as at the merge base; ``scripts/check_versioned_debt_not_grown.py`` accepts the new text instead of
#: calling it a new violation, and prints each move for the reviewer. PR-4 deletes the rows and this tuple.
#: Bound exactly once, as a literal tuple.
MOVED_DEBT: tuple[tuple[str, str, str, str], ...] = (
    (
        'backend/app/services/kinetics_selection/manifest.py',
        'fstring',
        "f'{COMPUTED_KINETICS_V2.name}@{COMPUTED_KINETICS_V2.version}'",
        "f'{COMPUTED_KINETICS.name}@{COMPUTED_KINETICS.version}'",
    ),
    (
        'backend/app/services/thermo_selection/manifest.py',
        'fstring',
        "f'{COMPUTED_THERMO_V2.name}@{COMPUTED_THERMO_V2.version}'",
        "f'{COMPUTED_THERMO.name}@{COMPUTED_THERMO.version}'",
    ),
)

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
        'backend/app/chemistry/structure_rules/*.yaml',
        'yaml',
        'SANTRA_MARTIN_2022_ARXIV_V2',
        "An id for the arXiv v2 copy of a cited paper (Santra and Martin 2022); a citation of someone else's revision, not a TCKDB version.",
    ),
    (
        'schemas/python/tckdb-schemas/tckdb_schemas/fragments/execution_environment.py',
        'fstring',
        "f'{module.name}/{module.version}'",
        "Formats an HPC module as name/version (gaussian/16); the module and its version are the site's, recorded as given.",
    ),
)

#: (path, kind, token, occurrences, removed by).
DEBT: tuple[tuple[str, str, str, int, str], ...] = (
    ('backend/app/chemistry/network_rules/network_rule_candidates.yaml', 'yaml', 'proposed evidence rule (the Network-v1 council record, section 3, "alternate fits of one solve").', 1, 'PR-4'),
    ('backend/app/chemistry/structure_rules/structure_rule_candidates.yaml', 'yaml', 'Records owner decision D (O5, 2026-10-13): relative conformer energies are a named-reference oper...', 1, 'PR-4'),
    ('backend/app/importers/cccbdb/__init__.py', 'string', 'cccbdb-experimental-species-parser/0.1.0', 1, 'PR-4'),
    ('backend/app/importers/cccbdb/diagnostics/runner.py', 'string', 'cccbdb-resolver-diagnostics/0.1.0', 1, 'PR-4'),
    ('backend/app/importers/cccbdb/form_resolver.py', 'string', 'cccbdb-form-resolver/0.1.0', 1, 'PR-4'),
    ('backend/app/importers/cccbdb/snapshot.py', 'string', 'cccbdb-experimental-payload-builder/0.1.0', 1, 'PR-4'),
    ('backend/app/importers/thermoml/__init__.py', 'string', 'thermoml-cp-mapping/0.1.0', 1, 'PR-4'),
    ('backend/app/importers/thermoml/__init__.py', 'string', 'thermoml-cp-parser/0.1.0', 1, 'PR-4'),
    ('backend/app/importers/thermoml/mapping.py', 'string', 'not on the observed-method-string allowlist (origin.smethodname_allowlist.v1)', 1, 'PR-4'),
    ('backend/app/importers/thermoml/mapping.py', 'string', 'origin.prediction_type_map.v1', 1, 'PR-4'),
    ('backend/app/importers/thermoml/mapping.py', 'string', 'origin.smethodname_allowlist.v1', 1, 'PR-4'),
    ('backend/app/importers/thermoml/mapping.py', 'string', 'pressure.kpa_to_bar.v1', 1, 'PR-4'),
    ('backend/app/importers/thermoml/mapping.py', 'string', 'state_basis.phase_map.v1', 1, 'PR-4'),
    ('backend/app/importers/thermoml/mapping.py', 'string', 'thermoml.mapping.v1', 1, 'PR-4'),
    ('backend/app/importers/thermoml/mapping.py', 'string', 'uncertainty.precedence.v1', 1, 'PR-4'),
    ('backend/app/schemas/reads/scientific_statmech_selection.py', 'string', 'statmech-selection-v1', 2, 'PR-4'),
    ('backend/app/services/archive/core.py', 'string', 'No tckdb.archive.v1 codec for SQL type', 2, 'PR-4'),
    ('backend/app/services/archive/core.py', 'string', 'Not a tckdb.archive.v1 manifest', 1, 'PR-4'),
    ('backend/app/services/archive/core.py', 'string', 'tckdb.archive.v1', 1, 'PR-4'),
    ('backend/app/services/consistency/thermo.py', 'string', 'nasa7-low;nasa9-upper;exact-points-v1', 1, 'PR-4'),
    ('backend/app/services/deposit/build.py', 'string', 'tckdb.deposit.v1', 1, 'PR-4'),
    ('backend/app/services/external_comparison/cp.py', 'string', 'external_cp_comparison_v1', 1, 'PR-4'),
    ('backend/app/services/gaussian_parameter_parser.py', 'string', 'gaussian_v1', 1, 'PR-4'),
    ('backend/app/services/hessian_parsing.py', 'string', 'hessian_v1', 1, 'PR-4'),
    ('backend/app/services/kinetics_selection/assessment.py', 'fstring', "f'evidence_rubric:{evidence.rubric}@{evidence.rubric_version}'", 1, 'PR-4'),
    ('backend/app/services/kinetics_selection/manifest.py', 'fstring', "f'{COMPUTED_KINETICS.name}@{COMPUTED_KINETICS.version}'", 1, 'PR-4'),
    ('backend/app/services/machine_review/providers/fake.py', 'string', 'fake_test/machine-review-v2', 1, 'PR-4'),
    ('backend/app/services/machine_review/providers/prompt.py', 'string', 'You are screening a submission to TCKDB, an archive of computed thermochemistry and reaction kine...', 1, 'PR-4'),
    ('backend/app/services/machine_review/recipe.py', 'fstring', "f'{rubric.name}_v{rubric.version}'", 1, 'PR-4'),
    ('backend/app/services/machine_review/recipe.py', 'string', 'machine_review_v1', 1, 'PR-4'),
    ('backend/app/services/machine_review/schemas.py', 'string', 'machine_review_v2', 2, 'PR-4'),
    ('backend/app/services/molpro_parameter_parser.py', 'string', 'molpro_v1', 1, 'PR-4'),
    ('backend/app/services/orca_parameter_parser.py', 'string', 'orca_v2', 1, 'PR-4'),
    ('backend/app/services/psi4_parameter_parser.py', 'string', 'psi4-0.1.0', 1, 'PR-4'),
    ('backend/app/services/release/manifest.py', 'string', 'tckdb.export.v0', 1, 'PR-4'),
    ('backend/app/services/release/versions.py', 'string', 'record_review.v1', 1, 'PR-4'),
    ('backend/app/services/release/versions.py', 'string', 'tckdb.archive.v1', 1, 'PR-4'),
    ('backend/app/services/release/versions.py', 'string', 'tckdb.dataset_release.v2', 1, 'PR-4'),
    ('backend/app/services/reproducibility_rubric.py', 'string', 'no_typed_result_contract_v1', 1, 'PR-4'),
    ('backend/app/services/reproducibility_rubric.py', 'string', 'selected_collection_requires_scientific_provenance_v1', 1, 'PR-4'),
    ('backend/app/services/scientific_read/export.py', 'string', 'tckdb.archive.v1', 1, 'PR-4'),
    ('backend/app/services/scientific_read/export.py', 'string', 'tckdb.dataset_release.v1', 1, 'PR-4'),
    ('backend/app/services/scientific_read/export.py', 'string', 'tckdb.export.v0', 1, 'PR-4'),
    ('backend/app/services/scientific_read/ml_dataset.py', 'string', 'tckdb.ml.v0', 1, 'PR-4'),
    ('backend/app/services/selection_kernel/graph.py', 'fstring', "f'{f.rule_id}@{f.rule_version}'", 1, 'PR-4'),
    ('backend/app/services/selection_kernel/graph.py', 'fstring', "f'{r.rule_id}@{r.rule_version}'", 1, 'PR-4'),
    ('backend/app/services/statmech_selection/manifest.py', 'fstring', "f'statmech_qualification@{ASSESSMENT_VERSION}'", 1, 'PR-4'),
    ('backend/app/services/statmech_selection/models.py', 'string', 'statmech-selection-v1', 1, 'PR-4'),
    ('backend/app/services/statmech_selection/vocabulary.py', 'string', 'statmech-selection-v1', 1, 'PR-4'),
    ('backend/app/services/structure_selection/loader.py', 'fstring', "f'{c.software_release.software.name}@{c.software_release.version}'", 1, 'PR-4'),
    ('backend/app/services/thermo_selection/assessment.py', 'fstring', "f'evidence_rubric:{evidence.rubric}@{evidence.rubric_version}'", 1, 'PR-4'),
    ('backend/app/services/thermo_selection/manifest.py', 'fstring', "f'{COMPUTED_THERMO.name}@{COMPUTED_THERMO.version}'", 1, 'PR-4'),
    ('backend/app/services/trust/fragment.py', 'fstring', "f'_v{evaluation.rubric_version}'", 1, 'PR-4'),
    ('backend/tests/api/golden/openapi.json', 'openapi', 'value:statmech-selection-v1', 1, 'PR-4'),
    ('backend/tests/api/golden/openapi.json', 'openapi', 'value:tckdb.execution-environment.v1', 2, 'PR-4'),
    ('clients/python/src/tckdb_client/scientific_types.py', 'string', 'statmech-selection-v1', 1, 'PR-4'),
    ('schemas/python/tckdb-schemas/tckdb_schemas/fragments/execution_environment.py', 'string', 'tckdb.execution-environment.v1', 1, 'PR-4'),
)
