"""split every stored name-with-a-version string into a name and an integer version

Versioning PR-4 (the last of four). A versioned thing TCKDB owns is identified by a stable ``name`` and a separate
integer ``version``; no stored value fuses the two. This revision rewrites the stored shapes that did
(``computed_thermo_v2``, ``machine_review_v1``, ``tckdb.dataset_release.v2``, ``gaussian_v1``, ``record_review.v1``,
``tckdb.execution-environment.v1``, ``torsion_basin`` / ``v1`` ...) into ``name`` + ``version`` columns, and the
application, in the same release, resets every TCKDB-owned version to 1. A version owned by somebody else (an ESS
release, a producer's own parser, NIST's ThermoML) stays text in its own field and is never renumbered.

The rule for every rewrite: **split cleanly or abort.** Before anything is written the revision inspects every stored
shape it is about to touch and refuses to run (nothing committed) if one is a fused name it does not know, a look-alike
of a TCKDB name (``gaussian_v2``, ``GAUSSIAN_V1``), or a shape that could not be restored exactly by ``downgrade()``.
After the rewrite it checks again that no new ``*_name`` column still ends in a version segment (``.vN``, ``_vN``,
``-vN``, any case) and aborts if one does. No row is ever left half-labelled.

What it rewrites, by table (fourteen tables)
--------------------------------------------
* ``record_machine_review``: ``prompt_version`` text -> ``prompt_name`` text + ``prompt_version`` int;
  ``rubric_versions_json`` (one key ``<rubric>_v<N>`` valued ``"N"``) -> ``rubric_name`` + ``rubric_version`` int;
  ``context_schema_version`` text ``'vN'`` -> int; the model strings ``external_cp_comparison_v1`` (scientific-check
  provider) and ``fake_test/machine-review-v2`` (the test-only fake provider) lose their version suffix.
  **Old versions map to 0.** Every TCKDB-owned version restarted at 1, so a stored version number can no longer say
  which rubric or prompt a review was made under. Each stored stamp is therefore written so that it cannot equal an
  active one unless it truly is the same thing:

  - a rubric's **latest** version on main becomes ``1`` (the same rubric, unchanged) and **every older version becomes
    ``0``** (the frozen table ``_OLD_LATEST_RUBRIC``, read from main's ``ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS``);
  - every prompt becomes version ``0``: the LLM prompt ``machine_review_v1`` changed its wording in this release (the
    answer format moved to a ``format`` block) while its version was reset to 1, and a deterministic runner's old stamp
    (its unversioned name, or ``external_cp_comparison_v1``) carried no version at all;
  - version 0 is never current -- ``VersionRef`` and every registry refuse it -- so **every pre-reset review reads stale
    by a prompt or rubric mismatch**, a true reason, with the context recipe version left at 1.

  ``context_schema_version`` ``'vN'`` -> int (kept). Shapes it accepts (anything else aborts):

  - an LLM or fake provider's prompt: ``machine_review_v1`` (or any other text; only ``machine_review`` can be given
    back);
  - a deterministic runner's prompt: its *unversioned* name or ``external_cp_comparison_v1``; any other
    ``<name>_v<N>`` there is refused (the downgrade could not give it back);
  - a rubric key ``<name>_v<N>`` with ``"N"`` as its value, in a dict of exactly one key (the machine-review currency
    rule says it cannot hold two; a second key would be silently dropped, so the revision proves it instead);
  - ``context_schema_version`` is ``vN``.

  The downgrade gives back exactly where a 0 or 1 names one specific old version: ``machine_review_v1``; a runner's
  unversioned name; a rubric's latest old version (from 1); and the one older version of a rubric whose latest was 2
  (from 0). It **refuses** where several older versions collapsed into 0 (``computed_transition_state``, latest 5, older
  1-4), for a rubric main no longer had, and for any LLM prompt other than ``machine_review`` at 0.
* ``record_reproducibility_assessment``: ``rubric_version`` text ``'v1'`` -> int **0** (the rubric's reason tokens were
  renamed in this release while its version was reset to 1, so every stored assessment is a pre-reset one and is never
  current), ``context_json.rubric.version`` likewise, and the two reason tokens that ended ``_v1`` lose the suffix inside
  the JSON columns, on a word boundary (``token_v10`` is a different token and is not touched). The rows read stale
  (rubric mismatch, and the context hash no longer matches) and are reassessed; the downgrade gives back ``'v1'``.
* ``submission_audit_event``: ``details_json['schema_version'] == 'machine_review_v2'`` ->
  ``details_json['format'] == {'name': 'machine_review', 'version': 1}``.
* ``release_manifest`` / ``release_artifact`` / ``dataset_release`` (owner decision 3, design F3): the manifest's format
  tag sits inside a frozen checksum, so the rows cannot be relabelled without breaking the checksum. They are
  **unpublished**: every manifest and artifact row is deleted. A *published* release goes back to ``draft``
  (``published_at`` cleared); the owner re-publishes it. A *withdrawn* release **stays withdrawn**, with ``published_at``,
  ``withdrawn_at`` and ``withdrawn_reason`` intact: the curator's record of the withdrawal is not this revision's to
  erase, and a withdrawn release must not become re-publishable. Every release keeps its ``doi`` and its curated
  ``release_selection`` rows, so **a minted DOI points at an unpublished release until it is re-published**. The three
  fused columns become ``*_name`` + ``*_version`` pairs.
* ``execution_environment_manifest``: ``schema_version`` ``'tckdb.execution-environment.v1'`` -> ``schema_name`` +
  int ``schema_version``; ``canonical_json`` is rewritten and ``content_digest`` recomputed with a **frozen copy** of the
  canonicaliser (never an import of application code). ``calculation.execution_environment_manifest_id`` is untouched.
* ``calculation.parameters_parser_version``, ``calculation_parameter.parser_version``, ``calc_hessian.parser_version``:
  split into ``*_name`` + text version. TCKDB's own stamps map to (``gaussian_parameters`` | ``orca_parameters`` |
  ``molpro_parameters`` | ``psi4_parameters`` | ``hessian``, ``'1'``). **``orca_v1`` and ``orca_v2`` were two different
  parsers** and stay distinguishable: ``orca_v2`` is (``orca_parameters``, ``'1'``) and ``orca_v1`` is
  (``orca_parameters``, ``'0'``), the predecessor of version 1. Any other non-null stamp is a producer's own and is kept
  verbatim under the name ``producer``, unless it is a *variant of one of the six spellings TCKDB itself wrote*
  (``gaussian_v2``, ``GAUSSIAN_V1``, ``orca_v3``, ``psi4-0.2.0``), in which case the revision aborts. Ordinary free text
  that merely starts with a family name (``Gaussian 16``, ``orca 5.0.4``, ``psi4-1.9.1``) is a producer stamp: it was legal
  on main and may sit on a frozen calculation the operator cannot edit.
* ``external_source_record`` / ``external_source``: only rows that are ThermoML (``thermoml_article``, parser
  ``thermoml`` at ``thermoml-cp-parser/0.1.0``, mapping ``thermoml-cp-mapping/0.1.0``) are rewritten; any other row
  aborts the upgrade. They become (``thermoml_cp`` / ``thermoml_cp_mapping``, ``'1'``) with a new ``mapping_name``; the
  ``.v1`` suffix leaves the rule ids inside ``mapping_report_json`` (exact rule ids only: ``.v10`` is untouched); the
  depositor-upload ``source_release`` ``'v1'`` -> ``'1'``. ``uq_external_source_record_identity`` now includes
  ``parser_name`` and ``mapping_name``: every TCKDB version is 1, so the versions alone can no longer tell two parsers
  apart.
* ``molecular_property_observation``: the CCCBDB parser stamp -> (``cccbdb_experimental_species``, ``'1'``) with a new
  ``external_source_parser_name``; any other non-null stamp is a producer's (``producer``, verbatim) unless it is a
  variant of the one CCCBDB spelling TCKDB wrote (``cccbdb-experimental-species-parser/0.2.0``), which aborts; a
  depositor's ``CCCBDB scrape 2024`` is a producer stamp. ``raw_payload_json`` is verbatim upstream data and is **never** rewritten.
* ``conformer_assignment_scheme``: ``version`` text -> integer. ``torsion_basin`` ``'v1'`` -> ``1`` and its content-derived
  ``public_ref`` recomputed from ``cas:name=torsion_basin;version=1;scope=canonical`` with a frozen copy of
  ``make_content_ref``; any other row must already be a plain integer, else the upgrade aborts.

Every integer version the revision introduces is held by a named CHECK constraint: ``>= 1`` for the ones nothing stores
as 0, ``>= 0`` for a machine-review prompt or rubric and a reproducibility rubric (``ck_record_machine_review_versions_in_range``,
``ck_record_reproducibility_assessment_rubric_version_nonneg``).

The accepted-science freeze is bypassed, for this one revision only (owner decision 3, 2026-10-24)
-----------------------------------------------------------------------------------------------
Three of the rewritten tables are frozen by trigger: ``calculation``, ``calculation_parameter`` and ``calc_hessian`` are
under accepted-science immutability (``c6f2a9d4e7b1``), ``record_reproducibility_assessment``, ``release_manifest``,
``release_artifact`` and ``execution_environment_manifest`` are append-only or immutable. The relabel is a rewrite of
stored provenance stamps on approved calculations and of frozen documents; no scientific value changes (no energy,
frequency, geometry, correction or result is read or written). The owner decided that the stored data is rewritten freely
here, bypassing the freeze, with the bypass documented in this file and **no declared-repair machinery**
(``e2c9a4f7b163``) for it. The bypass is exactly this:

1. before any rewrite, every user trigger on the touched tables is looked at. One that is *enabled* has its exact mode
   recorded (``O`` ENABLE, ``A`` ENABLE ALWAYS, ``R`` ENABLE REPLICA) and is disabled by name (``ALTER TABLE ... DISABLE
   TRIGGER <name>``, transactional: the lock is held to the end of the revision, so no other session writes a table while
   its guard is off). One that was *already disabled* is left exactly as it was, is not this revision's to re-enable, and
   is only logged;
2. the rewrites run;
3. every trigger the revision disabled is put back in the mode recorded in (1), and the revision then **raises**
   (rolling everything back) if any of them is not in that mode again. A trigger this revision turned off cannot
   survive it, and a trigger it did not turn off is never blamed on it.

``downgrade()`` runs the same bypass in reverse. It cannot restore what the upgrade deleted (the unpublished release
manifests and artifacts) and it refuses to run while a release manifest exists, because a manifest frozen after this
revision carries the new format header and cannot be reproduced under the old one. It also refuses a stored stamp the old
schema cannot spell (a parser stamped at a version that was never a string, a Cp record under another parser): it
restores the strings it was given exactly, or it stops.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d4a9e2b6f1"
down_revision: Union[str, Sequence[str], None] = "e6b3a9d1f742"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_log = logging.getLogger("alembic.runtime.migration")

#: Every table this revision writes. The bypass disables the user triggers of exactly these.
_TOUCHED_TABLES: tuple[str, ...] = (
    "record_machine_review",
    "record_reproducibility_assessment",
    "submission_audit_event",
    "release_manifest",
    "release_artifact",
    "dataset_release",
    "execution_environment_manifest",
    "calculation",
    "calculation_parameter",
    "calc_hessian",
    "external_source",
    "external_source_record",
    "molecular_property_observation",
    "conformer_assignment_scheme",
)

# Frozen facts of the rewrite (shapes as they are on disk when this revision runs; never imported from the app).
_ENV_NAME_OLD = "tckdb.execution-environment.v1"
_ENV_NAME_NEW = "tckdb.execution-environment"
_SCIENTIFIC_CHECK_PROVIDER = "tckdb.scientific_checks"
_FAKE_PROVIDER = "FakeMachineReviewProvider"
_FAKE_MODEL_OLD = "fake_test/machine-review-v2"
_FAKE_MODEL_NEW = "fake_test/machine-review"
_CP_RUNNER = "external_cp_comparison"

#: Each machine-review rubric's LATEST version on main (``2ed11e0f``, ``ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS``, read
#: from that code, not from memory). The application restarted every rubric at version 1, so a stored review made under a
#: rubric's latest old version is stored as ``1`` -- the same rubric, unchanged -- and one made under any OLDER version is
#: stored as ``0``: version 0 is never current, so it reads stale for a true reason (it was graded under a rubric that is
#: no longer the active one). A rubric name not in this table (retired before main) is stored as ``0`` too.
_OLD_LATEST_RUBRIC: dict[str, int] = {
    "computed_calculation": 2,
    "computed_kinetics": 2,
    "computed_statmech": 2,
    "computed_thermo": 2,
    "computed_transition_state": 5,
    "computed_transport": 2,
    "computed_ts_statmech": 1,
    "external_cp_comparison": 2,
    "gibbs_self_consistency": 1,
    "hess_consistency": 1,
    "isotope_identity_consistency": 1,
    "kirchhoff_consistency": 1,
    "thermo_consistency": 1,
    "thermo_kinetics_consistency": 1,
}
#: The one LLM prompt main ever wrote. Its wording changed in this release (the answer format moved from a fused
#: ``schema_version`` marker to a ``format`` block) while its version was reset to 1, so it is stored as version 0.
_LLM_PROMPT = "machine_review"

#: TCKDB's own parser stamps: old fused text -> (name, version). ``orca_v1`` is the predecessor of ``orca_v2`` and is
#: kept distinct as version ``'0'``; every other stamp is version ``'1'``.
_PARSER_OLD_TO_NEW: dict[str, tuple[str, str]] = {
    "gaussian_v1": ("gaussian_parameters", "1"),
    "orca_v1": ("orca_parameters", "0"),
    "orca_v2": ("orca_parameters", "1"),
    "molpro_v1": ("molpro_parameters", "1"),
    "psi4-0.1.0": ("psi4_parameters", "1"),
    "hessian_v1": ("hessian", "1"),
}
_PARSER_NEW_TO_OLD: dict[tuple[str, str], str] = {new: old for old, new in _PARSER_OLD_TO_NEW.items()}
_PRODUCER = "producer"
#: A variant of one of the six spellings TCKDB itself wrote (``gaussian_v1``, ``orca_v1``, ``orca_v2``, ``molpro_v1``,
#: ``hessian_v1``, ``psi4-0.1.0``) that is none of the exact ones: ``gaussian_v2``, ``GAUSSIAN_V1``, ``orca_v3``,
#: ``psi4-0.2.0``. That, and only that, is refused. Ordinary free-text producer stamps that merely start with a family
#: name (``Gaussian 16``, ``gaussian16``, ``orca 5.0.4``, ``psi4-1.9.1``, ``molpro2024.1``) were legal on main, may sit on
#: an approved (frozen) calculation the operator cannot edit, and become ``('producer', verbatim)``.
_PARSER_LOOKALIKE = re.compile(r"^((gaussian|orca|molpro|hessian)_v\d+|psi4-0\.\d+\.\d+)$", re.IGNORECASE)
#: The same for the CCCBDB parser: a variant of ``cccbdb-experimental-species-parser/0.1.0``. A depositor's own
#: ``CCCBDB scrape 2024`` is a producer stamp.
_CCCBDB_LOOKALIKE = re.compile(r"^cccbdb-[a-z-]+-parser/\d+\.\d+\.\d+$", re.IGNORECASE)

#: ThermoML rule ids that carried ``.v1``; the suffix is removed from the stored reports.
_RULE_IDS = (
    "thermoml.mapping",
    "uncertainty.precedence",
    "origin.smethodname_allowlist",
    "pressure.kpa_to_bar",
    "state_basis.phase_map",
    "origin.prediction_type_map",
)
_THERMOML_PARSER_NAME_OLD = "thermoml"
_THERMOML_PARSER_OLD = "thermoml-cp-parser/0.1.0"
_THERMOML_MAPPING_OLD = "thermoml-cp-mapping/0.1.0"
_CCCBDB_PARSER_OLD = "cccbdb-experimental-species-parser/0.1.0"

_REPRO_TOKENS = ("no_typed_result_contract", "selected_collection_requires_scientific_provenance")

#: A name ending in a version segment: ``computed_thermo_v2``, ``tckdb.archive.v1``, ``x-V3`` (frozen copy of the
#: segment pattern the application's ``embeds_version`` uses).
_VERSIONED_SEGMENT = re.compile(r"(?<=[A-Za-z0-9])[_.@-][vV]\d+(?![A-Za-z0-9])")
_SUFFIX = re.compile(r"^(?P<name>.+)_v(?P<n>[1-9][0-9]*)$")
_STRICT_V = re.compile(r"^v([1-9][0-9]*)$")

#: Constraint names this revision adds (the models carry the same names).
_CK_MACHINE_REVIEW = "ck_record_machine_review_versions_in_range"
_CK_MANIFEST = "ck_release_manifest_versions_positive"
_CK_ENV = "ck_execution_environment_manifest_schema_version_positive"
_CK_REPRO = "ck_record_reproducibility_assessment_rubric_version_nonneg"
_CK_SCHEME = "ck_conformer_assignment_scheme_version_positive"
_UQ_ESR = "uq_external_source_record_identity"

_ENABLE_SQL = {"O": "ENABLE", "A": "ENABLE ALWAYS", "R": "ENABLE REPLICA"}


# ---------------------------------------------------------------------------
# The documented freeze bypass
# ---------------------------------------------------------------------------


class _Guards:
    """What :func:`_disable_guards` did: the triggers it turned off (with their modes) and the ones already off."""

    def __init__(self) -> None:
        self.disabled: dict[tuple[str, str], str] = {}
        self.already_off: list[tuple[str, str]] = []


def _disable_guards(bind: sa.engine.Connection) -> _Guards:
    """Disable every enabled user trigger on the touched tables, recording its exact mode (see the docstring)."""
    guards = _Guards()
    for table in _TOUCHED_TABLES:
        rows = bind.execute(
            sa.text(
                "SELECT tgname, tgenabled::text FROM pg_trigger WHERE tgrelid = CAST(:t AS regclass) "
                "AND NOT tgisinternal ORDER BY tgname"
            ),
            {"t": table},
        ).all()
        for name, mode in rows:
            if mode == "D":
                guards.already_off.append((table, name))
                _log.warning(
                    "trigger %s.%s was already disabled before revision c7d4a9e2b6f1; it is left as it was", table, name
                )
                continue
            if mode not in _ENABLE_SQL:
                raise RuntimeError(f"trigger {table}.{name} is in the unknown mode {mode!r}; refusing to touch it")
            op.execute(f'ALTER TABLE "{table}" DISABLE TRIGGER "{name}"')
            guards.disabled[(table, name)] = mode
    return guards


def _enable_guards(bind: sa.engine.Connection, guards: _Guards) -> None:
    """Put every trigger this revision disabled back in its recorded mode, and refuse to finish if one is not."""
    for (table, name), mode in guards.disabled.items():
        op.execute(f'ALTER TABLE "{table}" {_ENABLE_SQL[mode]} TRIGGER "{name}"')
    wrong = []
    for (table, name), mode in guards.disabled.items():
        now = bind.execute(
            sa.text("SELECT tgenabled::text FROM pg_trigger WHERE tgrelid = CAST(:t AS regclass) AND tgname = :n"),
            {"t": table, "n": name},
        ).scalar()
        if now != mode:
            wrong.append(f"{table}.{name} (was {mode!r}, is {now!r})")
    if wrong:
        raise RuntimeError(
            "revision c7d4a9e2b6f1 disabled a guard trigger and could not put it back: " + ", ".join(wrong)
            + ". Nothing was committed."
        )


# ---------------------------------------------------------------------------
# Frozen helpers (copies of application code as of this revision)
# ---------------------------------------------------------------------------


def _content_ref(prefix: str, canonical_identity: str) -> str:
    """Frozen copy of ``app.services.public_refs.make_content_ref`` (sha256, first 16 bytes, base32, 26 chars)."""
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).digest()[:16]
    return f"{prefix}_{base64.b32encode(digest).decode('ascii').lower().rstrip('=')[:26]}"


def _environment_digest(canonical: dict) -> str:
    """Frozen copy of ``ExecutionEnvironmentManifestPayload.content_digest`` over a canonical payload."""
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _split_prompt(provider: str | None, text: str) -> tuple[str, int] | None:
    """The (name, version) a stored prompt stamp becomes -- always version 0 -- or ``None`` if it cannot be split cleanly.

    Every pre-reset prompt stamp is stored at version 0: the LLM prompt's wording changed in this release while its
    version was reset to 1, and a deterministic runner's old stamp carried no version at all. Version 0 is never
    current, so every pre-reset review is stale by a prompt mismatch.

    A deterministic runner's stamp is either its unversioned name or ``external_cp_comparison_v1``; any other
    ``<name>_v<N>`` there is refused (the downgrade could not give it back). An LLM provider's ``<name>_v<N>`` loses its
    suffix; a stamp with no suffix is kept whole.
    """
    match = _SUFFIX.match(text)
    if provider == _SCIENTIFIC_CHECK_PROVIDER:
        if text == _CP_RUNNER + "_v1":
            name = _CP_RUNNER
        elif match:
            return None
        else:
            name = text
    else:
        name = match["name"] if match else text
        if match and name == _LLM_PROMPT and match["n"] != "1":
            return None  # main only ever wrote machine_review_v1; the downgrade would give back v1, not this
    if _VERSIONED_SEGMENT.search(name):
        return None
    return name, 0


def _split_rubric(key: str, value: str) -> tuple[str, int] | None:
    """The (name, version) a stored rubric key becomes: the rubric's latest old version is 1, any older one is 0."""
    match = _SUFFIX.match(key)
    if not match or match["n"] != value:
        return None
    name = match["name"]
    if _VERSIONED_SEGMENT.search(name):
        return None
    return name, 1 if int(value) == _OLD_LATEST_RUBRIC.get(name) else 0


def _parser_problem(stamp: str) -> bool:
    return stamp not in _PARSER_OLD_TO_NEW and bool(_PARSER_LOOKALIKE.match(stamp))


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    _check_stored_shapes(bind)
    guards = _disable_guards(bind)
    _upgrade_machine_review(bind)
    _upgrade_reproducibility(bind)
    _upgrade_audit_events(bind)
    _upgrade_releases(bind)
    _upgrade_execution_environments(bind)
    _upgrade_parser_stamps(bind)
    _upgrade_external_sources(bind)
    _upgrade_observations(bind)
    _upgrade_conformer_scheme(bind)
    _assert_no_fused_names(bind)
    _enable_guards(bind, guards)


def _check_stored_shapes(bind: sa.engine.Connection) -> None:
    """Abort, before anything is written, on every stored shape this revision cannot split cleanly."""
    problems: list[str] = []

    bad = bind.execute(
        sa.text(
            "SELECT count(*) FROM record_machine_review WHERE CASE WHEN jsonb_typeof(rubric_versions_json) = 'object' "
            "THEN (SELECT count(*) FROM jsonb_object_keys(rubric_versions_json)) ELSE -1 END <> 1"
        )
    ).scalar()
    if bad:
        raise RuntimeError(
            f"{bad} record_machine_review row(s) hold a rubric_versions_json that is not exactly one key. A review is "
            "stamped with the one rubric that graded it, so this cannot happen; refusing to drop the extra keys "
            "silently. Inspect the rows and decide which rubric each was graded under before upgrading."
        )
    for key, value in bind.execute(
        sa.text("SELECT DISTINCT kv.key, kv.value FROM record_machine_review r, LATERAL jsonb_each_text(r.rubric_versions_json) kv")
    ):
        if _split_rubric(key, value) is None:
            problems.append(
                f"record_machine_review rubric {key!r}: {value!r} is not '<name>_v<N>' valued 'N' with a clean name"
            )
    for provider, prompt in bind.execute(
        sa.text("SELECT DISTINCT provider, prompt_version FROM record_machine_review")
    ):
        if _split_prompt(provider, prompt) is None:
            problems.append(
                f"record_machine_review prompt_version {prompt!r} (provider {provider!r}) is a fused name this revision "
                "does not know, or one it could not restore exactly"
            )
    for (schema,) in bind.execute(sa.text("SELECT DISTINCT context_schema_version FROM record_machine_review")):
        if not _STRICT_V.match(schema):
            problems.append(f"record_machine_review context_schema_version {schema!r} is not 'vN'")

    for (version,) in bind.execute(sa.text("SELECT DISTINCT rubric_version FROM record_reproducibility_assessment")):
        if version != "v1":
            problems.append(f"record_reproducibility_assessment rubric_version {version!r} is not 'v1', the only one main wrote")

    for (version,) in bind.execute(sa.text("SELECT DISTINCT schema_version FROM execution_environment_manifest")):
        if version != _ENV_NAME_OLD:
            problems.append(
                f"execution_environment_manifest schema_version {version!r}: this revision knows only {_ENV_NAME_OLD!r}"
            )

    for table, column in (
        ("calculation", "parameters_parser_version"),
        ("calculation_parameter", "parser_version"),
        ("calc_hessian", "parser_version"),
    ):
        for (stamp,) in bind.execute(sa.text(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL")):
            if _parser_problem(stamp):
                problems.append(
                    f"{table}.{column} parser stamp {stamp!r} looks like a TCKDB parser stamp but is none this revision "
                    "knows; it would be left half-labelled"
                )
    for (stamp,) in bind.execute(
        sa.text(
            "SELECT DISTINCT external_source_parser_version FROM molecular_property_observation "
            "WHERE external_source_parser_version IS NOT NULL"
        )
    ):
        if stamp != _CCCBDB_PARSER_OLD and (_CCCBDB_LOOKALIKE.match(stamp) or _parser_problem(stamp)):
            problems.append(
                f"molecular_property_observation parser stamp {stamp!r} looks like a TCKDB parser stamp but is none this "
                "revision knows; it would be left half-labelled"
            )

    stray = bind.execute(
        sa.text(
            "SELECT count(*) FROM external_source_record WHERE NOT (record_kind = 'thermoml_article' "
            "AND parser_name = :pn AND parser_version = :pv AND mapping_version = :mv)"
        ),
        {"pn": _THERMOML_PARSER_NAME_OLD, "pv": _THERMOML_PARSER_OLD, "mv": _THERMOML_MAPPING_OLD},
    ).scalar()
    if stray:
        problems.append(
            f"{stray} external_source_record row(s) are not ThermoML records this revision knows "
            f"(thermoml_article parsed by {_THERMOML_PARSER_OLD!r} under {_THERMOML_MAPPING_OLD!r}); it will not give "
            "them ThermoML names"
        )

    for name, version in bind.execute(sa.text("SELECT name, version FROM conformer_assignment_scheme")):
        if not (name == "torsion_basin" and version == "v1") and not re.fullmatch(r"[1-9][0-9]*", version):
            problems.append(
                f"conformer_assignment_scheme {name!r} has version {version!r}: neither the seeded torsion_basin 'v1' "
                "nor a plain integer"
            )

    if problems:
        shown = "; ".join(problems[:20]) + (f"; ... and {len(problems) - 20} more" if len(problems) > 20 else "")
        raise RuntimeError(
            "revision c7d4a9e2b6f1 refuses to run, and nothing was committed: " + shown
            + ". Fix or remove these rows, then upgrade again."
        )


def _upgrade_machine_review(bind: sa.engine.Connection) -> None:
    op.add_column("record_machine_review", sa.Column("prompt_name", sa.String(128), nullable=True))
    op.add_column("record_machine_review", sa.Column("prompt_version_int", sa.Integer(), nullable=True))
    op.add_column("record_machine_review", sa.Column("rubric_name", sa.String(128), nullable=True))
    op.add_column("record_machine_review", sa.Column("rubric_version", sa.Integer(), nullable=True))
    for provider, prompt in bind.execute(sa.text("SELECT DISTINCT provider, prompt_version FROM record_machine_review")).all():
        name, n = _split_prompt(provider, prompt)  # type: ignore[misc]  # _check_stored_shapes proved it splits
        bind.execute(
            sa.text(
                "UPDATE record_machine_review SET prompt_name = :n, prompt_version_int = :v "
                "WHERE provider IS NOT DISTINCT FROM :p AND prompt_version = :t"
            ),
            {"n": name, "v": n, "p": provider, "t": prompt},
        )
    for key, value in bind.execute(
        sa.text("SELECT DISTINCT kv.key, kv.value FROM record_machine_review r, LATERAL jsonb_each_text(r.rubric_versions_json) kv")
    ).all():
        name, n = _split_rubric(key, value)  # type: ignore[misc]
        bind.execute(
            sa.text(
                "UPDATE record_machine_review SET rubric_name = :n, rubric_version = :v "
                "WHERE rubric_versions_json ? :k AND rubric_versions_json ->> :k = :val"
            ),
            {"n": name, "v": n, "k": key, "val": value},
        )
    op.execute(
        sa.text(
            "UPDATE record_machine_review SET model = regexp_replace(model, '_v[0-9]+$', '') "
            "WHERE provider = :p AND model = 'external_cp_comparison_v1'"
        ).bindparams(p=_SCIENTIFIC_CHECK_PROVIDER)
    )
    op.execute(
        sa.text("UPDATE record_machine_review SET model = :new WHERE provider = :p AND model = :old").bindparams(
            new=_FAKE_MODEL_NEW, p=_FAKE_PROVIDER, old=_FAKE_MODEL_OLD
        )
    )
    op.execute(
        "ALTER TABLE record_machine_review ALTER COLUMN context_schema_version TYPE integer USING ("
        "substring(context_schema_version from '^v([0-9]+)$')::integer)"
    )
    op.drop_column("record_machine_review", "prompt_version")
    op.alter_column("record_machine_review", "prompt_version_int", new_column_name="prompt_version")
    bad = bind.execute(
        sa.text(
            "SELECT count(*) FROM record_machine_review WHERE prompt_name IS NULL OR prompt_version IS NULL "
            "OR rubric_name IS NULL OR rubric_version IS NULL OR prompt_version < 0 OR rubric_version < 0 "
            "OR context_schema_version < 1"
        )
    ).scalar()
    if bad:
        raise RuntimeError(f"{bad} record_machine_review row(s) would be left without a clean name and version.")
    op.alter_column("record_machine_review", "prompt_name", nullable=False)
    op.alter_column("record_machine_review", "prompt_version", nullable=False)
    op.alter_column("record_machine_review", "rubric_name", nullable=False)
    op.alter_column("record_machine_review", "rubric_version", nullable=False)
    op.drop_column("record_machine_review", "rubric_versions_json")
    op.create_check_constraint(
        op.f(_CK_MACHINE_REVIEW),
        "record_machine_review",
        "context_schema_version >= 1 AND prompt_version >= 0 AND rubric_version >= 0",
    )


def _word_replace_sql(column: str, pairs: list[tuple[str, str]]) -> tuple[str, dict[str, str]]:
    """A ``regexp_replace`` chain over ``column::text`` that replaces each ``old`` only as a whole word."""
    expr = f"{column}::text"
    params: dict[str, str] = {}
    for i, (old, new) in enumerate(pairs):
        params[f"o{i}"] = r"\m" + re.escape(old) + r"\M"
        params[f"n{i}"] = new.replace("\\", "\\\\")
        expr = f"regexp_replace({expr}, :o{i}, :n{i}, 'g')"
    return f"({expr})::jsonb", params


def _upgrade_reproducibility(bind: sa.engine.Connection) -> None:
    pairs = [(f"{token}_v1", token) for token in _REPRO_TOKENS]
    for column in ("context_json", "missing_json", "passed_json", "warnings_json"):
        expr, params = _word_replace_sql(column, pairs)
        where = " OR ".join(f"{column}::text ~ :w{i}" for i in range(len(pairs)))
        for i, (old, _) in enumerate(pairs):
            params[f"w{i}"] = r"\m" + re.escape(old) + r"\M"
        bind.execute(
            sa.text(f"UPDATE record_reproducibility_assessment SET {column} = {expr} WHERE {column} IS NOT NULL AND ({where})"),
            params,
        )
    op.execute(
        """
        UPDATE record_reproducibility_assessment SET context_json = jsonb_set(
            context_json, '{rubric,version}', to_jsonb(0))
        WHERE jsonb_typeof(context_json #> '{rubric,version}') = 'string'
        """
    )
    # The rubric's reason tokens were renamed in this release while its version was reset to 1, so every pre-reset
    # assessment is stored at version 0: never current, so it reads stale by a rubric mismatch (and its context hash).
    op.execute(
        "ALTER TABLE record_reproducibility_assessment ALTER COLUMN rubric_version TYPE integer USING (0)"
    )
    op.create_check_constraint(op.f(_CK_REPRO), "record_reproducibility_assessment", "rubric_version >= 0")


def _upgrade_audit_events(bind: sa.engine.Connection) -> None:
    op.execute(
        """
        UPDATE submission_audit_event SET details_json =
            (details_json - 'schema_version')
            || jsonb_build_object('format', jsonb_build_object('name', 'machine_review', 'version', 1))
        WHERE jsonb_typeof(details_json) = 'object' AND details_json ->> 'schema_version' = 'machine_review_v2'
        """
    )


def _upgrade_releases(bind: sa.engine.Connection) -> None:
    """Unpublish (design F3): the format tag is inside a frozen checksum, so nothing is relabelled.

    A published release returns to draft. A withdrawn release stays withdrawn, with its reason and dates intact.
    """
    op.execute("DELETE FROM release_artifact")
    op.execute("DELETE FROM release_manifest")
    op.execute("UPDATE dataset_release SET status = 'draft', published_at = NULL WHERE status = 'published'")
    op.add_column("release_manifest", sa.Column("manifest_format_name", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("manifest_format_version", sa.Integer(), nullable=False))
    op.add_column("release_manifest", sa.Column("review_policy_name", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("review_policy_version_int", sa.Integer(), nullable=False))
    op.add_column("release_manifest", sa.Column("recovery_archive_format_name", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("recovery_archive_format_version", sa.Integer(), nullable=False))
    op.drop_column("release_manifest", "manifest_schema")
    op.drop_column("release_manifest", "review_policy_version")
    op.drop_column("release_manifest", "recovery_archive_schema")
    op.alter_column("release_manifest", "review_policy_version_int", new_column_name="review_policy_version")
    op.create_check_constraint(
        op.f(_CK_MANIFEST),
        "release_manifest",
        "manifest_format_version >= 1 AND review_policy_version >= 1 AND recovery_archive_format_version >= 1",
    )


def _upgrade_execution_environments(bind: sa.engine.Connection) -> None:
    op.add_column("execution_environment_manifest", sa.Column("schema_name", sa.String(64), nullable=True))
    op.execute("ALTER TABLE execution_environment_manifest ADD COLUMN schema_version_int integer")
    rows = bind.execute(sa.text("SELECT id, canonical_json FROM execution_environment_manifest ORDER BY id")).all()
    for row_id, canonical in rows:
        canonical = dict(canonical)
        canonical.pop("schema_version", None)
        canonical["schema_name"] = _ENV_NAME_NEW
        canonical["schema_version"] = 1
        bind.execute(
            sa.text(
                "UPDATE execution_environment_manifest SET schema_name = :n, schema_version_int = 1, "
                "canonical_json = CAST(:c AS jsonb), content_digest = :d WHERE id = :id"
            ),
            {"n": _ENV_NAME_NEW, "c": json.dumps(canonical), "d": _environment_digest(canonical), "id": row_id},
        )
    op.alter_column("execution_environment_manifest", "schema_name", nullable=False)
    op.alter_column("execution_environment_manifest", "schema_version_int", nullable=False)
    op.drop_column("execution_environment_manifest", "schema_version")
    op.alter_column("execution_environment_manifest", "schema_version_int", new_column_name="schema_version")
    op.create_check_constraint(op.f(_CK_ENV), "execution_environment_manifest", "schema_version >= 1")


_STAMP_COLUMNS = (
    ("calculation", "parameters_parser_version", "parameters_parser_name"),
    ("calculation_parameter", "parser_version", "parser_name"),
    ("calc_hessian", "parser_version", "parser_name"),
)


def _upgrade_parser_stamps(bind: sa.engine.Connection) -> None:
    for table, column, name_column in _STAMP_COLUMNS:
        op.add_column(table, sa.Column(name_column, sa.Text(), nullable=True))
        for old, (name, version) in _PARSER_OLD_TO_NEW.items():
            bind.execute(
                sa.text(f"UPDATE {table} SET {name_column} = :n, {column} = :v WHERE {column} = :o"),
                {"n": name, "v": version, "o": old},
            )
        # A stamp that is none of TCKDB's own is a producer's: kept verbatim, named for what it is. (A look-alike of
        # a TCKDB stamp was refused before anything was written.)
        bind.execute(
            sa.text(f"UPDATE {table} SET {name_column} = :p WHERE {column} IS NOT NULL AND {name_column} IS NULL"),
            {"p": _PRODUCER},
        )


def _upgrade_external_sources(bind: sa.engine.Connection) -> None:
    # Every row is a ThermoML row: _check_stored_shapes refused anything else.
    op.add_column("external_source_record", sa.Column("mapping_name", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE external_source_record SET parser_name = 'thermoml_cp', parser_version = '1', "
            "mapping_name = 'thermoml_cp_mapping', mapping_version = '1' "
            "WHERE record_kind = 'thermoml_article' AND parser_name = :pn AND parser_version = :pv AND mapping_version = :mv"
        ).bindparams(pn=_THERMOML_PARSER_NAME_OLD, pv=_THERMOML_PARSER_OLD, mv=_THERMOML_MAPPING_OLD)
    )
    pairs = [(f"{rule}.v1", rule) for rule in _RULE_IDS] + [(_THERMOML_PARSER_OLD, "1"), (_THERMOML_MAPPING_OLD, "1")]
    expr, params = _word_replace_sql("mapping_report_json", pairs)
    bind.execute(
        sa.text(f"UPDATE external_source_record SET mapping_report_json = {expr} WHERE mapping_report_json IS NOT NULL"), params
    )
    op.execute(
        "UPDATE external_source_record SET mapping_report_json = mapping_report_json || "
        "jsonb_build_object('parser_name', parser_name, 'mapping_name', mapping_name) "
        "WHERE jsonb_typeof(mapping_report_json) = 'object' AND mapping_report_json ? 'mapping_version'"
    )
    op.alter_column("external_source_record", "mapping_name", nullable=False)
    op.execute(
        "UPDATE external_source SET source_release = '1' "
        "WHERE source_name = 'TCKDB depositor ThermoML upload' AND source_release = 'v1'"
    )
    op.drop_constraint(_UQ_ESR, "external_source_record", type_="unique")
    op.create_unique_constraint(
        _UQ_ESR,
        "external_source_record",
        [
            "external_source_id",
            "source_record_key",
            "content_sha256",
            "parser_name",
            "parser_version",
            "mapping_name",
            "mapping_version",
        ],
    )


def _upgrade_observations(bind: sa.engine.Connection) -> None:
    op.add_column("molecular_property_observation", sa.Column("external_source_parser_name", sa.Text(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE molecular_property_observation SET external_source_parser_name = 'cccbdb_experimental_species', "
            "external_source_parser_version = '1' WHERE external_source_parser_version = :p"
        ),
        {"p": _CCCBDB_PARSER_OLD},
    )
    # Any other stamp is a producer's own, kept verbatim (a look-alike of a TCKDB stamp was refused up front).
    bind.execute(
        sa.text(
            "UPDATE molecular_property_observation SET external_source_parser_name = :p "
            "WHERE external_source_parser_version IS NOT NULL AND external_source_parser_name IS NULL"
        ),
        {"p": _PRODUCER},
    )


def _upgrade_conformer_scheme(bind: sa.engine.Connection) -> None:
    rows = bind.execute(
        sa.text("SELECT id, name, scope::text FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND version = 'v1'")
    ).all()
    for row_id, name, scope in rows:
        ref = _content_ref("cas", f"cas:name={name.strip().lower()};version=1;scope={scope}")
        bind.execute(
            sa.text("UPDATE conformer_assignment_scheme SET version = '1', public_ref = :r WHERE id = :id"),
            {"r": ref, "id": row_id},
        )
    op.execute(
        "ALTER TABLE conformer_assignment_scheme ALTER COLUMN version TYPE integer USING (version::integer)"
    )
    op.create_check_constraint(op.f(_CK_SCHEME), "conformer_assignment_scheme", "version >= 1")


#: Every column this revision introduced that holds a name: none may end in a version segment.
_NEW_NAME_COLUMNS = (
    ("record_machine_review", "prompt_name"),
    ("record_machine_review", "rubric_name"),
    ("release_manifest", "manifest_format_name"),
    ("release_manifest", "review_policy_name"),
    ("release_manifest", "recovery_archive_format_name"),
    ("execution_environment_manifest", "schema_name"),
    ("calculation", "parameters_parser_name"),
    ("calculation_parameter", "parser_name"),
    ("calc_hessian", "parser_name"),
    ("external_source_record", "parser_name"),
    ("external_source_record", "mapping_name"),
    ("molecular_property_observation", "external_source_parser_name"),
)


def _assert_no_fused_names(bind: sa.engine.Connection) -> None:
    """After the rewrite: no new name column still holds a fused name+version string. Abort if one does."""
    left = []
    for table, column in _NEW_NAME_COLUMNS:
        for (value,) in bind.execute(sa.text(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL")):
            if _VERSIONED_SEGMENT.search(value):
                left.append(f"{table}.{column} = {value!r}")
    if left:
        raise RuntimeError(
            "revision c7d4a9e2b6f1 would leave a fused name+version string in a new name column: "
            + ", ".join(left) + ". Nothing was committed."
        )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Reverse the split. Cannot restore the release manifests and artifacts the upgrade deleted (see the docstring)."""
    bind = op.get_bind()
    manifests = bind.execute(sa.text("SELECT count(*) FROM release_manifest")).scalar()
    if manifests:
        raise RuntimeError(
            "Refusing to downgrade c7d4a9e2b6f1: a release manifest exists. It was frozen after this revision, so its "
            "document carries the new format header and cannot be reproduced under the old tags. Export it first."
        )
    _check_downgradable(bind)
    guards = _disable_guards(bind)
    _downgrade_conformer_scheme(bind)
    _downgrade_observations(bind)
    _downgrade_external_sources(bind)
    _downgrade_parser_stamps(bind)
    _downgrade_execution_environments(bind)
    _downgrade_releases(bind)
    _downgrade_audit_events(bind)
    _downgrade_reproducibility(bind)
    _downgrade_machine_review(bind)
    _enable_guards(bind, guards)


def _check_downgradable(bind: sa.engine.Connection) -> None:
    """Refuse a stored stamp the old schema cannot spell: restore the strings exactly, or stop."""
    problems: list[str] = []
    for table, column, name_column in _STAMP_COLUMNS:
        for name, version in bind.execute(
            sa.text(f"SELECT DISTINCT {name_column}, {column} FROM {table} WHERE {name_column} IS NOT NULL")
        ):
            if name != _PRODUCER and (name, version) not in _PARSER_NEW_TO_OLD:
                problems.append(f"{table}: parser {name!r} at version {version!r} has no old spelling")
    for name, version in bind.execute(
        sa.text(
            "SELECT DISTINCT external_source_parser_name, external_source_parser_version FROM molecular_property_observation "
            "WHERE external_source_parser_name IS NOT NULL"
        )
    ):
        if name != _PRODUCER and (name, version) != ("cccbdb_experimental_species", "1"):
            problems.append(f"molecular_property_observation: parser {name!r} at version {version!r} has no old spelling")
    stray = bind.execute(
        sa.text(
            "SELECT count(*) FROM external_source_record WHERE NOT (parser_name = 'thermoml_cp' AND parser_version = '1' "
            "AND mapping_name = 'thermoml_cp_mapping' AND mapping_version = '1')"
        )
    ).scalar()
    if stray:
        problems.append(f"{stray} external_source_record row(s) are not ThermoML at parser and mapping version 1")
    # A 0 stands for "an older version, any of them". It can be given back only where there was exactly one older
    # version to give back: a rubric whose latest old version was 2 (older: 1), and the one LLM prompt main wrote.
    unique_older = sorted(name for name, latest in _OLD_LATEST_RUBRIC.items() if latest == 2)
    lost_rubric = bind.execute(
        sa.text(
            "SELECT DISTINCT rubric_name FROM record_machine_review WHERE rubric_version = 0 "
            "AND rubric_name <> ALL(CAST(:ok AS text[]))"
        ),
        {"ok": unique_older},
    ).scalars().all()
    if lost_rubric:
        problems.append(
            "record_machine_review rubric(s) stored at version 0 (an older version, one of several, or a rubric main no "
            "longer had) cannot be given back as one specific old version: " + ", ".join(sorted(lost_rubric))
        )
    lost_prompt = bind.execute(
        sa.text(
            "SELECT DISTINCT prompt_name FROM record_machine_review WHERE provider IS DISTINCT FROM :sc "
            "AND prompt_version = 0 AND prompt_name <> :llm"
        ),
        {"sc": _SCIENTIFIC_CHECK_PROVIDER, "llm": _LLM_PROMPT},
    ).scalars().all()
    if lost_prompt:
        problems.append(
            "record_machine_review prompt(s) stored at version 0 other than machine_review cannot be given back: "
            + ", ".join(sorted(lost_prompt))
        )
    # A version of 2 or more exists only after the reset. The old numbering has its own "version 2" of each of these,
    # which is a different thing, so writing it back as ``<name>_v2`` (or ``v2``) would mislabel the row. Refuse, naming
    # the kind of row, rather than guess.
    for label, table, column in (
        ("record_machine_review rubric_version", "record_machine_review", "rubric_version"),
        ("record_machine_review prompt_version", "record_machine_review", "prompt_version"),
        ("record_machine_review context_schema_version", "record_machine_review", "context_schema_version"),
        ("record_reproducibility_assessment rubric_version", "record_reproducibility_assessment", "rubric_version"),
        ("execution_environment_manifest schema_version", "execution_environment_manifest", "schema_version"),
    ):
        ambiguous = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE {column} >= 2")).scalar()
        if ambiguous:
            problems.append(
                f"{ambiguous} row(s) of {label} are at version 2 or above, which only exists after this revision and "
                "would be misread as the old numbering's own version of that thing"
            )
    if problems:
        raise RuntimeError(
            "revision c7d4a9e2b6f1 cannot be downgraded, and nothing was changed: " + "; ".join(problems[:20])
        )


def _downgrade_conformer_scheme(bind: sa.engine.Connection) -> None:
    op.drop_constraint(op.f(_CK_SCHEME), "conformer_assignment_scheme", type_="check")
    op.execute(
        "ALTER TABLE conformer_assignment_scheme ALTER COLUMN version TYPE varchar(64) USING (version::text)"
    )
    rows = bind.execute(
        sa.text("SELECT id, name, scope::text FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND version = '1'")
    ).all()
    for row_id, name, scope in rows:
        ref = _content_ref("cas", f"cas:name={name.strip().lower()};version=v1;scope={scope}")
        bind.execute(
            sa.text("UPDATE conformer_assignment_scheme SET version = 'v1', public_ref = :r WHERE id = :id"),
            {"r": ref, "id": row_id},
        )


def _restore_words_sql(column: str, pairs: list[tuple[str, str]]) -> tuple[str, dict[str, str]]:
    """Like :func:`_word_replace_sql`, but ``(old, new)`` restores ``new`` where ``old`` is a whole word.

    A rule id is restored only where it is not already followed by a ``.v<digit>`` segment.
    """
    expr = f"{column}::text"
    params: dict[str, str] = {}
    for i, (old, new) in enumerate(pairs):
        params[f"o{i}"] = r"\m" + re.escape(old) + r"\M(?!\.v[0-9])"
        params[f"n{i}"] = new.replace("\\", "\\\\")
        expr = f"regexp_replace({expr}, :o{i}, :n{i}, 'g')"
    return f"({expr})::jsonb", params


def _downgrade_observations(bind: sa.engine.Connection) -> None:
    bind.execute(
        sa.text(
            "UPDATE molecular_property_observation SET external_source_parser_version = :p "
            "WHERE external_source_parser_name = 'cccbdb_experimental_species'"
        ),
        {"p": _CCCBDB_PARSER_OLD},
    )
    op.drop_column("molecular_property_observation", "external_source_parser_name")


def _downgrade_external_sources(bind: sa.engine.Connection) -> None:
    op.execute(
        sa.text(
            "UPDATE external_source_record SET mapping_report_json = jsonb_set(jsonb_set("
            "(mapping_report_json - 'parser_name' - 'mapping_name'), '{parser_version}', to_jsonb(CAST(:p AS text))), "
            "'{mapping_version}', to_jsonb(CAST(:m AS text))) "
            "WHERE jsonb_typeof(mapping_report_json) = 'object' AND mapping_report_json ? 'mapping_name'"
        ).bindparams(p=_THERMOML_PARSER_OLD, m=_THERMOML_MAPPING_OLD)
    )
    pairs = [(rule, f"{rule}.v1") for rule in _RULE_IDS]
    expr, params = _restore_words_sql("mapping_report_json", pairs)
    bind.execute(
        sa.text(f"UPDATE external_source_record SET mapping_report_json = {expr} WHERE mapping_report_json IS NOT NULL"), params
    )
    op.execute(
        "UPDATE external_source SET source_release = 'v1' "
        "WHERE source_name = 'TCKDB depositor ThermoML upload' AND source_release = '1'"
    )
    op.drop_constraint(_UQ_ESR, "external_source_record", type_="unique")
    op.execute(
        sa.text(
            "UPDATE external_source_record SET parser_name = :pn, parser_version = :pv, mapping_version = :mv "
            "WHERE parser_name = 'thermoml_cp' AND parser_version = '1' "
            "AND mapping_name = 'thermoml_cp_mapping' AND mapping_version = '1'"
        ).bindparams(pn=_THERMOML_PARSER_NAME_OLD, pv=_THERMOML_PARSER_OLD, mv=_THERMOML_MAPPING_OLD)
    )
    op.drop_column("external_source_record", "mapping_name")
    op.create_unique_constraint(
        _UQ_ESR,
        "external_source_record",
        ["external_source_id", "source_record_key", "content_sha256", "parser_version", "mapping_version"],
    )


def _downgrade_parser_stamps(bind: sa.engine.Connection) -> None:
    for table, column, name_column in _STAMP_COLUMNS:
        for (name, version), old in _PARSER_NEW_TO_OLD.items():
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :o WHERE {name_column} = :n AND {column} = :v"),
                {"o": old, "n": name, "v": version},
            )
        op.drop_column(table, name_column)


def _downgrade_execution_environments(bind: sa.engine.Connection) -> None:
    op.drop_constraint(op.f(_CK_ENV), "execution_environment_manifest", type_="check")
    op.execute("ALTER TABLE execution_environment_manifest ADD COLUMN schema_version_text varchar(32)")
    rows = bind.execute(sa.text("SELECT id, canonical_json FROM execution_environment_manifest ORDER BY id")).all()
    for row_id, canonical in rows:
        canonical = dict(canonical)
        canonical.pop("schema_name", None)
        canonical["schema_version"] = _ENV_NAME_OLD
        bind.execute(
            sa.text(
                "UPDATE execution_environment_manifest SET schema_version_text = :v, "
                "canonical_json = CAST(:c AS jsonb), content_digest = :d WHERE id = :id"
            ),
            {"v": _ENV_NAME_OLD, "c": json.dumps(canonical), "d": _environment_digest(canonical), "id": row_id},
        )
    op.alter_column("execution_environment_manifest", "schema_version_text", nullable=False)
    op.drop_column("execution_environment_manifest", "schema_version")
    op.drop_column("execution_environment_manifest", "schema_name")
    op.alter_column("execution_environment_manifest", "schema_version_text", new_column_name="schema_version")


def _downgrade_releases(bind: sa.engine.Connection) -> None:
    """The columns come back; the unpublished rows do not (the table is empty here: see :func:`downgrade`)."""
    op.drop_constraint(op.f(_CK_MANIFEST), "release_manifest", type_="check")
    op.add_column("release_manifest", sa.Column("manifest_schema", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("review_policy_version_text", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("recovery_archive_schema", sa.String(64), nullable=False))
    op.drop_column("release_manifest", "manifest_format_name")
    op.drop_column("release_manifest", "manifest_format_version")
    op.drop_column("release_manifest", "review_policy_name")
    op.drop_column("release_manifest", "review_policy_version")
    op.drop_column("release_manifest", "recovery_archive_format_name")
    op.drop_column("release_manifest", "recovery_archive_format_version")
    op.alter_column("release_manifest", "review_policy_version_text", new_column_name="review_policy_version")


def _downgrade_audit_events(bind: sa.engine.Connection) -> None:
    op.execute(
        """
        UPDATE submission_audit_event SET details_json =
            (details_json - 'format') || jsonb_build_object('schema_version', 'machine_review_v2')
        WHERE jsonb_typeof(details_json) = 'object'
          AND details_json #>> '{format,name}' = 'machine_review' AND details_json #>> '{format,version}' = '1'
        """
    )


def _downgrade_reproducibility(bind: sa.engine.Connection) -> None:
    op.drop_constraint(op.f(_CK_REPRO), "record_reproducibility_assessment", type_="check")
    op.execute(
        "ALTER TABLE record_reproducibility_assessment ALTER COLUMN rubric_version TYPE varchar(64) USING "
        "('v' || (CASE WHEN rubric_version = 0 THEN 1 ELSE rubric_version END)::text)"
    )
    op.execute(
        """
        UPDATE record_reproducibility_assessment SET context_json = jsonb_set(
            context_json, '{rubric,version}',
            to_jsonb('v' || (CASE WHEN (context_json #>> '{rubric,version}') = '0' THEN '1'
                                  ELSE context_json #>> '{rubric,version}' END)))
        WHERE jsonb_typeof(context_json #> '{rubric,version}') = 'number'
        """
    )
    pairs = [(token, f"{token}_v1") for token in _REPRO_TOKENS]
    for column in ("context_json", "missing_json", "passed_json", "warnings_json"):
        expr, params = _restore_words_sql(column, pairs)
        where = " OR ".join(f"{column}::text ~ :w{i}" for i in range(len(pairs)))
        for i, (old, _) in enumerate(pairs):
            params[f"w{i}"] = r"\m" + re.escape(old) + r"\M"
        bind.execute(
            sa.text(f"UPDATE record_reproducibility_assessment SET {column} = {expr} WHERE {column} IS NOT NULL AND ({where})"),
            params,
        )


def _downgrade_machine_review(bind: sa.engine.Connection) -> None:
    op.drop_constraint(op.f(_CK_MACHINE_REVIEW), "record_machine_review", type_="check")
    op.add_column("record_machine_review", sa.Column("rubric_versions_json", sa.dialects.postgresql.JSONB(), nullable=True))
    # A rubric's current version 1 is its old latest version; a 0 is its one older version (the pre-check allows 0 only
    # where that is 1); any version above 1 is a post-reset version and is written as itself.
    latest = " ".join(f"WHEN '{name}' THEN {value}" for name, value in sorted(_OLD_LATEST_RUBRIC.items()))
    old = (
        "(CASE WHEN rubric_version >= 2 THEN rubric_version WHEN rubric_version = 0 THEN 1 "
        f"ELSE (CASE rubric_name {latest} ELSE 1 END) END)"
    )
    op.execute(
        "UPDATE record_machine_review SET rubric_versions_json = "
        f"jsonb_build_object(rubric_name || '_v' || {old}::text, {old}::text)"
    )
    op.alter_column("record_machine_review", "rubric_versions_json", nullable=False)
    op.execute(
        "ALTER TABLE record_machine_review ALTER COLUMN context_schema_version TYPE varchar(32) USING "
        "('v' || context_schema_version::text)"
    )
    op.add_column("record_machine_review", sa.Column("prompt_version_text", sa.String(64), nullable=True))
    # A deterministic runner's old stamp was its unversioned name, or ``external_cp_comparison_v1``; the LLM prompt's
    # was ``machine_review_v1``. Every pre-reset prompt is stored at 0 and a post-reset one at 1, and both are given back as
    # that one old spelling; any other prompt at 0 was refused by the pre-check.
    bind.execute(
        sa.text(
            "UPDATE record_machine_review SET prompt_version_text = CASE "
            "WHEN provider = :sc THEN (CASE WHEN prompt_name = :cp THEN :cp || '_v1' ELSE prompt_name END) "
            "WHEN prompt_name = :llm AND prompt_version IN (0, 1) THEN :llm || '_v1' "
            "ELSE prompt_name || '_v' || prompt_version::text END"
        ),
        {"sc": _SCIENTIFIC_CHECK_PROVIDER, "cp": _CP_RUNNER, "llm": _LLM_PROMPT},
    )
    op.alter_column("record_machine_review", "prompt_version_text", nullable=False)
    op.drop_column("record_machine_review", "prompt_version")
    op.alter_column("record_machine_review", "prompt_version_text", new_column_name="prompt_version")
    op.execute(
        sa.text(
            "UPDATE record_machine_review SET model = model || '_v1' WHERE provider = :p AND model = 'external_cp_comparison'"
        ).bindparams(p=_SCIENTIFIC_CHECK_PROVIDER)
    )
    op.execute(
        sa.text("UPDATE record_machine_review SET model = :old WHERE provider = :p AND model = :new").bindparams(
            old=_FAKE_MODEL_OLD, p=_FAKE_PROVIDER, new=_FAKE_MODEL_NEW
        )
    )
    op.drop_column("record_machine_review", "prompt_name")
    op.drop_column("record_machine_review", "rubric_name")
    op.drop_column("record_machine_review", "rubric_version")
