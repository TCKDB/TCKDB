"""Tests for the public-ref helper module + ORM auto-population.

Phase A scope: helpers exist, work, and are wired up to ORM inserts.
No API response shapes are touched — that's Phase B.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import inspect

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    MoleculeKind,
    StereoKind,
)
from app.db.models.geometry import Geometry
from app.db.models.kinetics import Kinetics
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import (
    ConformerAssignmentScheme,
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.submission import Submission
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.transport import Transport
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.services.public_refs import (
    PREFIXES,
    PUBLIC_REF_BODY_LEN,
    PUBLIC_REF_LEN,
    _assert_public_ref_prefix_budget,
    backfill_public_refs,
    generate_ref_for,
    make_content_ref,
    make_opaque_ref,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


_REF_BODY_PATTERN = re.compile(r"^[a-z2-7]+$")


# ---------------------------------------------------------------------------
# Helper unit tests (no DB needed)
# ---------------------------------------------------------------------------


class TestRefFormat:
    def test_content_ref_is_deterministic(self):
        a = make_content_ref("lot", "method=wb97xd;basis=def2tzvp")
        b = make_content_ref("lot", "method=wb97xd;basis=def2tzvp")
        assert a == b

    def test_content_ref_changes_with_canonical_input(self):
        a = make_content_ref("lot", "method=wb97xd;basis=def2tzvp")
        b = make_content_ref("lot", "method=wb97xd;basis=cc-pvtz")
        assert a != b

    def test_content_ref_accepts_bytes_or_str(self):
        a = make_content_ref("spc", "smiles=C")
        b = make_content_ref("spc", b"smiles=C")
        assert a == b

    def test_opaque_ref_is_unique_per_call(self):
        seen = {make_opaque_ref("calc") for _ in range(50)}
        assert len(seen) == 50

    @pytest.mark.parametrize("prefix", ["lot", "spc", "calc", "thm", "kin"])
    def test_ref_body_is_lowercase_base32(self, prefix):
        ref = make_content_ref(prefix, "anything")
        assert ref.startswith(f"{prefix}_")
        body = ref[len(prefix) + 1:]
        assert _REF_BODY_PATTERN.match(body), f"body {body!r} not lowercase base32"
        assert len(body) == 26, f"body length {len(body)} != 26"

    def test_opaque_ref_body_is_lowercase_base32(self):
        ref = make_opaque_ref("calc")
        body = ref[len("calc_"):]
        assert _REF_BODY_PATTERN.match(body)
        assert len(body) == 26

    def test_total_ref_length_fits_column(self):
        # Longest prefix + underscore + body must fit in the column.
        longest_prefix = max(len(p) for p in PREFIXES.values())
        assert longest_prefix + 1 + PUBLIC_REF_BODY_LEN <= PUBLIC_REF_LEN

    def test_prefix_budget_invariant_passes_for_current_registry(self):
        """The import-time invariant must accept the current registry —
        if it fails after a future prefix addition, the column must grow
        before the new prefix lands.
        """
        _assert_public_ref_prefix_budget()

    def test_prefix_budget_invariant_rejects_oversize_prefix(
        self, monkeypatch
    ):
        """A hypothetical prefix that overflows the budget must trip the
        guard. Confirms the invariant has teeth.
        """
        from app.services import public_refs as pr

        oversize = "x" * (PUBLIC_REF_LEN - PUBLIC_REF_BODY_LEN)  # no room for '_'
        patched = dict(PREFIXES)
        patched["FakeOverflow"] = oversize
        monkeypatch.setattr(pr, "PREFIXES", patched)
        with pytest.raises(RuntimeError, match="prefix budget exceeded"):
            _assert_public_ref_prefix_budget()


class TestPrefixRegistry:
    def test_phase_a_classes_all_have_prefixes(self):
        # All 24 Phase A classes; missing one would mean ORM listener won't
        # auto-populate that table's rows.
        expected = {
            "Species", "SpeciesEntry",
            "ChemReaction", "ReactionEntry",
            "Thermo", "Kinetics",
            "Calculation", "Geometry",
            "ConformerGroup", "ConformerObservation", "ConformerAssignmentScheme",
            "Statmech", "Transport",
            "TransitionState", "TransitionStateEntry",
            "LevelOfTheory",
            "Software", "SoftwareRelease",
            "WorkflowTool", "WorkflowToolRelease",
            "Literature",
            "FrequencyScaleFactor", "EnergyCorrectionScheme",
            "Submission",
        }
        assert expected.issubset(PREFIXES.keys())

    def test_no_duplicate_prefixes(self):
        prefixes = list(PREFIXES.values())
        assert len(prefixes) == len(set(prefixes))


# ---------------------------------------------------------------------------
# ORM-level: the column is on the model, the listener populates new rows
# ---------------------------------------------------------------------------


def _make_lot(session, *, method: str = "wb97xd", basis: str | None = "def2tzvp"):
    """Build a LoT row with lot_hash populated; let the listener set public_ref."""
    import hashlib

    raw = f"{method}|{basis or ''}".encode()
    lot = LevelOfTheory(
        method=method,
        basis=basis,
        lot_hash=hashlib.sha256(raw).hexdigest(),
    )
    session.add(lot)
    session.flush()
    return lot


def _next_inchi(prefix: str) -> str:
    """Generate a 27-char InChI-key-shaped string for tests."""
    if not hasattr(_next_inchi, "_n"):
        _next_inchi._n = 0
    _next_inchi._n += 1
    return (prefix + str(_next_inchi._n).rjust(21, "X"))[:27].upper()


class TestColumnExists:
    @pytest.mark.parametrize(
        "model_cls",
        [
            Species, SpeciesEntry,
            ChemReaction, ReactionEntry,
            Thermo, Kinetics,
            Calculation, Geometry,
            ConformerGroup, ConformerObservation, ConformerAssignmentScheme,
            Statmech, Transport,
            TransitionState, TransitionStateEntry,
            LevelOfTheory,
            Software, SoftwareRelease,
            WorkflowTool, WorkflowToolRelease,
            Literature,
            Submission,
        ],
    )
    def test_model_has_public_ref_column(self, model_cls):
        cols = inspect(model_cls).columns
        assert "public_ref" in cols, f"{model_cls.__name__} missing public_ref"
        col = cols["public_ref"]
        assert col.nullable is False
        # SQLAlchemy index/unique flags travel via different paths depending
        # on whether the column was declared with index=True/unique=True or
        # via a later index. The mixin uses index=True + unique=True; the
        # migration creates a UNIQUE INDEX. Either way, the table-level
        # constraints expose uniqueness.


class TestAutoPopulationOnInsert:
    def test_lot_ref_populated_on_insert(self, db_session):
        lot = _make_lot(db_session)
        assert lot.public_ref
        assert lot.public_ref.startswith("lot_")

    def test_lot_ref_is_deterministic_from_lot_hash(self, db_session):
        """Same lot_hash → same public_ref. Confirms content-derived behavior."""
        lot1 = _make_lot(db_session, method="wb97xd", basis="def2tzvp")
        # Same identity in a fresh transient instance — recompute the ref
        # via the helper directly (we can't insert a duplicate row because
        # of the lot_hash unique constraint).
        ref_again = generate_ref_for(
            LevelOfTheory(
                method="wb97xd",
                basis="def2tzvp",
                lot_hash=lot1.lot_hash,
            )
        )
        assert ref_again == lot1.public_ref

    def test_species_ref_populated_on_insert(self, db_session):
        sp = Species(
            kind=MoleculeKind.molecule,
            smiles="C[CH2]",
            inchi_key=_next_inchi("PR"),
            charge=0,
            multiplicity=2,
            stereo_kind=StereoKind.achiral,
        )
        db_session.add(sp)
        db_session.flush()
        assert sp.public_ref.startswith("spc_")

    def test_species_entry_ref_populated_on_insert(self, db_session):
        sp = Species(
            kind=MoleculeKind.molecule,
            smiles="C",
            inchi_key=_next_inchi("PE"),
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind.achiral,
        )
        db_session.add(sp)
        db_session.flush()
        entry = SpeciesEntry(species_id=sp.id)
        db_session.add(entry)
        db_session.flush()
        assert entry.public_ref.startswith("spe_")

    def test_calculation_ref_is_opaque_and_prefixed(self, db_session):
        sp = Species(
            kind=MoleculeKind.molecule,
            smiles="N",
            inchi_key=_next_inchi("PC"),
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind.achiral,
        )
        db_session.add(sp)
        db_session.flush()
        entry = SpeciesEntry(species_id=sp.id)
        db_session.add(entry)
        db_session.flush()
        calc = Calculation(type=CalculationType.sp, species_entry_id=entry.id)
        db_session.add(calc)
        db_session.flush()
        assert calc.public_ref.startswith("calc_")
        # Two consecutive calcs should get different refs.
        calc2 = Calculation(type=CalculationType.opt, species_entry_id=entry.id)
        db_session.add(calc2)
        db_session.flush()
        assert calc.public_ref != calc2.public_ref

    def test_geometry_ref_is_deterministic_from_geom_hash(self, db_session):
        """Same geom_hash → same content-derived public_ref."""
        import hashlib

        gh = hashlib.sha256(b"H 0 0 0\nH 0 0 1").hexdigest()
        g = Geometry(natoms=2, geom_hash=gh)
        db_session.add(g)
        db_session.flush()
        ref_again = generate_ref_for(Geometry(natoms=2, geom_hash=gh))
        assert g.public_ref == ref_again
        assert g.public_ref.startswith("geom_")

    def test_uniqueness_enforced_at_db_level(self, db_session):
        """Two LoTs with the same lot_hash → ref collision → IntegrityError.

        The lot_hash unique constraint actually trips first in this scenario,
        but the public_ref unique constraint is the parallel guarantee for
        any future case where two rows of a content-derived class are
        inserted with identical canonical inputs.
        """
        from sqlalchemy.exc import IntegrityError

        lot1 = _make_lot(db_session, method="b3lyp", basis="6-31g")
        with pytest.raises(IntegrityError):
            db_session.add(
                LevelOfTheory(
                    method="b3lyp",
                    basis="6-31g",
                    lot_hash=lot1.lot_hash,
                )
            )
            db_session.flush()


class TestSeededConformerAssignmentScheme:
    def test_default_torsion_basin_scheme_has_public_ref(self, db_session):
        """The migration backfills the seeded torsion_basin v1 row's public_ref."""
        from sqlalchemy import select

        cas = db_session.scalar(
            select(ConformerAssignmentScheme).where(
                ConformerAssignmentScheme.name == "torsion_basin"
            )
        )
        assert cas is not None
        assert cas.public_ref
        assert cas.public_ref.startswith("cas_")
        # Recompute: the migration's backfill must match the helper's output.
        expected = generate_ref_for(cas)
        # generate_ref_for hashes the live attributes; the seeded row uses
        # name='torsion_basin', version='v1', scope='canonical'.
        assert cas.public_ref == expected


class TestBackfillHelper:
    def test_backfill_is_noop_when_all_refs_present(self, db_session):
        counts = backfill_public_refs(db_session)
        for table, n in counts.items():
            assert n == 0, f"{table} had {n} unrefilled rows after fresh DB"


# ---------------------------------------------------------------------------
# EnergyCorrectionScheme — regression tests for the content-derived ref.
#
# The DB unique constraint and ``resolve_or_create_scheme`` both dedup on
# ``(kind, name, level_of_theory_id, version)``. The canonical ref must
# match (or exceed) that identity; otherwise two rows the resolver treats
# as distinct can mint the same public_ref and trip the
# ``ix_energy_correction_scheme_public_ref`` unique index on insert. See
# the original failure mode: kind/lot differed but the legacy ref was
# computed only from (name, version) and collided.
# ---------------------------------------------------------------------------


class TestEnergyCorrectionSchemeRefIdentity:
    @staticmethod
    def _build(
        session,
        *,
        kind,
        name="atom_energy",
        lot=None,
        version=None,
        units=None,
        source_literature_id=None,
    ):
        """Build (don't add) an ECS instance for canonical-ref comparison."""
        from app.db.models.energy_correction import EnergyCorrectionScheme

        return EnergyCorrectionScheme(
            kind=kind,
            name=name,
            level_of_theory_id=(lot.id if lot is not None else None),
            source_literature_id=source_literature_id,
            version=version,
            units=units,
        )

    def test_refs_differ_when_lot_differs(self, db_session):
        from app.db.models.common import EnergyCorrectionSchemeKind

        lot_a = _make_lot(db_session, method="wb97xd", basis="def2tzvp")
        lot_b = _make_lot(db_session, method="b3lyp", basis="6-31g")
        a = self._build(
            db_session, kind=EnergyCorrectionSchemeKind.atom_energy, lot=lot_a
        )
        b = self._build(
            db_session, kind=EnergyCorrectionSchemeKind.atom_energy, lot=lot_b
        )
        assert generate_ref_for(a) != generate_ref_for(b)

    def test_refs_differ_when_kind_differs(self, db_session):
        """Two schemes with same name/version/lot but different kind must
        not share a ref — the resolver inserts both as distinct rows.
        """
        from app.db.models.common import EnergyCorrectionSchemeKind

        lot = _make_lot(db_session)
        a = self._build(
            db_session, kind=EnergyCorrectionSchemeKind.atom_energy, lot=lot
        )
        b = self._build(
            db_session, kind=EnergyCorrectionSchemeKind.atom_hf, lot=lot
        )
        assert generate_ref_for(a) != generate_ref_for(b)

    def test_refs_differ_when_units_differ(self, db_session):
        from app.db.models.common import (
            EnergyCorrectionSchemeKind,
            EnergyUnit,
        )

        lot = _make_lot(db_session)
        a = self._build(
            db_session,
            kind=EnergyCorrectionSchemeKind.atom_energy,
            lot=lot,
            units=EnergyUnit.hartree,
        )
        b = self._build(
            db_session,
            kind=EnergyCorrectionSchemeKind.atom_energy,
            lot=lot,
            units=EnergyUnit.kj_mol,
        )
        assert generate_ref_for(a) != generate_ref_for(b)

    def test_refs_match_for_identical_identity(self, db_session):
        """Sanity check: same (kind, name, lot, version, units, source_lit)
        → same ref. This is the content-derived determinism property.
        """
        from app.db.models.common import EnergyCorrectionSchemeKind

        lot = _make_lot(db_session)
        a = self._build(
            db_session,
            kind=EnergyCorrectionSchemeKind.atom_energy,
            lot=lot,
            version=None,
        )
        b = self._build(
            db_session,
            kind=EnergyCorrectionSchemeKind.atom_energy,
            lot=lot,
            version=None,
        )
        assert generate_ref_for(a) == generate_ref_for(b)


class TestResolveOrCreateScheme:
    """`resolve_or_create_scheme` reuses an existing identical scheme
    rather than inserting a second row. Combined with the canonical-ref
    fix above, this guarantees the repeated computed-reaction upload
    scenario cannot collide on ``ix_energy_correction_scheme_public_ref``.
    """

    def test_repeat_call_reuses_existing_scheme(self, db_session):
        from app.db.models.common import (
            EnergyCorrectionSchemeKind,
            EnergyUnit,
        )
        from app.schemas.workflows.energy_correction_upload import (
            EnergyCorrectionSchemeRef,
        )
        from app.services.energy_correction_resolution import (
            resolve_or_create_scheme,
        )

        ref = EnergyCorrectionSchemeRef(
            kind=EnergyCorrectionSchemeKind.atom_energy,
            name="atom_energy",
            version=None,
            units=EnergyUnit.hartree,
            note="Per-species AEC computed by Arkane.",
        )
        first = resolve_or_create_scheme(db_session, ref)
        second = resolve_or_create_scheme(db_session, ref)
        assert first.id == second.id
        assert first.public_ref == second.public_ref

    def test_repeat_upload_with_different_kind_does_not_collide(
        self, db_session
    ):
        """Two schemes with identical (name, version) but different kind
        must each get a distinct public_ref — this is the regression for
        the original ``ix_energy_correction_scheme_public_ref`` failure.
        """
        from app.db.models.common import EnergyCorrectionSchemeKind
        from app.schemas.workflows.energy_correction_upload import (
            EnergyCorrectionSchemeRef,
        )
        from app.services.energy_correction_resolution import (
            resolve_or_create_scheme,
        )

        ref_a = EnergyCorrectionSchemeRef(
            kind=EnergyCorrectionSchemeKind.atom_energy,
            name="atom_energy",
        )
        ref_b = EnergyCorrectionSchemeRef(
            kind=EnergyCorrectionSchemeKind.atom_hf,
            name="atom_energy",
        )
        a = resolve_or_create_scheme(db_session, ref_a)
        b = resolve_or_create_scheme(db_session, ref_b)
        # The DB unique constraint (kind, name, lot, version) lets both rows
        # coexist; the public_ref unique index must also tolerate them.
        assert a.id != b.id
        assert a.public_ref != b.public_ref


# ---------------------------------------------------------------------------
# Canonicalizer-invariant audit tests
#
# For every content-derived public_ref class, assert two properties of
# the canonicalizer that build into ``generate_ref_for``:
#
# - **Determinism**: two ORM instances built with the same
#   identity-bearing field values produce the same public_ref.
# - **Distinctness**: two instances that the resolver/DB would treat as
#   distinct rows produce different public_refs.
#
# See ``docs/audits/public_ref_identity_audit.md`` for the per-class
# verdict table.
# ---------------------------------------------------------------------------


def _make_software(session, *, name: str = "Gaussian"):
    """Build a Software row; the listener sets public_ref."""
    sw = Software(name=name)
    session.add(sw)
    session.flush()
    return sw


def _make_software_release(
    session,
    *,
    software,
    version: str | None = "16",
    revision: str | None = None,
    build: str | None = None,
):
    rel = SoftwareRelease(
        software_id=software.id,
        version=version,
        revision=revision,
        build=build,
    )
    session.add(rel)
    session.flush()
    return rel


def _make_workflow_tool(session, *, name: str = "ARC"):
    wt = WorkflowTool(name=name)
    session.add(wt)
    session.flush()
    return wt


def _make_workflow_tool_release(
    session,
    *,
    workflow_tool,
    version: str | None = "1.0",
    git_commit: str | None = None,
):
    rel = WorkflowToolRelease(
        workflow_tool_id=workflow_tool.id,
        version=version,
        git_commit=git_commit,
    )
    session.add(rel)
    session.flush()
    return rel


class TestCanonicalizerInvariants:
    """Audit-level checks: every content-derived canonicalizer must give
    distinct rows distinct refs, and identical rows identical refs.

    The tests build ORM instances directly (without going through the
    resolver) and assert on the output of :func:`generate_ref_for`. This
    lets us probe the canonicalizer in isolation from resolver dedup.
    """

    # ------------------------------------------------------------------
    # LevelOfTheory — content-derived via lot_hash
    # ------------------------------------------------------------------

    def test_lot_refs_differ_when_lot_hash_differs(self, db_session):
        a = _make_lot(db_session, method="b3lyp", basis="6-31g")
        b = _make_lot(db_session, method="wb97xd", basis="def2tzvp")
        assert a.public_ref != b.public_ref

    def test_lot_refs_match_for_identical_lot_hash(self, db_session):
        import hashlib

        lot_hash = hashlib.sha256(b"identical-lot-content").hexdigest()
        a = LevelOfTheory(method="m062x", basis="def2tzvp", lot_hash=lot_hash)
        b = LevelOfTheory(method="m062x", basis="def2tzvp", lot_hash=lot_hash)
        assert generate_ref_for(a) == generate_ref_for(b)

    # ------------------------------------------------------------------
    # Species — content-derived via inchi_key (+ charge / multiplicity /
    # stereo_kind defensively included by the canonicalizer)
    # ------------------------------------------------------------------

    def test_species_refs_differ_when_inchi_key_differs(self):
        a = Species(
            kind=MoleculeKind.molecule,
            smiles="CC",
            inchi_key=_next_inchi("ASPC"),
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind.achiral,
        )
        b = Species(
            kind=MoleculeKind.molecule,
            smiles="CCO",
            inchi_key=_next_inchi("BSPC"),
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind.achiral,
        )
        assert generate_ref_for(a) != generate_ref_for(b)

    def test_species_refs_match_for_identical_identity(self):
        ik = _next_inchi("SPCM")
        a = Species(
            kind=MoleculeKind.molecule,
            smiles="CC",
            inchi_key=ik,
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind.achiral,
        )
        b = Species(
            kind=MoleculeKind.molecule,
            smiles="CC",
            inchi_key=ik,
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind.achiral,
        )
        assert generate_ref_for(a) == generate_ref_for(b)

    # ------------------------------------------------------------------
    # ChemReaction — content-derived via stoichiometry_hash
    # ------------------------------------------------------------------

    def test_chem_reaction_refs_differ_when_stoichiometry_hash_differs(self):
        a = ChemReaction(reversible=True, stoichiometry_hash="aaa111")
        b = ChemReaction(reversible=True, stoichiometry_hash="bbb222")
        assert generate_ref_for(a) != generate_ref_for(b)

    def test_chem_reaction_refs_match_for_identical_stoichiometry_hash(self):
        a = ChemReaction(reversible=True, stoichiometry_hash="ccc333")
        b = ChemReaction(reversible=True, stoichiometry_hash="ccc333")
        assert generate_ref_for(a) == generate_ref_for(b)

    def test_chem_reaction_without_stoichiometry_hash_uses_opaque_ref(self):
        """Without a ``stoichiometry_hash`` the canonicalizer returns
        ``None``, so each fresh instance gets a per-row random opaque ref
        — same fallback contract as Literature without a DOI/ISBN.

        Replaces the prior ``id(obj)`` fallback, which could collide when
        CPython recycled object addresses between successive instantiations.
        """
        from app.services.public_refs import _CANONICALIZERS

        bare = ChemReaction(reversible=True)
        assert _CANONICALIZERS["ChemReaction"](bare) is None

        refs = {
            generate_ref_for(ChemReaction(reversible=True)) for _ in range(50)
        }
        # All opaque → all unique. (Same prefix, random body.)
        assert len(refs) == 50
        assert all(r.startswith("rxn_") for r in refs)

    # ------------------------------------------------------------------
    # Geometry — content-derived via geom_hash
    # ------------------------------------------------------------------

    def test_geometry_refs_differ_when_geom_hash_differs(self):
        a = Geometry(geom_hash="ghash_a")
        b = Geometry(geom_hash="ghash_b")
        assert generate_ref_for(a) != generate_ref_for(b)

    def test_geometry_refs_match_for_identical_geom_hash(self):
        a = Geometry(geom_hash="ghash_dup")
        b = Geometry(geom_hash="ghash_dup")
        assert generate_ref_for(a) == generate_ref_for(b)

    # ------------------------------------------------------------------
    # SoftwareRelease — content-derived locally via software_id + version
    # tuple (FK-dependent; collision-safe in one DB)
    # ------------------------------------------------------------------

    def test_software_release_refs_differ_when_version_differs(
        self, db_session
    ):
        sw = _make_software(db_session, name="Gaussian-srdiff")
        a = _make_software_release(db_session, software=sw, version="16-A")
        b = _make_software_release(db_session, software=sw, version="16-B")
        assert a.public_ref != b.public_ref

    def test_software_release_refs_differ_when_software_differs(
        self, db_session
    ):
        sw_a = _make_software(db_session, name="Gaussian-srA")
        sw_b = _make_software(db_session, name="ORCA-srA")
        a = _make_software_release(db_session, software=sw_a, version="16")
        b = _make_software_release(db_session, software=sw_b, version="16")
        assert a.public_ref != b.public_ref

    # ------------------------------------------------------------------
    # WorkflowToolRelease — same shape as SoftwareRelease
    # ------------------------------------------------------------------

    def test_workflow_tool_release_refs_differ_when_git_commit_differs(
        self, db_session
    ):
        wt = _make_workflow_tool(db_session, name="ARC-wtrdiff")
        a = _make_workflow_tool_release(
            db_session, workflow_tool=wt, version="1.0", git_commit="abc"
        )
        b = _make_workflow_tool_release(
            db_session, workflow_tool=wt, version="1.0", git_commit="def"
        )
        assert a.public_ref != b.public_ref

    # ------------------------------------------------------------------
    # Literature — content-derived via DOI / ISBN; opaque fallback when
    # neither is present (every fresh insert is its own row).
    # ------------------------------------------------------------------

    def test_literature_refs_differ_when_doi_differs(self):
        from app.db.models.common import LiteratureKind

        a = Literature(
            kind=LiteratureKind.article,
            title="A",
            doi="10.1000/aaa",
        )
        b = Literature(
            kind=LiteratureKind.article,
            title="B",
            doi="10.1000/bbb",
        )
        assert generate_ref_for(a) != generate_ref_for(b)

    def test_literature_refs_match_for_identical_doi(self):
        from app.db.models.common import LiteratureKind

        a = Literature(
            kind=LiteratureKind.article,
            title="A",
            doi="10.1000/zzz",
        )
        b = Literature(
            kind=LiteratureKind.article,
            title="A repeat",
            doi="10.1000/zzz",
        )
        # Title/year differ but only DOI participates in the canonical
        # identity; refs should match.
        assert generate_ref_for(a) == generate_ref_for(b)

    def test_literature_without_doi_isbn_falls_back_to_opaque(self):
        from app.db.models.common import LiteratureKind
        from app.services.public_refs import _CANONICALIZERS

        # Direct canonicalizer call: returns None → dispatcher uses
        # ``make_opaque_ref``. Verify the canonicalizer signals fallback.
        lit = Literature(
            kind=LiteratureKind.book, title="No DOI / no ISBN"
        )
        assert _CANONICALIZERS["Literature"](lit) is None

    # ------------------------------------------------------------------
    # ConformerAssignmentScheme — content-derived via name + version + scope
    # ------------------------------------------------------------------

    def test_conformer_assignment_scheme_refs_differ_when_name_differs(self):
        from app.db.models.common import ConformerAssignmentScopeKind

        a = ConformerAssignmentScheme(
            name="alpha",
            version="v1",
            scope=ConformerAssignmentScopeKind.canonical,
        )
        b = ConformerAssignmentScheme(
            name="beta",
            version="v1",
            scope=ConformerAssignmentScopeKind.canonical,
        )
        assert generate_ref_for(a) != generate_ref_for(b)

    def test_conformer_assignment_scheme_refs_match_for_identical_identity(
        self,
    ):
        from app.db.models.common import ConformerAssignmentScopeKind

        a = ConformerAssignmentScheme(
            name="gamma",
            version="v2",
            scope=ConformerAssignmentScopeKind.canonical,
        )
        b = ConformerAssignmentScheme(
            name="gamma",
            version="v2",
            scope=ConformerAssignmentScopeKind.canonical,
        )
        assert generate_ref_for(a) == generate_ref_for(b)

    # ------------------------------------------------------------------
    # FrequencyScaleFactor — full 6-tuple canonical identity
    # ------------------------------------------------------------------

    def test_frequency_scale_factor_refs_differ_when_value_differs(
        self, db_session
    ):
        from app.db.models.common import FrequencyScaleKind
        from app.db.models.energy_correction import FrequencyScaleFactor

        lot = _make_lot(db_session, method="m062x_fsf", basis="def2tzvp")
        sw = _make_software(db_session, name="Gaussian-fsfa")
        a = FrequencyScaleFactor(
            level_of_theory_id=lot.id,
            software_id=sw.id,
            scale_kind=FrequencyScaleKind.zpe,
            value=0.97,
        )
        b = FrequencyScaleFactor(
            level_of_theory_id=lot.id,
            software_id=sw.id,
            scale_kind=FrequencyScaleKind.zpe,
            value=0.98,
        )
        assert generate_ref_for(a) != generate_ref_for(b)

    def test_frequency_scale_factor_refs_differ_when_scale_kind_differs(
        self, db_session
    ):
        from app.db.models.common import FrequencyScaleKind
        from app.db.models.energy_correction import FrequencyScaleFactor

        lot = _make_lot(db_session, method="m062x_fsfk", basis="def2tzvp")
        sw = _make_software(db_session, name="Gaussian-fsfk")
        a = FrequencyScaleFactor(
            level_of_theory_id=lot.id,
            software_id=sw.id,
            scale_kind=FrequencyScaleKind.zpe,
            value=0.97,
        )
        b = FrequencyScaleFactor(
            level_of_theory_id=lot.id,
            software_id=sw.id,
            scale_kind=FrequencyScaleKind.fundamental,
            value=0.97,
        )
        assert generate_ref_for(a) != generate_ref_for(b)
