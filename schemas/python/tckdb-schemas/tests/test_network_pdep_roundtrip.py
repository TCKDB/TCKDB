"""Roundtrip and rejection tests for ``NetworkPDepUploadRequest``.

The fixture is a realistic multi-well hydrazine PDep network:

    N2H4 (well)  <->  2 NH2       (barrierless bond fission)
    N2H4 (well)  <->  N2H3 + H    (saddle point, TS declared)

with an argon bath, a Chebyshev fit on the barrierless channel and a PLOG
fit on the saddle-point channel.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from tckdb_schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

_XYZ_N2H4 = (
    "6\nhydrazine\n"
    "N -0.707  0.000  0.000\n"
    "N  0.707  0.000  0.000\n"
    "H -1.010  0.820  0.520\n"
    "H -1.010 -0.820  0.520\n"
    "H  1.010  0.820 -0.520\n"
    "H  1.010 -0.820 -0.520"
)
_XYZ_NH2 = (
    "3\namidogen\n"
    "N  0.000  0.000  0.000\n"
    "H  0.800  0.590  0.000\n"
    "H -0.800  0.590  0.000"
)
_XYZ_N2H3 = (
    "5\nhydrazinyl\n"
    "N -0.700  0.000  0.000\n"
    "N  0.700  0.000  0.000\n"
    "H -1.020  0.830  0.510\n"
    "H  1.010  0.820 -0.520\n"
    "H  1.010 -0.820 -0.520"
)
_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_AR = "1\nargon\nAr 0.0 0.0 0.0"
_XYZ_TS = (
    "6\nN2H4 -> N2H3 + H saddle point\n"
    "N -0.707  0.000  0.000\n"
    "N  0.707  0.000  0.000\n"
    "H -1.010  0.820  0.520\n"
    "H -1.010 -0.820  0.520\n"
    "H  1.010  0.820 -0.520\n"
    "H  2.410 -0.820 -0.520"
)

_CONVENTIONS = {
    "energy_zero_convention": "lowest_state",
    "correction_convention": "electronic_plus_zpe",
}


def _species(key: str, smiles: str, charge: int, mult: int, xyz: str) -> dict:
    """One species with a single conformer and its primary opt calculation."""
    return {
        "key": key,
        "species_entry": {"smiles": smiles, "charge": charge, "multiplicity": mult},
        "label": key,
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": dict(_SOFTWARE),
                    "level_of_theory": dict(_LOT),
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [
            {
                "key": f"{key}-freq",
                "type": "freq",
                "geometry_key": f"{key}-geom",
                "software_release": dict(_SOFTWARE),
                "level_of_theory": dict(_LOT),
                "freq_n_imag": 0,
            }
        ],
    }


def _network_data() -> dict:
    return {
        "name": "hydrazine-pdep",
        "description": "N2H4 well with bond fission and H-loss channels.",
        "species": [
            _species("n2h4", "NN", 0, 1, _XYZ_N2H4),
            _species("nh2", "[NH2]", 0, 2, _XYZ_NH2),
            _species("n2h3", "[NH]N", 0, 2, _XYZ_N2H3),
            _species("h", "[H]", 0, 2, _XYZ_H),
            _species("ar", "[Ar]", 0, 1, _XYZ_AR),
        ],
        "transition_states": [
            {
                "key": "ts-hloss",
                "micro_reaction_key": "r-hloss",
                "charge": 0,
                "multiplicity": 1,
                "geometry": {"key": "ts-hloss-geom", "xyz_text": _XYZ_TS},
                "calculation": {
                    "key": "ts-hloss-opt",
                    "type": "opt",
                    "software_release": dict(_SOFTWARE),
                    "level_of_theory": dict(_LOT),
                    "opt_converged": True,
                },
                "calculations": [
                    {
                        "key": "ts-hloss-freq",
                        "type": "freq",
                        "geometry_key": "ts-hloss-geom",
                        "software_release": dict(_SOFTWARE),
                        "level_of_theory": dict(_LOT),
                        "freq_n_imag": 1,
                        "freq_imag_freq_cm1": -1204.5,
                    }
                ],
            }
        ],
        "micro_reactions": [
            {
                "key": "r-fission",
                "reversible": True,
                "reactants": [{"species_key": "n2h4"}],
                "products": [{"species_key": "nh2"}, {"species_key": "nh2"}],
                "label": "N2H4 <=> 2 NH2",
            },
            {
                "key": "r-hloss",
                "reversible": True,
                "reactants": [{"species_key": "n2h4"}],
                "products": [{"species_key": "n2h3"}, {"species_key": "h"}],
                "label": "N2H4 <=> N2H3 + H",
            },
        ],
        "states": [
            {
                "key": "w-n2h4",
                "kind": "well",
                "label": "N2H4",
                "participants": [{"species_key": "n2h4"}],
            },
            {
                "key": "bi-nh2",
                "kind": "bimolecular",
                "label": "2 NH2",
                "participants": [{"species_key": "nh2", "stoichiometry": 2}],
            },
            {
                "key": "bi-n2h3-h",
                "kind": "bimolecular",
                "label": "N2H3 + H",
                "participants": [
                    {"species_key": "n2h3"},
                    {"species_key": "h"},
                ],
            },
        ],
        "channels": [
            {
                "key": "ch-fission",
                "source_state_key": "w-n2h4",
                "sink_state_key": "bi-nh2",
                "kind": "dissociation",
                "microreaction_paths": [
                    # Barrierless bond fission: no saddle point exists.
                    {"micro_reaction_key": "r-fission"}
                ],
            },
            {
                "key": "ch-hloss",
                "source_state_key": "w-n2h4",
                "sink_state_key": "bi-n2h3-h",
                "kind": "dissociation",
                "microreaction_paths": [
                    {
                        "micro_reaction_key": "r-hloss",
                        "transition_state_key": "ts-hloss",
                    }
                ],
            },
        ],
        "solve": {
            "me_method": "chemically_significant_eigenvalues",
            "interpolation_model": "chebyshev",
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "grain_size_cm_inv": 100.0,
            "grain_count": 500,
            "emax_kj_mol": 400.0,
            "bath_gas": [{"species_key": "ar", "mole_fraction": 1.0}],
            "energy_transfer": [
                {
                    "state_key": "w-n2h4",
                    "collider_species_key": "ar",
                    "model": "single_exponential_down",
                    "alpha0_cm_inv": 250.0,
                    "t_exponent": 0.85,
                    "t_ref_k": 300.0,
                }
            ],
            "state_energies": [
                {"state_key": "w-n2h4", "energy_kj_mol": 0.0, **_CONVENTIONS},
                {"state_key": "bi-nh2", "energy_kj_mol": 276.4, **_CONVENTIONS},
                {"state_key": "bi-n2h3-h", "energy_kj_mol": 366.1, **_CONVENTIONS},
            ],
            "channel_barriers": [
                {
                    "channel_key": "ch-hloss",
                    "micro_reaction_key": "r-hloss",
                    "transition_state_key": "ts-hloss",
                    "forward_barrier_kj_mol": 371.2,
                    "reverse_barrier_kj_mol": 5.1,
                    "source_calculation_key": "ts-hloss-opt",
                    **_CONVENTIONS,
                }
            ],
            "source_calculations": [
                {"calculation_key": "n2h4-opt", "role": "well_energy"},
                {"calculation_key": "n2h4-freq", "role": "well_freq"},
                {"calculation_key": "ts-hloss-opt", "role": "barrier_energy"},
                {"calculation_key": "ts-hloss-freq", "role": "barrier_freq"},
            ],
            "channel_kinetics": [
                {
                    "channel_key": "ch-fission",
                    "model_kind": "chebyshev",
                    "chebyshev": {
                        "n_temperature": 3,
                        "n_pressure": 2,
                        "coefficients": [
                            [11.2, 0.31],
                            [-0.84, 0.12],
                            [-0.07, -0.02],
                        ],
                    },
                    "tmin_k": 300.0,
                    "tmax_k": 2000.0,
                    "pmin_bar": 0.01,
                    "pmax_bar": 100.0,
                    "rate_units": "per_s",
                    "pressure_units": "bar",
                    "temperature_units": "kelvin",
                    "stores_log10_k": True,
                },
                {
                    "channel_key": "ch-hloss",
                    "model_kind": "plog",
                    "plog": {
                        "entries": [
                            {
                                "pressure_bar": 0.01,
                                "a": 4.1e15,
                                "a_units": "per_s",
                                "n": -0.2,
                                "ea_kj_mol": 372.0,
                            },
                            {
                                "pressure_bar": 1.0,
                                "a": 8.6e15,
                                "a_units": "per_s",
                                "n": -0.1,
                                "ea_kj_mol": 374.5,
                            },
                            {
                                "pressure_bar": 100.0,
                                "a": 1.2e16,
                                "a_units": "per_s",
                                "n": 0.0,
                                "ea_kj_mol": 376.9,
                            },
                        ]
                    },
                    "tmin_k": 300.0,
                    "tmax_k": 2000.0,
                    "pmin_bar": 0.01,
                    "pmax_bar": 100.0,
                    "rate_units": "per_s",
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_multi_well_network_validates_and_roundtrips() -> None:
    payload = NetworkPDepUploadRequest.model_validate(_network_data())

    assert {sp.key for sp in payload.species} == {
        "n2h4",
        "nh2",
        "n2h3",
        "h",
        "ar",
    }
    assert {st.key for st in payload.states} == {
        "w-n2h4",
        "bi-nh2",
        "bi-n2h3-h",
    }
    assert payload.solve is not None
    # The barrierless path carries no barrier; only the saddle-point one does.
    assert len(payload.solve.channel_barriers) == 1
    assert payload.channels[0].microreaction_paths[0].transition_state_key is None

    dumped = payload.model_dump(mode="json", exclude_none=True)
    revalidated = NetworkPDepUploadRequest.model_validate(dumped)

    assert revalidated.solve is not None
    kinetics = {nk.channel_key: nk for nk in revalidated.solve.channel_kinetics}
    assert kinetics["ch-fission"].model_kind.value == "chebyshev"
    assert kinetics["ch-fission"].chebyshev is not None
    assert kinetics["ch-fission"].chebyshev.coefficients[0] == [11.2, 0.31]
    assert kinetics["ch-hloss"].model_kind.value == "plog"
    assert kinetics["ch-hloss"].plog is not None
    assert [e.pressure_bar for e in kinetics["ch-hloss"].plog.entries] == [
        0.01,
        1.0,
        100.0,
    ]
    assert revalidated.solve.energy_transfer[0].state_key == "w-n2h4"
    assert revalidated.solve.state_energies[0].energy_zero_convention.value == (
        "lowest_state"
    )


def test_upload_schema_exposes_no_fk_ids_or_hashes() -> None:
    """Upload payloads carry local keys and scientific content only."""

    def _walk(model_cls, seen: set) -> list[str]:
        if model_cls in seen:
            return []
        seen.add(model_cls)
        offenders: list[str] = []
        for name, field in model_cls.model_fields.items():
            if name.endswith("_hash") or name in {"id", "public_ref"}:
                offenders.append(f"{model_cls.__name__}.{name}")
            elif name.endswith("_id") and not name.endswith("_uuid"):
                offenders.append(f"{model_cls.__name__}.{name}")
            for sub in _nested_models(field.annotation):
                offenders.extend(_walk(sub, seen))
        return offenders

    def _nested_models(annotation) -> list:
        found = []
        stack = [annotation]
        while stack:
            current = stack.pop()
            if isinstance(current, type) and hasattr(current, "model_fields"):
                found.append(current)
                continue
            stack.extend(getattr(current, "__args__", ()) or ())
        return found

    offenders = _walk(NetworkPDepUploadRequest, set())
    # ``CalculationIn.literature_id`` is a pre-existing FK leak inherited from
    # the shared calculation fragment; it is not introduced by this schema.
    assert [o for o in offenders if o != "CalculationIn.literature_id"] == []


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


def test_energy_transfer_must_cover_every_well_collider_pair() -> None:
    data = _network_data()
    data["solve"]["energy_transfer"] = []
    with pytest.raises(ValidationError, match="energy_transfer must cover exactly one"):
        NetworkPDepUploadRequest.model_validate(data)


def test_energy_transfer_requires_explicit_scope() -> None:
    data = _network_data()
    del data["solve"]["energy_transfer"][0]["collider_species_key"]
    with pytest.raises(ValidationError, match="requires an explicit state_key"):
        NetworkPDepUploadRequest.model_validate(data)


def test_chebyshev_requires_all_four_reduced_variable_bounds() -> None:
    data = _network_data()
    del data["solve"]["channel_kinetics"][0]["pmax_bar"]
    with pytest.raises(ValidationError, match="reduced variables; missing"):
        NetworkPDepUploadRequest.model_validate(data)


def test_chebyshev_grid_dimensions_must_match_declared_orders() -> None:
    data = _network_data()
    data["solve"]["channel_kinetics"][0]["chebyshev"]["n_pressure"] = 3
    with pytest.raises(ValidationError, match="must have n_pressure=3 columns"):
        NetworkPDepUploadRequest.model_validate(data)


def test_plog_rejects_chebyshev_only_log10_flag() -> None:
    data = _network_data()
    data["solve"]["channel_kinetics"][1]["stores_log10_k"] = True
    with pytest.raises(ValidationError, match="Chebyshev-only concept"):
        NetworkPDepUploadRequest.model_validate(data)


def test_tabulated_network_kinetics_is_rejected() -> None:
    data = _network_data()
    entry = data["solve"]["channel_kinetics"][1]
    entry["model_kind"] = "tabulated"
    entry.pop("plog")
    with pytest.raises(ValidationError, match="Tabulated network kinetics upload"):
        NetworkPDepUploadRequest.model_validate(data)


def test_barrierless_path_must_not_carry_a_barrier() -> None:
    data = _network_data()
    data["solve"]["channel_barriers"].append(
        {
            "channel_key": "ch-fission",
            "micro_reaction_key": "r-fission",
            "transition_state_key": "ts-hloss",
            "forward_barrier_kj_mol": 100.0,
            "reverse_barrier_kj_mol": 10.0,
            **_CONVENTIONS,
        }
    )
    with pytest.raises(ValidationError, match="channel_barriers must provide exactly"):
        NetworkPDepUploadRequest.model_validate(data)


def test_submerged_barrier_is_accepted() -> None:
    """A negative barrier is physical, not a typo — no positivity bound."""
    data = _network_data()
    data["solve"]["channel_barriers"][0]["reverse_barrier_kj_mol"] = -12.4
    payload = NetworkPDepUploadRequest.model_validate(data)
    assert payload.solve is not None
    assert payload.solve.channel_barriers[0].reverse_barrier_kj_mol == -12.4


def test_disconnected_state_is_rejected() -> None:
    data = _network_data()
    data["channels"] = [data["channels"][0]]
    data["solve"]["channel_barriers"] = []
    data["solve"]["channel_kinetics"] = [data["solve"]["channel_kinetics"][0]]
    with pytest.raises(ValidationError, match="States not connected"):
        NetworkPDepUploadRequest.model_validate(data)


def test_bath_gas_mole_fractions_must_sum_to_one() -> None:
    data = _network_data()
    data["solve"]["bath_gas"][0]["mole_fraction"] = 0.5
    with pytest.raises(ValidationError, match="mole fractions must sum to 1.0"):
        NetworkPDepUploadRequest.model_validate(data)


def test_state_energies_must_cover_every_state() -> None:
    data = _network_data()
    data["solve"]["state_energies"] = data["solve"]["state_energies"][:2]
    with pytest.raises(ValidationError, match="exactly one energy for every"):
        NetworkPDepUploadRequest.model_validate(data)


def test_other_energy_convention_requires_a_note() -> None:
    data = _network_data()
    data["solve"]["state_energies"][0]["energy_zero_convention"] = "other"
    with pytest.raises(ValidationError, match="convention_note is required"):
        NetworkPDepUploadRequest.model_validate(data)


def test_duplicate_model_kind_on_one_channel_is_rejected() -> None:
    data = _network_data()
    duplicate = copy.deepcopy(data["solve"]["channel_kinetics"][0])
    data["solve"]["channel_kinetics"].append(duplicate)
    with pytest.raises(ValidationError, match="unique by \\(channel_key, model_kind\\)"):
        NetworkPDepUploadRequest.model_validate(data)


def test_channel_may_carry_both_chebyshev_and_plog() -> None:
    data = _network_data()
    data["solve"]["channel_kinetics"].append(
        {
            "channel_key": "ch-fission",
            "model_kind": "plog",
            "plog": {
                "entries": [
                    {
                        "pressure_bar": 1.0,
                        "a": 2.2e16,
                        "a_units": "per_s",
                        "n": 0.0,
                        "ea_kj_mol": 280.0,
                    }
                ]
            },
        }
    )
    payload = NetworkPDepUploadRequest.model_validate(data)
    assert payload.solve is not None
    assert len(payload.solve.channel_kinetics) == 3


def test_plog_entries_must_be_unique_by_pressure_and_index() -> None:
    data = _network_data()
    entries = data["solve"]["channel_kinetics"][1]["plog"]["entries"]
    entries.append(copy.deepcopy(entries[0]))
    with pytest.raises(ValidationError, match="unique by \\(pressure_bar, entry_index\\)"):
        NetworkPDepUploadRequest.model_validate(data)


def test_duplicate_arrhenius_plog_at_one_pressure_is_accepted() -> None:
    data = _network_data()
    entries = data["solve"]["channel_kinetics"][1]["plog"]["entries"]
    second = copy.deepcopy(entries[0])
    second["entry_index"] = 2
    entries.append(second)
    payload = NetworkPDepUploadRequest.model_validate(data)
    assert payload.solve is not None
    assert len(payload.solve.channel_kinetics[1].plog.entries) == 4


def test_unused_species_is_rejected() -> None:
    data = _network_data()
    data["species"].append(_species("he", "[He]", 0, 1, "1\nhelium\nHe 0.0 0.0 0.0"))
    with pytest.raises(ValidationError, match="never referenced"):
        NetworkPDepUploadRequest.model_validate(data)


def test_globally_duplicate_calculation_keys_are_rejected() -> None:
    data = _network_data()
    data["species"][1]["calculations"][0]["key"] = "n2h4-freq"
    with pytest.raises(ValidationError, match="Calculation keys must be globally unique"):
        NetworkPDepUploadRequest.model_validate(data)


def test_species_calculation_must_use_its_own_conformer_geometry() -> None:
    data = _network_data()
    data["species"][1]["calculations"][0]["geometry_key"] = "n2h4-geom"
    with pytest.raises(
        ValidationError, match="must reference one of that species's conformer"
    ):
        NetworkPDepUploadRequest.model_validate(data)


def test_channel_ts_must_belong_to_its_micro_reaction() -> None:
    data = _network_data()
    data["channels"][0]["microreaction_paths"][0]["transition_state_key"] = "ts-hloss"
    with pytest.raises(ValidationError, match="does not belong to micro reaction"):
        NetworkPDepUploadRequest.model_validate(data)


def test_conformer_primary_calculation_must_be_opt() -> None:
    data = _network_data()
    data["species"][0]["conformers"][0]["calculation"]["type"] = "sp"
    with pytest.raises(ValidationError, match="primary calculation must be type 'opt'"):
        NetworkPDepUploadRequest.model_validate(data)


def test_unknown_field_is_rejected() -> None:
    data = _network_data()
    data["unexpected_field"] = "nope"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        NetworkPDepUploadRequest.model_validate(data)


def test_legacy_endpoint_selector_resolves_to_channel_key() -> None:
    data = _network_data()
    entry = data["solve"]["channel_kinetics"][1]
    del entry["channel_key"]
    entry["source_state_key"] = "w-n2h4"
    entry["sink_state_key"] = "bi-n2h3-h"
    payload = NetworkPDepUploadRequest.model_validate(data)
    assert payload.solve is not None
    assert payload.solve.channel_kinetics[1].channel_key == "ch-hloss"


def test_channel_kinetics_without_any_selector_is_rejected() -> None:
    data = _network_data()
    del data["solve"]["channel_kinetics"][1]["channel_key"]
    with pytest.raises(ValidationError, match="channel_key is required"):
        NetworkPDepUploadRequest.model_validate(data)
