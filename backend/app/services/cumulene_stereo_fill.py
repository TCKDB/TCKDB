"""Fill the allene and odd-cumulene positions of existing species-entry descriptors from their geometries (#827).

Before allene ``Ra``/``Sa`` and cumulene ``E``/``Z`` were representable, every such element of every entry was
``?``. The data step of the migration that introduced the tokens re-reads each such entry from the geometries
linked to it, with the entry's own descriptor as the supplied label, so nothing already known can change:

* every linked geometry agrees: the ``?`` positions the geometries settle are filled in place;
* the geometries disagree (``species_stereo_contradiction``): the entry is listed and left alone;
* no geometry is linked, or none settles the element: the entry is left alone;
* a merged-away entry (``merged_into_species_entry_id`` set) is frozen: listed, never touched;
* a fill that would give an entry the descriptor another live entry of the same species already holds stops the
  whole step with a list. Existing data is development data: stopping is preferred to merging.

``species_entry`` has no accepted-science guard (checked at run time, see :func:`assert_no_accepted_science_guard`),
so the update is a plain ``UPDATE``. The merged-row freeze trigger is never bypassed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from app.api.error_contract import CodedValueError
from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles

_CHAIN = ("axial", "cumulene")


@dataclass
class FillReport:
    examined: int = 0
    changes: list[tuple[str, str, str]] = field(default_factory=list)  # (entry ref, old, new)
    merged_skipped: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    without_geometry: int = 0
    unsettled: int = 0
    stale_machine_reviews: int = 0
    stale_record_reviews: int = 0

    def summary(self) -> str:
        return (
            f"{len(self.changes)} filled, {self.unsettled} left (geometries do not settle the element), "
            f"{self.without_geometry} left (no linked geometry), {len(self.disagreements)} left (geometries "
            f"disagree), {len(self.unreadable)} left (descriptor unreadable), {len(self.merged_skipped)} merged "
            f"row(s) skipped, {self.examined} candidate(s) examined"
        )


def assert_no_accepted_science_guard(bind) -> None:
    """Stop if ``species_entry`` ever gains an accepted-science guard: this step would then need a declared repair."""

    guarded = bind.execute(
        text(
            "SELECT t.tgname FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
            "WHERE t.tgrelid = 'public.species_entry'::regclass AND NOT t.tgisinternal "
            "AND p.proname IN ('tckdb_guard_accepted_root', 'tckdb_guard_accepted_child', "
            "'tckdb_guard_accepted_via_child', 'tckdb_guard_calculation_geometry', 'tckdb_reject_mutation')"
        )
    ).scalars().all()
    if guarded:
        raise RuntimeError(
            f"species_entry now carries an accepted-science guard ({', '.join(guarded)}); this data step has to "
            "declare a repair for stereo_descriptor, stereo_resolution, stereo_label and stereo_smiles instead of "
            "a plain UPDATE. It never bypasses a trigger."
        )


def _geometries(bind, entry_id: int) -> list[tuple[str, dict[int, int]]]:
    rows = bind.execute(
        text(
            "SELECT g.id, g.xyz_text FROM geometry g WHERE g.id IN ("
            "  SELECT o.geometry_id FROM calculation c JOIN calculation_output_geometry o ON o.calculation_id = c.id "
            "  WHERE c.species_entry_id = :e "
            "  UNION SELECT i.geometry_id FROM calculation c JOIN calculation_input_geometry i ON i.calculation_id = c.id "
            "  WHERE c.species_entry_id = :e) ORDER BY g.id"
        ),
        {"e": entry_id},
    ).all()
    out: list[tuple[str, dict[int, int]]] = []
    for row in rows:
        atoms = bind.execute(
            text(
                "SELECT atom_index, element, x, y, z, isotope_mass_number FROM geometry_atom "
                "WHERE geometry_id = :g ORDER BY atom_index"
            ),
            {"g": row.id},
        ).all()
        if not atoms:
            continue
        xyz = row.xyz_text or "\n".join(f"{a.element} {a.x} {a.y} {a.z}" for a in atoms)
        out.append((xyz, {a.atom_index: a.isotope_mass_number for a in atoms if a.isotope_mass_number}))
    return out


def fill_cumulene_stereo(bind, *, revision: str, apply: bool = True) -> FillReport:
    """Re-read every entry with an unknown cumulene element from its geometries; fill the unanimous ones.

    :param bind: A SQLAlchemy connection.
    :param revision: Named in the printed lines.
    :param apply: ``False`` only reports.
    :raises RuntimeError: When a fill would collide with an existing live entry (or with another fill).
    """

    assert_no_accepted_science_guard(bind)
    report = FillReport()
    rows = bind.execute(
        text(
            "SELECT e.id, e.public_ref, e.species_id, e.stereo_descriptor, e.stereo_label, "
            "       e.merged_into_species_entry_id AS merged_into, "
            "       e.electronic_state_kind::text AS state_kind, e.electronic_state_label, e.term_symbol, "
            "       e.isotope_key, mol_to_smiles(e.mol)::text AS mol_smiles, s.smiles AS species_smiles "
            "FROM species_entry e JOIN species s ON s.id = e.species_id "
            "WHERE e.stereo_descriptor LIKE '%?%' AND s.kind::text = 'molecule' ORDER BY e.id"
        )
    ).all()
    planned: list[tuple[object, stereo.ResolvedStereo]] = []
    for row in rows:
        try:
            frame = stereo.frame_from_mol(identity_mol_from_smiles(row.mol_smiles or row.species_smiles))
        except (ValueError, CodedValueError):
            continue
        tokens = row.stereo_descriptor.split(",")
        settleable = [e for e in frame.elements if e.kind in _CHAIN and not e.never_settled]
        mismatch = len(tokens) != len(frame.elements)
        if not settleable and not mismatch:
            continue
        report.examined += 1
        if row.merged_into is not None:
            report.merged_skipped.append(row.public_ref)
            continue
        if mismatch:
            if any(t != stereo.UNKNOWN for t in tokens):
                report.unreadable.append(
                    f"{row.public_ref}: descriptor {row.stereo_descriptor!r} has {len(tokens)} token(s) but the "
                    f"molecule now has {len(frame.elements)} stereo element(s)"
                )
                continue
            label = None  # all unknown: nothing to carry over
        else:
            label = row.stereo_descriptor
        geometries = _geometries(bind, row.id)
        if not geometries:
            report.without_geometry += 1
            if not mismatch:
                continue
        try:
            resolved = stereo.resolve_stereo(frame, geometries=geometries, supplied_label=label, use_marks=False)
        except CodedValueError as exc:
            if exc.code == stereo.W_SPECIES_STEREO_CONTRADICTION:
                report.disagreements.append(f"{row.public_ref}: {exc}")
            else:
                report.unreadable.append(f"{row.public_ref}: {exc}")
            continue
        if resolved.descriptor == row.stereo_descriptor:
            report.unsettled += 1
            continue
        planned.append((row, resolved))

    taken: dict[tuple, str] = {}
    collisions: list[str] = []
    for row, resolved in planned:
        key = (row.species_id, resolved.descriptor, row.state_kind, row.electronic_state_label, row.term_symbol, row.isotope_key)
        other = bind.execute(
            text(
                "SELECT public_ref FROM species_entry WHERE species_id = :sp AND stereo_descriptor = :d "
                "AND electronic_state_kind::text IS NOT DISTINCT FROM :k "
                "AND electronic_state_label IS NOT DISTINCT FROM :l AND term_symbol IS NOT DISTINCT FROM :t "
                "AND isotope_key IS NOT DISTINCT FROM :i AND merged_into_species_entry_id IS NULL AND id <> :id"
            ),
            {
                "sp": row.species_id,
                "d": resolved.descriptor,
                "k": row.state_kind,
                "l": row.electronic_state_label,
                "t": row.term_symbol,
                "i": row.isotope_key,
                "id": row.id,
            },
        ).scalar()
        if other is None and key in taken:
            other = taken[key]
        if other is not None:
            collisions.append(f"{row.public_ref} would become {resolved.descriptor!r}, which {other} already holds")
        taken[key] = row.public_ref
    if collisions:
        raise RuntimeError(
            f"[{revision}] {len(collisions)} filled descriptor(s) would collide with a live entry of the same "
            "species and state; merge or correct them first (this step never merges): " + "; ".join(collisions)
        )

    changed_ids: list[int] = []
    for row, resolved in planned:
        report.changes.append((row.public_ref, row.stereo_descriptor, resolved.descriptor))
        if resolved.label != row.stereo_label:
            changed_ids.append(row.id)
        if apply:
            bind.execute(
                text(
                    "UPDATE species_entry SET stereo_descriptor = :d, stereo_resolution = CAST(:r AS stereo_resolution), "
                    "stereo_label = :label, stereo_smiles = :smiles WHERE id = :id AND merged_into_species_entry_id IS NULL"
                ),
                {
                    "d": resolved.descriptor,
                    "r": resolved.resolution.value,
                    "label": resolved.label,
                    "smiles": resolved.stereo_smiles,
                    "id": row.id,
                },
            )
    if changed_ids:
        report.stale_machine_reviews = bind.execute(
            text(
                "SELECT count(*) FROM record_machine_review "
                "WHERE record_type::text = 'species_entry' AND record_id = ANY(:ids)"
            ),
            {"ids": changed_ids},
        ).scalar_one()
        report.stale_record_reviews = bind.execute(
            text(
                "SELECT count(*) FROM record_review WHERE record_type::text = 'species_entry' AND record_id = ANY(:ids)"
            ),
            {"ids": changed_ids},
        ).scalar_one()
    return report


def print_report(report: FillReport, revision: str) -> None:
    for ref, old, new in report.changes:
        print(f"[{revision}] species_entry stereo filled: {ref}: {old} -> {new}")
    if not report.changes:
        print("0 changed")
    for ref in report.merged_skipped:
        print(f"[{revision}] merged species_entry skipped (frozen): {ref}")
    for line in report.disagreements:
        print(f"[{revision}] geometries disagree, entry left as it was: {line}")
    for line in report.unreadable:
        print(f"[{revision}] entry left as it was: {line}")
    print(f"[{revision}] cumulene stereo fill: {report.summary()}")
    if report.stale_machine_reviews or report.stale_record_reviews:
        print(
            f"[{revision}] {report.stale_machine_reviews} machine review(s) and {report.stale_record_reviews} "
            "record review(s) sit on filled entries whose stereo_label changed; stereo_label is part of the "
            "species_entry digest (stereo_descriptor, stereo_resolution and stereo_smiles are not), so those "
            "reviews will read as stale."
        )
    else:
        print(f"[{revision}] no stored review sits on an entry whose stereo_label changed, so none restales")
