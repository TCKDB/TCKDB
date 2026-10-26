"""A rubric's checks cannot change without its version changing.

Stored assessments name the rubric version they were made under, and "stale" means "that is not the current version".
That only holds if the version moves when the rules move, so each rubric's version is pinned beside a fingerprint of
its checks and grading (``app/services/trust/rubric_pins.py``, ``rubric_pins.json``). These tests are the guard.

* the committed pins must match the code (``test_pins_match_the_code``): a changed check with the same version fails,
  and so does a bumped version that was not re-pinned;
* the fingerprint must react to what decides a check (a threshold, a severity, a weight, a record type, a helper in
  another module, a method of an enum, a dispatch table) and ignore what does not (a docstring, a comment, an
  ``explain`` string);
* what the walk cannot follow (a computed ``getattr``, the environment, settings, an uncanonicalisable constant) is
  refused, never skipped;
* the generator refuses to re-pin a changed rubric under its old version;
* a rubric that declares checks cannot escape the pin, and a rubric that declares none is named, not silently skipped.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import linecache
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.services.trust import rubric_pins
from app.services.trust.models import EvidenceCheckKind, EvidenceCheckSpec, EvidenceRubric
from app.services.trust.rubrics import RUBRICS

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "backend" / "scripts" / "generate_rubric_pins.py"
REPRODUCIBILITY_NAMES = {"tckdb_reproducibility", "tckdb_reproducibility_ts_statmech"}


def test_pins_match_the_code() -> None:
    problems = rubric_pins.diagnose(rubric_pins.current_fingerprints(), rubric_pins.load_pins())
    assert not problems, "\n".join(
        ["A rubric's checks or version disagree with backend/app/services/trust/rubric_pins.json:", *problems]
    )


def test_pins_were_made_under_this_python() -> None:
    assert rubric_pins.pinned_python() == rubric_pins.python_minor(), (
        "ast.dump differs between Python minor versions; re-pin with generate_rubric_pins.py --repin-for-python"
    )


def test_generator_check_mode_passes_on_a_clean_tree() -> None:
    result = subprocess.run([sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr


def test_every_rubric_with_checks_is_pinned_and_the_rest_are_named() -> None:
    pinned = set(rubric_pins.load_pins())
    with_checks = {ref.name for ref in RUBRICS.refs() if RUBRICS.get(ref.name, ref.version).checks}
    # Not vacuous: an empty registry (or an empty pin file) must not satisfy this.
    assert with_checks and len(pinned) >= len(with_checks)
    assert REPRODUCIBILITY_NAMES <= pinned
    assert pinned == with_checks | REPRODUCIBILITY_NAMES
    assert set(rubric_pins.unguarded_rubric_names()) | with_checks == {ref.name for ref in RUBRICS.refs()}
    # A rubric with checks and no grading entrypoint would have its grading unguarded.
    assert with_checks <= set(rubric_pins.GRADING_ENTRYPOINTS)


def test_unbumped_overrides_are_surfaced_with_their_reasons() -> None:
    """Every override leaves a trace: an unbumped re-pin, a Python re-pin and an algorithm re-pin are all printed."""
    overrides = rubric_pins.load_overrides()
    for entry in overrides:
        if "name" in entry:
            assert str(entry.get("reason", "")).strip(), f"unbumped override without a reason: {entry}"
        else:
            assert set(entry) <= {"python_repin", "algorithm_repin"}, f"unrecognised override entry: {entry}"
    for entry in overrides:
        print("pins override:", entry)


# --- the fingerprint reacts to what decides a check, and only to that --------------------------------------------


_FILES = iter(range(10_000))


def _load(tmp_path: Path, name: str, source: str):
    """Import *source* as a real module file (``inspect.getsource`` needs one)."""
    path = tmp_path / f"{next(_FILES)}" / f"{name.rsplit('.', 1)[-1]}.py"
    path.parent.mkdir(parents=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_RUNNER = """
    THRESHOLD = {threshold}

    def helper(x):
        '''{doc}'''
        # {comment}
        return x > THRESHOLD

    def runner(record):
        '''{doc}'''
        return "passed" if helper(record) else "missing"
"""


@pytest.fixture
def fp(tmp_path, monkeypatch):
    monkeypatch.setitem(rubric_pins.GRADING_ENTRYPOINTS, "t", ())

    def make(*, threshold=5, doc="Doc.", comment="note", kind=EvidenceCheckKind.required, weight=1, explain="x",
             record_type="thing"):
        module = _load(
            tmp_path,
            "app._pinmod",  # the same module name each time: only the source differs
            _RUNNER.format(threshold=threshold, doc=doc, comment=comment),
        )
        rubric = EvidenceRubric(
            name="t",
            version=1,
            record_type=record_type,
            checks=(
                EvidenceCheckSpec(name="c", kind=kind, weight=weight, explain=explain, runner=module.runner),
            ),
        )
        return rubric_pins.rubric_fingerprint(rubric)

    return make


def test_a_threshold_change_moves_the_fingerprint(fp) -> None:
    assert fp(threshold=5) != fp(threshold=6)


def test_a_docstring_or_comment_or_explain_change_does_not(fp) -> None:
    base = fp()
    assert fp(doc="A completely reworded docstring.") == base
    assert fp(comment="another remark") == base
    assert fp(explain="reworded explanation shown to readers") == base


def test_severity_weight_and_record_type_move_it(fp) -> None:
    base = fp()
    assert fp(kind=EvidenceCheckKind.optional) != base
    assert fp(weight=3) != base
    assert fp(record_type="other") != base


def test_a_runner_with_no_in_scope_code_fingerprints_without_error(monkeypatch) -> None:
    monkeypatch.setitem(rubric_pins.GRADING_ENTRYPOINTS, "t", ())
    rubric = EvidenceRubric(
        name="t",
        version=1,
        record_type="thing",
        checks=(EvidenceCheckSpec(name="c", kind=EvidenceCheckKind.required, runner=len),),
    )
    # ``len`` is a builtin outside scope: nothing to read, nothing a verdict depends on here.
    rubric_pins.rubric_fingerprint(rubric)


def test_a_threshold_in_a_helper_of_a_real_rubric_moves_its_fingerprint(monkeypatch) -> None:
    """The real rubrics read module constants: change one and the rubric that reaches it moves."""
    from app.services.trust import rubrics

    before = rubric_pins.current_fingerprints()
    monkeypatch.setattr(rubrics, "_TYPES_REQUIRING_GEOMETRY_VALIDATION", frozenset())
    after = rubric_pins.current_fingerprints()
    moved = {name for name in before if before[name] != after[name]}
    assert "computed_calculation" in moved


# --- the diagnosis and the generator say what to do -----------------------------------------------------------------


def test_changed_checks_without_a_bump_fail_and_say_bump() -> None:
    problems = rubric_pins.diagnose({"r": (1, "new")}, {"r": {"1": "old"}})
    assert len(problems) == 1 and "Bump the rubric's version" in problems[0]


def test_a_bump_without_a_re_pin_fails_and_says_re_pin() -> None:
    problems = rubric_pins.diagnose({"r": (2, "new")}, {"r": {"1": "old"}})
    assert len(problems) == 1 and "without re-pinning" in problems[0]


def test_bump_and_re_pin_pass() -> None:
    assert rubric_pins.diagnose({"r": (2, "new")}, {"r": {"2": "new"}}) == []


def test_a_rubric_that_drops_its_checks_is_told_so_not_called_unregistered(monkeypatch) -> None:
    monkeypatch.setattr(rubric_pins, "unguarded_rubric_names", lambda: ("r",))
    problems = rubric_pins.diagnose({}, {"r": {"1": "x"}})
    assert len(problems) == 1 and "declares no checks" in problems[0] and "unregistered" not in problems[0]


def test_the_generator_refuses_to_re_pin_an_unbumped_change() -> None:
    fingerprints = rubric_pins.current_fingerprints()
    name = next(iter(fingerprints))
    committed = {n: {str(v): fp} for n, (v, fp) in fingerprints.items()}
    committed[name] = {str(fingerprints[name][0]): "0" * 64}
    assert rubric_pins.unbumped_changes(fingerprints, committed) == [name]
    # A bump is not an unbumped change.
    committed[name] = {str(fingerprints[name][0] - 1 or 99): "0" * 64}
    assert rubric_pins.unbumped_changes(fingerprints, committed) == []


def test_allow_unbumped_needs_a_reason_and_is_recorded(tmp_path, monkeypatch) -> None:
    """Run the generator against a copy of the pins with one fingerprint altered."""
    import json

    work = tmp_path / "pins.json"
    document = rubric_pins.load_document()
    name = next(iter(document["pins"]))
    version = next(iter(document["pins"][name]))
    document["pins"][name][version] = "0" * 64
    work.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(rubric_pins, "PINS_PATH", work)
    spec = importlib.util.spec_from_file_location("_gen", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    monkeypatch.setattr(generator.rubric_pins, "PINS_PATH", work)

    assert generator.main([]) == 1  # refused without a bump
    assert generator.main(["--allow-unbumped", name]) == 1  # a reason is required
    assert generator.main(["--allow-unbumped", name, "--reason", "wording only in a message"]) == 0
    recorded = json.loads(work.read_text())["unbumped_overrides"]
    assert recorded[-1] == {"name": name, "version": int(version), "reason": "wording only in a message"}
    assert generator.main(["--check"]) == 0


def test_repin_for_python_only_when_the_python_version_is_the_only_change(tmp_path, monkeypatch) -> None:
    import json

    work = tmp_path / "pins.json"
    document = rubric_pins.load_document()
    document["python"] = "0.0"
    work.write_text(json.dumps(document), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_gen2", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    monkeypatch.setattr(generator.rubric_pins, "PINS_PATH", work)

    assert generator.main(["--check"]) == 1  # the Python version differs
    assert generator.main(["--repin-for-python"]) == 0
    after = json.loads(work.read_text())
    assert after["python"] == rubric_pins.python_minor()
    assert {"python_repin": {"from": "0.0", "to": rubric_pins.python_minor()}} in after["unbumped_overrides"]
    assert generator.main(["--check"]) == 0
    # Refused when a rubric's version moved as well.
    name = next(iter(after["pins"]))
    after["python"] = "0.0"
    after["pins"][name] = {"999": "x"}
    work.write_text(json.dumps(after), encoding="utf-8")
    assert generator.main(["--repin-for-python"]) == 1


# --- slips the first version of the guard missed: each must now change the fingerprint or be refused ----------------


def _fingerprint(tmp_path, monkeypatch, sources: dict[str, str]):
    """Fingerprint ``runner`` of ``app._pinmod`` after loading *sources* (module name -> text) from files."""
    monkeypatch.setitem(rubric_pins.GRADING_ENTRYPOINTS, "t", ())
    for name, text in sorted(sources.items(), key=lambda item: item[0] == "app._pinmod"):
        _load(tmp_path, name, text)  # helpers first, so the runner module can import them
    rubric = EvidenceRubric(
        name="t",
        version=1,
        record_type="thing",
        checks=(
            EvidenceCheckSpec(name="c", kind=EvidenceCheckKind.required, runner=sys.modules["app._pinmod"].runner),
        ),
    )
    return rubric_pins.rubric_fingerprint(rubric)


def _differs_or_refused(tmp_path, monkeypatch, template: str, extra: dict[str, str] | None = None) -> None:
    """The slip (threshold 5 -> 6) must change the fingerprint, or the walk must refuse. Equal is the defect."""
    outcomes = []
    for threshold in (5, 6):
        sources = {"app._pinmod": template.replace("{T}", str(threshold))}
        for name, text in (extra or {}).items():
            sources[name] = text.replace("{T}", str(threshold))
        try:
            outcomes.append(_fingerprint(tmp_path, monkeypatch, sources))
        except Exception as exc:
            assert type(exc).__name__ == "Unfingerprintable", exc
            return
    assert outcomes[0] != outcomes[1], "a change to what the check decides left the fingerprint unchanged"


_PATTERNS = {
    "dispatch_dict": """
        def _small(x): return x < {T}
        def _big(x): return x > {T}
        TABLE = {"a": _small, "b": _big}
        def runner(record): return TABLE[record.kind](record.value)
    """,
    "tuple_of_lambdas": """
        RULES = (("low", lambda x: x < {T}), ("high", lambda x: x > 100))
        def runner(record): return all(rule(record.value) for _, rule in RULES)
    """,
    "functools_cache": """
        import functools
        @functools.cache
        def limit(): return {T}
        def runner(record): return record.value > limit()
    """,
    "class_attribute_threshold": """
        class Rule:
            LIMIT = {T}
            @staticmethod
            def passes(x): return x > Rule.LIMIT
        def runner(record): return Rule.passes(record.value)
    """,
    "frozen_dataclass_constant": """
        from dataclasses import dataclass
        @dataclass(frozen=True)
        class Limits:
            low: int
        LIMITS = Limits(low={T})
        def runner(record): return record.value > LIMITS.low
    """,
    "enum_property": """
        import enum
        class Kind(enum.Enum):
            a = "a"
            @property
            def cutoff(self): return {T}
        def runner(record): return record.value > Kind.a.cutoff
    """,
    "enum_method_via_member": """
        import enum
        class Finding(enum.Enum):
            torn = "torn"
            def is_break(self): return self.value != "{T}"
        def runner(record): return Finding(record.kind).is_break()
    """,
    "contextmanager_helper": """
        import contextlib
        CUTOFF = {T}
        def _hidden(x): return x > CUTOFF
        @contextlib.contextmanager
        def _scope(x):
            if _hidden(x):
                yield
            yield
        def runner(record):
            with _scope(record.value):
                return True
    """,
}


@pytest.mark.parametrize("name", sorted(_PATTERNS))
def test_slip_pattern_is_caught(name, tmp_path, monkeypatch) -> None:
    _differs_or_refused(tmp_path, monkeypatch, textwrap.dedent(_PATTERNS[name]))


def test_a_helper_in_tckdb_schemas_is_in_scope(tmp_path, monkeypatch) -> None:
    _differs_or_refused(
        tmp_path,
        monkeypatch,
        "from tckdb_schemas._pinhelper import limit\ndef runner(record): return record.value > limit()\n",
        {"tckdb_schemas._pinhelper": "def limit(): return {T}\n"},
    )


def test_getattr_with_a_computed_name_is_refused(tmp_path, monkeypatch) -> None:
    template = """
        TABLE = {"a": lambda x: x > 5}
        def runner(record): return getattr(record, "pick" + record.kind)(record.value)
    """
    with pytest.raises(Exception) as excinfo:
        _fingerprint(tmp_path, monkeypatch, {"app._pinmod": textwrap.dedent(template)})
    assert type(excinfo.value).__name__ == "Unfingerprintable"
    assert "computed attribute name" in str(excinfo.value)


def test_getattr_with_a_literal_name_is_fine(tmp_path, monkeypatch) -> None:
    _differs_or_refused(tmp_path, monkeypatch, "def runner(record): return getattr(record, 'value') > {T}\n")


@pytest.mark.parametrize(
    "body",
    [
        "import os\ndef runner(record): return record.value > int(os.environ['LIMIT'])\n",
        "import os\ndef runner(record): return record.value > int(os.getenv('LIMIT', '5'))\n",
        "from os import environ\ndef runner(record): return record.value > int(environ['LIMIT'])\n",
    ],
)
def test_an_environment_read_is_refused(body, tmp_path, monkeypatch) -> None:
    with pytest.raises(Exception) as excinfo:
        _fingerprint(tmp_path, monkeypatch, {"app._pinmod": body})
    assert type(excinfo.value).__name__ == "Unfingerprintable"
    assert "environment" in str(excinfo.value)


def test_a_settings_read_is_refused(tmp_path, monkeypatch) -> None:
    body = (
        "from app.api.config import settings\n"
        "def runner(record): return record.value > settings.rate_limit_auth_read_per_minute\n"
    )
    with pytest.raises(Exception) as excinfo:
        _fingerprint(tmp_path, monkeypatch, {"app._pinmod": body})
    assert type(excinfo.value).__name__ == "Unfingerprintable"
    assert "settings" in str(excinfo.value)


def test_an_unreadable_in_scope_constant_is_refused_not_skipped(tmp_path, monkeypatch) -> None:
    body = """
        class Opaque:
            def __init__(self): self.n = 5
        LIMIT = Opaque()
        def runner(record): return record.value > LIMIT.n
    """
    with pytest.raises(Exception) as excinfo:
        _fingerprint(tmp_path, monkeypatch, {"app._pinmod": textwrap.dedent(body)})
    assert type(excinfo.value).__name__ == "Unfingerprintable"


# --- the real code paths the reviewer showed passing ----------------------------------------------------------------


def test_real_helper_code_the_guard_used_to_miss_is_now_followed() -> None:
    """``is_break`` (an enum method), the TS context constants and the hidden-calculation helper are all reached."""
    ts_code, ts_constants = rubric_pins.reachable("computed_transition_state")
    assert any("trust_floor:_hidden_calculation_ids" in k for k in ts_code)
    assert any("_CURVATURE_ROLE" in k for k in ts_constants)
    assert any("_DECLARED_KINDS" in k for k in ts_constants)
    repro_code, _ = rubric_pins.reachable("tckdb_reproducibility")
    assert "class app.db.models.common:ArtifactIntegrityFinding" in repro_code


def _recompiled(module_name: str, function_name: str, old: str, new: str):
    """*function_name* of *module_name* with one source fragment replaced, as a function whose source is readable."""
    module = importlib.import_module(module_name)
    source = inspect.getsource(getattr(module, function_name))
    assert old in source, f"{old!r} not found; the proof needs updating"
    path = f"/nonexistent/{module_name}.{function_name}.py"
    text = source.replace(old, new)
    linecache.cache[path] = (len(text), None, text.splitlines(True), path)
    namespace = dict(vars(module))
    exec(compile(text, path, "exec"), namespace)
    return namespace[function_name]


def test_a_rule_change_in_the_reproducibility_grading_moves_its_fingerprint(monkeypatch) -> None:
    """The reviewer's slip: ``elif not level_passed(auditable)`` -> ``elif False`` was reported as up to date."""
    from app.services import reproducibility_rubric as module

    before = rubric_pins.current_fingerprints()["tckdb_reproducibility"]
    changed = _recompiled(
        "app.services.reproducibility_rubric",
        "evaluate_reproducibility",
        "elif not level_passed(CheckLevel.auditable):",
        "elif False:",
    )
    monkeypatch.setattr(module, "evaluate_reproducibility", changed)
    after = rubric_pins.current_fingerprints()["tckdb_reproducibility"]
    assert before[0] == after[0] and before[1] != after[1]
    problems = rubric_pins.diagnose(rubric_pins.current_fingerprints(), rubric_pins.load_pins())
    assert any("tckdb_reproducibility" in p and "Bump the rubric's version" in p for p in problems)


def test_every_computed_getattr_site_is_declared() -> None:
    """The sites the guard permits are exactly the ones in the walked code; a stale declaration is a defect too."""
    assert rubric_pins.discover_dynamic_attribute_sites() == sorted(rubric_pins.DYNAMIC_ATTRIBUTE_READS)


# --- round 2: imports inside a function body, enum columns, the statmech chooser, determinism --------------------------


def _moved_by(monkeypatch, module_name: str, function_name: str, old: str, new: str) -> set[str]:
    """The rubrics whose fingerprint changes when one fragment of one repository function is edited."""
    module = importlib.import_module(module_name)
    before = rubric_pins.current_fingerprints()
    original = getattr(module, function_name)
    changed = _recompiled(module_name, function_name, old, new)
    # A real edit changes the function wherever it is bound: every module that imported it by name sees the new one.
    for holder in list(sys.modules.values()):
        for attribute, value in list(vars(holder).items()) if hasattr(holder, "__dict__") else []:
            if value is original:
                monkeypatch.setattr(holder, attribute, changed)
    after = rubric_pins.current_fingerprints()
    return {name for name in before if before[name] != after[name]}


def test_status_hidden_under_profile_moves_the_transition_state_rubric(monkeypatch) -> None:
    """``>`` to ``>=`` in handles.py, reached by the transition-state rubric through ``ref_guard.classify_refs``, which imports
    it at module level (``ref_guard.py``), on the scoped assessment's path.

    Since step 5 of ADR 0023 the trust evaluators read the evidence rule, not the record floor, so the kinetics rubric no
    longer reaches this function. (A function-local import being followed is pinned by
    ``test_a_local_import_is_followed_visible_statuses``.)
    """
    moved = _moved_by(
        monkeypatch,
        "app.services.scientific_read.handles",
        "status_hidden_under_profile",
        "REVIEW_FLOOR_ORDER[status] > REVIEW_FLOOR_ORDER[floor]",
        "REVIEW_FLOOR_ORDER[status] >= REVIEW_FLOOR_ORDER[floor]",
    )
    assert "computed_transition_state" in moved


def test_the_evidence_rule_the_trust_evaluators_read_is_walked(monkeypatch) -> None:
    """Editing ``ids_withheld_as_evidence`` (what ``trust_floor`` takes out of the graph) moves both trust rubrics that use it."""
    moved = _moved_by(
        monkeypatch,
        "app.services.scientific_read.common",
        "ids_withheld_as_evidence",
        "RecordReview.status.in_(WITHHELD_EVIDENCE_STATUSES),",
        "RecordReview.status.not_in(WITHHELD_EVIDENCE_STATUSES),",
    )
    assert {"computed_transition_state", "computed_kinetics"} <= moved


def test_a_local_import_is_followed_visible_statuses(monkeypatch) -> None:
    """Dropping the profile floor from ``visible_statuses``, reached through the import in ``declared_curvature_pins``."""
    moved = _moved_by(
        monkeypatch,
        "app.services.scientific_read.common",
        "visible_statuses",
        "include_rejected=include_rejected and profile_floor is None,",
        "include_rejected=include_rejected,",
    )
    assert "computed_transition_state" in moved


def test_the_structure_selection_chain_in_the_scoped_context_is_walked() -> None:
    code, _ = rubric_pins.reachable("computed_transition_state")
    assert "app.services.structure_selection.selection:select_entry_structures" in code


def test_the_trust_rubrics_reach_the_enum_methods_behind_orm_instances() -> None:
    """``artifact.integrity_events[-1].finding.is_break`` reaches the enum only through ORM columns."""
    code, _ = rubric_pins.reachable("computed_calculation")
    assert "class app.db.models.common:ArtifactIntegrityFinding" in code


def test_the_runtime_statmech_chooser_is_pinned(monkeypatch) -> None:
    moved = _moved_by(
        monkeypatch,
        "app.services.trust.rubrics",
        "statmech_rubric_for",
        "if statmech.species_entry_id is None and statmech.transition_state_entry_id is not None:",
        "if False:",
    )
    assert {"computed_statmech", "computed_ts_statmech"} <= moved


def test_every_evaluator_the_read_paths_call_is_pinned() -> None:
    """Each ``evaluate_*`` the read services and ``public_assessments`` pass or call is reached by a pinned rubric."""
    import ast
    import pkgutil

    import app.services.scientific_read as read_package
    from app.services import scientific_read as _  # noqa: F401

    reached: set[str] = set()
    for name in rubric_pins.GRADING_ENTRYPOINTS:
        reached |= rubric_pins.reachable(name)[0]
    reached |= rubric_pins.reachable("tckdb_reproducibility")[0]
    called: dict[str, str] = {}
    for info in pkgutil.iter_modules(read_package.__path__):
        module = importlib.import_module(f"{read_package.__name__}.{info.name}")
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            name = node.id if isinstance(node, ast.Name) else (node.attr if isinstance(node, ast.Attribute) else None)
            if name and name.startswith(("evaluate_loaded_", "evaluate_shown_", "evaluate_reproducibility")):
                target = getattr(module, name, None)
                if inspect.isfunction(target):
                    called[f"{target.__module__}:{target.__qualname__}"] = info.name
    assert called, "no evaluator found in the read services: the scan is vacuous"
    missing = sorted(key for key in called if key not in reached)
    assert not missing, f"evaluators the read paths use that no pinned rubric reaches: {missing}"


def test_fingerprints_are_identical_across_processes() -> None:
    """No object address, hash seed or absolute path may reach a fingerprint."""
    code = (
        "from app.services.trust import rubric_pins as r\n"
        "print(sorted((k, v[0], v[1]) for k, v in r.current_fingerprints().items()))\n"
    )
    outputs = []
    for seed in ("1", "2"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT / "backend",
            env={**__import__("os").environ, "PYTHONHASHSEED": seed},
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]


def test_no_object_address_reaches_the_walk() -> None:
    for name in rubric_pins.GRADING_ENTRYPOINTS:
        _, constants = rubric_pins.reachable(name)
        assert not any(" at 0x" in label for label in constants)
    assert " at 0x" not in rubric_pins.stable_dump_for_tests("tckdb_reproducibility")


def test_an_algorithm_bump_is_recorded_not_silent(tmp_path, monkeypatch, capsys) -> None:
    import json

    work = tmp_path / "pins.json"
    document = rubric_pins.load_document()
    document["algorithm"] = rubric_pins.ALGORITHM - 1
    first = next(iter(document["pins"]))
    document["pins"][first] = {next(iter(document["pins"][first])): "0" * 64}
    work.write_text(json.dumps(document), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_gen3", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    monkeypatch.setattr(generator.rubric_pins, "PINS_PATH", work)
    assert generator.main(["--check"]) == 1
    assert generator.main([]) == 0
    printed = capsys.readouterr().out
    recorded = json.loads(work.read_text())["unbumped_overrides"]
    assert {"algorithm_repin": {"from": rubric_pins.ALGORITHM - 1, "to": rubric_pins.ALGORITHM}} in recorded
    assert "algorithm_repin" in printed, "the generator must print the entry it records"


def test_module_level_sql_is_fingerprinted_not_skipped(tmp_path, monkeypatch) -> None:
    template = """
        from sqlalchemy import column, select, table
        STMT = select(column("a")).select_from(table("t", column("a"))).where(column("a") > {T})
        def runner(record): return STMT
    """
    _differs_or_refused(tmp_path, monkeypatch, textwrap.dedent(template))


def test_a_sys_modules_or_dict_lookup_is_refused(tmp_path, monkeypatch) -> None:
    for body in (
        "import sys\ndef runner(record): return sys.modules['os'].sep\n",
        "def runner(record): return runner.__globals__['x'] if False else record.__dict__['x']\n",
    ):
        with pytest.raises(Exception) as excinfo:
            _fingerprint(tmp_path, monkeypatch, {"app._pinmod": body})
        assert type(excinfo.value).__name__ == "Unfingerprintable"


def test_a_hybrid_property_on_a_visited_model_is_followed(tmp_path, monkeypatch) -> None:
    template = """
        from sqlalchemy import Integer
        from sqlalchemy.ext.hybrid import hybrid_property
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
        class B(DeclarativeBase): pass
        class Row(B):
            __tablename__ = "row"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            @hybrid_property
            def big(self): return self.id > {T}
        def runner(record): return Row
    """
    _differs_or_refused(tmp_path, monkeypatch, textwrap.dedent(template))


def test_the_getattr_allowlist_is_per_call_not_per_function() -> None:
    assert all("::" in key for key in rubric_pins.DYNAMIC_ATTRIBUTE_READS), "key each allowance by function and call"


def test_a_second_computed_getattr_in_an_allowed_function_is_refused(tmp_path, monkeypatch) -> None:
    body = """
        def runner(record):
            a = getattr(record, "x" + record.k)
            b = getattr(record, "y" + record.k)
            return a, b
    """
    key = "app._pinmod:runner :: getattr(record, 'x' + record.k)"
    monkeypatch.setitem(rubric_pins.DYNAMIC_ATTRIBUTE_READS, key, "test")
    with pytest.raises(Exception) as excinfo:
        _fingerprint(tmp_path, monkeypatch, {"app._pinmod": textwrap.dedent(body)})
    assert type(excinfo.value).__name__ == "Unfingerprintable"
    assert "'y' + record.k" in str(excinfo.value)


# --- false positives: what does not change a verdict must not move the fingerprint ----------------------------------


_HARMLESS = {
    "reworded_raise_message": (
        "def runner(record):\n    if record.value < 0:\n        raise ValueError('{T} is negative')\n    return True\n"
    ),
    "reworded_fstring_raise": (
        "def runner(record):\n    if record.value < 0:\n        raise ValueError(f'bad value {record.value} ({T})')\n"
        "    return True\n"
    ),
    "pydantic_field_description": (
        "from pydantic import BaseModel, Field\nclass M(BaseModel):\n"
        "    x: int = Field(default=1, description='{T}', title='t{T}', examples=['{T}'])\n"
        "def runner(record): return M()\n"
    ),
}


@pytest.mark.parametrize("name", sorted(_HARMLESS))
def test_harmless_wording_does_not_move_the_fingerprint(name, tmp_path, monkeypatch) -> None:
    a = _fingerprint(tmp_path, monkeypatch, {"app._pinmod": _HARMLESS[name].replace("{T}", "five")})
    b = _fingerprint(tmp_path, monkeypatch, {"app._pinmod": _HARMLESS[name].replace("{T}", "six")})
    assert a == b


def test_what_can_change_a_verdict_in_those_places_still_moves_it(tmp_path, monkeypatch) -> None:
    raise_code = "def runner(record):\n    if record.value < {T}:\n        raise ValueError('x')\n    return True\n"
    assert _fingerprint(tmp_path, monkeypatch, {"app._pinmod": raise_code.replace("{T}", "1")}) != _fingerprint(
        tmp_path, monkeypatch, {"app._pinmod": raise_code.replace("{T}", "2")}
    )
    default = (
        "from pydantic import BaseModel, Field\nclass M(BaseModel):\n    x: int = Field(default={T}, description='d')\n"
        "def runner(record): return M()\n"
    )
    assert _fingerprint(tmp_path, monkeypatch, {"app._pinmod": default.replace("{T}", "1")}) != _fingerprint(
        tmp_path, monkeypatch, {"app._pinmod": default.replace("{T}", "2")}
    )
    coded = "def runner(record):\n    raise CodedValueError('x', code='{T}')\n"
    assert _fingerprint(tmp_path, monkeypatch, {"app._pinmod": coded.replace("{T}", "a")}) != _fingerprint(
        tmp_path, monkeypatch, {"app._pinmod": coded.replace("{T}", "b")}
    )


def test_every_environment_derived_constant_is_declared() -> None:
    """Constants read from the environment at import are listed (and only those): a stale declaration is a defect too."""
    assert rubric_pins.discover_environment_constants() == sorted(rubric_pins.DECLARED_ENVIRONMENT_CONSTANTS)


def test_fingerprints_do_not_depend_on_the_deployment_environment() -> None:
    """CI sets storage endpoints and credentials; a laptop does not. The fingerprints must be the same."""
    import os

    code = (
        "from app.services.trust import rubric_pins as r\n"
        "print(sorted((k, v[0], v[1]) for k, v in r.current_fingerprints().items()))\n"
    )
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("S3_")}
    outputs = []
    for extra in ({}, {"S3_ENDPOINT_URL": "http://ci.invalid:9000", "S3_BUCKET": "ci-bucket", "S3_SECRET_KEY": "x"}):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT / "backend",
            env={**base_env, **extra},
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]


# --- round 3: declared environment constants, helpers that read the environment, error codes -----------------------------


def test_declared_environment_constants_are_only_the_storage_configuration() -> None:
    assert rubric_pins.DECLARED_ENVIRONMENT_CONSTANTS
    for name in rubric_pins.DECLARED_ENVIRONMENT_CONSTANTS:
        assert name.startswith("app.services.artifact_storage.S3_"), f"{name} is not storage configuration"


_ENV_HELPER = """
    import os
    def env_float(name, default): return float(os.environ.get(name, default))
"""


def test_a_declared_environment_constants_expression_is_fingerprinted(tmp_path, monkeypatch) -> None:
    template = """
        import os
        def _env(name, default): return os.environ.get(name, default)
        LIMIT = _env("LIMIT", {T})
        def runner(record): return record.value > LIMIT
    """
    monkeypatch.setitem(rubric_pins.DECLARED_ENVIRONMENT_CONSTANTS, "app._pinmod.LIMIT", "test configuration")
    rubric_pins._ENV_CACHE.clear()
    first = _fingerprint(tmp_path, monkeypatch, {"app._pinmod": textwrap.dedent(template).replace("{T}", "5")})
    rubric_pins._ENV_CACHE.clear()
    second = _fingerprint(tmp_path, monkeypatch, {"app._pinmod": textwrap.dedent(template).replace("{T}", "6")})
    rubric_pins._ENV_CACHE.clear()
    assert first != second, "a declared environment constant's default is code and must move the fingerprint"


def test_an_environment_read_through_an_imported_helper_is_refused(tmp_path, monkeypatch) -> None:
    body = """
        from app._envhelper import env_float
        LIMIT = env_float("LIMIT", 5)
        def runner(record): return record.value > LIMIT
    """
    rubric_pins._ENV_CACHE.clear()
    with pytest.raises(Exception) as excinfo:
        _fingerprint(
            tmp_path,
            monkeypatch,
            {"app._envhelper": textwrap.dedent(_ENV_HELPER), "app._pinmod": textwrap.dedent(body)},
        )
    rubric_pins._ENV_CACHE.clear()
    assert type(excinfo.value).__name__ == "Unfingerprintable"
    assert "environment" in str(excinfo.value)


def test_constructing_settings_inside_a_runner_is_refused(tmp_path, monkeypatch) -> None:
    body = """
        from pydantic_settings import BaseSettings
        class Local(BaseSettings):
            limit: int = 5
        def runner(record): return record.value > Local().limit
    """
    with pytest.raises(Exception) as excinfo:
        _fingerprint(tmp_path, monkeypatch, {"app._pinmod": textwrap.dedent(body)})
    assert type(excinfo.value).__name__ == "Unfingerprintable"
    assert "settings" in str(excinfo.value)


@pytest.mark.parametrize(
    "template",
    [
        "def runner(record):\n    raise CodedValueError('{T}', 'a message')\n",
        "def runner(record):\n    raise CodedValueError('x', detail='code_{T}: a message')\n",
        "def runner(record):\n    raise ValueError(f'code_{T}: bad {record}')\n",
        "def runner(record):\n    raise CodedValueError('x', code='{T}')\n",
    ],
)
def test_an_error_code_in_a_raise_still_moves_the_fingerprint(template, tmp_path, monkeypatch) -> None:
    a = _fingerprint(tmp_path, monkeypatch, {"app._pinmod": template.replace("{T}", "alpha")})
    b = _fingerprint(tmp_path, monkeypatch, {"app._pinmod": template.replace("{T}", "beta")})
    assert a != b


def test_a_reworded_message_after_the_code_does_not_move_it(tmp_path, monkeypatch) -> None:
    template = "def runner(record):\n    raise CodedValueError('same_code', detail='same_code: {T} wording')\n"
    a = _fingerprint(tmp_path, monkeypatch, {"app._pinmod": template.replace("{T}", "one")})
    b = _fingerprint(tmp_path, monkeypatch, {"app._pinmod": template.replace("{T}", "two")})
    assert a == b
