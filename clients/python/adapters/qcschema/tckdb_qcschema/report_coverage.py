"""Every field of an imported document lands in exactly one report bucket (#573).

:mod:`tckdb_qcschema.mapping` classifies the fields its driver branches read.
Before issue #573 nothing checked that this covered the whole document, and a
``driver="hessian"`` import dropped ``properties.return_energy``,
``extras.qcvars`` and nine other ``properties.*`` fields without naming them.
This module closes that gap in three steps, run by
:func:`complete_mapping_report` once the mapping branches are done.

1. **Spelling.** The branches name fields in QCSchema v1 spelling
   (``model.method``). For a v2 document each entry is rewritten to the path
   it has in that document (``input_data.specification.model.method``), so
   every entry can be found in the document it describes.
2. **Classification.** The raw document is walked, every key, recursively.
   A field no branch classified is looked up in this module's static rules
   (fields this profile knows and never maps: ``properties.calcinfo_nbasis``
   is ``retained_only``, ``molecule.symbols`` is ``transformed``, ...). A
   field the rules do not name either is **unknown** and is listed as
   ``unsupported``.
3. **Check.** Every present field must then be covered by exactly one
   bucket. A field the branch itself owns (:data:`_BRANCH_OWNED`:
   ``return_result``, ``properties.return_energy``, a mapped molecule's
   geometry, ...) left unclassified, or any field covered by two buckets,
   raises ``mapping_report_incomplete``.

Why an unknown field is ``unsupported`` and not a refusal: the untouched
document is always uploaded beside the deposit as its raw artifact, so
nothing unknown is lost; listing it names it in the report, which is the
promise. Refusing would turn a producer's extra metadata (a key in
``extras``, ``model`` or ``provenance``, which qcelemental accepts freely)
into a failed import of good science. Under the exact ``qcelemental`` pin,
an unknown key can only appear in those open containers; a closed
qcelemental model refuses one at validation. A field a *future* qcelemental
declares therefore cannot reach this module until the pin is raised, and
``tests/test_report_coverage.py`` fails when the pinned models declare a
field these rules do not name, so raising the pin forces the decision.

Why a branch-owned field is a refusal and not ``unsupported``: those fields
are the scientific result the branch exists to map. One left out is a
defect in the adapter (issue #573 was exactly this), and falling back to
``unsupported`` would hide it.

Buckets:

``transformed``
    Read by the mapping and determining what is stored: a typed field, a
    parameter observation, or a validity gate the stored record rests on
    (``success``, ``molecule.real``, ``molecule.fragments``,
    ``molecule.masses``).
``retained_only``
    Recognised and deliberately not mapped to a TCKDB field. Kept verbatim
    in the raw artifact; the allow-listed provenance keys are also copied
    into ``parameters_json["tckdb_qcschema"]["provenance"]``.
``unsupported``
    Not represented by this profile: outside profile v1 (``stdout``,
    ``wavefunction``, native files), an operator-identifying provenance key,
    or a key this adapter does not know. Kept only in the raw artifact.
``rejected``
    Always empty here; see :mod:`tckdb_qcschema.mapping`.

A field is *present* unless it is ``null``, ``""``, ``[]`` or ``{}``: the
report describes what this document carries, not what its schema allows.
"""

from __future__ import annotations

from typing import Any, Iterable

import qcelemental.models.v1 as qcel_v1
import qcelemental.models.v2 as qcel_v2

from .errors import E_MAPPING_REPORT_INCOMPLETE, QCSchemaAdapterError
from .reader import QCRecord

T = "transformed"
R = "retained_only"
U = "unsupported"

_BUCKETS = ("transformed", "retained_only", "unsupported", "rejected")

#: v1 spelling -> v2 spelling, per record kind, most specific first. Applied
#: to the entries the mapping branches write, for a v2 document only.
_V2_SPELLING: dict[str, tuple[tuple[str, str], ...]] = {
    "atomic": (
        ("driver", "input_data.specification.driver"),
        ("model", "input_data.specification.model"),
        ("keywords", "input_data.specification.keywords"),
        ("protocols", "input_data.specification.protocols"),
    ),
    "optimization": (
        ("input_specification.model", "input_data.specification.specification.model"),
        ("input_specification.keywords", "input_data.specification.specification.keywords"),
        ("input_specification.driver", "input_data.specification.specification.driver"),
        # v1 names the ESS program in the optimizer's own keywords; v2 in the
        # inner (gradient) specification.
        ("keywords.program", "input_data.specification.specification.program"),
        ("keywords", "input_data.specification.keywords"),
        ("protocols", "input_data.specification.protocols"),
        ("initial_molecule", "input_data.initial_molecule"),
        ("energies[-1]", "trajectory_properties[-1].return_energy"),
        ("energies", "trajectory_properties"),
        ("trajectory", "trajectory_results"),
    ),
}

#: Fields the mapping branch itself must classify, in v1 spelling. Never
#: filled in by a static rule, so a branch that forgets one fails loudly.
_BRANCH_OWNED: dict[str, tuple[str, ...]] = {
    "atomic": (
        "return_result",
        "properties.return_energy",
        "molecule.geometry",
        "molecule.molecular_charge",
        "molecule.molecular_multiplicity",
        "model.method",
        "model.basis",
        "keywords",
        "provenance.creator",
        "provenance.version",
    ),
    "optimization": (
        "success",
        "initial_molecule.geometry",
        "final_molecule.geometry",
        "final_molecule.molecular_charge",
        "final_molecule.molecular_multiplicity",
        "input_specification.model.method",
        "input_specification.model.basis",
        "input_specification.keywords",
        "trajectory",
        "energies",
        "provenance.creator",
        "provenance.version",
    ),
}

#: Molecule fields, relative to the molecule. ``geometry`` is absent on
#: purpose: a mapped molecule's geometry is branch-owned, and a molecule the
#: mapping never reads is classified as a whole.
_MOLECULE_RULES: tuple[tuple[str, str], ...] = (
    # Read: coordinates, isotope detection, the nonstandard-mass gate, the
    # ghost-atom and single-fragment gates.
    ("symbols", T),
    ("mass_numbers", T),
    ("masses", T),
    ("real", T),
    ("fragments", T),
    # Not read. Charge and multiplicity are branch-owned on the molecule
    # that anchors identity; on any other molecule they are not read.
    ("molecular_charge", R),
    ("molecular_multiplicity", R),
    ("fragment_charges", R),
    ("fragment_multiplicities", R),
    ("atomic_numbers", R),
    ("atom_labels", R),
    ("name", R),
    ("comment", R),
    ("connectivity", R),
    ("fix_com", R),
    ("fix_orientation", R),
    ("fix_symmetry", R),
    ("identifiers", R),
    ("provenance", R),
    ("id", R),
    ("schema_name", R),
    ("schema_version", R),
    ("validated", R),
)


def _molecule(prefix: str) -> dict[str, str]:
    return {f"{prefix}.{name}": bucket for name, bucket in _MOLECULE_RULES}


def _declared(model_cls: Any) -> list[str]:
    """Field names a pinned qcelemental model declares (either pydantic)."""
    fields = getattr(model_cls, "model_fields", None)
    if fields is None:
        fields = model_cls.__fields__
    return [getattr(f, "alias", None) or name for name, f in fields.items()]


def _properties(model_cls: Any) -> dict[str, str]:
    """Every declared property is ``retained_only``: profile v1 stores none
    of them except the driver's own energy, which is branch-owned."""
    return {
        f"properties.{name}": R
        for name in _declared(model_cls)
        if name != "return_energy"
    }


def _static_rules(family: str, record_kind: str) -> dict[str, str]:
    """Fields this profile knows and never maps, in the document's spelling."""
    if record_kind == "atomic":
        rules: dict[str, str] = {
            "schema_name": R,
            "schema_version": R,
            "id": R,
            "success": T,
            "error": U,
            # qcengine's dump of every program variable; the values TCKDB
            # maps are the typed ones above, the rest are this job's detail.
            "extras.qcvars": R,
            **_molecule("molecule"),
        }
        if family == "v1":
            rules.update({"driver": T, "protocols": R})
            rules.update(_properties(qcel_v1.AtomicResultProperties))
        else:
            rules.update(
                {
                    "input_data.specification.driver": T,
                    "input_data.specification.protocols": R,
                    "input_data.specification.program": R,
                    "input_data.specification.schema_name": R,
                    "input_data.id": R,
                    "input_data.schema_name": R,
                    "input_data.schema_version": R,
                    "input_data.provenance": R,
                    # The request's copy of the molecule; the mapping reads
                    # the result's own ``molecule``.
                    "input_data.molecule": R,
                }
            )
            rules.update(_properties(qcel_v2.AtomicProperties))
        return rules

    rules = {"schema_name": R, "schema_version": R, "id": R, "error": U}
    if family == "v1":
        rules.update(
            {
                "hash_index": R,
                # The optimizer's own settings, not ESS parameters.
                "keywords": R,
                "protocols": R,
                "input_specification.schema_name": R,
                "input_specification.schema_version": R,
                "input_specification.driver": R,
                **_molecule("initial_molecule"),
                **_molecule("final_molecule"),
            }
        )
        return rules
    rules.update(
        {
            "input_data.id": R,
            "input_data.schema_name": R,
            "input_data.schema_version": R,
            "input_data.provenance": R,
            "input_data.specification.schema_name": R,
            "input_data.specification.program": R,
            "input_data.specification.keywords": R,
            "input_data.specification.protocols": R,
            "input_data.specification.specification.schema_name": R,
            "input_data.specification.specification.program": R,
            "input_data.specification.specification.driver": R,
            "input_data.specification.specification.protocols": R,
            **_molecule("input_data.initial_molecule"),
            **_molecule("final_molecule"),
        }
    )
    rules.update(
        {
            f"properties.{name}": R
            for name in _declared(qcel_v2.OptimizationProperties)
        }
    )
    return rules


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def _is_within(path: str, prefix: str) -> bool:
    """``path`` is ``prefix`` or lies under it (``.``, ``[`` or `` `` boundary)."""
    return path == prefix or (
        path.startswith(prefix) and path[len(prefix)] in ".[ "
    )


def _ancestors(path: str) -> list[str]:
    """``a.b.c`` -> ``["a", "a.b", "a.b.c"]``."""
    parts = path.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def document_spelling(family: str, record_kind: str, entry: str) -> str:
    """One mapping-branch entry (v1 spelling) as it is spelled in a document
    of ``family``. Entries with no v2 counterpart pass through unchanged."""
    if family != "v2":
        return entry
    for v1_prefix, v2_prefix in _V2_SPELLING[record_kind]:
        if _is_within(entry, v1_prefix):
            return v2_prefix + entry[len(v1_prefix):]
    return entry


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def present_leaves(document: dict) -> list[str]:
    """Every present non-dict value's path, in document order.

    A dict is walked; anything else (a scalar, or a list such as
    ``geometry`` or ``trajectory``) is a leaf.
    """
    out: list[str] = []

    def walk(node: Any, here: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{here}.{key}" if here else str(key))
        elif _present(node):
            out.append(here)

    walk(document, "")
    return out


def _buckets_covering(report_dict: dict[str, list[str]], leaf: str) -> list[str]:
    return [
        bucket
        for bucket in _BUCKETS
        if any(_is_within(leaf, entry) for entry in report_dict[bucket])
    ]


# ---------------------------------------------------------------------------
# the three steps
# ---------------------------------------------------------------------------


def _extras_containers(rules: dict[str, str]) -> list[str]:
    """Every free-form ``extras`` dict a document of this shape can carry:
    the top-level one, each molecule's, and each specification's."""
    molecules = [p.removesuffix(".symbols") for p in rules if p.endswith(".symbols")]
    return [
        "extras",
        *(f"{m}.extras" for m in molecules),
        "input_data.specification.extras",
        "input_specification.extras",
        "input_data.specification.specification.extras",
    ]


def _respell(record: QCRecord, report: Any) -> None:
    for bucket in _BUCKETS:
        entries = getattr(report, bucket)
        entries[:] = list(
            dict.fromkeys(
                document_spelling(record.family, record.record_kind, entry)
                for entry in entries
            )
        )


def _classify_remaining(record: QCRecord, report: Any) -> None:
    family, kind = record.family, record.record_kind
    rules = _static_rules(family, kind)
    owned = [document_spelling(family, kind, p) for p in _BRANCH_OWNED[kind]]
    branch_entries = [e for b in _BUCKETS for e in getattr(report, b)]

    # A path is a known container when a rule, an owned field or a branch
    # entry lies at or below it, or it is one of the free-form ``extras``
    # dicts. An unknown key is reported at the first level where the path
    # leaves everything known: ``molecule.extras.label``, not
    # ``molecule.extras``.
    known: set[str] = set()
    for path in [*rules, *owned, *branch_entries, *_extras_containers(rules)]:
        known.update(_ancestors(path))

    for leaf in present_leaves(record.raw_document):
        if _buckets_covering(report.to_dict(), leaf):
            continue
        if any(_is_within(leaf, p) for p in owned):
            continue  # left for the check below to refuse, loudly
        rule = next(
            (a for a in reversed(_ancestors(leaf)) if a in rules), None
        )
        if rule is not None:
            bucket = rules[rule]
            # List the rule's whole subtree once (``extras.qcvars``), unless
            # a branch classified something inside it, in which case only
            # this field is listed so that nothing lands in two buckets.
            inside = any(_is_within(e, rule) and e != rule for e in branch_entries)
            entry = leaf if inside else rule
        else:
            bucket = U
            entry = next(a for a in _ancestors(leaf) if a not in known)
        entries = getattr(report, bucket)
        if entry not in entries:
            entries.append(entry)


def check_mapping_report(document: dict, report_dict: dict[str, list[str]]) -> None:
    """Refuse unless every present field of ``document`` is covered by exactly
    one bucket of ``report_dict``.

    :raises QCSchemaAdapterError: ``mapping_report_incomplete``, naming the
        unclassified fields and the fields covered twice.
    """
    unclassified: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for leaf in present_leaves(document):
        buckets = _buckets_covering(report_dict, leaf)
        if not buckets:
            unclassified.append(leaf)
        elif len(buckets) > 1:
            ambiguous[leaf] = buckets
    if unclassified or ambiguous:
        raise QCSchemaAdapterError(
            E_MAPPING_REPORT_INCOMPLETE,
            "the mapping report does not account for every field of this "
            "document exactly once, so a deposit could drop a field without "
            f"naming it. Unclassified: {unclassified}. In more than one "
            f"bucket: {ambiguous}. This is a defect in tckdb-qcschema, not in "
            "the document; nothing was posted.",
            unclassified=unclassified,
            ambiguous=ambiguous,
        )


def complete_mapping_report(record: QCRecord, report: Any) -> None:
    """Respell, classify the rest, and check; see the module docstring.

    Mutates ``report`` in place. Run after every mapping branch (and the
    provenance allowlist) has written its entries.
    """
    _respell(record, report)
    _classify_remaining(record, report)
    check_mapping_report(record.raw_document, report.to_dict())


def rule_paths(family: str, record_kind: str) -> Iterable[str]:
    """Every path a static rule or a branch-owned field names, in the
    document's spelling (used by the tests to hold the rules to the pinned
    qcelemental models)."""
    yield from _static_rules(family, record_kind)
    for path in _BRANCH_OWNED[record_kind]:
        yield document_spelling(family, record_kind, path)


__all__ = [
    "check_mapping_report",
    "complete_mapping_report",
    "document_spelling",
    "present_leaves",
    "rule_paths",
]
