"""Contract: ``ComputedReactionUploadResult`` now carries
``calculation_keys``.

Phase 7 (artifact planning) adds a response-only field exposing the
bundle-local calc-key → assigned-calculation-id map. The request
payload shape is unchanged; this test fixes the response wire
contract that the builder's ``artifact_plan(result)`` reads.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app.api.routes.uploads import ComputedReactionUploadResult
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionUploadRequest,
)
from app.workflows.computed_reaction import persist_computed_reaction_upload

_XYZ_H = "1\nH\nH 0.0 0.0 0.0"
_XYZ_CH3 = (
    "4\nch3\n"
    "C  0.000  0.000  0.000\n"
    "H  1.080  0.000  0.000\n"
    "H -0.540  0.935  0.000\n"
    "H -0.540 -0.935  0.000"
)
_XYZ_CH4 = (
    "5\nch4\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
_XYZ_TS = (
    "6\nts\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629\n"
    "H  1.500  0.000  0.000"
)
_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}


def _species_block(key: str, smiles: str, charge: int, mult: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {"smiles": smiles, "charge": charge, "multiplicity": mult},
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
                "key": f"{key}-sp",
                "type": "sp",
                "geometry_key": f"{key}-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "sp_electronic_energy_hartree": -40.5,
            }
        ],
    }


def _bundle_payload() -> dict:
    return {
        "species": [
            _species_block("ch3", "[CH3]", 0, 2, _XYZ_CH3),
            _species_block("h", "[H]", 0, 2, _XYZ_H),
            _species_block("ch4", "C", 0, 1, _XYZ_CH4),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "transition_state": {
            "charge": 0,
            "multiplicity": 2,
            "geometry": {"key": "ts-geom", "xyz_text": _XYZ_TS},
            "calculation": {
                "key": "ts-opt",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_converged": True,
            },
            "calculations": [
                {
                    "key": "ts-freq",
                    "type": "freq",
                    "geometry_key": "ts-geom",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "freq_n_imag": 1,
                    "freq_imag_freq_cm1": -1500.0,
                }
            ],
        },
        "kinetics": [
            {
                "reactant_keys": ["ch3", "h"],
                "product_keys": ["ch4"],
                "a": 1.2e13,
                "a_units": "cm3_mol_s",
                "n": 0.5,
                "reported_ea": 10.0,
                "reported_ea_units": "kj_mol",
            }
        ],
    }


@contextmanager
def _isolated_session(db_engine) -> Iterator[Session]:
    """Per-test transactional session, rolled back at teardown."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_response_includes_calculation_keys_mapping(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        session.add(AppUser(username="artifact_plan_tester", role=AppUserRole.user))
        session.flush()
        user_id = session.scalar(
            __import__("sqlalchemy").select(AppUser.id).where(
                AppUser.username == "artifact_plan_tester"
            )
        )

        request = ComputedReactionUploadRequest(**_bundle_payload())
        result_dict = persist_computed_reaction_upload(
            session, request, created_by=user_id
        )

        assert "calculation_keys" in result_dict
        ck = result_dict["calculation_keys"]
        assert isinstance(ck, dict)
        # Every bundle calc key must resolve to an int id.
        expected_keys = {
            "ch3-opt", "ch3-sp",
            "h-opt", "h-sp",
            "ch4-opt", "ch4-sp",
            "ts-opt", "ts-freq",
        }
        assert expected_keys.issubset(set(ck))
        assert all(isinstance(v, int) for v in ck.values())

        # The Pydantic response model validates and round-trips the field.
        validated = ComputedReactionUploadResult(**result_dict)
        dumped = validated.model_dump(mode="json")
        assert dumped["calculation_keys"] == ck


def test_response_calculation_keys_field_is_optional() -> None:
    """Old workflow outputs (no ``calculation_keys`` key) still validate.

    Defaults to an empty dict so the response stays additive — clients
    older than this PR (or downstream consumers that don't read the
    field) keep working unchanged.
    """
    minimal_payload = {
        "reaction_entry_id": 1,
        "reaction_id": 1,
        "kinetics_ids": [],
        "thermo_ids": [],
        "species_entry_ids": [],
        "species_count": 0,
    }
    validated = ComputedReactionUploadResult(**minimal_payload)
    assert validated.calculation_keys == {}
