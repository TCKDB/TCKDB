"""The coded refusals of the relative conformer energy operation and the bounds that enforce them.

Two refusals are made *before anything is read*: an energy kind or certificate this release does not evaluate. The
population bound is checked over the complete visible set, so exceeding it refuses the request outright; there is
never an answer from a prefix. A refusal names the bound and the visible count (what the caller is entitled to
know); it never names or counts rows the read profile hides.
"""

from __future__ import annotations

from app.api.error_contract import CodedValueError
from app.services.relative_conformer_energy.models import (
    BOUNDS_VERSION,
    MAX_MEMBERS,
    SUPPORTED_CERTIFICATES,
    SUPPORTED_KINDS,
    Certificate,
    EnergyKind,
    RelativeEnergyRequest,
)

CODE_UNSUPPORTED = "relative_conformer_energy_unsupported"
CODE_POPULATION_TOO_LARGE = "relative_conformer_energy_population_too_large"


def unsupported(reason: str, message: str, **context: object) -> CodedValueError:
    """A structured refusal for an operation this release does not evaluate."""
    return CodedValueError(CODE_UNSUPPORTED, message, context={"reason": reason, **context}, message_prefix=False)


def parse_energy_kind(raw: str) -> EnergyKind:
    """The energy a wire request names. An unknown name is an invalid request (``ValueError``)."""
    try:
        return EnergyKind(raw)
    except ValueError:
        raise ValueError(f"unknown energy_kind {raw!r}") from None


def parse_certificate(raw: str) -> Certificate:
    try:
        return Certificate(raw)
    except ValueError:
        raise ValueError(f"unknown certificate {raw!r}") from None


def check_supported(request: RelativeEnergyRequest) -> None:
    """Refuse (422 ``relative_conformer_energy_unsupported``) a kind or certificate this release does not evaluate.

    Called before the first query. An enthalpy or Gibbs energy is never answered with the electronic energy, and a
    request for no minimum certificate is never answered with a certified one.
    """
    if request.energy_kind not in SUPPORTED_KINDS:
        raise unsupported(
            "deferred_energy_kind",
            f"{request.energy_kind.value!r} is a recognised energy this release does not evaluate: relative conformer "
            "energies compare an electronic energy or a zero-kelvin energy, and never substitute one for another.",
            energy_kind=request.energy_kind.value,
        )
    if request.certificate not in SUPPORTED_CERTIFICATES:
        raise unsupported(
            "deferred_certificate",
            f"certificate {request.certificate.value!r} is not evaluated by this release: a relative energy needs a "
            "local-minimum certificate on each conformer.",
            certificate=request.certificate.value,
        )


def check_members(visible: int) -> None:
    """Refuse (422 ``relative_conformer_energy_population_too_large``) when more groups than the bound are compared.

    A count equal to the limit is allowed: the limit is the largest population that is answered.
    """
    if visible > MAX_MEMBERS:
        raise CodedValueError(
            CODE_POPULATION_TOO_LARGE,
            f"{visible} visible conformer groups exceed the limit of {MAX_MEMBERS}; nothing was assessed and nothing "
            "was answered from a subset.",
            context={"bound": "members", "visible": visible, "limit": MAX_MEMBERS, "bounds_version": BOUNDS_VERSION},
            message_prefix=False,
        )
