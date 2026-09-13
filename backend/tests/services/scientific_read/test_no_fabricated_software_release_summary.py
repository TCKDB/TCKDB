"""No site may synthesize a ``SoftwareReleaseSummary`` with a zero id or
an empty ref.

Before the correction-scheme-provenance plan (v1/v2), three read-layer
sites fabricated a ``SoftwareReleaseSummary`` because the row they were
projecting had no real release to point at:
``energy_correction_schemes.py``'s ``_build_software_release_summary``
(``software_release_id=0, software_release_ref=""``),
``frequency_scale_factors.py``'s same-named function, and
``statmech.py``'s ``_build_software_for_software_id``. Plan v2 PR 2
(#459) replaced the first with a real ``software_release`` join; PR 6
(the ``frequency_scale_factor`` sibling revision) replaces the other
two. After PR 6 lands, no site in the codebase should construct that
object with a sentinel id or an empty ref -- if one ever does again, a
reader either double-counts a legitimate release with id 0 or renders a
ref that can never resolve.

This guard makes that regression loud instead of silent. It is
structural (AST), not behavioural: it does not require exercising the
fabricating code path with a request, the way
``test_ecs_detail_serves_software_and_workflow_tool_release``'s mutation
check does for one call site -- it catches *any* construction site,
including one nobody thought to write an API test for. Same device as
``tests/api/test_no_row_ids_in_user_facing_text.py`` and
``tests/services/test_calculation_geometry_composition_guard.py``: parse
the source, look for the shape, refuse it everywhere at once.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[3] / "app"

_TARGET_CLASS = "SoftwareReleaseSummary"


def _constant_value(node: ast.AST):
    """Return the literal value of a constant AST node, or a sentinel."""
    if isinstance(node, ast.Constant):
        return node.value
    return _NOT_A_CONSTANT


_NOT_A_CONSTANT = object()


def _find_construction_sites() -> list[tuple[Path, ast.Call]]:
    sites: list[tuple[Path, ast.Call]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "db/models" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == _TARGET_CLASS
            ):
                sites.append((path, node))
    return sites


def test_no_site_synthesizes_a_fabricated_software_release_summary() -> None:
    sites = _find_construction_sites()
    assert sites, (
        f"found no {_TARGET_CLASS}(...) construction sites at all -- "
        "the AST walk broke"
    )

    offenders: list[str] = []
    for path, call in sites:
        rel = path.relative_to(APP_ROOT.parent)
        for kw in call.keywords:
            value = _constant_value(kw.value)
            if kw.arg == "software_release_id" and value == 0:
                offenders.append(f"{rel}:{call.lineno}: software_release_id=0")
            if kw.arg == "software_release_ref" and value == "":
                offenders.append(f'{rel}:{call.lineno}: software_release_ref=""')

    assert offenders == [], (
        f"fabricated {_TARGET_CLASS} construction(s) found: {offenders}. "
        "A missing release must be represented by returning None from the "
        "builder function, never by a sentinel id or an empty ref."
    )


def test_every_construction_site_is_reachable_from_a_release_id_parameter() -> None:
    """A softer companion check: every construction site takes its
    ``software_release_id``/``software_release_ref``/``version`` from a
    loaded row's attributes (an ``ast.Attribute``), never from a bare
    literal -- the shape a hand-fabricated summary would have even if it
    used a nonzero placeholder id instead of ``0``.

    Deliberately narrow: this only refuses a *literal* id/ref, not every
    conceivable fabrication (e.g. one built from an in-scope variable that
    happens to hold a placeholder). It exists to catch the exact defect
    this file's docstring describes reappearing in a fourth call site
    with a different placeholder value than ``0``/``""``.
    """
    sites = _find_construction_sites()
    assert sites

    offenders: list[str] = []
    for path, call in sites:
        rel = path.relative_to(APP_ROOT.parent)
        for kw in call.keywords:
            if kw.arg != "software_release_id":
                continue
            if isinstance(kw.value, ast.Constant) and kw.value.value is not None:
                offenders.append(
                    f"{rel}:{call.lineno}: software_release_id is a literal "
                    f"constant ({kw.value.value!r}), not derived from a "
                    "loaded row"
                )

    assert offenders == [], (
        f"literal software_release_id construction(s): {offenders}"
    )
