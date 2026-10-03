"""D7: a protium species entry's calculation geometries against their own isotope declarations.

Since #672 (``docs/adr/0022``) a ``D``/``T`` element token is an isotope
declaration, and an upload that files such a geometry under a protium species
entry is refused. Rows deposited **before** that decision are not: they hold
``D``/``T`` in ``geometry_atom.element`` with a NULL ``isotope_mass_number``,
sit under whatever entry they were deposited with, and cannot be rewritten
(``trg_as_geometry_atom``). Where such a row is attached to a protium entry the
record says two contradictory things about one nucleus, and every mass-weighted
number computed from the geometry describes an isotopologue under a protium
label.

This check finds them. It is advisory in ADR 0008's sense -- one append-only
``record_machine_review`` row a curator may read, no status, selection, trust
or approval effect, and no rewriting of anything. The Pi measured zero such rows
on 2026-10-03; self-hosted instances are unknown, which is why the check exists.

What is read: every geometry linked as an input or output of a calculation owned
by the entry. For each atom the declared nuclide is its stored mass number or,
for a legacy row, the mass number its own symbol names
(:func:`~app.chemistry.isotopes.implied_isotope_mass_number`). A nuclide that is
not the element's most abundant isotope is a declaration. Nothing is borrowed
from a sibling record.

What is judged: only entries whose ``isotope_key`` is NULL (TCKDB's all-standard
key). An isotope-labelled entry would need an atom-level correspondence the
repository does not have, so it is reported as not judged, never as agreeing.
"""
from collections import Counter

from app.chemistry.geometry import resolve_element_symbol
from app.chemistry.isotopes import implied_isotope_mass_number, most_common_isotope
from app.services.consistency.core import AdvisoryResult, encoded, finding, snapshot
from app.services.trust.rubrics import ISOTOPE_IDENTITY_CONSISTENCY_V1

RUNNER = "isotope_identity_consistency"

ENTRY_NOT_PROTIUM = "entry_declares_isotopes_not_judged"
GEOMETRY_DECLARES_ISOTOPE_ENTRY_IS_PROTIUM = "geometry_declares_isotope_entry_is_protium"
NO_CONFLICT_FOUND = "no_conflict_found"


def declared_nuclides(atoms):
    """Return ``{"<mass><element>": count}`` of the non-standard nuclides these rows declare.

    :param atoms: ``geometry_atom`` rows (or anything with ``element`` and
        ``isotope_mass_number``).
    """
    counts: Counter[str] = Counter()
    for atom in atoms:
        mass_number = atom.isotope_mass_number
        if mass_number is None:
            mass_number = implied_isotope_mass_number(atom.element)
        if mass_number is None:
            continue
        element = resolve_element_symbol(atom.element)
        if mass_number != most_common_isotope(element):
            counts[f"{mass_number}{element}"] += 1
    return dict(sorted(counts.items()))


def _linked_geometries(calculation):
    """``(role, geometry)`` pairs for one calculation, inputs then outputs, in link order."""
    linked = [("input", link.geometry) for link in calculation.input_geometries]
    linked += [("output", link.geometry) for link in calculation.output_geometries]
    return linked


def compare_isotope_identity(entry):
    """Pure comparison of one resolved species entry against its calculations' geometries."""
    refs = [entry.public_ref]
    context = {"isotope_key": entry.isotope_key}
    calculations = sorted(entry.calculations, key=lambda c: (c.public_ref or "", c.id))
    findings = []
    examined = []
    conflicts = 0
    if entry.isotope_key is not None:
        findings.append(finding(entry, {**context, "reason": ENTRY_NOT_PROTIUM}, refs))
    else:
        for calculation in calculations:
            for role, geometry in _linked_geometries(calculation):
                declared = declared_nuclides(geometry.atoms)
                examined.append({
                    "calculation": calculation.public_ref, "role": role, "geom_hash": geometry.geom_hash,
                    "atoms": [(a.atom_index, a.element.strip(), a.isotope_mass_number)
                              for a in sorted(geometry.atoms, key=lambda a: a.atom_index)],
                })
                if not declared:
                    continue
                conflicts += 1
                legacy = any(a.isotope_mass_number is None and implied_isotope_mass_number(a.element) is not None
                             for a in geometry.atoms)
                findings.append(finding(entry, {
                    **context, "reason": GEOMETRY_DECLARES_ISOTOPE_ENTRY_IS_PROTIUM,
                    "calculation_ref": calculation.public_ref, "geometry_role": role,
                    "declared_nuclides": declared, "declared_by_legacy_symbol_only": legacy,
                    "geom_hash": geometry.geom_hash,
                }, [entry.public_ref, calculation.public_ref]))
        if not conflicts:
            findings.append(finding(entry, {**context, "reason": NO_CONFLICT_FOUND,
                                            "geometries_examined": len(examined)}, refs))
    return AdvisoryResult(entry, RUNNER, ISOTOPE_IDENTITY_CONSISTENCY_V1, tuple(findings), encoded({
        "target": snapshot(entry, ("species",)),
        "geometries": examined,
        "policy": "protium-entry-only;stored-mass-else-own-symbol;non-standard-nuclide-is-a-declaration;"
                  "calculation-owned-input-and-output-geometries",
    }))
