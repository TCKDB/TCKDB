"""The bundle routes must annotate absent provenance, not swallow it.

``collect_thermo_provenance_warnings``, ``collect_statmech_provenance_warnings``
and ``collect_kinetics_provenance_warnings`` existed and were called only
from the standalone routes. On ``/uploads/computed-species`` and
``/uploads/computed-reaction`` — the two routes the ARC adapter actually
uses — omitting ``workflow_tool_release``, ``software_release``,
``literature`` or a frequency scale factor produced no warning of any
kind, while the identical omission field-by-field was reported.

ADR 0011 and ADR 0008 both rest on the same principle: absence is
incompleteness, and incompleteness is *annotated* rather than refused. A
bundle depositor got neither the refusal nor the annotation, so their
record was silently less complete than a field-by-field one and nothing
told them.

Every test here goes through the real route and asserts on the real
response envelope, because a collector that is *called* and a warning a
depositor *receives* are different claims — and the first was never in
doubt.

Assertions are on ``(field, code)`` pairs rather than on message
substrings: the messages are shared with the standalone routes and
rewording one should not break these, whereas a warning landing on the
wrong species or under the wrong code should.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_ANALYSIS_SOFTWARE = {"name": "RMG-Py", "version": "3.2.0"}
_WTR = {"name": "ARC", "version": "1.2.0"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_FSF = {
    "level_of_theory": _LOT,
    "scale_kind": "fundamental",
    "value": 0.97,
}
_LITERATURE = {
    "kind": "article",
    "title": "A rate for something",
    "journal": "J. Test",
    "year": 2026,
    "doi": "10.1000/tckdb.bundle.provenance.warnings",
}

_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"


def _pairs(resp) -> set[tuple[str, str]]:
    """``(field, code)`` for every warning on an upload response."""
    return {(w["field"], w["code"]) for w in resp.json()["warnings"]}


def _codes_under(resp, prefix: str) -> set[str]:
    return {
        w["code"] for w in resp.json()["warnings"] if w["field"].startswith(prefix)
    }


#: The provenance-absence codes, as distinct from the *content*-absence
#: codes (untraceable statmech, missing tunneling evidence) that the same
#: response carries. Kept separate so a "provenance is silent" assertion
#: cannot be accidentally satisfied — or accidentally broken — by a
#: content warning landing on the same field prefix.
PROVENANCE_CODES = frozenset(
    {
        "missing_software_release_provenance",
        "missing_workflow_tool_provenance",
        "missing_literature_provenance",
        "missing_frequency_scale_factor_provenance",
        "missing_level_of_theory_provenance",
    }
)


def _provenance_codes_under(resp, prefix: str) -> set[str]:
    return _codes_under(resp, prefix) & PROVENANCE_CODES


def _statmech_rows(db_session, resp) -> list:
    """Statmech rows written by this reaction upload, in id order.

    Read back through ``species_entry_ids`` rather than a ``statmech_ids``
    field, because ``ComputedReactionUploadResult`` does not expose one —
    it returns ``kinetics_ids`` and ``thermo_ids`` but not the statmech
    ids the workflow already collects internally. Noted rather than
    worked around silently; it is a response-shape gap, not a
    persistence one.
    """
    from sqlalchemy import select

    from app.db.models.statmech import Statmech

    entry_ids = resp.json()["species_entry_ids"]
    return list(
        db_session.scalars(
            select(Statmech)
            .where(Statmech.species_entry_id.in_(entry_ids))
            .order_by(Statmech.id)
        ).all()
    )


# ---------------------------------------------------------------------------
# /uploads/computed-species
# ---------------------------------------------------------------------------


def _species_bundle(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _XYZ_H},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_result": {"converged": True},
                },
            }
        ],
    }
    base.update(overrides)
    return base


def test_species_bundle_thermo_without_provenance_is_annotated(client: TestClient):
    """The headline gap for the species route's thermo."""
    bundle = _species_bundle(
        thermo={"scientific_origin": "computed", "h298_kj_mol": 218.0}
    )
    resp = client.post("/api/v1/uploads/computed-species", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    assert ("thermo.software_release", "missing_software_release_provenance") in _pairs(
        resp
    )
    assert (
        "thermo.workflow_tool_release",
        "missing_workflow_tool_provenance",
    ) in _pairs(resp)


def test_species_bundle_thermo_with_provenance_is_not_annotated(client: TestClient):
    """The control. Supplying it must silence exactly these warnings."""
    bundle = _species_bundle(
        thermo={
            "scientific_origin": "computed",
            "h298_kj_mol": 218.0,
            "software_release": _ANALYSIS_SOFTWARE,
            "workflow_tool_release": _WTR,
        }
    )
    resp = client.post("/api/v1/uploads/computed-species", json=bundle)
    assert resp.status_code == 201, resp.text[:800]
    assert _codes_under(resp, "thermo.") == set(), resp.json()["warnings"]


def test_species_bundle_statmech_without_provenance_is_annotated(client: TestClient):
    """Statmech carries a third anchor the others do not: the scale factor."""
    bundle = _species_bundle(
        statmech={
            "scientific_origin": "computed",
            "statmech_treatment": "rrho",
            "external_symmetry": 1,
        }
    )
    resp = client.post("/api/v1/uploads/computed-species", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    assert _codes_under(resp, "statmech.software_release") == {
        "missing_software_release_provenance"
    }
    assert _codes_under(resp, "statmech.workflow_tool_release") == {
        "missing_workflow_tool_provenance"
    }
    assert _codes_under(resp, "statmech.freq_scale_factor") == {
        "missing_frequency_scale_factor_provenance"
    }


def test_species_bundle_statmech_with_provenance_is_not_annotated(client: TestClient):
    bundle = _species_bundle(
        statmech={
            "scientific_origin": "computed",
            "statmech_treatment": "rrho",
            "external_symmetry": 1,
            "software_release": _ANALYSIS_SOFTWARE,
            "workflow_tool_release": _WTR,
            "freq_scale_factor": _FSF,
        }
    )
    resp = client.post("/api/v1/uploads/computed-species", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    provenance_codes = {
        "missing_software_release_provenance",
        "missing_workflow_tool_provenance",
        "missing_frequency_scale_factor_provenance",
        "missing_literature_provenance",
    }
    assert _codes_under(resp, "statmech.") & provenance_codes == set(), resp.json()[
        "warnings"
    ]


def test_a_non_computed_species_bundle_product_wants_literature_instead(
    client: TestClient,
):
    """Origin selects which anchor is expected, on the bundle route too.

    An experimental thermo has no software release to name, so demanding
    one would fire on every correct deposit. What it should carry is the
    paper it came out of.
    """
    bundle = _species_bundle(
        thermo={"scientific_origin": "experimental", "h298_kj_mol": 218.0}
    )
    resp = client.post("/api/v1/uploads/computed-species", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    assert _codes_under(resp, "thermo.") == {"missing_literature_provenance"}


def test_a_species_bundle_with_no_products_gets_no_product_warnings(
    client: TestClient,
):
    """A conformer-only deposit claims no thermo and no statmech.

    Guards the shape of the fix rather than its content: warning about
    absent provenance on a record that does not exist would make the
    conformer-only upload path noisy for no reason.
    """
    resp = client.post("/api/v1/uploads/computed-species", json=_species_bundle())
    assert resp.status_code == 201, resp.text[:800]

    for field, _code in _pairs(resp):
        assert not field.startswith(("thermo.", "statmech.")), (
            f"warning on absent product: {field}"
        )


# ---------------------------------------------------------------------------
# /uploads/computed-reaction
# ---------------------------------------------------------------------------


def _reaction_species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {
            "smiles": smiles,
            "charge": 0,
            "multiplicity": multiplicity,
        },
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [
            {
                "key": f"{key}-freq",
                "type": "freq",
                "geometry_key": f"{key}-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_n_imag": 0,
                "freq_zpe_hartree": 0.01,
            },
        ],
    }


def _reaction_bundle(**overrides) -> dict:
    """``H + H -> H2``. Balanced, no transition state, no atom map needed."""
    base: dict = {
        "species": [
            _reaction_species("h", "[H]", 2, _XYZ_H),
            _reaction_species("h2", "[H][H]", 1, _XYZ_H2),
        ],
        "reversible": True,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
    }
    base.update(overrides)
    return base


def _kinetics(**overrides) -> dict:
    base: dict = {
        "scientific_origin": "computed",
        "model_kind": "arrhenius",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "n": 0.0,
        "reported_ea": 10.0,
        "reported_ea_units": "kj_mol",
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
    }
    base.update(overrides)
    return base


def test_reaction_bundle_warnings_name_the_species_they_concern(client: TestClient):
    """The requirement a bundle adds over the standalone routes.

    A flat "missing workflow tool" on a multi-species bundle is true and
    useless. Both species here omit thermo provenance, and the two
    warnings must be distinguishable — asserting on the *set* of fields
    is what makes this fail if the species key is dropped, since two
    unnamed warnings would collapse to one entry.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = {
        "scientific_origin": "computed",
        "h298_kj_mol": 218.0,
    }
    bundle["species"][1]["thermo"] = {
        "scientific_origin": "computed",
        "h298_kj_mol": 0.0,
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    pairs = _pairs(resp)
    assert (
        "species['h'].thermo.software_release",
        "missing_software_release_provenance",
    ) in pairs
    assert (
        "species['h2'].thermo.software_release",
        "missing_software_release_provenance",
    ) in pairs
    assert (
        "species['h'].thermo.workflow_tool_release",
        "missing_workflow_tool_provenance",
    ) in pairs
    assert (
        "species['h2'].thermo.workflow_tool_release",
        "missing_workflow_tool_provenance",
    ) in pairs


def test_reaction_bundle_statmech_without_provenance_is_annotated(client: TestClient):
    bundle = _reaction_bundle()
    bundle["species"][0]["statmech"] = {
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    assert _provenance_codes_under(resp, "species['h'].statmech.") == {
        "missing_software_release_provenance",
        "missing_workflow_tool_provenance",
        "missing_frequency_scale_factor_provenance",
    }


def test_bundle_level_provenance_silences_the_per_species_warning(
    client: TestClient,
):
    """Warn on the effective value, not the raw field.

    The reaction workflow falls a per-species ``software_release`` back to
    the bundle-level ``analysis_software_release`` and persists the
    fallback. Warning on the raw per-species field would name provenance
    that was in fact recorded — a false report, and the fastest way to
    teach depositors to ignore the warnings entirely.
    """
    bundle = _reaction_bundle(
        analysis_software_release=_ANALYSIS_SOFTWARE,
        workflow_tool_release=_WTR,
    )
    bundle["species"][0]["thermo"] = {
        "scientific_origin": "computed",
        "h298_kj_mol": 218.0,
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    assert _codes_under(resp, "species['h'].thermo.") == set(), resp.json()["warnings"]


def test_per_species_provenance_silences_it_without_a_bundle_default(
    client: TestClient,
):
    """The other half of the fallback: the override alone is enough."""
    bundle = _reaction_bundle()
    bundle["species"][0]["statmech"] = {
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
        "software_release": _ANALYSIS_SOFTWARE,
        "workflow_tool_release": _WTR,
        "freq_scale_factor": _FSF,
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    assert _provenance_codes_under(resp, "species['h'].statmech.") == set(), (
        resp.json()["warnings"]
    )


def test_reaction_bundle_kinetics_without_provenance_is_annotated(client: TestClient):
    """Kinetics provenance is bundle-scoped, and the field paths say so.

    ``BundleKineticsIn`` carries no provenance fields at all; the workflow
    writes the bundle-root ``analysis_software_release`` /
    ``workflow_tool_release`` / ``literature`` onto every kinetics row.
    So the warning must point at the root fields — a path like
    ``kinetics[0].software_release`` would name a field that does not
    exist, and ``SchemaBase`` is extra="forbid", so a depositor following
    that advice would get a 422.
    """
    bundle = _reaction_bundle(kinetics=[_kinetics()])

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    pairs = _pairs(resp)
    assert ("software_release", "missing_software_release_provenance") in pairs
    assert ("workflow_tool_release", "missing_workflow_tool_provenance") in pairs


def test_kinetics_is_never_warned_about_a_level_of_theory_it_cannot_carry(
    client: TestClient,
):
    """The failure mode this whole task exists to prevent.

    ``collect_kinetics_provenance_warnings`` asks the standalone route for
    ``energy_level_of_theory``. No bundle model has that field — not
    ``BundleKineticsIn``, not the bundle root — and it is not a column on
    ``kinetics`` either; on the standalone route it is a resolution hint
    used to auto-resolve source SP calculations. Emitting it here would
    tell a depositor to supply something they cannot supply, which is a
    worse outcome than the silence it replaced.
    """
    bundle = _reaction_bundle(kinetics=[_kinetics()])

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    codes = {code for _field, code in _pairs(resp)}
    assert "missing_level_of_theory_provenance" not in codes, (
        "warned about energy_level_of_theory, which no bundle payload can carry"
    )


def test_bundle_kinetics_provenance_is_reported_once_not_once_per_fit(
    client: TestClient,
):
    """Several fits share one set of root fields.

    N identical warnings for one missing field is noise, and noise is how
    a warnings list stops being read.
    """
    bundle = _reaction_bundle(
        kinetics=[
            _kinetics(),
            _kinetics(tmin_k=200.0, tmax_k=300.0),
        ]
    )

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    software = [
        w
        for w in resp.json()["warnings"]
        if w["code"] == "missing_software_release_provenance"
        and w["field"] == "software_release"
    ]
    assert len(software) == 1, software


def test_reaction_bundle_kinetics_with_provenance_is_not_annotated(
    client: TestClient,
):
    bundle = _reaction_bundle(
        kinetics=[_kinetics()],
        analysis_software_release=_ANALYSIS_SOFTWARE,
        workflow_tool_release=_WTR,
        literature=_LITERATURE,
    )

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    provenance_codes = {
        "missing_software_release_provenance",
        "missing_workflow_tool_provenance",
        "missing_literature_provenance",
        "missing_level_of_theory_provenance",
    }
    offenders = [
        w
        for w in resp.json()["warnings"]
        if w["code"] in provenance_codes and "." not in w["field"]
    ]
    assert offenders == [], offenders


def test_a_reaction_bundle_with_no_products_gets_no_provenance_warnings(
    client: TestClient,
):
    """No thermo, no statmech, no kinetics — nothing to be missing."""
    resp = client.post("/api/v1/uploads/computed-reaction", json=_reaction_bundle())
    assert resp.status_code == 201, resp.text[:800]

    provenance_codes = {
        "missing_software_release_provenance",
        "missing_workflow_tool_provenance",
        "missing_literature_provenance",
        "missing_frequency_scale_factor_provenance",
        "missing_level_of_theory_provenance",
    }
    offenders = [
        w for w in resp.json()["warnings"] if w["code"] in provenance_codes
    ]
    assert offenders == [], offenders


def test_reaction_bundle_reports_untraceable_statmech_like_the_species_route(
    client: TestClient,
):
    """The content gap, not just the provenance one.

    ``collect_statmech_content_warnings`` was wired into the species
    bundle and not the reaction bundle, so the same computed statmech
    with nothing to trace it to was named on one route and silent on the
    other.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["statmech"] = {
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    assert (
        "species['h'].statmech.source_calculations",
        "missing_statmech_source_calculations",
    ) in _pairs(resp)


def test_rotational_constants_make_the_frequency_source_gap_visible(
    client: TestClient,
):
    """#142 and #113 meet here, which is why they are one PR.

    ``statmech_has_rotational_structure`` reads the rotational constants
    to decide whether a species has vibrational modes worth tracing. On
    this route those constants did not exist as a field until #142, so
    the check could only ever see torsions and a polyatomic deposited in
    the ordinary ARC shape — constants, no torsions — looked monatomic
    to it. Warning before the fields existed would have been the wrong
    order.
    """
    bundle = _reaction_bundle()
    bundle["species"][1]["statmech"] = {
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "rotational_constant_a_cm1": 60.8,
        "source_calculations": [{"calculation_key": "h2-opt", "role": "opt"}],
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    assert (
        "species['h2'].statmech.source_calculations",
        "missing_statmech_frequency_source",
    ) in _pairs(resp)


# ---------------------------------------------------------------------------
# #142: the widened statmech, through the route
# ---------------------------------------------------------------------------


def test_reaction_bundle_statmech_persists_its_widened_fields(client, db_session):
    """#142's fields must reach the row, not merely validate.

    A schema that accepts a field and a workflow that stores it are
    different claims, and the six fields added here had DB columns
    waiting for them the whole time — the gap was purely in the contract
    and the projection.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["statmech"] = {
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
        "software_release": _ANALYSIS_SOFTWARE,
        "workflow_tool_release": _WTR,
        "literature": _LITERATURE,
        "rotational_constant_a_cm1": 10.5,
        "rotational_constant_b_cm1": 8.25,
        "rotational_constant_c_cm1": 4.75,
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    rows = _statmech_rows(db_session, resp)
    assert len(rows) == 1, rows
    row = rows[0]

    assert row.rotational_constant_a_cm1 == 10.5
    assert row.rotational_constant_b_cm1 == 8.25
    assert row.rotational_constant_c_cm1 == 4.75
    assert row.literature_id is not None, "statmech literature was dropped"
    assert row.software_release_id is not None
    assert row.workflow_tool_release_id is not None


def test_statmech_provenance_override_beats_the_bundle_default(client, db_session):
    """The override must win, and the fallback must still work.

    Before #142 the reaction route had no per-species statmech provenance
    at all: every statmech row took the bundle-level analysis software.
    That is the ordinary-case failure — one participant taken from a
    paper, the rest computed here — so the precedence is worth pinning.
    """
    from app.db.models.software import SoftwareRelease

    bundle = _reaction_bundle(analysis_software_release=_ANALYSIS_SOFTWARE)
    bundle["species"][0]["statmech"] = {
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
        "software_release": {"name": "MESS", "version": "2023.07"},
    }
    bundle["species"][1]["statmech"] = {
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    rows = _statmech_rows(db_session, resp)
    assert len(rows) == 2, rows
    names = {
        db_session.get(SoftwareRelease, r.software_release_id).software.name
        for r in rows
    }
    assert names == {"MESS", "RMG-Py"}, (
        f"expected the override and the bundle default side by side, got {names}"
    )
