"""The number each scientific check is printed under in the generated register document.

The register's numbers are stable identifiers other documents cite, so the order they are assigned in is a contract:
groups in :data:`READING_ORDER` first, in that order, then any other group alphabetically; within a group, the
collection order. A new group must be appended to :data:`READING_ORDER` (after every group that already exists), or it
may sort between two existing groups and renumber everything after it.
That protects against new *groups* only. A new check added to an existing, earlier group still renumbers every
entry after it; nothing here catches that, only the pin test does (it pins the opening words of every entry).
``tests/db/test_scientific_check_register.py::test_existing_entries_keep_their_numbers`` pins the published numbers.

Shared by ``backend/scripts/generate_scientific_check_register.py`` (the register document) and
``backend/scripts/generate_producer_contract.py`` (which names the entry behind each warning code).
"""

from __future__ import annotations

from app.scientific_checks import ScientificCheck
from app.scientific_checks.declarations import register

#: Groups in reading order, most fundamental claim first. A conservation law comes before atom-map bookkeeping.
READING_ORDER: tuple[str, ...] = (
    "Conservation across a reaction",
    "A structure against its own label",
    "Stationary points",
    "Atom mapping across a reaction",
    "Rate coefficients",
    "Statistical mechanics",
    "Pressure-dependent networks",
    "Custody of the evidence",
    "Reproducibility",
    # Groups added after the register was first numbered, in the order they have always printed (they used to fall
    # through to the alphabetical tail). Listing them keeps their numbers when a new group is added: a new group
    # sorting between two of them would renumber everything after it.
    "Advisory consistency",
    "External reference comparison",
    "Scan coordinates",
    "Vibrational frequency provenance",
    "Stereochemistry of a species entry",
    "Wavefunction reliability diagnostics",
)


def ordered_groups(checks: list[ScientificCheck]) -> list[str]:
    """Groups in reading order. Any group not listed in :data:`READING_ORDER` still appears, after the ones that are."""
    present = {check.group for check in checks}
    ordered = [group for group in READING_ORDER if group in present]
    ordered += sorted(present - set(ordered))
    return ordered


def number_checks(checks: list[ScientificCheck]) -> tuple[list[str], dict[int, int]]:
    """The groups in reading order, and the number each check is printed under (keyed by ``id(check)``)."""
    groups = ordered_groups(checks)
    numbering: dict[int, int] = {}
    index = 0
    for group in groups:
        for check in checks:
            if check.group == group:
                index += 1
                numbering[id(check)] = index
    return groups, numbering


def numbered_register() -> list[tuple[int, ScientificCheck]]:
    """Every register entry with the number the generated document prints for it, in number order."""
    checks = register()
    _, numbering = number_checks(checks)
    return sorted(((numbering[id(check)], check) for check in checks), key=lambda row: row[0])
