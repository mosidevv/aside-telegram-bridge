"""Hermetic tests for model qualification and session creation."""

import json
import os
import tempfile
import unittest


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
        ):
            with self.subTest(selected=selected):
                with self.assertRaises(self.module.ModelResolutionError):
                    self.module.resolve_model(
                        selected,
                        {},
                        frozenset({"openai-codex"}),
                    )


if __name__ == "__main__":
    unittest.main()
