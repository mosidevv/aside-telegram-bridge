#!/usr/bin/env python3
"""Safe model qualification and Aside session creation helpers.

The bridge accepts friendly model names in its local configuration, but Aside
requires a provider-qualified ``provider/model`` identifier.  This module owns
that trust boundary so callers cannot accidentally let Aside guess a provider.
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, FrozenSet, Mapping


DEFAULT_MODEL_IDS = {
    "claude-sonnet-5": "claude-code/claude-sonnet-5",
    "claude-fable-5": "claude-code/claude-fable-5",
    "claude-opus-4-8": "claude-code/claude-opus-4-8",
    "claude-opus-5": "claude-code/claude-opus-5",
    "claude-haiku-4-5": "claude-code/claude-haiku-4-5",
    "gpt-5.6-sol": "openai-codex/gpt-5.6-sol",
    "gpt-5.6-luna": "openai-codex/gpt-5.6-luna",
    "gpt-5.6-terra": "openai-codex/gpt-5.6-terra",
    "gpt-5.5": "openai-codex/gpt-5.5",
    "gpt-5.4": "openai-codex/gpt-5.4",
    "gpt-5.4-mini": "openai-codex/gpt-5.4-mini",
}


@dataclass(frozen=True)
class ResolvedModel:
    """A model selection proven to have an explicit connected provider."""

    model_id: str
    provider_id: str
    qualified_model: str


class ModelResolutionError(ValueError):
    """Safe, classified failure at the model qualification boundary."""

    def __init__(self, code: str, user_message: str, diagnostic: str):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.diagnostic = diagnostic


def merge_model_ids(config: Mapping[str, object]) -> Dict[str, str]:
    """Return built-in model IDs merged with valid local overrides."""

    merged = dict(DEFAULT_MODEL_IDS)
    overrides = config.get("model_ids", {}) if isinstance(config, Mapping) else {}
    if isinstance(overrides, Mapping):
        for model_id, qualified_model in overrides.items():
            if isinstance(model_id, str) and isinstance(qualified_model, str):
                merged[model_id] = qualified_model
    return merged


def read_connected_providers(credentials_path: str) -> FrozenSet[str]:
    """Read provider IDs only; credential values never leave this function."""

    path = os.path.expanduser(credentials_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            credentials = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ModelResolutionError(
            "configuration",
            "Aside provider credentials could not be read.",
            "Provider credential metadata is missing or malformed: {}".format(
                type(exc).__name__
            ),
        ) from exc

    if not isinstance(credentials, dict):
        raise ModelResolutionError(
            "configuration",
            "Aside provider credentials are not configured correctly.",
            "Provider credential metadata must be a JSON object.",
        )

    return frozenset(
        provider_id
        for provider_id in credentials
        if isinstance(provider_id, str) and provider_id
    )


def _split_qualified_model(qualified_model: str):
    if (
        not isinstance(qualified_model, str)
        or qualified_model.count("/") != 1
        or any(character.isspace() for character in qualified_model)
    ):
        raise ModelResolutionError(
            "configuration",
            "The selected model must use a valid provider/model identifier.",
            "Rejected malformed provider-qualified model identifier.",
        )

    provider_id, model_id = qualified_model.split("/", 1)
    if not provider_id or not model_id:
        raise ModelResolutionError(
            "configuration",
            "The selected model must use a valid provider/model identifier.",
            "Rejected incomplete provider-qualified model identifier.",
        )
    return provider_id, model_id


def resolve_model(
    selection: str,
    model_ids: Mapping[str, str],
    connected_providers: FrozenSet[str],
) -> ResolvedModel:
    """Resolve a selection without permitting implicit provider inference."""

    if not isinstance(selection, str) or not selection or selection != selection.strip():
        raise ModelResolutionError(
            "configuration",
            "The selected model is empty or malformed.",
            "Rejected empty or padded model selection.",
        )

    if "/" in selection:
        qualified_model = selection
    else:
        qualified_model = model_ids.get(selection)
        if not isinstance(qualified_model, str):
            raise ModelResolutionError(
                "configuration",
                "The selected model is not mapped in config model_ids.",
                "No provider-qualified mapping exists for the selected model.",
            )

    provider_id, model_id = _split_qualified_model(qualified_model)
    if provider_id not in connected_providers:
        raise ModelResolutionError(
            "configuration",
            "The selected model provider is not connected in Aside.",
            "Provider {!r} is not present in credential metadata.".format(provider_id),
        )

    return ResolvedModel(
        model_id=model_id,
        provider_id=provider_id,
        qualified_model=qualified_model,
    )
