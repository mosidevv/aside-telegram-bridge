#!/usr/bin/env python3
"""Safe model qualification and Aside session creation helpers.

The bridge accepts friendly model names in its local configuration, but Aside
requires a provider-qualified ``provider/model`` identifier.  This module owns
that trust boundary so callers cannot accidentally let Aside guess a provider.
"""

import argparse
import json
import os
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Mapping, Optional, Tuple


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


@dataclass(frozen=True)
class SessionCreationResult:
    """Non-secret evidence describing a requested session creation."""

    status: str
    error_code: Optional[str]
    model_id: str
    provider_id: Optional[str]
    qualified_model: Optional[str]
    session_id: Optional[str]
    user_message: str
    diagnostic: str


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
        or any(
            unicodedata.category(character).startswith("C")
            for character in qualified_model
        )
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


def _session_result(
    resolved: ResolvedModel,
    status: str,
    error_code: Optional[str],
    session_id: Optional[str],
    user_message: str,
    diagnostic: str,
) -> SessionCreationResult:
    return SessionCreationResult(
        status=status,
        error_code=error_code,
        model_id=resolved.model_id,
        provider_id=resolved.provider_id,
        qualified_model=resolved.qualified_model,
        session_id=session_id,
        user_message=user_message,
        diagnostic=diagnostic,
    )


def _session_directories(sessions_dir: str):
    try:
        names = os.listdir(sessions_dir)
    except OSError:
        return frozenset()
    return frozenset(
        name
        for name in names
        if "_" in name and os.path.isdir(os.path.join(sessions_dir, name))
    )


def _record_contains(record, expected: str) -> bool:
    if isinstance(record, str):
        return expected.casefold() in record.casefold()
    if isinstance(record, list):
        return any(_record_contains(value, expected) for value in record)
    if isinstance(record, dict):
        return any(_record_contains(value, expected) for value in record.values())
    return False


def _assistant_error(record) -> Tuple[bool, bool]:
    """Return whether a record is an assistant error and an availability error."""

    if not isinstance(record, dict):
        return False, False
    message = record.get("message")
    candidate = message if isinstance(message, dict) else record
    if candidate.get("role") != "assistant" or candidate.get("stopReason") != "error":
        return False, False
    unavailable = _record_contains(
        candidate,
        "is not available for this account",
    )
    return True, unavailable


def _assistant_completed(record) -> bool:
    if not isinstance(record, dict):
        return False
    message = record.get("message")
    candidate = message if isinstance(message, dict) else record
    return bool(
        candidate.get("role") == "assistant"
        and candidate.get("stopReason") in ("stop", "length")
    )


def _inspect_transcript(path: str, marker: str):
    """Return ``(state, availability_error)`` without returning transcript data."""

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return "unreadable", False

    if not lines:
        return "pending", False

    parsed = []
    invalid = False
    for line in lines:
        if not line.strip():
            continue
        try:
            parsed.append(json.loads(line))
        except ValueError:
            invalid = True

    if not parsed:
        return ("invalid" if invalid else "pending"), False
    if not any(_record_contains(record, marker) for record in parsed):
        return ("invalid" if invalid else "unmatched"), False
    if invalid:
        return "invalid", False

    for record in parsed:
        is_error, unavailable = _assistant_error(record)
        if is_error:
            return "error", unavailable
    if any(_assistant_completed(record) for record in parsed):
        return "valid", False
    return "pending", False


def _process_reports_unavailable(stdout: str, stderr: str) -> bool:
    phrase = "is not available for this account"
    for output in (stdout, stderr):
        if not isinstance(output, str):
            continue
        bounded = output[:4096]
        if len(output) > 4096:
            bounded += output[-4096:]
        if phrase in bounded.casefold():
            return True
    return False


def create_new_session(
    resolved: ResolvedModel,
    prompt: str,
    sessions_dir: str,
    marker: str,
    execute: Callable[[str], Tuple[int, str, str]],
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> SessionCreationResult:
    """Create a session and require transcript evidence before reporting success."""

    del prompt  # The executor owns prompt delivery; results must never echo it.
    sessions_dir = os.path.expanduser(sessions_dir)
    before = _session_directories(sessions_dir)

    try:
        returncode, stdout, stderr = execute(resolved.qualified_model)
    except Exception as exc:  # noqa: BLE001 - convert process boundary failures
        return _session_result(
            resolved,
            "rejected",
            "process",
            None,
            "Aside could not start the requested session.",
            "Session process raised {}.".format(type(exc).__name__),
        )

    if _process_reports_unavailable(stdout, stderr):
        return _session_result(
            resolved,
            "rejected",
            "availability",
            None,
            "The selected model is not available for this Aside account.",
            "The session process reported model unavailability.",
        )

    if returncode != 0:
        return _session_result(
            resolved,
            "rejected",
            "process",
            None,
            "Aside rejected the session request before it became usable.",
            "Session process exited with status {}.".format(returncode),
        )

    deadline = monotonic() + max(0.0, timeout_seconds)
    while True:
        candidates = sorted(_session_directories(sessions_dir) - before)
        matched = []
        terminal_invalid = False
        for name in candidates:
            transcript = os.path.join(sessions_dir, name, "messages.jsonl")
            state, unavailable = _inspect_transcript(transcript, marker)
            if state == "valid":
                matched.append((name, False))
            elif state == "error":
                matched.append((name, unavailable))
            elif state in ("invalid", "unreadable"):
                terminal_invalid = True

        if len(matched) > 1:
            return _session_result(
                resolved,
                "rejected",
                "session",
                None,
                "Aside created ambiguous session evidence; no session was selected.",
                "More than one new transcript matched the session marker.",
            )
        if len(matched) == 1:
            name, unavailable = matched[0]
            session_id = name.rsplit("_", 1)[-1]
            state, _ignored = _inspect_transcript(
                os.path.join(sessions_dir, name, "messages.jsonl"), marker
            )
            if state == "error":
                error_code = "availability" if unavailable else "session"
                message = (
                    "The selected model is not available for this Aside account."
                    if unavailable
                    else "Aside reported an error while creating the session."
                )
                return _session_result(
                    resolved,
                    "rejected",
                    error_code,
                    None,
                    message,
                    "The matching session transcript ended with an assistant error.",
                )
            return _session_result(
                resolved,
                "created",
                None,
                session_id,
                "Aside session created and verified.",
                "One new session transcript matched the request marker.",
            )

        if monotonic() >= deadline:
            diagnostic = (
                "New session evidence was unreadable or invalid."
                if terminal_invalid
                else "No usable new session transcript appeared before the deadline."
            )
            return _session_result(
                resolved,
                "timed_out",
                "session",
                None,
                "Aside did not produce a verified usable session in time.",
                diagnostic,
            )
        sleep(max(0.01, poll_interval))


def _read_json_object(path: str, label: str):
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ModelResolutionError(
            "configuration",
            "The {} file could not be read.".format(label),
            "{} metadata is missing or malformed: {}.".format(
                label.capitalize(), type(exc).__name__
            ),
        ) from exc
    if not isinstance(value, dict):
        raise ModelResolutionError(
            "configuration",
            "The {} file must contain a JSON object.".format(label),
            "{} metadata has the wrong top-level type.".format(label.capitalize()),
        )
    return value


def _safe_cli_payload(result: SessionCreationResult):
    return {
        "status": result.status,
        "error_code": result.error_code,
        "qualified_model": result.qualified_model,
        "session_id": result.session_id,
        "diagnostic": result.diagnostic,
    }


def _configuration_result(selection: object, error: ModelResolutionError):
    model_id = selection if isinstance(selection, str) else ""
    return SessionCreationResult(
        status="rejected",
        error_code=error.code,
        model_id=model_id,
        provider_id=None,
        qualified_model=None,
        session_id=None,
        user_message=error.user_message,
        diagnostic=error.diagnostic,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify the Aside session boundary")
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("smoke", help="create one bounded harmless session")
    smoke.add_argument("--config", required=True)
    smoke.add_argument("--state")
    smoke.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    selection = ""
    try:
        config = _read_json_object(args.config, "config")
        state = _read_json_object(args.state, "state") if args.state else {}
        selection = state.get("model") or config.get("default_model")
        model_ids = merge_model_ids(config)
        credentials_path = config.get(
            "credentials_path", "~/.aside/u/0/credentials.json"
        )
        providers = read_connected_providers(credentials_path)
        resolved = resolve_model(selection, model_ids, providers)
    except ModelResolutionError as exc:
        result = _configuration_result(selection, exc)
        print(json.dumps(_safe_cli_payload(result), sort_keys=True))
        return 1

    prompt = "Reply exactly: ok"
    sessions_dir = os.path.expanduser(
        config.get("sessions_dir", "~/.aside/u/0/sessions")
    )
    aside_cli = os.path.expanduser(config.get("aside_cli", "aside"))

    def execute(qualified_model):
        try:
            completed = subprocess.run(
                [
                    aside_cli,
                    "exec",
                    "-m",
                    qualified_model,
                    "--effort",
                    "low",
                    "--",
                    prompt,
                ],
                capture_output=True,
                text=True,
                timeout=max(1.0, args.timeout),
                check=False,
            )
            return completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired:
            return 124, "", ""

    result = create_new_session(
        resolved=resolved,
        prompt=prompt,
        sessions_dir=sessions_dir,
        marker=prompt,
        execute=execute,
        timeout_seconds=max(0.0, args.timeout),
    )
    print(json.dumps(_safe_cli_payload(result), sort_keys=True))
    return 0 if result.status == "created" else 1


if __name__ == "__main__":
    raise SystemExit(main())
