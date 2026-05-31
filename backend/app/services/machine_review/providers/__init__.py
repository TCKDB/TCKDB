"""Native v2 machine-review provider plumbing (foundation slice).

Producer-side plumbing in front of the already-implemented v2 contract +
audit-adapter dispatch. This package contains the provider interface, the
disabled/off provider, a test-only fake v2 provider, the deployer-facing
factory, and the strict-parse / serialization boundary helpers.

It performs **no** real online or local model calls, no persistence, no
upload/precheck wiring, no automatic task creation, and no public exposure.
Cloud/local modes validate configuration and then raise ``NotImplementedError``
until the real providers land (spec
``backend/docs/specs/machine_review_real_provider_plumbing.md``).

Config namespace: ``AI_REVIEW_ASSISTANT_MODE`` + ``LLM_PRECHECK_*`` (no parallel
``MACHINE_REVIEW_*`` env vars). Output contract:
:class:`~app.services.machine_review.schemas.MachineReviewProviderResultV2`.
``machine_review`` remains the future *public* concept.
"""

from app.services.machine_review.providers.disabled import (
    DISABLED_SUMMARY,
    DisabledMachineReviewProvider,
)
from app.services.machine_review.providers.factory import (
    build_machine_review_provider,
)
from app.services.machine_review.providers.fake import (
    FakeMachineReviewProvider,
    build_fake_machine_review_provider,
    make_critical_result,
    make_failed_result,
    make_pass_result,
    make_warning_result,
)
from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    MachineReviewProvider,
    MachineReviewProviderConfigurationError,
    machine_review_v2_result_to_details_json,
    parse_machine_review_v2_payload,
)

__all__ = [
    "DISABLED_SUMMARY",
    "DisabledMachineReviewProvider",
    "FakeMachineReviewProvider",
    "MachineReviewContext",
    "MachineReviewProvider",
    "MachineReviewProviderConfigurationError",
    "build_fake_machine_review_provider",
    "build_machine_review_provider",
    "machine_review_v2_result_to_details_json",
    "make_critical_result",
    "make_failed_result",
    "make_pass_result",
    "make_warning_result",
    "parse_machine_review_v2_payload",
]
