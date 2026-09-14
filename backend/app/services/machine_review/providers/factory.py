"""Deployer-facing factory for native v2 machine-review providers.

Resolves ``AI_REVIEW_ASSISTANT_MODE`` (+ ``LLM_PRECHECK_*`` config) to a
provider:

* ``off``   -> :class:`DisabledMachineReviewProvider` (no dependencies).
* ``cloud`` -> validates required config (model + API-key-env), then builds a
  :class:`~app.services.machine_review.providers.cloud.CloudMachineReviewProvider`
  over the default Anthropic transport. Nothing is called at build time; the
  model call happens when a review is actually requested.
* ``local`` -> validates required config (model + base URL), then raises
  :class:`NotImplementedError` — no local call is implemented in this slice.
* ``test``  -> refuses: the fake provider is test-only and is reached via
  :func:`~app.services.machine_review.providers.fake.build_fake_machine_review_provider`,
  never through deployment config (spec §3/§5).

Config validation lives here (the smallest compatible change) rather than in
:class:`~app.api.config.Settings`: ``Settings`` already rejects an invalid
``AI_REVIEW_ASSISTANT_MODE`` via its ``Literal`` type, and adding cross-field
requirements there would be a larger settings rewrite. A missing required
value raises :class:`MachineReviewProviderConfigurationError`, which the
service layer converts into an advisory failed result rather than a crash.
"""

from __future__ import annotations

import os
from typing import Any

from app.api.config import settings as app_settings
from app.services.machine_review.providers.disabled import (
    DisabledMachineReviewProvider,
)
from app.services.machine_review.providers.interface import (
    MachineReviewProvider,
    MachineReviewProviderConfigurationError,
)


def _validate_cloud_config(settings_obj: Any) -> None:
    """Require model + a present, non-empty API-key env var for cloud mode."""
    if not settings_obj.llm_precheck_model:
        raise MachineReviewProviderConfigurationError(
            "Cloud mode requires LLM_PRECHECK_MODEL to be set."
        )
    key_env = settings_obj.llm_precheck_api_key_env
    if not key_env:
        raise MachineReviewProviderConfigurationError(
            "Cloud mode requires LLM_PRECHECK_API_KEY_ENV to name the "
            "environment variable that holds the API key."
        )
    if not os.environ.get(key_env):
        raise MachineReviewProviderConfigurationError(
            "Cloud mode requires the environment variable named by "
            f"LLM_PRECHECK_API_KEY_ENV ({key_env!r}) to be set and non-empty."
        )


def _validate_local_config(settings_obj: Any) -> None:
    """Require model + base URL for local mode."""
    if not settings_obj.llm_precheck_model:
        raise MachineReviewProviderConfigurationError(
            "Local mode requires LLM_PRECHECK_MODEL to be set."
        )
    if not settings_obj.llm_precheck_base_url:
        raise MachineReviewProviderConfigurationError(
            "Local mode requires LLM_PRECHECK_BASE_URL to be set."
        )


def build_machine_review_provider(
    settings_obj: Any = app_settings,
) -> MachineReviewProvider:
    """Build the configured machine-review provider. Makes no model call.

    Off returns the disabled provider. Cloud validates its configuration and
    returns a real provider -- constructing one calls nothing; the model is
    reached only when a review is requested. Local still validates and raises
    :class:`NotImplementedError`, which is a later slice. The fake provider is
    never returned here, whatever the mode says.
    """
    mode = settings_obj.ai_review_assistant_mode

    if mode == "off":
        return DisabledMachineReviewProvider()

    if mode == "cloud":
        _validate_cloud_config(settings_obj)
        # Imported here, not at module scope: the transport reaches for the
        # optional ``llm`` extra, and an install that never turns cloud mode on
        # should not need it. ``_validate_cloud_config`` has already proved the
        # key env var is set and non-empty.
        from app.services.machine_review.providers.anthropic_transport import (
            AnthropicMessagesClient,
        )
        from app.services.machine_review.providers.cloud import (
            CloudMachineReviewProvider,
        )

        return CloudMachineReviewProvider(
            client=AnthropicMessagesClient(
                api_key=os.environ[settings_obj.llm_precheck_api_key_env],
            ),
            model=settings_obj.llm_precheck_model,
        )

    if mode == "local":
        _validate_local_config(settings_obj)
        raise NotImplementedError(
            "Local machine-review provider is not implemented yet; "
            "no local model call is made."
        )

    if mode == "test":
        raise MachineReviewProviderConfigurationError(
            "The fake machine-review provider is test-only and is not "
            "selectable via AI_REVIEW_ASSISTANT_MODE; use "
            "build_fake_machine_review_provider() in tests."
        )

    raise MachineReviewProviderConfigurationError(
        f"Unsupported AI Review Assistant mode: {mode!r}."
    )
