"""A free electron may be deposited; its coordinates may not.

``molecule_kind: electron`` was bound to a SMILES sentinel, a charge and a
multiplicity, but not to having no structure, so a computed-reaction bundle
could declare an electron and hang a one-atom conformer off it. The atom-map
fragment already treats "a free electron has no geometry anywhere in the
deposit" as an invariant — it refuses a ``geometry_key`` beside an empty
``atom_to_ts`` on exactly that ground — and that invariant held only because
no depositor had tried the other door.

Two doors, not one. A conformer geometry was already refused, but late and
elsewhere: ``resolve_species_entry`` composition-checks it and returns
``species_geometry_composition_mismatch`` from inside the transaction. A
*calculation* geometry reached no composition check on any path — that
function's own docstring says so — so an electron carrying
``input_geometries`` was accepted and the structure stored. The rule here
covers both, at the seam, before any species is resolved.

These tests go through the real route rather than the model, because a schema
that rejects in isolation and a route that rejects are different claims, and
the second is the one a depositor meets. The reaction is
``OH- + H -> H2O + e-``: charge -1 on both sides, OH2 on both sides, the
electron contributing neither. It is the reaction the electron participant
work was really about, so the accepting cases here are not hypothetical
payloads invented to be refused.
"""

from __future__ import annotations

from tckdb_schemas.fragments.identity import ELECTRON_SMILES

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

_XYZ_OH = "2\nhydroxide\nO 0.0 0.0 0.0\nH 0.0 0.0 0.97"
_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_H2O = (
    "3\nwater\n"
    "O  0.000  0.000  0.000\n"
    "H  0.758  0.000  0.587\n"
    "H -0.758  0.000  0.587"
)

_ELECTRON_IDENTITY = {
    "molecule_kind": "electron",
    "smiles": ELECTRON_SMILES,
    "charge": -1,
    "multiplicity": 2,
}


def _conformer(key: str, xyz: str) -> dict:
    return {
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


def _species(key: str, identity: dict, xyz: str | None) -> dict:
    species: dict = {"key": key, "species_entry": dict(identity)}
    if xyz is not None:
        species["conformers"] = [_conformer(key, xyz)]
    return species


def _bundle(*, electron_xyz: str | None = None, electron_calculations=None) -> dict:
    electron = _species("e", _ELECTRON_IDENTITY, electron_xyz)
    if electron_calculations is not None:
        electron["calculations"] = electron_calculations
    return {
        "species": [
            _species("oh", {"smiles": "[OH-]", "charge": -1, "multiplicity": 1}, _XYZ_OH),
            _species("h", {"smiles": "[H]", "charge": 0, "multiplicity": 2}, _XYZ_H),
            _species("h2o", {"smiles": "O", "charge": 0, "multiplicity": 1}, _XYZ_H2O),
            electron,
        ],
        "reversible": False,
        "reactant_keys": ["oh", "h"],
        "product_keys": ["h2o", "e"],
    }


def _post(client, bundle: dict):
    return client.post("/api/v1/uploads/computed-reaction", json=bundle)


# ---------------------------------------------------------------------------
# The door stays open
# ---------------------------------------------------------------------------


def test_an_electron_without_structure_still_deposits(client):
    """The control. If this ever fails, the refusal below has gone too far.

    An electron is a legitimate participant with nothing to say about
    geometry, and a check that also refused *this* would have closed the
    door the electron-participant work opened.
    """
    resp = _post(client, _bundle())
    assert resp.status_code == 201, resp.text[:800]


def test_a_molecule_with_the_same_conformer_still_deposits(client):
    """The other control: the conformer shape itself is not what is refused.

    The refused payload below differs from an accepted one only in the
    ``molecule_kind`` of the species the conformer hangs off.
    """
    bundle = _bundle()
    assert bundle["species"][0]["conformers"], "control needs a real conformer"
    resp = _post(client, bundle)
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# ...and coordinates do not come through it
# ---------------------------------------------------------------------------


def test_an_electron_carrying_a_conformer_is_refused(client):
    """One atom of structure deposited under a participant known to have none.

    This particular payload did not get through before: the conformer
    geometry reached ``assert_geometry_composition_matches_identity`` via
    ``resolve_species_entry`` and came back as
    ``species_geometry_composition_mismatch``. What changes is *where* — a
    422 naming ``species['e'].conformers['e-conf'].geometry`` at the schema
    seam, instead of a composition error raised part-way through a
    transaction that had already resolved three other species. The rule is
    also now stated once for every geometry an electron could carry, rather
    than holding for conformers alone.

    Asserted on the error's *location* and type, not on a substring of the
    message: pydantic echoes rejected input back into its error string, so
    ``"electron" in resp.text`` is true whether the payload was refused for
    the right reason, the wrong reason, or accepted and refused later for
    something else entirely.
    """
    resp = _post(client, _bundle(electron_xyz=_XYZ_H))
    assert resp.status_code == 422, resp.text[:800]

    errors = resp.json()["detail"]
    assert isinstance(errors, list), resp.text[:800]
    atomless = [
        e
        for e in errors
        if e.get("type") == "value_error"
        and "has no atoms, but carries structure" in e.get("msg", "")
    ]
    assert atomless, f"no atomless-structure error in {errors}"

    # It names the species and the field that carried the structure, so the
    # depositor does not have to guess which of four species was wrong.
    message = atomless[0]["msg"]
    assert "Species 'e'" in message, message
    assert "conformers['e-conf'].geometry" in message, message

    # And it is the *species* model that refused, not something downstream.
    assert atomless[0]["loc"][:2] == ["body", "species"], atomless[0]["loc"]


def test_an_electron_carrying_a_calculation_geometry_is_refused(client):
    """The quieter door, and the one no other layer closes.

    Conformer geometries reach a composition check in
    ``resolve_species_entry`` — before this rule existed, an electron with a
    conformer was already refused there, mid-transaction, as
    ``species_geometry_composition_mismatch``. Calculation geometries reach
    no composition check on any path, by that function's own explicit
    account, so this payload was **accepted** and the structure stored.

    An ``opt`` calculation is used deliberately: it is the one type that may
    omit ``geometry_key``, so this probes the composition gap rather than
    tripping the pre-existing "geometry_key must name one of this species's
    conformers" validator, which would refuse the payload for an unrelated
    reason and prove nothing.
    """
    resp = _post(
        client,
        _bundle(
            electron_calculations=[
                {
                    "key": "e-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_converged": True,
                    "input_geometries": [{"xyz_text": _XYZ_H}],
                }
            ]
        ),
    )
    assert resp.status_code == 422, resp.text[:800]

    errors = resp.json()["detail"]
    assert isinstance(errors, list), resp.text[:800]
    atomless = [
        e
        for e in errors
        if e.get("type") == "value_error"
        and "has no atoms, but carries structure" in e.get("msg", "")
    ]
    assert atomless, f"no atomless-structure error in {errors}"
    message = atomless[0]["msg"]
    assert "calculations['e-opt'].input_geometries" in message, message


def test_a_pseudo_participant_may_still_carry_a_geometry():
    """``pseudo`` is not ``electron``, and this is where that has to hold.

    A lumped construct's composition is *unknown*, not empty. Refusing a
    geometry under one would refuse a real molecule's coordinates on the
    grounds that nobody had said which molecule it was — the opposite of
    what the atomless rule asserts. ``ATOMLESS_MOLECULE_KINDS`` omits
    ``pseudo`` deliberately; this test is what notices if it stops.

    Asserted against the model rather than the route because the route
    cannot reach the question: the computed-reaction bundle resolves every
    participant's identity through RDKit, so a lumped construct's
    unparseable SMILES is refused for *that* reason well before any
    atomless check runs. Going through the route here would therefore pass
    on a 422 that has nothing to do with the rule under test — which is
    exactly the failure mode the rest of this file is written to avoid.
    """
    from tckdb_schemas.workflows.computed_reaction_upload import BundleSpeciesIn

    species = BundleSpeciesIn.model_validate(
        _species(
            "lump",
            {
                "molecule_kind": "pseudo",
                "smiles": "lumped_sink_0001",
                "charge": -1,
                "multiplicity": 2,
            },
            _XYZ_H,
        )
    )
    # The geometry survived validation rather than being stripped.
    assert [c.geometry.xyz_text for c in species.conformers] == [_XYZ_H.strip()]
