"""Tests for the correction-scheme-provenance widening of
``resolve_or_create_scheme`` and its warning collector.

Covers the citation-not-discarded fix (§1.3/§3 of
``docs/plans/correction-scheme-provenance.md``), the new software
dimension, and the ``uq_energy_correction_scheme_identity`` unique
index the resolver's lookup must match.
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
        assert gaussian.software.name == "Gaussian"
        assert orca.software.name == "ORCA"

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
