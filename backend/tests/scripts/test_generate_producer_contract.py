"""The producer contract is current, complete, and says what a producer needs.

``schemas/python/tckdb-schemas/tckdb_schemas/contract/`` is what an adapter
in another repository reads before it maps anything, so every claim below is
of the form "the contract cannot silently stop saying X":

* the committed files are exactly what the generator renders today
  (``--check``), and a wire-model edit that was not regenerated turns that red;
* every producer route of the live app is in it, and dropping one is caught;
* every client-facing refusal code is in it, and dropping one is caught;
* every example validates against both its JSON Schema and its model;
* the thermo section states the two rules the ARC adapter never learned --
  enthalpy content needs ``enthalpy_reference_kind`` (including a point
  ``g_kj_mol``) and ``reference_pressure_bar`` is never defaulted -- in text
  generated from the source, and removing that source text is caught.

Each "is caught" is a mutation test: the check is shown to fail on a
deliberately broken input, so a green here is not a check that verified
nothing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
from tckdb_schemas import contract as shipped
from tckdb_schemas.enthalpy_reference import enthalpy_reference_error

from app.api.code_catalogue import CATALOGUE
from app.schemas.workflows.thermo_upload import ThermoUploadRequest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
GENERATOR_PATH = BACKEND_ROOT / "scripts" / "generate_producer_contract.py"
PACKAGE_ROOT = REPO_ROOT / "schemas" / "python" / "tckdb-schemas"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_producer_contract", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("generate_producer_contract", module)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()


@pytest.fixture(scope="module")
def builder():
    return generator.ContractBuilder()


@pytest.fixture(scope="module")
def committed_markdown() -> str:
    return (generator.CONTRACT_DIR / generator.MARKDOWN_NAME).read_text()


# ---------------------------------------------------------------------------
# Helpers the tests assert with. Each is also run against a mutated input
# below, which is what makes its green mean something.
# ---------------------------------------------------------------------------


def missing_routes(markdown: str, routes) -> list[str]:
    """Producer routes the contract does not name, as ``METHOD path``."""
    return [info.label for info in routes if f"`{info.label}`" not in markdown]


def missing_codes(markdown: str, codes) -> list[str]:
    """Codes without an entry of their own in the refusal code reference."""
    return [code for code in codes if f"#### `{code}`" not in markdown]


def surface_section(markdown: str, model_name: str) -> str:
    start = markdown.index(f"## Surface `{model_name}`")
    end = markdown.find("\n## ", start + 1)
    return markdown[start:end] if end != -1 else markdown[start:]


#: What the thermo section must say, each paired with the phrase in the
#: *source* that says it. The phrases are lifted from
#: ``tckdb_schemas.enthalpy_reference.enthalpy_reference_error``'s docstring,
#: ``ThermoUploadRequest``'s field descriptions and its
#: ``apply_computed_origin_defaults`` docstring -- so this list is a check
#: that the generator carried them through, not a second copy of the rule.
THERMO_REQUIREMENTS: dict[str, str] = {
    "enthalpy content requires the declaration": "Enthalpy content, any of which requires ``enthalpy_reference_kind``",
    "the one accepted value": "``enthalpy_reference_kind`` accepts one value, ``formation_298k``",
    "a point Gibbs energy is enthalpy content": "a point ``g_kj_mol``",
    "a declaration without content is refused": "without all of which a declaration is refused",
    "reference pressure is never defaulted (validator)": "``reference_pressure_bar`` is NEVER defaulted, for any origin",
    "reference pressure is never defaulted (field)": "Never defaulted, for any scientific_origin",
    "1 atm is not 1 bar": "1 atm is 1.01325 bar, not 1.0",
    "the refusal code for a missing declaration": "`enthalpy_declaration_absent`",
}


def thermo_rule_gaps(markdown: str) -> list[str]:
    section = surface_section(markdown, "ThermoUploadRequest")
    return [name for name, phrase in THERMO_REQUIREMENTS.items() if phrase not in section]


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def test_committed_contract_is_in_sync() -> None:
    """The committed contract is what the code renders today.

    The failure message is the generator's own fix instructions, which name
    the version bump ``check_package_version_bump.py`` will demand.
    """
    diff = generator.diff_files(generator.render(), generator.committed_files())
    assert not diff, "\n" + diff[:8000] + "\n" + generator.FIX_INSTRUCTIONS
    assert generator.main(["--check"]) == 0


def test_the_generator_is_deterministic() -> None:
    """Two renders are byte-identical, or ``--check`` would fire at random."""
    first = generator.render()
    second = generator.render()
    assert first.keys() == second.keys()
    for name in first:
        assert first[name] == second[name], f"{name} differs between two renders"


def test_the_contract_is_stamped_with_the_package_version(committed_markdown: str) -> None:
    version = re.search(r'^version\s*=\s*"([^"]+)"', (PACKAGE_ROOT / "pyproject.toml").read_text(), re.M)
    assert version is not None
    assert f"**tckdb-schemas version:** `{version.group(1)}`" in committed_markdown


def test_an_unregenerated_wire_model_change_fails_check(tmp_path: Path) -> None:
    """Mutation: edit a wire model's source, do not regenerate, run ``--check``.

    The package is copied to a temporary directory and put first on
    ``PYTHONPATH``, so the edit is real source the generator imports -- not
    a monkeypatched object -- and the committed contract is never touched.
    """
    copy = tmp_path / "tckdb_schemas"
    shutil.copytree(PACKAGE_ROOT / "tckdb_schemas", copy)
    thermo = copy / "thermo.py"
    source = thermo.read_text()
    original = "    temperature_k: float = Field(gt=0)\n"
    assert original in source, "the mutation target moved; pick another field"
    thermo.write_text(source.replace(original, "    temperature_k: float = Field(ge=0)\n", 1))

    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), str(BACKEND_ROOT)])}
    probe = subprocess.run(
        [sys.executable, "-c", "import tckdb_schemas.thermo as t; print(t.__file__)"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert probe.stdout.strip().startswith(str(tmp_path)), probe.stdout

    result = subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--check"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "temperature_k" in result.stderr
    assert ">= 0" in result.stderr
    assert "raise `version` in" in result.stderr, "the failure must say to bump the package version"


# ---------------------------------------------------------------------------
# Completeness: routes
# ---------------------------------------------------------------------------


def test_every_producer_route_of_the_live_app_is_in_the_contract(committed_markdown: str) -> None:
    routes = [info for info in generator.discover_routes() if info.category == "producer"]
    # Guard the guard: an empty walk would make the next assertion vacuous.
    assert len(routes) >= 20, [info.label for info in routes]
    for expected in (
        "POST /api/v1/uploads/thermo",
        "POST /api/v1/uploads/computed-species",
        "POST /api/v1/uploads/computed-reaction",
        "POST /api/v1/bundles/submit",
        "POST /api/v1/calculations/{calculation_id}/artifacts",
    ):
        assert expected in {info.label for info in routes}, expected
    assert missing_routes(committed_markdown, routes) == []


def test_every_write_route_is_either_a_surface_or_listed_with_a_reason(committed_markdown: str) -> None:
    """No write route falls through both lists."""
    for info in generator.discover_routes():
        if info.category == "read":
            continue
        assert f"`{info.label}`" in committed_markdown, f"{info.label} ({info.category}) is in neither list"


def test_a_route_dropped_from_the_walk_is_reported_missing() -> None:
    """Mutation: render from a walk with one producer route removed."""
    routes = generator.discover_routes()
    dropped = next(info for info in routes if info.label == "POST /api/v1/uploads/transport")
    mutated = [info for info in routes if info is not dropped]
    markdown = generator.ContractBuilder(routes=mutated).render_markdown()
    producer = [info for info in routes if info.category == "producer"]
    assert missing_routes(markdown, producer) == [dropped.label]


# ---------------------------------------------------------------------------
# Completeness: codes
# ---------------------------------------------------------------------------


def test_every_client_facing_code_is_in_the_contract(committed_markdown: str) -> None:
    """Each client-facing code has an entry: traced to a surface, or listed as untraced."""
    codes = sorted({entry.code for entry in CATALOGUE if entry.is_client_facing})
    assert len(codes) >= 100, len(codes)
    assert missing_codes(committed_markdown, codes) == []
    untraced = committed_markdown.split("### Client-facing codes not traced to any producer route", 1)
    assert len(untraced) == 2, "the untraced-codes list is missing"


def test_a_code_dropped_from_the_contract_is_reported_missing(committed_markdown: str) -> None:
    """Mutation: remove one code's entry and show the check notices."""
    code = "enthalpy_declaration_absent"
    start = committed_markdown.index(f"#### `{code}`")
    end = committed_markdown.index("\n#### ", start + 1)
    mutated = committed_markdown[:start] + committed_markdown[end + 1 :]
    assert missing_codes(mutated, [code]) == [code]


def test_the_thermo_surface_lists_the_enthalpy_refusals(builder) -> None:
    surface = next(s for s in builder.surfaces if s.title == "ThermoUploadRequest")
    traced = builder.surface_codes(surface)
    for code in (
        "enthalpy_declaration_absent",
        "enthalpy_declaration_without_content",
        "enthalpy_quantity_not_storable_here",
        "enthalpy_reference_kind_unrecognized",
    ):
        assert code in traced, code


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


def test_every_example_validates_against_its_json_schema_and_its_model(builder) -> None:
    assert len(builder.surfaces) >= 16
    for surface in builder.surfaces:
        schema = shipped.json_schema(surface.title)
        committed = json.loads((generator.CONTRACT_DIR / surface.schema_file).read_text())
        assert schema == committed
        examples = schema.get("examples")
        assert examples, f"{surface.title}'s JSON Schema carries no example"
        validator = jsonschema.Draft202012Validator(schema)
        for example in examples:
            validator.validate(example)
            surface.model.model_validate(example)


def test_a_broken_example_fails_both_validators(builder) -> None:
    """Mutation: drop a required field; both the JSON Schema and the model refuse."""
    schema = shipped.json_schema("ThermoUploadRequest")
    broken = dict(schema["examples"][0])
    del broken["species_entry"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(broken)
    with pytest.raises(ValueError):
        ThermoUploadRequest.model_validate(broken)


def test_the_thermo_example_satisfies_the_workflow_enthalpy_rule() -> None:
    """The published example must pass the rule the contract exists to teach."""
    example = shipped.json_schema("ThermoUploadRequest")["examples"][0]
    assert enthalpy_reference_error(ThermoUploadRequest.model_validate(example)) is None
    assert example.get("reference_pressure_bar") is not None


def test_a_model_without_an_example_fails_generation(monkeypatch) -> None:
    monkeypatch.setitem(ThermoUploadRequest.model_config, "json_schema_extra", {})
    with pytest.raises(generator.ExampleError):
        generator.checked_example(ThermoUploadRequest)


# ---------------------------------------------------------------------------
# The motivating case
# ---------------------------------------------------------------------------


def test_the_thermo_section_states_the_rules_the_adapter_never_learned(committed_markdown: str) -> None:
    assert thermo_rule_gaps(committed_markdown) == []


@pytest.mark.parametrize(
    "model_name",
    ["ComputedSpeciesUploadRequest", "ComputedReactionUploadRequest", "ContributionBundleV0"],
)
def test_every_surface_that_carries_thermo_states_the_enthalpy_rule(committed_markdown: str, model_name: str) -> None:
    section = surface_section(committed_markdown, model_name)
    assert THERMO_REQUIREMENTS["enthalpy content requires the declaration"] in section
    assert THERMO_REQUIREMENTS["a point Gibbs energy is enthalpy content"] in section


def test_dropping_the_enthalpy_rule_text_is_caught(monkeypatch) -> None:
    """Mutation: the rule's docstring is gone at the source; the thermo check fails."""
    monkeypatch.setattr(enthalpy_reference_error, "__doc__", "A thermo rule.")
    markdown = generator.ContractBuilder().render_markdown()
    gaps = thermo_rule_gaps(markdown)
    assert "enthalpy content requires the declaration" in gaps
    assert "a point Gibbs energy is enthalpy content" in gaps


def test_dropping_the_reference_pressure_text_is_caught(monkeypatch) -> None:
    """Mutation: the validator docstring and the field description lose the pressure rule."""
    validator = ThermoUploadRequest.__pydantic_decorators__.model_validators["apply_computed_origin_defaults"].func
    validator = getattr(validator, "__func__", validator)
    monkeypatch.setattr(validator, "__doc__", "Fill the phase default for computed uploads.")
    monkeypatch.setattr(ThermoUploadRequest.model_fields["reference_pressure_bar"], "description", None)
    markdown = generator.ContractBuilder().render_markdown()
    gaps = thermo_rule_gaps(markdown)
    assert "reference pressure is never defaulted (validator)" in gaps
    assert "reference pressure is never defaulted (field)" in gaps


# ---------------------------------------------------------------------------
# Rules have text
# ---------------------------------------------------------------------------


def test_every_validator_in_every_payload_has_rule_text(builder) -> None:
    """A validator with no docstring, refusal message or documented helper fails generation."""
    rules = [rule for model in builder.models for rule in generator.validator_rules(model)]
    assert len(rules) >= 200, len(rules)
    assert all(rule.text.strip() for rule in rules)


def test_an_undocumented_validator_is_refused(monkeypatch) -> None:
    """Mutation: strip a validator that has only a docstring; generation must refuse."""
    validator = ThermoUploadRequest.__pydantic_decorators__.model_validators["apply_computed_origin_defaults"].func
    validator = getattr(validator, "__func__", validator)
    monkeypatch.setattr(validator, "__doc__", None)
    with pytest.raises(generator.UndocumentedRule):
        generator.validator_rules(ThermoUploadRequest)
