"""Deployer-facing factory for native v2 machine-review providers.

Resolves ``AI_REVIEW_ASSISTANT_MODE`` (+ ``LLM_PRECHECK_*`` config) to a
provider:

* ``off``   -> :class:`DisabledMachineReviewProvider` (no dependencies).
* ``cloud`` -> validates required config (model + API-key-env), then raises
  :class:`NotImplementedError` — the real external call is not implemented in
  this slice and no API call is made.
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
    """Build the configured machine-review provider without any real model call.

    Off returns the disabled provider. Cloud/local validate their required
    configuration and then raise :class:`NotImplementedError` (the real
    provider is a later slice). The fake provider is never returned here.
    """
    mode = settings_obj.ai_review_assistant_mode

    if mode == "off":
        return DisabledMachineReviewProvider()

    if mode == "cloud":
        _validate_cloud_config(settings_obj)
        raise NotImplementedError(
            "Cloud machine-review provider is not implemented yet; "
            "no external model call is made."
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
