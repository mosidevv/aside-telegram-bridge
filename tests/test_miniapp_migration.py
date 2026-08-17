"""Hermetic tests for recoverable Mini App path migration."""

import importlib.util
import json
import os
import plistlib
import stat
import tempfile
import unittest


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MiniappMigrationFixture(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "miniapp_setup_tested_%s" % id(self),
            os.path.join(HERE, "miniapp", "setup-miniapp.py"),
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory(prefix="miniapp-migration-test-")
        self.repo = os.path.join(self.tmp.name, "repo")
        self.config_path = os.path.join(self.repo, "config.json")
        self.plist_path = os.path.join(self.tmp.name, "LaunchAgents", "service.plist")
        self.backups = os.path.join(self.tmp.name, "backups")
        self.node = os.path.join(self.tmp.name, "bin", "node")
        self.entry = os.path.join(
            self.repo, "miniapp", "server", "dist", "index.js"
        )
        self.write_bytes(self.node, b"#!/bin/sh\n", 0o700)
        self.write_bytes(self.entry, b"// built\n", 0o600)

    def tearDown(self):
        self.tmp.cleanup()

    def write_bytes(self, path, value, mode=0o600):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(value)
        os.chmod(path, mode)

    def read_bytes(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def seed(self):
        old_config = (
            b'{"token":"SECRET","miniapp":{"port":8790,'
            b'"state_dir":"/old","tunnel":"cloudflared",'
            b'"auto_register_menu":false,"custom":"preserve"}}\n'
        )
        old_plist = plistlib.dumps({
            "Label": self.module.LABEL,
            "WorkingDirectory": "/old",
        })
        self.write_bytes(self.config_path, old_config, 0o600)
        self.write_bytes(self.plist_path, old_plist, 0o600)
        return old_config, old_plist


class MiniappPathBuilderTests(MiniappMigrationFixture):
    def test_desired_section_replaces_only_stale_state_path(self):
        current = {
            "port": 8790,
            "tunnel": "cloudflared",
            "auto_register_menu": False,
            "state_dir": "/Users/mosi/aside-telegram-bridge",
            "custom": "preserve-me",
        }
        desired = self.module.desired_miniapp_section(
            "/repo/config.json", current, "/repo"
        )
        self.assertEqual(desired["state_dir"], "/repo")
        self.assertEqual(desired["custom"], "preserve-me")
        self.assertEqual(desired["tunnel"], "cloudflared")
        self.assertFalse(desired["auto_register_menu"])

    def test_plist_uses_current_repository_paths(self):
        plist = self.module.build_service_plist(
            "/repo/config.json",
            {"port": 8790, "state_dir": "/repo"},
            "/repo",
            "/usr/local/bin/node",
        )
        self.assertEqual(
            plist["ProgramArguments"],
            ["/usr/local/bin/node", "/repo/miniapp/server/dist/index.js"],
        )
        self.assertEqual(plist["WorkingDirectory"], "/repo/miniapp")
        self.assertEqual(
            plist["EnvironmentVariables"]["MINIAPP_CONFIG"],
            "/repo/config.json",
        )
        self.assertEqual(plist["StandardErrorPath"], "/repo/miniapp.err.log")

    def test_plist_never_embeds_config_secrets(self):
        plist = self.module.build_service_plist(
            "/repo/config.json",
            {"port": 8790, "token": "DO-NOT-EMBED"},
            "/repo",
            "/usr/local/bin/node",
        )
        self.assertNotIn("DO-NOT-EMBED", repr(plist))


class MiniappAtomicMigrationTests(MiniappMigrationFixture):
    def migrate(self, reload_service=lambda: True, check_health=lambda _port: True):
        return self.module.migrate_paths(
            config_path=self.config_path,
            plist_path=self.plist_path,
            repository_root=self.repo,
            node_path=self.node,
            reload_service=reload_service,
            check_health=check_health,
            backup_root=self.backups,
        )

    def test_success_preserves_unrelated_config_and_writes_owner_only_files(self):
        self.seed()
        reloads = []
        result = self.migrate(reload_service=lambda: reloads.append(True) or True)
        self.assertTrue(result.ok)
        self.assertTrue(result.changed)
        self.assertFalse(result.restored)
        self.assertEqual(reloads, [True])
        config = json.loads(self.read_bytes(self.config_path).decode("utf-8"))
        self.assertEqual(config["token"], "SECRET")
        self.assertEqual(config["miniapp"]["custom"], "preserve")
        self.assertEqual(config["miniapp"]["state_dir"], self.repo)
        plist = plistlib.loads(self.read_bytes(self.plist_path))
        self.assertEqual(plist["WorkingDirectory"], os.path.join(self.repo, "miniapp"))
        self.assertEqual(stat.S_IMODE(os.stat(self.config_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.plist_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(result.backup_dir).st_mode), 0o700)
        for name in ("config.json.original", "service.plist.original"):
            self.assertEqual(
                stat.S_IMODE(os.stat(os.path.join(result.backup_dir, name)).st_mode),
                0o600,
            )

    def test_failed_health_restores_exact_config_and_plist_bytes(self):
        old_config, old_plist = self.seed()
        reloads = []
        result = self.migrate(
            reload_service=lambda: reloads.append(True) or True,
            check_health=lambda _port: False,
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.restored)
        self.assertEqual(reloads, [True, True])
        self.assertEqual(self.read_bytes(self.config_path), old_config)
        self.assertEqual(self.read_bytes(self.plist_path), old_plist)
        self.assertEqual(stat.S_IMODE(os.stat(self.config_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.plist_path).st_mode), 0o600)

    def test_reload_failure_restores_both_files(self):
        old_config, old_plist = self.seed()
        calls = []

        def reload_service():
            calls.append(True)
            return len(calls) > 1

        result = self.migrate(reload_service=reload_service)
        self.assertFalse(result.ok)
        self.assertTrue(result.restored)
        self.assertEqual(self.read_bytes(self.config_path), old_config)
        self.assertEqual(self.read_bytes(self.plist_path), old_plist)

    def test_failed_restoration_reload_is_not_reported_as_restored(self):
        old_config, old_plist = self.seed()
        reloads = []

        def reload_service():
            reloads.append(True)
            return False

        result = self.migrate(reload_service=reload_service)
        self.assertFalse(result.ok)
        self.assertFalse(result.restored)
        self.assertEqual(reloads, [True, True])
        self.assertIn("service reload failed", result.message)
        self.assertEqual(self.read_bytes(self.config_path), old_config)
        self.assertEqual(self.read_bytes(self.plist_path), old_plist)

    def test_malformed_config_fails_before_reload(self):
        self.write_bytes(self.config_path, b"not-json\n")
        self.write_bytes(self.plist_path, plistlib.dumps({"Label": "old"}))
        calls = []
        result = self.migrate(reload_service=lambda: calls.append(True) or True)
        self.assertFalse(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(calls, [])

    def test_missing_node_or_entry_fails_before_writes(self):
        old_config, old_plist = self.seed()
        for missing in (self.node, self.entry):
            with self.subTest(missing=missing):
                os.remove(missing)
                result = self.migrate()
                self.assertFalse(result.ok)
                self.assertEqual(self.read_bytes(self.config_path), old_config)
                self.assertEqual(self.read_bytes(self.plist_path), old_plist)
                self.write_bytes(
                    missing,
                    b"#!/bin/sh\n" if missing == self.node else b"// built\n",
                    0o700 if missing == self.node else 0o600,
                )


if __name__ == "__main__":
    unittest.main()
