"""Tests for the correction-scheme-provenance widening of
``resolve_or_create_scheme`` and its warning collector.

Covers the citation-not-discarded fix (v1 §1.3/§3), the software-release
dimension and unit dimension v2 added (``docs/plans/
correction-scheme-provenance.md`` §3-§5.1), and the
``uq_energy_correction_scheme_identity`` unique index the resolver's
lookup must match.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.energy_correction import EnergyCorrectionScheme
from app.schemas.workflows.energy_correction_upload import EnergyCorrectionSchemeRef
from app.services.energy_correction_resolution import resolve_or_create_scheme
from app.services.provenance_warnings import (
    W_AMBIGUOUS_ENERGY_CORRECTION_SCHEME_WITHOUT_LITERATURE,
    W_MISSING_ENERGY_CORRECTION_SCHEME_SOFTWARE,
    W_MISSING_LITERATURE_PROVENANCE,
)

_LOT = {"method": "B3LYP", "basis": "def2-TZVP"}


def _aec_ref(**overrides) -> EnergyCorrectionSchemeRef:
    base = {
        "kind": "atom_energy",
        "name": "AEC provenance test",
        "level_of_theory": dict(_LOT),
        "version": "1.0",
        "units": "hartree",
    }
    base.update(overrides)
    return EnergyCorrectionSchemeRef(**base)


# ---------------------------------------------------------------------------
# Citation is never lost (§1.3/§3)
# ---------------------------------------------------------------------------


def test_supplied_citation_on_existing_uncited_identity_is_not_discarded(
    db_conn,
) -> None:
    """The bug this plan closes: a citation supplied against an identity
    that already exists (uncited) must not be silently dropped — it must
    resolve to (or create) a row that actually carries it."""
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_scheme(session, _aec_ref())
        assert first.source_literature_id is None

        second = resolve_or_create_scheme(
            session,
            _aec_ref(
                source_literature={"kind": "article", "title": "Petersson 1998"}
            ),
        )

        # Different scheme identity: a citation makes this scientifically
        # distinct, not a silent overwrite of the first row.
        assert second.id != first.id
        assert second.source_literature_id is not None
        assert second.source_literature.title == "Petersson 1998"

        # The original, uncited row is untouched.
        session.refresh(first)
        assert first.source_literature_id is None

        count = session.scalar(
            select(func.count()).select_from(EnergyCorrectionScheme).where(
                EnergyCorrectionScheme.kind == "atom_energy",
                EnergyCorrectionScheme.name == "AEC provenance test",
            )
        )
        assert count == 2


def test_repeated_upload_with_same_citation_reuses_the_cited_row(db_conn) -> None:
    """Once a citation exists on a row, re-supplying the *same* one reuses
    it rather than creating a third row.

    Uses a DOI-bearing citation because ``resolve_or_create_literature``
    only dedups identifier-driven literature (DOI/ISBN); two manual
    citations with the same title are deliberately never merged (house
    literature policy), so they would correctly create two distinct
    scheme rows -- a different fact than the one this test checks.
    """
    citation = {
        "doi": "10.1021/petersson1998",
        "kind": "article",
        "title": "Petersson 1998",
    }
    with Session(db_conn) as session, session.begin():
        cited = resolve_or_create_scheme(
            session, _aec_ref(source_literature=dict(citation))
        )
        again = resolve_or_create_scheme(
            session, _aec_ref(source_literature=dict(citation))
        )
        assert again.id == cited.id


# ---------------------------------------------------------------------------
# Software is a real identity dimension (§2.1, §3)
# ---------------------------------------------------------------------------


def test_two_schemes_same_identity_different_software_are_distinct_rows(
    db_conn,
) -> None:
    """A Gaussian scheme and an ORCA scheme at the same (kind, lot, version)
    are not a collision -- they coexist as two distinct, individually
    correct rows (plan §2.1)."""
    with Session(db_conn) as session, session.begin():
        gaussian = resolve_or_create_scheme(
            session, _aec_ref(software={"name": "Gaussian"})
        )
        orca = resolve_or_create_scheme(
            session, _aec_ref(software={"name": "ORCA"})
        )

        assert gaussian.id != orca.id
        assert gaussian.software_release.software.name == "Gaussian"
        assert orca.software_release.software.name == "ORCA"

        # Re-supplying the same software reuses the same row.
        again = resolve_or_create_scheme(
            session, _aec_ref(software={"name": "Gaussian"})
        )
        assert again.id == gaussian.id


def test_two_fully_uncited_software_less_schemes_still_collapse(db_conn) -> None:
    """Residual ambiguity (plan §2.4): two schemes NULL on every widened
    dimension still collapse into one row, exactly as before the widening
    -- ``NULLS NOT DISTINCT`` still treats them as duplicates."""
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_scheme(session, _aec_ref())
        second = resolve_or_create_scheme(session, _aec_ref())
        assert first.id == second.id


def test_workflow_tool_release_is_also_part_of_the_widened_identity(
    db_conn,
) -> None:
    with Session(db_conn) as session, session.begin():
        with_wtr = resolve_or_create_scheme(
            session,
            _aec_ref(workflow_tool_release={"name": "ARC", "version": "1.1.0"}),
        )
        without_wtr = resolve_or_create_scheme(session, _aec_ref())

        assert with_wtr.id != without_wtr.id
        assert with_wtr.workflow_tool_release is not None
        assert with_wtr.workflow_tool_release.workflow_tool.name == "ARC"


# ---------------------------------------------------------------------------
# Warnings collector (§4)
# ---------------------------------------------------------------------------


def test_new_uncited_software_less_scheme_gets_both_missing_warnings(
    db_conn,
) -> None:
    with Session(db_conn) as session, session.begin():
        warnings: list = []
        resolve_or_create_scheme(session, _aec_ref(), warnings_out=warnings)

        codes = {w.code for w in warnings}
        assert W_MISSING_LITERATURE_PROVENANCE in codes
        assert W_MISSING_ENERGY_CORRECTION_SCHEME_SOFTWARE in codes


def test_atom_hf_kind_never_gets_the_software_warning(db_conn) -> None:
    """atom_hf/atom_thermal/soc are physical/reference constants; the
    software axis does not apply to them (plan §1.6)."""
    with Session(db_conn) as session, session.begin():
        warnings: list = []
        ref = EnergyCorrectionSchemeRef(
            kind="atom_hf",
            name="Atom HF reference",
            level_of_theory=dict(_LOT),
        )
        resolve_or_create_scheme(session, ref, warnings_out=warnings)

        codes = {w.code for w in warnings}
        assert W_MISSING_ENERGY_CORRECTION_SCHEME_SOFTWARE not in codes


def test_reuse_of_an_existing_row_emits_no_repeat_warning(db_conn) -> None:
    """Warnings fire only for a row this call actually created -- reusing
    an existing (already-warned-about) row does not repeat the message on
    every future deposit that cites it."""
    with Session(db_conn) as session, session.begin():
        first_warnings: list = []
        resolve_or_create_scheme(session, _aec_ref(), warnings_out=first_warnings)
        assert first_warnings  # sanity: the first create did warn

        second_warnings: list = []
        resolve_or_create_scheme(session, _aec_ref(), warnings_out=second_warnings)
        assert second_warnings == []


def test_ambiguous_uncited_sibling_warning_names_the_other_ref(db_conn) -> None:
    """Two schemes, same kind/lot/software, both uncited: the archive
    cannot tell them apart, and says so by name (plan §2.4)."""
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_scheme(
            session, _aec_ref(version="1.0", software={"name": "Gaussian"})
        )
        warnings: list = []
        resolve_or_create_scheme(
            session,
            _aec_ref(version="2.0", software={"name": "Gaussian"}),
            warnings_out=warnings,
        )

        ambiguous = [
            w
            for w in warnings
            if w.code == W_AMBIGUOUS_ENERGY_CORRECTION_SCHEME_WITHOUT_LITERATURE
        ]
        assert len(ambiguous) == 1
        assert first.public_ref in ambiguous[0].message


def test_differing_software_siblings_are_not_flagged_ambiguous(db_conn) -> None:
    """Software distinguishes -- two schemes differing only by software
    are not ambiguous at all (plan §2.1), so no ambiguity warning fires."""
    with Session(db_conn) as session, session.begin():
        resolve_or_create_scheme(session, _aec_ref(software={"name": "Gaussian"}))
        warnings: list = []
        resolve_or_create_scheme(
            session, _aec_ref(software={"name": "ORCA"}), warnings_out=warnings
        )

        ambiguous = [
            w
            for w in warnings
            if w.code == W_AMBIGUOUS_ENERGY_CORRECTION_SCHEME_WITHOUT_LITERATURE
        ]
        assert ambiguous == []


# ---------------------------------------------------------------------------
# Release grain and units (correction-scheme-provenance plan v2, PR 1)
# ---------------------------------------------------------------------------


def test_two_schemes_same_identity_different_units_are_distinct_rows(
    db_conn,
) -> None:
    """``units`` joins the identity index (plan v2 §3.3, ruling 13): a
    scheme identical on every other axis but ``units`` is a second row,
    not a collision."""
    with Session(db_conn) as session, session.begin():
        hartree = resolve_or_create_scheme(session, _aec_ref(units="hartree"))
        kcal_mol = resolve_or_create_scheme(session, _aec_ref(units="kcal_mol"))

        assert hartree.id != kcal_mol.id

        # Re-supplying the same units reuses the same row.
        again = resolve_or_create_scheme(session, _aec_ref(units="hartree"))
        assert again.id == hartree.id


def test_same_correction_in_a_second_unit_creates_a_second_row_not_a_conflict(
    db_conn,
) -> None:
    """The bug ``units`` in the identity fixes (plan v2 §2.5/§3.3): before
    this widening, a depositor re-sending the same correction in a
    different energy unit resolved onto the existing row and
    ``_assert_param_value_compatible`` raised, wrongly calling numerically
    different values (because they're in different units) a conflict.
    Now the second unit is a second row, so no conflict is ever raised,
    and the depositor's own digits in both units are preserved exactly."""
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_scheme(
            session,
            _aec_ref(
                units="hartree",
                atom_params=[{"element": "H", "value": -0.5010929786112002}],
            ),
        )
        # No ValueError from _assert_param_value_compatible: this is a
        # different row, not a merge attempt against `first`.
        second = resolve_or_create_scheme(
            session,
            _aec_ref(
                units="kcal_mol",
                atom_params=[{"element": "H", "value": -314.860165}],
            ),
        )

        assert second.id != first.id

        count = session.scalar(
            select(func.count()).select_from(EnergyCorrectionScheme).where(
                EnergyCorrectionScheme.kind == "atom_energy",
                EnergyCorrectionScheme.name == "AEC provenance test",
            )
        )
        assert count == 2


def test_versioned_and_version_less_release_of_same_program_are_distinct_rows(
    db_conn,
) -> None:
    """A software release with a stated build and the version-less
    release of the same program are two different, individually correct
    values of ``software_release_id`` (plan v2 §3.2's table), not a
    collision."""
    with Session(db_conn) as session, session.begin():
        versioned = resolve_or_create_scheme(
            session,
            _aec_ref(software={"name": "Gaussian", "version": "16", "revision": "C.02"}),
        )
        version_less = resolve_or_create_scheme(
            session, _aec_ref(software={"name": "Gaussian"})
        )

        assert versioned.id != version_less.id
        assert versioned.software_release_id != version_less.software_release_id


def test_software_ref_with_version_resolves_the_exact_release(db_conn) -> None:
    """A depositor who states version/revision resolves to exactly that
    release row, not the bare-program one."""
    with Session(db_conn) as session, session.begin():
        scheme = resolve_or_create_scheme(
            session,
            _aec_ref(software={"name": "Gaussian", "version": "16", "revision": "C.02"}),
        )

        assert scheme.software_release is not None
        assert scheme.software_release.version == "16"
        assert scheme.software_release.revision == "C.02"
        assert scheme.software_release.software.name == "Gaussian"


def test_software_ref_name_only_resolves_and_reuses_the_version_less_release(
    db_conn,
) -> None:
    """"Program known, build not stated" is a first-class, complete value
    of ``software_release_id`` (plan v2 §3.2), not a degraded one: a
    depositor naming only the program resolves to the one version-less
    release row for it, and a second scheme identity naming the same bare
    program reuses that same release row rather than minting another."""
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_scheme(
            session, _aec_ref(software={"name": "Gaussian"})
        )

        assert first.software_release is not None
        assert first.software_release.version is None
        assert first.software_release.software.name == "Gaussian"

        second = resolve_or_create_scheme(
            session,
            _aec_ref(name="A distinct scheme identity", software={"name": "Gaussian"}),
        )

        assert second.id != first.id
        assert second.software_release_id == first.software_release_id
