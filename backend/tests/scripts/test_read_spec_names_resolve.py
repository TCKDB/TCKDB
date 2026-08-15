"""A symbol named in a spec must exist.

Originally this guarded one document —
``backend/docs/specs/scientific_calculation_reads.md``, the file ``CLAUDE.md``
routes people to for calculation reads. Three names had rotted before the guard
existed: the spec annotated fields as ``CalculationResultSPSummary`` /
``…OptSummary`` / ``…FreqSummary`` while the real classes are
``CalculationSPResultSummary`` / ``CalculationOptResultSummary`` /
``CalculationFreqResultSummary`` — the words transposed, which is exactly the
kind of drift a human eye slides over.

**Why it now covers every spec (#167).** The very next PR produced an instance
one document could not see: ``docs/specs/computed-species-bundle-spec.md``
cited ``_assert_calculation_owned_by``, a helper removed when five per-product
ownership checks were consolidated into one (#162). The same sweep found two
more in that one file (``_assert_dependency_role_type_compatible``,
``_assert_thermo_role_type_compatible``) and a fourth in the artifact-upload
spec (``_persist_artifact``, listed with a ✅ against a module it had been
extracted out of). The defect class recurs, so the guard follows every
document under ``backend/docs/specs/`` and ``docs/specs/``.

**A spec both defines and references names, and only one of those is
checkable.** These documents are partly design proposals: they declare shapes
they are *asking for* (``class CalculationsSearchResponse(BaseModel):`` and
friends) alongside references to shapes that already exist. Requiring every
name to resolve would fail on the proposals; exempting every unresolved name
would check nothing. So the rule is: a name a document **defines** in one of
its own fenced blocks is exempt; a name it merely **references** must resolve.
That distinction is what makes the check usable on a design proposal, and it is
precisely what caught the original three — they appeared only as field
annotations, never as definitions.

**Two patterns, deliberately narrow.**

*Classes* are matched by suffix (``…Summary|Read|Response|Payload|Block``)
rather than "any CamelCase word". Measured over all 55 specs, the suffix rule
sees 205 references; matching any CamelCase word instead sees 706, of which 113
are unresolved and almost all are noise — ``PostgreSQL``, ``OpenAPI``,
``InChIKey``, ``MinIO``, ``ValueError``, ``C2H4``. A guard with that
signal-to-noise ratio is abandoned within a week.

*Functions* are matched only as a backticked private helper of **three or more
underscore-separated segments** (``_assert_calculation_owned_by``). This is
narrower than it looks and the narrowness is load-bearing. Matching any
backticked ``snake_case`` token sees 2317 references with 1062 unresolved — it
is every column, field and enum token in the system, and useless. Restricting
to a leading underscore drops that to 31 references, but 12 of the 17 misses
are markdown *elision*, where prose abbreviates a shared prefix::

    Child rows (`energy_correction_scheme_atom_param`, `_bond_param`, …)
    (`solve_temperature_min_k` / `_max_k` / `solve_pressure_min_bar` / `_max_bar`)

Every one of those elisions has one or two segments; every real helper name in
the corpus that this guard needs to catch has three or more. The three-segment
floor removes all of them and keeps the ones that matter. The known cost is
that a genuinely stale two-segment helper is invisible — ``_persist_artifact``
was found by hand during #167, not by this pattern. Widening the pattern to
catch it would re-admit the elisions, so the trade was made deliberately.

**Resolution is deliberately generous.** A name is considered real if it is
defined anywhere in ``backend/``, ``schemas/`` or ``clients/`` — imported
module attributes plus a source scan for ``def``/``class`` statements. The
question this guard asks is "does this symbol exist at all?", not "is it
importable from the module the prose implies". Being generous matters: several
names are legitimately defined in ``backend/tests/`` (``_walk``,
``_disable_rate_limit_by_default``), and the class that was line-wrapped in
``machine_review_admin_ui_mock.md`` lives in ``app/api/routes/admin.py``, not
under ``app.schemas`` at all — the original ``app.schemas``-only walk would
have reported it as stale.
"""

from __future__ import annotations

import functools
import importlib
import pkgutil
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

#: Every spec corpus this guard follows.
SPEC_DIRS = (
    BACKEND_ROOT / "docs" / "specs",
    REPO_ROOT / "docs" / "specs",
)

#: The one document the guard covered before #167. Named so that widening the
#: sweep can never quietly stop covering the file it started from.
ORIGINAL_SPEC = "backend/docs/specs/scientific_calculation_reads.md"

#: Trees searched for a definition of a referenced name.
SOURCE_ROOTS = (
    BACKEND_ROOT,
    REPO_ROOT / "schemas",
    REPO_ROOT / "clients",
)

#: Names shaped like a Pydantic schema class. Suffix-driven; see the docstring.
_CLASS_NAME = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Summary|Read|Response|Payload|Block))\b")

#: A backticked private helper of three or more segments; see the docstring for
#: why two-segment names are excluded.
_HELPER_NAME = re.compile(r"`(_[a-z][a-z0-9]*(?:_[a-z0-9]+){2,})`")

#: A name the document declares for itself, in any of its fenced blocks.
_DOC_DEFINES = re.compile(r"^\s*(?:(?:async\s+)?def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)

#: The same, over real source files.
_SOURCE_DEFINES = _DOC_DEFINES

#: ``(spec path relative to repo root, name)`` pairs that are referenced,
#: undefined in their document, and unresolvable — but correctly so.
#:
#: Keyed by document as well as name: the same word may be a fair exemption in
#: one spec and genuine rot in another. **Every entry states why.** An
#: unexplained allowlist is how a guard rots, and
#: ``test_every_allowlist_entry_is_still_earning_its_place`` deletes entries
#: that stop applying.
_ALLOWED: dict[tuple[str, str], str] = {
    (
        "backend/docs/specs/read_query_api_audit.md",
        "ProvenanceSummary",
    ): (
        "A proposal, in prose rather than in a fenced block: the audit's "
        "recommendation #4 reads 'Define a `ProvenanceSummary` schema (LoT ref "
        "+ method/basis, ...)'. It is a shape being asked for, not one being "
        "cited, so it is exempt for the same reason a `class` declared in one "
        "of the document's own code blocks is."
    ),
    (
        "backend/docs/specs/scientific_statmech_reads.md",
        "SoftwareSummary",
    ): (
        "A proposal, in prose: 'a smaller `SoftwareSummary` shape would be the "
        "right v1 refactor; v0 reuses the existing fragment'. The document is "
        "explicit that it does not exist yet."
    ),
    (
        "docs/specs/path_search_calculation_schema.md",
        "NEBResultPayload",
    ): (
        "Deliberately names the pre-migration symbol. Section 10 is a "
        "before/after table -- 'NEBResultPayload -> PathSearchResultPayload' -- "
        "so the name NOT resolving is the migration having succeeded. "
        "Correcting it would destroy the only record of what was renamed."
    ),
    (
        "docs/specs/upload-idempotency-key-spec.md",
        "ResponsePayload",
    ): (
        "A type-variable placeholder in an illustrative generic signature, "
        "`def run_idempotent_write(..., operation: Callable[[], ResponsePayload])"
        "`, in a block prefaced 'A wrapper helper may be useful'. It stands for "
        "whatever the route returns; there is no such class and there should "
        "not be one."
    ),
    (
        "backend/docs/specs/scientific_calculation_path_includes.md",
        "CalculationScanCoordinateFullSummary",
    ): (
        "Deliberately names a shape that was never written. An earlier draft of "
        "the scan-response block cited it; #167 corrected the block and added a "
        "'What actually shipped' note saying the split was collapsed into "
        "`ScanCoordinateSummary`. The note has to spell the old name, because a "
        "reader who grepped for it is exactly who the note is for."
    ),
    (
        "backend/docs/specs/scientific_calculation_reads.md",
        "_reject_include_all",
    ): (
        "Deliberately names a removed guard, and the document says so in the "
        "same breath: 'an earlier `_reject_include_all` policy guard ... was "
        "removed when it was no longer doing useful work'. Its absence from "
        "the code is the point of the sentence."
    ),
}


def _spec_paths() -> list[Path]:
    return sorted(
        (path for directory in SPEC_DIRS for path in directory.glob("*.md")),
        key=lambda p: str(p),
    )


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@functools.lru_cache(maxsize=None)
def _resolvable_names() -> frozenset[str]:
    """Every name defined anywhere in the backend, wire schemas or clients."""
    names: set[str] = set()

    for package_name in ("app", "tckdb_schemas"):
        try:
            package = importlib.import_module(package_name)
        except Exception:  # pragma: no cover - an unimportable package is its own bug
            continue
        names.update(vars(package))
        for module_info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            names.add(module_info.name.rsplit(".", 1)[-1])
            try:
                names.update(vars(importlib.import_module(module_info.name)))
            except Exception:  # pragma: no cover - covered by the source scan below
                continue

    for root in SOURCE_ROOTS:
        if not root.is_dir():
            continue
        for source in root.rglob("*.py"):
            names.add(source.stem)
            try:
                names.update(_SOURCE_DEFINES.findall(source.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):  # pragma: no cover
                continue

    return frozenset(names)


def _referenced_but_not_defined(path: Path, pattern: re.Pattern[str]) -> list[str]:
    """Names the document cites without declaring them in one of its blocks."""
    text = path.read_text(encoding="utf-8")
    return sorted(set(pattern.findall(text)) - set(_DOC_DEFINES.findall(text)))


def _references(pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    return [(_relative(path), name) for path in _spec_paths() for name in _referenced_but_not_defined(path, pattern)]


def _checkable(pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    """References minus allowlisted ones — what the parametrized tests assert on."""
    return [pair for pair in _references(pattern) if pair not in _ALLOWED]


_CLASS_REFERENCES = _references(_CLASS_NAME)
_HELPER_REFERENCES = _references(_HELPER_NAME)
_CLASS_CHECKS = _checkable(_CLASS_NAME)
_HELPER_CHECKS = _checkable(_HELPER_NAME)


def _ids(pairs: list[tuple[str, str]]) -> list[str]:
    return [f"{Path(doc).name}::{name}" for doc, name in pairs]


# --------------------------------------------------------------------------
# Guarding the guard.
#
# Every assertion below exists because a scan that quietly stops matching
# reports success having checked nothing, and `parametrize` over an empty set
# is a skip rather than a failure. Each floor was measured on the corpus and
# then set below the measurement, so ordinary editing does not trip it but a
# broken scan collapses well past it.
# --------------------------------------------------------------------------


def test_every_spec_directory_exists_and_holds_documents() -> None:
    """55 specs across two directories when this was written."""
    for directory in SPEC_DIRS:
        assert directory.is_dir(), f"{directory} is missing; a spec corpus moved and this guard did not."
        assert list(directory.glob("*.md")), f"{directory} holds no specs to check."

    discovered = _spec_paths()
    assert len(discovered) >= 45, (
        f"Only {len(discovered)} spec documents discovered, against 55 when this "
        f"guard was written. Discovery has probably broken rather than the corpus "
        f"having shrunk that far."
    )
    assert ORIGINAL_SPEC in {_relative(p) for p in discovered}, (
        f"{ORIGINAL_SPEC} is the document this guard started from and is no longer being discovered."
    )


def test_the_resolution_namespace_is_populated() -> None:
    """An empty or near-empty name set would fail every check, not pass it —
    but a *thinned* one silently invents stale names. 15473 when written."""
    names = _resolvable_names()
    assert len(names) >= 8000, (
        f"Only {len(names)} resolvable names found, against 15473 when this guard "
        f"was written. The import walk or the source scan has broken, and every "
        f"failure this file reports would be an artefact of that."
    )
    # Two names that must always resolve: one imported, one reachable only by
    # the source scan. If either goes missing, half the resolver is dead.
    assert "CalculationSPResultSummary" in names, "the import walk is not working"
    assert "assert_calculation_owned_by" in names, "the source scan is not working"


def test_the_class_scan_finds_names_to_check() -> None:
    """205 class references across 26 documents when this guard was written."""
    documents = {doc for doc, _ in _CLASS_REFERENCES}
    assert len(_CLASS_REFERENCES) >= 150, (
        f"Only {len(_CLASS_REFERENCES)} referenced class names found across the "
        f"specs, against 205 when this guard was written. The scan has probably "
        f"broken rather than the specs having shrunk that far."
    )
    assert len(documents) >= 18, (
        f"Only {len(documents)} documents contributed a class name, against 26 when this guard was written."
    )
    assert _CLASS_CHECKS, "the allowlist has swallowed every class name to check"


def test_the_helper_scan_finds_names_to_check() -> None:
    """14 helper references across 7 documents when this guard was written."""
    documents = {doc for doc, _ in _HELPER_REFERENCES}
    assert len(_HELPER_REFERENCES) >= 9, (
        f"Only {len(_HELPER_REFERENCES)} referenced helper names found across the "
        f"specs, against 14 when this guard was written. The three-segment pattern "
        f"is narrow by design, so it has little margin — check it still matches "
        f"before assuming the specs changed."
    )
    assert len(documents) >= 4, (
        f"Only {len(documents)} documents contributed a helper name, against 7 when this guard was written."
    )
    assert _HELPER_CHECKS, "the allowlist has swallowed every helper name to check"


def test_the_document_this_guard_started_from_is_still_checked() -> None:
    """#167 widened the sweep; it must not have diluted the original guarantee."""
    from_original = [name for doc, name in _CLASS_CHECKS if doc == ORIGINAL_SPEC]
    assert len(from_original) >= 12, (
        f"Only {len(from_original)} referenced class names found in {ORIGINAL_SPEC}, "
        f"against 17 when the single-document version of this guard was written. "
        f"Widening the sweep has broken the case it was built for."
    )


def test_every_allowlist_entry_names_a_real_document() -> None:
    known = {_relative(path) for path in _spec_paths()}
    for document, name in _ALLOWED:
        assert document in known, (
            f"The allowlist exempts '{name}' in {document}, which is not a spec "
            f"this guard discovers. The document moved or was deleted; drop the "
            f"entry or repoint it."
        )


def test_every_allowlist_entry_carries_a_reason() -> None:
    for (document, name), reason in _ALLOWED.items():
        assert len(reason.split()) >= 8, (
            f"The allowlist entry for '{name}' in {document} does not explain "
            f"itself. An unexplained exemption is indistinguishable from a bug "
            f"someone silenced."
        )


def test_every_allowlist_entry_is_still_earning_its_place() -> None:
    """An exemption that no longer applies is rot with a comment on it."""
    still_referenced = set(_CLASS_REFERENCES) | set(_HELPER_REFERENCES)
    resolvable = _resolvable_names()
    for document, name in _ALLOWED:
        assert (document, name) in still_referenced, (
            f"The allowlist exempts '{name}' in {document}, but the document no longer references it. Delete the entry."
        )
        assert name not in resolvable, (
            f"The allowlist exempts '{name}' in {document} as a name that does "
            f"not exist, but it now resolves. Delete the entry so the reference "
            f"is checked like any other."
        )


# --------------------------------------------------------------------------
# The checks themselves.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("document", "name"), _CLASS_CHECKS, ids=_ids(_CLASS_CHECKS))
def test_a_referenced_class_name_resolves(document: str, name: str) -> None:
    assert name in _resolvable_names(), (
        f"{document} references the class '{name}', which is not defined anywhere "
        f"under backend/, schemas/ or clients/. Either it was renamed and the spec "
        f"was not updated, or the spec is proposing it — in which case declare it "
        f"in one of the document's own code blocks, which is how this guard tells "
        f"a proposal from a stale reference. If it names something that "
        f"deliberately no longer exists, say so in the document and add an "
        f"allowlist entry explaining why."
    )


@pytest.mark.parametrize(("document", "name"), _HELPER_CHECKS, ids=_ids(_HELPER_CHECKS))
def test_a_referenced_helper_name_resolves(document: str, name: str) -> None:
    assert name in _resolvable_names(), (
        f"{document} references the helper '{name}', which is not defined anywhere "
        f"under backend/, schemas/ or clients/. The usual cause is a private helper "
        f"that was consolidated into a shared one and kept its old name in the prose "
        f"— check for the same name without its leading underscore before assuming "
        f"it is gone. Do not fix this by deleting the sentence: correct the name, or "
        f"say in the document that the thing no longer exists and allowlist it."
    )
