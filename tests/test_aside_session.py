"""Hermetic tests for model qualification and session creation."""

import contextlib
import io
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock


class ModelQualificationTests(unittest.TestCase):
    def setUp(self):
        try:
            import aside_session
        except ImportError:
            aside_session = None
        self.assertIsNotNone(
            aside_session,
            "production module aside_session.py is not implemented",
        )
        self.module = aside_session
        self.tmp = tempfile.TemporaryDirectory(prefix="aside-model-test-")

    def tearDown(self):
        self.tmp.cleanup()

    def write_json(self, value):
        path = os.path.join(self.tmp.name, "credentials.json")
        with open(path, "w") as handle:
            json.dump(value, handle)
        return path

    def write_text(self, value):
        path = os.path.join(self.tmp.name, "credentials.json")
        with open(path, "w") as handle:
            handle.write(value)
        return path

    def test_qualified_connected_model_is_preserved(self):
        resolved = self.module.resolve_model(
            "openai-codex/gpt-5.6-sol",
            {},
            frozenset({"openai-codex"}),
        )
        self.assertEqual(resolved.model_id, "gpt-5.6-sol")
        self.assertEqual(resolved.provider_id, "openai-codex")
        self.assertEqual(
            resolved.qualified_model,
            "openai-codex/gpt-5.6-sol",
        )

    def test_bare_model_uses_merged_mapping(self):
        resolved = self.module.resolve_model(
            "gpt-5.6-sol",
            {"gpt-5.6-sol": "openai-codex/gpt-5.6-sol"},
            frozenset({"openai-codex"}),
        )
        self.assertEqual(
            resolved.qualified_model,
            "openai-codex/gpt-5.6-sol",
        )

    def test_unmapped_bare_model_fails_before_exec(self):
        with self.assertRaises(self.module.ModelResolutionError) as caught:
            self.module.resolve_model(
                "future-model",
                {},
                frozenset({"openai-codex"}),
            )
        self.assertEqual(caught.exception.code, "configuration")
        self.assertIn("model_ids", caught.exception.user_message)

    def test_unconnected_provider_fails_before_exec(self):
        with self.assertRaises(self.module.ModelResolutionError) as caught:
            self.module.resolve_model(
                "openai-codex/gpt-5.6-sol",
                {},
                frozenset({"claude-code"}),
            )
        self.assertEqual(caught.exception.code, "configuration")
        self.assertNotIn("token", caught.exception.diagnostic.lower())

    def test_config_override_replaces_builtin_mapping(self):
        merged = self.module.merge_model_ids({
            "model_ids": {
                "gpt-5.6-sol": "test-provider/test-model",
            },
        })
        self.assertEqual(
            merged["gpt-5.6-sol"],
            "test-provider/test-model",
        )
        self.assertIn("claude-sonnet-5", merged)

    def test_provider_reader_returns_only_top_level_keys(self):
        path = self.write_json({
            "openai-codex": {"access": "DO-NOT-RETURN"},
            "claude-code": {"api_key": "DO-NOT-RETURN-EITHER"},
        })
        providers = self.module.read_connected_providers(path)
        self.assertEqual(
            providers,
            frozenset({"openai-codex", "claude-code"}),
        )
        self.assertNotIn("DO-NOT-RETURN", repr(providers))

    def test_malformed_credentials_fail_closed(self):
        path = self.write_text("not-json")
        with self.assertRaises(self.module.ModelResolutionError) as caught:
            self.module.read_connected_providers(path)
        self.assertEqual(caught.exception.code, "configuration")

    def test_qualified_model_rejects_whitespace_and_extra_slash(self):
        for selected in (
            "openai-codex/gpt 5.6-sol",
            "openai-codex/family/gpt-5.6-sol",
            "openai-codex/gpt-5.6-sol\x00",
        ):
            with self.subTest(selected=selected):
                with self.assertRaises(self.module.ModelResolutionError):
                    self.module.resolve_model(
                        selected,
                        {},
                        frozenset({"openai-codex"}),
                    )


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.callbacks = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        for callback in list(self.callbacks):
            callback(self.now)


class SessionCreationTests(unittest.TestCase):
    marker = "marker prompt"

    def setUp(self):
        import aside_session

        self.module = aside_session
        self.tmp = tempfile.TemporaryDirectory(prefix="aside-session-test-")
        self.sessions = os.path.join(self.tmp.name, "sessions")
        os.makedirs(self.sessions)
        self.session_dir = os.path.join(self.sessions, "2026-08-17_valid")
        self.clock = FakeClock()
        self.resolved = aside_session.ResolvedModel(
            model_id="gpt-5.6-sol",
            provider_id="openai-codex",
            qualified_model="openai-codex/gpt-5.6-sol",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write_transcript(self, records, session_dir=None):
        target = session_dir or self.session_dir
        os.makedirs(target, exist_ok=True)
        path = os.path.join(target, "messages.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def publish_valid_session(self, marker=None, session_dir=None):
        self.write_transcript([
            {
                "role": "user",
                "content": [{"type": "text", "text": marker or self.marker}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "stopReason": "end_turn",
            },
        ], session_dir=session_dir)

    def publish_error(self, message):
        self.write_transcript([
            {
                "role": "user",
                "content": [{"type": "text", "text": self.marker}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": message}],
                "stopReason": "error",
            },
        ])

    def create(self, execute, timeout_seconds=2.0):
        return self.module.create_new_session(
            resolved=self.resolved,
            prompt=self.marker,
            sessions_dir=self.sessions,
            marker=self.marker,
            execute=execute,
            timeout_seconds=timeout_seconds,
            poll_interval=0.25,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
        )

    def test_exit_zero_without_new_session_is_not_success(self):
        result = self.create(lambda _model: (0, "", ""), timeout_seconds=1.0)
        self.assertEqual(result.status, "timed_out")
        self.assertEqual(result.error_code, "session")

    def test_directory_without_transcript_is_not_success(self):
        os.makedirs(os.path.join(self.sessions, "2026-08-17_missing"))
        result = self.create(lambda _model: (0, "", ""), timeout_seconds=1.0)
        self.assertNotEqual(result.status, "created")

    def test_prompt_only_transcript_is_not_a_usable_session(self):
        def execute(_model):
            self.write_transcript([{
                "role": "user",
                "content": [{"type": "text", "text": self.marker}],
            }])
            return 0, "", ""

        result = self.create(execute, timeout_seconds=1.0)
        self.assertNotEqual(result.status, "created")
        self.assertEqual(result.error_code, "session")

    def test_nonterminal_assistant_record_is_not_a_usable_session(self):
        def execute(_model):
            self.write_transcript([
                {
                    "role": "user",
                    "content": [{"type": "text", "text": self.marker}],
                },
                {
                    "role": "assistant",
                    "content": [],
                    "stopReason": "tool_use",
                },
            ])
            return 0, "", ""

        result = self.create(execute, timeout_seconds=1.0)
        self.assertNotEqual(result.status, "created")
        self.assertEqual(result.error_code, "session")

    def test_valid_session_after_more_than_one_second_is_created(self):
        def publish(now):
            if now >= 1.5 and not os.path.exists(self.session_dir):
                self.publish_valid_session()

        self.clock.callbacks.append(publish)
        result = self.create(lambda _model: (0, "", ""))
        self.assertEqual(result.status, "created")
        self.assertEqual(result.session_id, "valid")

    def test_session_marker_matching_is_case_insensitive(self):
        self.marker = "permanent Telegram thread"

        def execute(_model):
            self.publish_valid_session("permanent telegram thread")
            return 0, "", ""

        result = self.create(execute)
        self.assertEqual(result.status, "created")

    def test_availability_phrase_matching_is_case_insensitive(self):
        def execute(_model):
            self.publish_error(
                "Requested model Is Not Available For This Account."
            )
            return 0, "", ""

        result = self.create(execute)
        self.assertEqual(result.error_code, "availability")

    def test_transcript_provider_error_is_availability_rejection(self):
        def execute(_model):
            self.publish_error(
                "Requested model openai-codex/gpt-5.6-sol "
                "is not available for this account."
            )
            return 0, "", ""

        result = self.create(execute)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.error_code, "availability")
        self.assertIn("not available", result.user_message.lower())

    def test_nonzero_process_error_is_process_rejection(self):
        result = self.create(lambda _model: (1, "", "safe process failure"))
        self.assertEqual(result.error_code, "process")
        self.assertNotIn("couldn't find its id", result.user_message)

    def test_stderr_availability_error_is_safely_classified(self):
        secret_output = (
            "Requested model is not available for this account. "
            "SECRET-OUTPUT-DO-NOT-RETURN"
        )
        result = self.create(lambda _model: (0, "", secret_output))
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.error_code, "availability")
        self.assertNotIn(secret_output, result.diagnostic)
        self.assertNotIn("SECRET-OUTPUT", result.user_message)

    def test_execute_receives_only_qualified_model(self):
        received = []

        def execute(model):
            received.append(model)
            self.publish_valid_session()
            return 0, "", ""

        result = self.create(execute)
        self.assertEqual(result.status, "created")
        self.assertEqual(received, ["openai-codex/gpt-5.6-sol"])

    def test_multiple_matching_sessions_fail_safe(self):
        first = os.path.join(self.sessions, "2026-08-17_first")
        second = os.path.join(self.sessions, "2026-08-17_second")

        def execute(_model):
            self.publish_valid_session(session_dir=first)
            self.publish_valid_session(session_dir=second)
            return 0, "", ""

        result = self.create(execute)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.error_code, "session")

    def test_invalid_json_only_transcript_is_not_success(self):
        def execute(_model):
            os.makedirs(self.session_dir)
            with open(
                os.path.join(self.session_dir, "messages.jsonl"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("not-json\n")
            return 0, "", ""

        result = self.create(execute, timeout_seconds=1.0)
        self.assertNotEqual(result.status, "created")

    def test_unreadable_transcript_is_not_success(self):
        def execute(_model):
            os.makedirs(os.path.join(self.session_dir, "messages.jsonl"))
            return 0, "", ""

        result = self.create(execute, timeout_seconds=1.0)
        self.assertNotEqual(result.status, "created")

    def test_diagnostic_never_contains_prompt_or_transcript_error(self):
        secret_prompt = "SECRET-PROMPT-DO-NOT-RETURN"
        secret_error = "SECRET-TRANSCRIPT-DO-NOT-RETURN"
        self.marker = secret_prompt

        def execute(_model):
            self.publish_error(secret_error)
            return 0, "", ""

        result = self.create(execute)
        self.assertNotIn(secret_prompt, result.diagnostic)
        self.assertNotIn(secret_error, result.diagnostic)

    def test_smoke_cli_never_calls_aside_repl_or_prints_prompt(self):
        config_path = os.path.join(self.tmp.name, "config.json")
        state_path = os.path.join(self.tmp.name, "state.json")
        credentials_path = os.path.join(self.tmp.name, "credentials.json")
        with open(credentials_path, "w", encoding="utf-8") as handle:
            json.dump({"openai-codex": {"access": "DO-NOT-RETURN"}}, handle)
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({
                "default_model": "gpt-5.6-sol",
                "sessions_dir": self.sessions,
                "credentials_path": credentials_path,
            }, handle)
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump({"model": "gpt-5.6-sol"}, handle)

        calls = []
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            self.publish_valid_session("Reply exactly: ok")
            return completed

        stdout = io.StringIO()
        with mock.patch("aside_session.subprocess.run", side_effect=fake_run):
            with contextlib.redirect_stdout(stdout):
                code = self.module.main([
                    "smoke",
                    "--config",
                    config_path,
                    "--state",
                    state_path,
                    "--timeout",
                    "2",
                ])

        self.assertEqual(code, 0)
        self.assertTrue(all(call[1] == "exec" for call in calls))
        self.assertTrue(all("repl" not in call for call in calls))
        self.assertNotIn("Reply exactly: ok", stdout.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            set(payload),
            {"status", "error_code", "qualified_model", "session_id", "diagnostic"},
        )


class BridgeSessionIntegrationTests(unittest.TestCase):
    def setUp(self):
        import aside_session

        self.aside_session = aside_session
        self.tmp = tempfile.TemporaryDirectory(prefix="aside-bridge-test-")
        self.sessions = os.path.join(self.tmp.name, "sessions")
        os.makedirs(self.sessions)
        self.credentials = os.path.join(self.tmp.name, "credentials.json")
        self.config = os.path.join(self.tmp.name, "config.json")
        self.state_path = os.path.join(self.tmp.name, "state.json")
        with open(self.credentials, "w", encoding="utf-8") as handle:
            json.dump({"openai-codex": {"access": "DO-NOT-RETURN"}}, handle)
        with open(self.config, "w", encoding="utf-8") as handle:
            json.dump({
                "token": "fake-token",
                "chat_id": 123,
                "owner_name": "tester",
                "default_model": "gpt-5.6-sol",
                "sessions_dir": self.sessions,
                "credentials_path": self.credentials,
                "aside_cli": "/bin/false",
            }, handle)
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump({
                "session_id": "old-session",
                "model": "gpt-5.6-sol",
            }, handle)

        bridge_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "bridge.py"
        )
        spec = importlib.util.spec_from_file_location(
            "bridge_phase0_test", bridge_path
        )
        bridge = importlib.util.module_from_spec(spec)
        real_open = open
        account_reads = []

        def guarded_open(path, *args, **kwargs):
            rendered = os.path.abspath(os.path.expanduser(os.fspath(path)))
            if rendered.endswith(os.path.join(".aside", "accounts.json")):
                account_reads.append(rendered)
                raise AssertionError("explicit config must not read Aside accounts")
            return real_open(path, *args, **kwargs)

        with mock.patch.dict(os.environ, {
            "ASIDE_BRIDGE_DIR": self.tmp.name,
            "ASIDE_BRIDGE_CONFIG": self.config,
        }), mock.patch("builtins.open", side_effect=guarded_open):
            spec.loader.exec_module(bridge)
        self.assertEqual(account_reads, [])
        self.assertEqual(
            bridge.BRIDGE_DIR,
            self.tmp.name,
            "bridge must honor the hermetic ASIDE_BRIDGE_DIR seam",
        )
        self.bridge = bridge
        self.messages = []
        bridge.send_text = self.messages.append
        bridge.time.sleep = lambda _seconds: None
        self.real_run_aside = bridge.run_aside
        self.real_resolve_configured_model = bridge.resolve_configured_model
        bridge.run_aside = lambda *_args, **_kwargs: (0, "", "")
        bridge.newest_session_id = lambda **_kwargs: "unexpected-session"
        bridge.resolve_configured_model = lambda _selection: (
            aside_session.ResolvedModel(
                model_id="gpt-5.6-sol",
                provider_id="openai-codex",
                qualified_model="openai-codex/gpt-5.6-sol",
            )
        )

    def tearDown(self):
        self.tmp.cleanup()

    def result(self, status, session_id=None, error_code=None):
        return self.aside_session.SessionCreationResult(
            status=status,
            error_code=error_code,
            model_id="gpt-5.6-sol",
            provider_id="openai-codex",
            qualified_model="openai-codex/gpt-5.6-sol",
            session_id=session_id,
            user_message=(
                "Aside session created and verified."
                if status == "created"
                else "The selected model is not available."
            ),
            diagnostic="safe diagnostic",
        )

    def test_heavy_new_does_not_switch_on_rejected_result(self):
        old_session = self.bridge.state.get("session_id")
        self.bridge.create_new_session = lambda **_kwargs: self.result(
            "rejected", error_code="availability"
        )
        prepared = []
        self.bridge._prepare_new_session = prepared.append

        self.bridge.heavy_new()

        self.assertEqual(self.bridge.state.get("session_id"), old_session)
        self.assertEqual(prepared, [])
        self.assertIn("not available", self.messages[-1].lower())

    def test_heavy_new_switches_only_after_created_result(self):
        self.bridge.create_new_session = lambda **_kwargs: self.result(
            "created", session_id="verified-session"
        )
        prepared = []
        self.bridge._prepare_new_session = prepared.append

        self.bridge.heavy_new()

        self.assertEqual(
            self.bridge.state.get("session_id"), "verified-session"
        )
        self.assertEqual(prepared, ["verified-session"])

    def test_run_aside_rejects_unmapped_bare_model_without_exec(self):
        calls = []
        self.bridge.run_aside = self.real_run_aside
        self.bridge.resolve_configured_model = self.real_resolve_configured_model
        self.bridge.subprocess.run = lambda *args, **kwargs: calls.append(
            (args, kwargs)
        )

        code, _out, message = self.bridge.run_aside(
            "safe prompt", model="future-unmapped-model"
        )

        self.assertLess(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("model_ids", message)


if __name__ == "__main__":
    unittest.main()
