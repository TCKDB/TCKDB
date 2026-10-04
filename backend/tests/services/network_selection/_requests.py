"""Request builders for the network-selection tests."""

from __future__ import annotations

from typing import Any

from tckdb_schemas.network_declarations import NetworkComparisonObjective, NetworkObservable

from app.services.network_selection import (
    BathRequest,
    NetworkRequest,
    OutputRequest,
    PartitionRequest,
    Scope,
)

PRODUCT = NetworkObservable.product_resolved_coefficient


def channel_request(world, **changes: Any) -> NetworkRequest:
    """A single-channel request that a complete, declared ``assoc`` determination answers."""
    base: dict[str, Any] = {
        "network_ref": world.ref,
        "scope": Scope.single_channel,
        "channel_key": "assoc",
        "observable": PRODUCT,
        "coefficient_basis": "kernel",
        "temperature_min_k": 500.0,
        "temperature_max_k": 1500.0,
        "pressure_min_bar": 0.5,
        "pressure_max_bar": 5.0,
        "bath": BathRequest((world.ar.public_ref,)),
        "partition": PartitionRequest(retained=tuple(sorted(world.hashes.values()))),
        "objective": NetworkComparisonObjective.physical_accuracy,
    }
    base.update(changes)
    return NetworkRequest(**base)


def bundle_request(world, *channels: str, scope: Scope = Scope.projected_bundle, **changes: Any) -> NetworkRequest:
    outputs = tuple(OutputRequest(c, PRODUCT) for c in (channels or ("assoc", "elim")))
    base: dict[str, Any] = {"scope": scope, "channel_key": None, "observable": None, "outputs": outputs}
    base.update(changes)
    return channel_request(world, **base)
