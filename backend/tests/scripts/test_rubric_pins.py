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
    """An unbumped re-pin leaves a trace; the test prints each so a reviewer sees them."""
    overrides = [o for o in rubric_pins.load_overrides() if "name" in o]
    for entry in overrides:
        assert str(entry.get("reason", "")).strip(), f"unbumped override without a reason: {entry}"
    if overrides:  # pragma: no cover - empty today
        print("unbumped overrides:", overrides)


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
    assert recorded == [{"name": name, "version": int(version), "reason": "wording only in a message"}]
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
