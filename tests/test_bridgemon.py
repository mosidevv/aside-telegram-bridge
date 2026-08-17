"""Hermetic standard-library tests for bridgemon."""

import importlib.util
import json
import os
import shutil
import tempfile
import unittest


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakePrint:
    def __init__(self, loaded):
        self.loaded = loaded

    def __call__(self, label):
        class Result:
            pass

        result = Result()
        result.returncode = 0 if label in self.loaded else 1
        result.stdout = self.loaded.get(label, "")
        return result


class BridgemonTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bridgemon-test-")
        self.root = self.tmp.name
        self.old_dir = os.environ.get("BRIDGEMON_DIR")
        self.old_config = os.environ.get("BRIDGEMON_CONFIG")
        os.environ["BRIDGEMON_DIR"] = self.root
        os.environ["BRIDGEMON_CONFIG"] = os.path.join(
            self.root, "config.json")

        spec = importlib.util.spec_from_file_location(
            "bridgemon_tested_%s" % id(self),
            os.path.join(HERE, "bridgemon.py"),
        )
        self.bm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bm)

        self.agents = os.path.join(self.root, "LaunchAgents")
        os.makedirs(self.agents, exist_ok=True)
        self.bm._agents_dir = lambda: self.agents

    def tearDown(self):
        if self.old_dir is None:
            os.environ.pop("BRIDGEMON_DIR", None)
        else:
            os.environ["BRIDGEMON_DIR"] = self.old_dir
        if self.old_config is None:
            os.environ.pop("BRIDGEMON_CONFIG", None)
        else:
            os.environ["BRIDGEMON_CONFIG"] = self.old_config
        self.tmp.cleanup()

    def write(self, path, text=""):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def write_config(self, section=None):
        config = {"token": "unused", "chat_id": 1}
        if section is not None:
            config["miniapp"] = section
        self.write(os.path.join(self.root, "config.json"), json.dumps(config))

    def detect(self, loaded, plists=()):
        self.bm._print_service = FakePrint(loaded)
        for name in os.listdir(self.agents):
            os.remove(os.path.join(self.agents, name))
        for name in plists:
            self.write(os.path.join(self.agents, name + ".plist"))
        self.bm._LABEL_CACHE.clear()
        return self.bm._detect_label()

    def test_tunnel_url_parsing(self):
        self.assertEqual(
            self.bm.parse_tunnel_url([
                "public url: https://old-one-here.trycloudflare.com",
                "tunnel exited (1); restarting in 1000ms",
                "public url: https://fresh-name-goes.trycloudflare.com",
            ]),
            "https://fresh-name-goes.trycloudflare.com",
        )
        self.assertIsNone(
            self.bm.parse_tunnel_url(["see https://aside.so for details"]))
        self.assertEqual(
            self.bm.parse_tunnel_url([
                '{"msg":"public url: https://a-b-c.trycloudflare.com"}',
            ]),
            "https://a-b-c.trycloudflare.com",
        )
        self.assertIsNone(self.bm.parse_tunnel_url([]))

    def test_last_error_parsing(self):
        self.assertIsNotNone(self.bm.parse_last_error([
            '{"level":30,"msg":"ok"}',
            '{"level":50,"msg":"boom"}',
        ]))
        self.assertIn(
            "second",
            self.bm.parse_last_error(["error: first", "error: second"]),
        )
        self.assertIsNone(
            self.bm.parse_last_error(["listening on 8790", "public url: x"]))

    def test_integer_arguments_default_safely(self):
        self.assertEqual(self.bm._int_arg([], 30), 30)
        self.assertEqual(self.bm._int_arg(["5"], 30), 5)
        self.assertEqual(self.bm._int_arg(["oops"], 30), 30)

    def test_git_checkout_detection_includes_worktrees(self):
        self.assertFalse(self.bm._is_git_repo())
        os.makedirs(os.path.join(self.root, ".git"))
        self.assertTrue(self.bm._is_git_repo())
        shutil.rmtree(os.path.join(self.root, ".git"))
        self.write(
            os.path.join(self.root, ".git"),
            "gitdir: /somewhere/.git/worktrees/x\n",
        )
        self.assertTrue(self.bm._is_git_repo())

    def test_live_label_outranks_stale_plist(self):
        self.assertEqual(
            self.detect({
                "com.saiamartya.aside-telegram-bridge": "pid = 42",
            }),
            "com.saiamartya.aside-telegram-bridge",
        )
        self.assertEqual(
            self.detect(
                {"com.saiamartya.aside-telegram-bridge": "pid = 42"},
                plists=["com.aside.telegram-bridge"],
            ),
            "com.saiamartya.aside-telegram-bridge",
        )

    def test_live_label_with_pid_wins_when_both_loaded(self):
        self.assertEqual(
            self.detect({
                "com.aside.telegram-bridge": "state = waiting",
                "com.saiamartya.aside-telegram-bridge": "pid = 7",
            }),
            "com.saiamartya.aside-telegram-bridge",
        )

    def test_label_fallbacks_and_cache(self):
        self.assertEqual(
            self.detect({}, plists=["com.saiamartya.aside-telegram-bridge"]),
            "com.saiamartya.aside-telegram-bridge",
        )
        self.assertEqual(self.detect({}), "com.aside.telegram-bridge")
        self.assertEqual(self.bm.label(), self.bm.label())

    def test_miniapp_config_defaults_and_logs(self):
        self.write_config(None)
        self.assertEqual(self.bm.miniapp_section(), {})
        self.assertEqual(self.bm.miniapp_port(), self.bm.DEFAULT_MINIAPP_PORT)

        self.write_config({
            "port": "8791",
            "tunnel": "cloudflared",
            "state_dir": self.root,
            "auto_register_menu": True,
        })
        self.assertEqual(self.bm.miniapp_port(), 8791)

        self.write_config({"port": "not-a-port"})
        self.assertEqual(self.bm.miniapp_port(), self.bm.DEFAULT_MINIAPP_PORT)

        self.write_config({"state_dir": self.root})
        logs = self.bm.miniapp_logs()
        self.assertEqual(len(logs), 3)
        self.assertTrue(logs[0].endswith("miniapp.log"))

    def test_bare_tree_has_no_miniapp_even_when_user_has_real_plist(self):
        self.write_config(None)
        self.assertFalse(self.bm.miniapp_installed())

    def test_miniapp_installation_evidence(self):
        self.write_config({"port": 8790})
        self.assertTrue(self.bm.miniapp_installed())

        self.write_config(None)
        os.makedirs(self.bm.MINIAPP_DIR, exist_ok=True)
        self.assertTrue(self.bm.miniapp_installed())

    def test_dist_snapshot_and_restore(self):
        server_dist = os.path.join(self.bm.MINIAPP_DIR, "server", "dist")
        web_dist = os.path.join(self.bm.MINIAPP_DIR, "web", "dist")
        self.write(os.path.join(server_dist, "index.js"), "// good build\n")
        self.write(os.path.join(web_dist, "index.html"), "<!-- good -->\n")
        os.makedirs(self.bm.BACKUP_DIR, exist_ok=True)

        snapshot = self.bm._snapshot_miniapp_dist("20260727-000000")
        self.assertTrue(snapshot and os.path.isdir(snapshot))

        shutil.rmtree(web_dist)
        self.write(os.path.join(server_dist, "index.js"), "// broken\n")
        self.assertTrue(self.bm._restore_miniapp_dist(snapshot))
        with open(os.path.join(server_dist, "index.js")) as handle:
            self.assertEqual(handle.read(), "// good build\n")
        self.assertTrue(os.path.exists(os.path.join(web_dist, "index.html")))
        self.assertFalse(self.bm._restore_miniapp_dist(None))

    def test_command_table_preserves_operator_commands(self):
        for name in (
            "status", "update", "rollback", "logs", "init", "watch",
            "miniapp", "help", "--help", "-h",
        ):
            with self.subTest(name=name):
                self.assertIn(name, self.bm.COMMANDS)


if __name__ == "__main__":
    unittest.main()
