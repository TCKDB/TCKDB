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
from fastapi.routing import APIRoute
from pydantic import BaseModel, field_validator
from tckdb_schemas import contract as shipped
from tckdb_schemas.enthalpy_reference import enthalpy_reference_error

from app.api.app import create_app
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


def missing_codes(markdown: str, codes) -> list[str]:
    """Codes with neither a reference entry nor a line in the untraced list."""
    return [code for code in codes if f"#### `{code}`" not in markdown and f"\n- `{code}` (" not in markdown]


def surface_section(markdown: str, model_name: str) -> str:
    start = markdown.index(f"## Surface `{model_name}`")
    end = markdown.find("\n## ", start + 1)
    return markdown[start:end] if end != -1 else markdown[start:]


def shared_entries(markdown: str, heading: str) -> dict[str, str]:
    """anchor id -> text, for every ``<a id=...>`` entry of the ``## heading`` section."""
    start = markdown.index(f"\n## {heading}\n")
    end = markdown.find("\n## ", start + 1)
    body = markdown[start:end] if end != -1 else markdown[start:]
    parts = re.split(r'<a id="([^"]+)"></a>\n', body)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def section_with_linked_rules(markdown: str, model_name: str) -> str:
    """A surface's section plus the shared rule entries it links: what a producer reaches by reading it.

    A rule several surfaces reach is printed once, in "Checks several surfaces apply", and linked from
    each surface; following the link is part of reading the surface.
    """
    section = surface_section(markdown, model_name)
    entries = shared_entries(markdown, "Checks several surfaces apply")
    linked = [entries[anchor] for anchor in re.findall(r"\]\(#(k-[^)]+)\)", section) if anchor in entries]
    return "\n".join([section, *linked])


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
    section = section_with_linked_rules(markdown, "ThermoUploadRequest")
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
    assert f"**tckdb-schemas version `{version.group(1)}`**" in committed_markdown


def _mirror_checkout(root: Path) -> Path:
    """A throwaway checkout layout: the generator, the wire package, and ``app``.

    The generator refuses a ``tckdb_schemas`` that resolves outside its own
    checkout, so a mutated copy of the package has to sit in a checkout of its
    own. The package (with its committed contract) and the two generator files
    are copied; ``backend/app`` is a symlink to the real one, so the routes,
    catalogue and register are exactly the branch's.
    """
    package = root / "schemas" / "python" / "tckdb-schemas"
    shutil.copytree(
        PACKAGE_ROOT,
        package,
        ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__", ".pytest_cache", "tests"),
    )
    (root / "backend" / "scripts" / "lib").mkdir(parents=True)
    shutil.copy2(GENERATOR_PATH, root / "backend" / "scripts" / GENERATOR_PATH.name)
    shutil.copy2(
        BACKEND_ROOT / "scripts" / "lib" / "schemas_checkout.py",
        root / "backend" / "scripts" / "lib" / "schemas_checkout.py",
    )
    (root / "backend" / "app").symlink_to(BACKEND_ROOT / "app", target_is_directory=True)
    return package


def _run_mirrored_check(root: Path) -> subprocess.CompletedProcess[str]:
    package = root / "schemas" / "python" / "tckdb-schemas"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(package), str(root / "backend")])}
    probe = subprocess.run(
        [sys.executable, "-c", "import tckdb_schemas.thermo as t; print(t.__file__)"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert probe.stdout.strip().startswith(str(root)), probe.stdout
    return subprocess.run(
        [sys.executable, str(root / "backend" / "scripts" / GENERATOR_PATH.name), "--check"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(root),
    )


def test_an_unregenerated_wire_model_change_fails_check(tmp_path: Path) -> None:
    """Mutation: edit a wire model's source, do not regenerate, run ``--check``.

    Run in a mirrored checkout (:func:`_mirror_checkout`), so the edit is real
    source the generator imports -- not a monkeypatched object -- and the
    committed contract is never touched. The unmutated mirror is checked first:
    it must pass, or the red below could be the mirror's fault.
    """
    package = _mirror_checkout(tmp_path)
    clean = _run_mirrored_check(tmp_path)
    assert clean.returncode == 0, clean.stdout + clean.stderr

    thermo = package / "tckdb_schemas" / "thermo.py"
    source = thermo.read_text()
    original = "    temperature_k: float = Field(gt=0)\n"
    assert original in source, "the mutation target moved; pick another field"
    thermo.write_text(source.replace(original, "    temperature_k: float = Field(ge=0)\n", 1))

    result = _run_mirrored_check(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "temperature_k" in result.stderr
    assert ">= 0" in result.stderr
    assert "raise `version` in" in result.stderr, "the failure must say to bump the package version"


def test_a_foreign_schemas_package_is_refused(tmp_path: Path) -> None:
    """The generator will not render another checkout's wire models."""
    copy = tmp_path / "elsewhere"
    shutil.copytree(PACKAGE_ROOT / "tckdb_schemas", copy / "tckdb_schemas")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(copy), str(BACKEND_ROOT)])}
    result = subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--check"], capture_output=True, text=True, env=env, cwd=str(REPO_ROOT)
    )
    assert result.returncode != 0
    assert "resolves outside this checkout" in result.stderr


def test_a_version_without_a_changelog_entry_fails_generation(monkeypatch) -> None:
    """A bump with no CHANGELOG entry is refused, not papered over with a warning."""
    monkeypatch.setattr(generator, "_package_version", lambda: "99.0.0")
    with pytest.raises(generator.ChangelogError, match="99.0.0"):
        generator.ContractBuilder().render_markdown()
    assert generator.main(["--check"]) == 1


# ---------------------------------------------------------------------------
# Completeness: routes
# ---------------------------------------------------------------------------


#: Write methods, spelled here rather than imported from the generator: the
#: point of this section is a walk that does not share the generator's code.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def write_routes_with_a_body(app) -> list[str]:
    """``METHOD path`` for every route of ``app`` that writes and takes a body.

    Computed from ``app.routes`` directly, independently of the generator's
    ``discover_routes``/``classify_route``: a check that asked the generator
    which routes exist would only ever confirm the generator agrees with
    itself -- which is how dropping ``/uploads/networks`` from its walk once
    left every test green.
    """
    labels: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.body_field is None:
            continue
        for method in sorted(route.methods & _WRITE_METHODS):
            labels.append(f"{method} {route.path}")
    return labels


def routes_the_contract_omits(markdown: str, labels: list[str]) -> list[str]:
    """Routes that are neither a surface nor on the excluded-with-reason list."""
    surfaces, _, rest = markdown.partition("\n## Write routes that are not producer surfaces\n")
    excluded = rest.split("\n## ", 1)[0]
    return [
        label
        for label in labels
        if f"| `{label}` |" not in surfaces and f"| `{label}` |" not in excluded
    ]


def test_every_write_route_of_the_live_app_is_in_the_contract(committed_markdown: str) -> None:
    labels = write_routes_with_a_body(create_app())
    # Guard the guard: an empty walk would make the next assertion vacuous.
    assert len(labels) >= 60, labels
    for expected in (
        "POST /api/v1/uploads/thermo",
        "POST /api/v1/uploads/networks",
        "POST /api/v1/bundles/submit",
        "POST /api/v1/calculations/{calculation_id}/artifacts",
        "POST /api/v1/releases",
    ):
        assert expected in labels, expected
    assert routes_the_contract_omits(committed_markdown, labels) == []


def test_a_route_the_generator_stops_seeing_turns_the_check_red(monkeypatch) -> None:
    """Mutation: the generator's own walk drops ``/uploads/networks``.

    The independent walk above still expects it, so the regenerated contract
    fails the check. With the check reading the generator's walk instead,
    this mutation left every test green.
    """
    real = generator.discover_routes

    def without_networks(app=None):
        return [info for info in real(app) if info.path != "/api/v1/uploads/networks"]

    monkeypatch.setattr(generator, "discover_routes", without_networks)
    markdown = generator.ContractBuilder().render_markdown()
    labels = write_routes_with_a_body(create_app())
    assert routes_the_contract_omits(markdown, labels) == ["POST /api/v1/uploads/networks"]


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
    """Mutation: remove one traced code's entry, and one untraced code's line."""
    code = "enthalpy_declaration_absent"
    start = committed_markdown.index(f"#### `{code}`")
    end = committed_markdown.index("\n#### ", start + 1)
    mutated = committed_markdown[:start] + committed_markdown[end + 1 :]
    assert missing_codes(mutated, [code]) == [code]

    untraced = "email_taken"
    assert f"\n- `{untraced}` (" in committed_markdown, "pick a code that is still untraced"
    mutated = re.sub(rf"\n- `{untraced}` \([^\n]*", "", committed_markdown)
    assert missing_codes(mutated, [untraced]) == [untraced]


#: The contract was ~1 MB before it was trimmed; an agent cannot read that
#: whole. This is a ceiling to notice regrowth, not a target.
#:
#: History, each step a trim or a raise: 700_000 with codes on eight or more surfaces printed once
#: (composite-scheme work, ADR 0021); 705_000 for #638 (every register check printed per function);
#: 708_500 for the thermo target and protocol declarations (#671), after a first trim to a four-surface
#: threshold. Every one of them was a fight with a layout that printed a rule, a refusal code and a
#: nested-model list once per upload surface.
#:
#: #681 ended that: anything two or more surfaces share (a check, a marked rule, a refusal code, a
#: nested model) is printed once and linked, at any surface count. The file went from 708_183 to
#: 677_741 bytes, 4.3 percent, with no change to what any surface lists (the equivalence tests below).
#: The ceiling is that size plus about 3 percent, 700_000. The margin is deliberately not 10 percent:
#: a ceiling exists to notice regrowth, and 10 percent (68 KB) is about eight of the contract-touching
#: pull requests that each hit the old one, so none would be asked to trim. 22 KB covers two such
#: pull requests landing together; a third has to find savings first.
MARKDOWN_BYTE_CEILING = 700_000


def test_the_contract_stays_readable_in_pieces(committed_markdown: str) -> None:
    size = len(committed_markdown.encode("utf-8"))
    assert size < MARKDOWN_BYTE_CEILING, f"{size} bytes; trim before raising the ceiling"
    longest = max(len(line) for line in committed_markdown.splitlines() if not line.lstrip().startswith('"content_base64"'))
    assert longest < 2000, longest


# ---------------------------------------------------------------------------
# Printed once, listed everywhere (#681)
#
# A rule, a refusal code or a nested model that several surfaces share is printed once and linked from
# each of them. What each surface *says it has* must not change with that: the helpers below read the
# rendered markdown back, following the links, and compare it with what the generator knows.
# ---------------------------------------------------------------------------


def _subsection(section: str, heading: str) -> str:
    start = section.index(f"\n### {heading}\n")
    end = section.find("\n### ", start + 1)
    return section[start:end] if end != -1 else section[start:]


def listed_by_surface(markdown: str, title: str) -> dict[str, list[str]]:
    """What a surface's section lists, read back from the markdown with its shared links followed.

    ``codes``: its own table plus every row of each code group it links. ``rules``: the function key of
    every rule bullet, a linked one resolved through its shared entry. ``models``: its own nested-model
    bullets plus every bullet of each model group it links. Lists, not sets, so a double listing shows.
    """
    section = surface_section(markdown, title)
    code_groups = shared_entries(markdown, "Refusal codes several surfaces share")
    model_groups = shared_entries(markdown, "Nested models several surfaces share")
    rule_entries = shared_entries(markdown, "Checks several surfaces apply")

    codes_text = _subsection(section, "Refusal codes this surface can return")
    codes = re.findall(r"(?m)^\| \[`([^`]+)`\]", codes_text)
    for anchor in re.findall(r"\]\(#(cg-\d+)\)", codes_text):
        codes += re.findall(r"(?m)^\| \[`([^`]+)`\]", code_groups[anchor])

    rules_text = _subsection(section, "Rules the workflow applies")
    rules = re.findall(r"(?m)^- \*\*`([^`]+)`\*\*", rules_text)
    for anchor in re.findall(r"(?m)^- \[`[^`]+`\]\(#(k-[^)]+)\)", rules_text):
        rules.append(re.search(r"(?m)^`([^`]+)`[. ]", rule_entries[anchor]).group(1))

    fields_text = _subsection(section, "Payload fields")
    models: list[str] = []
    if "Nested models (" in fields_text:
        nested = fields_text[fields_text.index("Nested models (") :]
        models = re.findall(r"(?m)^- \[`([^`]+)`\]\(#m-", nested)
        for anchor in re.findall(r"\]\(#(mg-\d+)\)", nested):
            models += re.findall(r"(?m)^- \[`([^`]+)`\]", model_groups[anchor])
    return {"codes": codes, "rules": rules, "models": models}


def listing_gaps(builder, markdown: str) -> list[str]:
    """Every difference between what each surface lists and what the generator's model says it has."""
    common = set(builder.global_trace.code_sites)
    gaps: list[str] = []
    for surface in builder.surfaces:
        expected = {
            "codes": {code for code in builder.surface_codes(surface) if code not in common},
            "rules": {key for key, _func, _check in surface.workflow_rules},
            "models": {builder.names.display(model) for model in surface.closure[1:]},
        }
        listed = listed_by_surface(markdown, surface.title)
        for kind, want in expected.items():
            got = listed[kind]
            if len(got) != len(set(got)):
                gaps.append(f"{surface.title}: {kind} listed twice: {sorted(c for c in set(got) if got.count(c) > 1)}")
            if set(got) != want:
                gaps.append(
                    f"{surface.title}: {kind} missing {sorted(want - set(got))} extra {sorted(set(got) - want)}"
                )
    return gaps


def test_every_surface_lists_exactly_the_codes_checks_and_models_it_has(builder, committed_markdown: str) -> None:
    """The equivalence proof: sharing a block changed the layout, not what any surface lists."""
    assert listing_gaps(builder, committed_markdown) == []
    # Not vacuous: the comparison covers real, shared content.
    listed = [listed_by_surface(committed_markdown, s.title) for s in builder.surfaces]
    assert len(listed) >= 16
    assert sum(len(item["codes"]) for item in listed) > 400
    assert sum(len(item["rules"]) for item in listed) > 100
    assert sum(len(item["models"]) for item in listed) > 600


def test_every_shared_group_is_carried_by_every_surface_it_names(builder) -> None:
    for groups, _alone in (builder.code_groups(), builder.model_groups()):
        assert groups
        for group in groups:
            assert len(group.titles) >= 2 and group.items
        # A group is the whole of what its surfaces have in common: no item is in two groups.
        items = [item for group in groups for item in group.items]
        assert len(items) == len(set(items))
    for group in builder.code_groups()[0]:
        for title in group.titles:
            surface = next(s for s in builder.surfaces if s.title == title)
            assert set(group.items) <= set(builder.surface_codes(surface))


def _drop_first_line_matching(markdown: str, title: str, subsection: str, pattern: str) -> str:
    """Delete one line of a surface's subsection, so the surface no longer lists one thing."""
    section = surface_section(markdown, title)
    block = _subsection(section, subsection)
    lines = block.split("\n")
    index = next(i for i, line in enumerate(lines) if re.search(pattern, line))
    mutated_block = "\n".join(lines[:index] + lines[index + 1 :])
    return markdown.replace(block, mutated_block, 1)


@pytest.mark.parametrize(
    ("subsection", "pattern"),
    [
        ("Rules the workflow applies", r"^- \[`[^`]+`\]\(#k-"),
        ("Refusal codes this surface can return", r"\]\(#cg-\d+\)"),
        ("Payload fields", r"\]\(#mg-\d+\)"),
    ],
    ids=["shared rule", "code group", "model group"],
)
def test_a_surface_dropping_its_link_to_a_shared_block_is_caught(
    builder, committed_markdown: str, subsection: str, pattern: str
) -> None:
    """Mutation: one surface loses its reference to a shared block; the equivalence check must fail."""
    mutated = _drop_first_line_matching(committed_markdown, "ThermoUploadRequest", subsection, pattern)
    assert mutated != committed_markdown
    assert listing_gaps(builder, mutated), "the surface lost a reference and nothing noticed"


def test_a_code_listed_twice_on_a_surface_is_caught(builder, committed_markdown: str) -> None:
    """Mutation: a code in a group the surface links is also put in its own table."""
    title = "ThermoUploadRequest"
    groups = shared_entries(committed_markdown, "Refusal codes several surfaces share")
    codes_text = _subsection(surface_section(committed_markdown, title), "Refusal codes this surface can return")
    anchor = re.search(r"\]\(#(cg-\d+)\)", codes_text).group(1)
    row = re.search(r"(?m)^\| \[`[^`]+`\].*$", groups[anchor]).group(0)
    mutated = committed_markdown.replace(codes_text, codes_text.rstrip("\n") + "\n" + row + "\n", 1)
    assert any("listed twice" in gap for gap in listing_gaps(builder, mutated))


def repeated_bodies(builder, markdown: str) -> list[str]:
    """Shared rule bodies printed other than exactly once, in the shared section and nowhere else.

    The shared section carries each body once; a surface section carrying it again is the repeat
    sharing exists to prevent.
    """
    start = markdown.index("\n## Checks several surfaces apply\n")
    shared = markdown[start : markdown.index("\n## ", start + 1)]
    surfaces = markdown[markdown.index("\n## Surface `") : markdown.index("\n## Model reference\n")]
    found: list[str] = []
    for key, (func_checks, _titles) in builder.shared_checks().items():
        for check in func_checks:
            text = generator._one_line(check.asserts)
            if shared.count(text) != 1 or text in surfaces:
                found.append(f"{key}: check body")
    for key, (func, _titles) in builder.shared_producer_rules().items():
        text = "\n".join(generator._indent_block(generator._own_doc(func) or ""))
        if shared.count(text) != 1 or text in surfaces:
            found.append(f"{key}: rule body")
    return found


def test_a_shared_rule_body_is_printed_once(builder, committed_markdown: str) -> None:
    assert repeated_bodies(builder, committed_markdown) == []
    # Not vacuous: there are shared checks and shared marked rules to count, including the enthalpy
    # rule that used to be printed in full on every thermo surface.
    assert len(builder.shared_checks()) >= 10
    assert "tckdb_schemas.enthalpy_reference:enthalpy_reference_error" in builder.shared_producer_rules()


def test_printing_a_shared_body_twice_is_caught(builder, committed_markdown: str) -> None:
    """Mutation: a surface prints a shared body itself, as the generator did before #681; or the shared section does."""
    rule_key, (func, _titles) = next(iter(builder.shared_producer_rules().items()))
    rule_body = "\n".join(generator._indent_block(generator._own_doc(func) or ""))
    check_key, (func_checks, _t) = next(iter(builder.shared_checks().items()))
    check_body = generator._one_line(func_checks[0].asserts)
    in_a_surface = "### Rules the workflow applies\n"
    for body, expected in ((rule_body, f"{rule_key}: rule body"), (check_body, f"{check_key}: check body")):
        mutated = committed_markdown.replace(in_a_surface, in_a_surface + "\n" + body + "\n", 1)
        assert mutated != committed_markdown
        assert expected in repeated_bodies(builder, mutated)
        in_the_shared_section = committed_markdown.replace(body, body + "\n\n" + body, 1)
        assert expected in repeated_bodies(builder, in_the_shared_section)


def dangling_links(markdown: str) -> set[str]:
    anchors = set(re.findall(r'<a id="([^"]+)"', markdown))
    headings = {re.sub(r"[^a-z0-9]+", "-", h.lower()).strip("-") for h in re.findall(r"(?m)^#+ (.*)$", markdown)}
    return {link for link in re.findall(r"\]\(#([^)]+)\)", markdown) if link not in anchors | headings}


def test_every_link_to_a_shared_block_resolves(committed_markdown: str) -> None:
    """A surface that links a group or a rule is only as good as the entry the link reaches.

    Links to a refusal code (``#c-...``) are left out: a check's code that no producer route was traced to
    has no entry in the reference, and the shared check entries have always linked it regardless.
    """
    unresolved = {link for link in dangling_links(committed_markdown) if not link.startswith("c-")}
    assert unresolved == set()
    links = re.findall(r"\]\(#((?:k|cg|mg|s|m)-[^)]+)\)", committed_markdown)
    assert len(links) > 800, len(links)


def test_a_link_to_a_missing_group_is_caught(committed_markdown: str) -> None:
    """Mutation: a group entry is renamed away; the links to it dangle."""
    mutated = committed_markdown.replace('<a id="cg-1"></a>', "", 1)
    assert "cg-1" in dangling_links(mutated)


def test_a_repeated_body_would_breach_the_ceiling(committed_markdown: str) -> None:
    """Mutation: print every shared entry once more per surface; the size test must fail."""
    entries = shared_entries(committed_markdown, "Checks several surfaces apply")
    extra = "".join(entries.values()) * 16
    assert len((committed_markdown + extra).encode("utf-8")) >= MARKDOWN_BYTE_CEILING


def test_the_thermo_rules_are_within_a_screen_of_the_top(committed_markdown: str) -> None:
    top = "\n".join(committed_markdown.splitlines()[:40])
    thermo_row = next(line for line in top.splitlines() if line.startswith("| [`ThermoUploadRequest`]"))
    assert "must declare what its enthalpies mean" in thermo_row
    assert "`reference_pressure_bar`" in thermo_row
    assert "`enthalpy_declaration_absent`" in thermo_row


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
    section = section_with_linked_rules(committed_markdown, model_name)
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


# ---------------------------------------------------------------------------
# "can refuse" is printed only where a refusal is found, through helpers too
# ---------------------------------------------------------------------------


def _rule(model, name: str):
    return next(rule for rule in generator.validator_rules(model) if rule.name == name)


def test_a_check_that_refuses_through_a_helper_is_labelled_can_refuse() -> None:
    """The two cases the review demonstrated were printed as "no refusal".

    Both validators refuse only inside a helper they call, which a scan of
    their own body cannot see.
    """
    from tckdb_schemas.fragments.artifact import ArtifactIn

    from app.schemas.workflows.kinetics_upload import KineticsUploadRequest

    kinetics = _rule(KineticsUploadRequest, "validate_a_units_vs_molecularity")
    artifact = _rule(ArtifactIn, "_check_filename")
    assert kinetics.refusal_site and kinetics.refusal_site.endswith(":validate_a_units_for_molecularity")
    assert artifact.refusal_site and artifact.refusal_site.endswith(":_validate_filename")


def test_the_contract_never_claims_a_check_cannot_refuse(committed_markdown: str) -> None:
    """Absence of a found refusal is not printed as a claim."""
    assert "no refusal" not in committed_markdown
    assert "adjusts values" not in committed_markdown


def _quiet_helper(value: str) -> str:
    return value


def _refusing_helper(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


class _Probe(BaseModel):
    """A model whose only rule delegates to a module-level helper."""

    value: str

    @field_validator("value")
    @classmethod
    def check_value(cls, value: str) -> str:
        """Pass ``value`` through the helper."""
        return _quiet_helper(value)


def test_making_the_helper_raise_changes_the_label(monkeypatch) -> None:
    """Mutation: the helper starts refusing; the label must follow it."""
    monkeypatch.setattr(generator, "_in_scope", lambda obj: getattr(obj, "__module__", "") == __name__)
    assert _rule(_Probe, "check_value").refusal_site is None

    monkeypatch.setitem(globals(), "_quiet_helper", _refusing_helper)
    assert _rule(_Probe, "check_value").refusal_site == f"{__name__}:_refusing_helper"


def test_a_function_that_enforces_several_checks_keeps_every_one(builder, committed_markdown) -> None:
    """The TS evidence seam enforces four register checks; a single-valued map kept only the last."""
    key = "app.services.transition_state_validation:persist_transition_state_validation_evidence"
    codes = {check.code for check in builder.check_by_func[key]}
    assert {
        "transition_state_missing_irc_evidence",
        "transition_state_energy_ordering_mixed_levels",
        "ts_energy_ordering_stated_energy_mismatch",
        "transition_state_energy_ordering_not_compared",
    } <= codes
    section = committed_markdown.split("### `persist_transition_state_validation_evidence`", 1)[1].split("\n### ", 1)[0]
    assert f"enforces {len(codes)} checks" in section
    for code in codes:
        assert f"(#{generator._anchor('c', code)})" in section, code
