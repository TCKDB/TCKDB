"""Contract test: the client's ``T0`` reaches the wire schema as ``t0_k`` (#620).

The builder sends ``t0_k`` only when it is not 1 K. The payload is validated by
the backend's own ``BundleKineticsIn``, so a rename on either side is caught
here and not by a producer.
"""

from __future__ import annotations

from tests._ci_dependency import require_module

# Skips locally without the client; fails on CI, which installs it (#575).
require_module("tckdb_client.builders", install="pip install -e clients/python")

from tckdb_client.builders import Kinetics

from app.schemas.workflows.computed_reaction_upload import BundleKineticsIn


def _validated(**kwargs) -> BundleKineticsIn:
    kinetics = Kinetics.modified_arrhenius(
        A=1.0e10, A_units="cm3/mol/s", n=2.0, Ea=10.0, **kwargs
    )
    payload = kinetics.to_payload(
        reactant_keys=["a", "b"], product_keys=["c"], calc_key_lookup=lambda calc: "unused"
    )
    return BundleKineticsIn.model_validate(payload)


def test_a_builder_t0_is_the_schemas_t0_k():
    assert _validated(T0=298.15).t0_k == 298.15


def test_a_builder_without_t0_validates_as_one_kelvin():
    assert _validated().t0_k == 1.0
