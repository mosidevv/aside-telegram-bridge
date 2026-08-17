"""Hermetic standard-library tests for bridgemon."""

import contextlib
import importlib.util
import io
import json
import os
import plistlib
import shutil
import tempfile
import unittest
from unittest import mock


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


class _BridgemonFixture(unittest.TestCase):
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


class BridgemonTestCase(_BridgemonFixture):

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

    def test_miniapp_path_migration_invokes_exact_noninteractive_command(self):
        self.write_config({"port": 8790, "state_dir": "/stale"})
        entry = os.path.join(
            self.bm.MINIAPP_DIR, "server", "dist", "index.js"
        )
        self.write(entry, "// built\n")
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return FakeCompleted(
                returncode=7,
                stdout="failed: health check failed; original files restored\n",
            )

        self.bm.subprocess.run = fake_run
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = self.bm.cmd_miniapp(["migrate-paths"])

        self.assertEqual(code, 7)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], [
            "/usr/bin/python3",
            os.path.join(self.bm.MINIAPP_DIR, "setup-miniapp.py"),
            "--migrate-paths",
            "--config",
            self.bm.config_path(),
            "--repository-root",
            self.root,
        ])
        self.assertIn("before migration", stdout.getvalue())
        self.assertIn("after migration", stdout.getvalue())

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
            "miniapp", "doctor", "help", "--help", "-h",
        ):
            with self.subTest(name=name):
                self.assertIn(name, self.bm.COMMANDS)


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def ok(stdout=""):
    return FakeCompleted(stdout=stdout)


class GitFake:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.results.get(args, FakeCompleted(
            returncode=99,
            stderr="unexpected git call: %r" % (args,),
        ))


class GitSyncTests(_BridgemonFixture):
    def git_repo(self):
        os.makedirs(os.path.join(self.root, ".git"), exist_ok=True)

    def configured(self, extra=None):
        results = {
            ("status", "--porcelain", "--untracked-files=no"): ok(""),
            ("rev-parse", "--abbrev-ref", "HEAD"): ok("feature\n"),
            ("config", "--get", "branch.feature.remote"): ok("fork\n"),
            ("config", "--get", "branch.feature.merge"): ok(
                "refs/heads/feature\n"
            ),
            ("remote", "get-url", "fork"): ok("git@example/fork.git\n"),
            ("rev-parse", "HEAD"): ok("aaaa\n"),
            ("fetch", "fork", "feature"): ok(""),
            ("rev-parse", "fork/feature"): ok("bbbb\n"),
            ("merge-base", "--is-ancestor", "HEAD", "fork/feature"): ok(""),
            ("merge", "--ff-only", "fork/feature"): ok(""),
        }
        results.update(extra or {})
        return GitFake(results)

    def test_git_sync_uses_configured_fork_upstream(self):
        self.git_repo()
        fake = self.configured()
        self.bm._git = fake

        result = self.bm._git_sync()

        self.assertTrue(result.ok)
        self.assertTrue(result.changed)
        self.assertEqual((result.remote, result.branch), ("fork", "feature"))
        self.assertIn(("fetch", "fork", "feature"), fake.calls)
        self.assertNotIn(("reset", "--hard", "fork/feature"), fake.calls)

    def test_non_git_deployment_is_an_explicit_safe_skip(self):
        result = self.bm._git_sync()
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(result.status, "skipped")

    def test_git_sync_blocks_dirty_tracked_source(self):
        self.git_repo()
        fake = self.configured({
            ("status", "--porcelain", "--untracked-files=no"): ok(
                " M bridge.py\n"
            ),
        })
        self.bm._git = fake
        result = self.bm._git_sync()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked")
        self.assertNotIn(("fetch", "fork", "feature"), fake.calls)

    def test_git_sync_blocks_missing_upstream(self):
        self.git_repo()
        fake = self.configured({
            ("config", "--get", "branch.feature.remote"): FakeCompleted(1),
        })
        self.bm._git = fake
        result = self.bm._git_sync()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked")

    def test_git_sync_fails_missing_remote(self):
        self.git_repo()
        fake = self.configured({
            ("remote", "get-url", "fork"): FakeCompleted(2, stderr="missing"),
        })
        self.bm._git = fake
        result = self.bm._git_sync()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")

    def test_git_sync_fails_fetch(self):
        self.git_repo()
        fake = self.configured({
            ("fetch", "fork", "feature"): FakeCompleted(1, stderr="offline"),
        })
        self.bm._git = fake
        result = self.bm._git_sync()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")

    def test_git_sync_blocks_non_fast_forward(self):
        self.git_repo()
        fake = self.configured({
            ("merge-base", "--is-ancestor", "HEAD", "fork/feature"):
                FakeCompleted(1),
        })
        self.bm._git = fake
        result = self.bm._git_sync()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked")
        self.assertNotIn(("merge", "--ff-only", "fork/feature"), fake.calls)

    def test_git_sync_reports_already_current(self):
        self.git_repo()
        fake = self.configured({
            ("rev-parse", "fork/feature"): ok("aaaa\n"),
        })
        self.bm._git = fake
        result = self.bm._git_sync()
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(result.status, "unchanged")

    def test_git_sync_fails_merge_without_reset(self):
        self.git_repo()
        fake = self.configured({
            ("merge", "--ff-only", "fork/feature"):
                FakeCompleted(1, stderr="merge failed"),
        })
        self.bm._git = fake
        result = self.bm._git_sync()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertFalse(any(call[:2] == ("reset", "--hard")
                             for call in fake.calls))

    def test_update_stops_before_restart_when_git_sync_blocks(self):
        self.write(self.bm.BRIDGE_PY, "print('safe')\n")
        self.bm._git_sync = lambda: self.bm.GitSyncResult(
            status="blocked",
            changed=False,
            ok=False,
            message="dirty tracked source",
        )
        restarted = []
        self.bm.kickstart_restart = lambda *_args: restarted.append(True)
        self.assertEqual(self.bm.cmd_update([]), 1)
        self.assertEqual(restarted, [])

    def test_update_stops_before_stale_miniapp_restart(self):
        self.write(self.bm.BRIDGE_PY, "print('safe')\n")
        self.write_config({"state_dir": self.root, "port": 8790})
        self.bm._git_sync = lambda: self.bm.GitSyncResult(
            status="skipped",
            changed=False,
            ok=True,
            message="local-only",
        )
        restarted = []
        self.bm.kickstart_restart = lambda *_args: restarted.append(True)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = self.bm.cmd_update([])
        self.assertEqual(code, 1)
        self.assertEqual(restarted, [])
        self.assertIn("bridgemon miniapp migrate-paths", stdout.getvalue())

    def test_failed_restart_never_overwrites_bridge_source(self):
        self.write(self.bm.BRIDGE_PY, "print('current')\n")
        self.write(os.path.join(self.root, "aside_session.py"), "VALUE = 1\n")
        self.bm._git_sync = lambda: self.bm.GitSyncResult(
            status="skipped",
            changed=False,
            ok=True,
            message="local-only",
        )
        self.bm._health_check = lambda: (False, "no fresh start")
        self.bm.kickstart_restart = lambda *_args: None
        with mock.patch.object(self.bm.shutil, "copy2") as copy2:
            code = self.bm.cmd_update([])
        self.assertEqual(code, 1)
        copy2.assert_not_called()
        with open(self.bm.BRIDGE_PY, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "print('current')\n")


class RollbackTests(_BridgemonFixture):
    def test_rollback_never_copies_last_good_over_bridge(self):
        self.write(self.bm.BRIDGE_PY, "current\n")
        self.write(self.bm.LAST_GOOD, "old\n")
        with mock.patch.object(self.bm.shutil, "copy2") as copy2:
            code = self.bm.cmd_rollback([])
        self.assertEqual(code, 1)
        copy2.assert_not_called()
        with open(self.bm.BRIDGE_PY, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "current\n")


class DeploymentHealthTests(_BridgemonFixture):
    def test_source_valid_requires_session_boundary_to_compile(self):
        self.write(self.bm.BRIDGE_PY, "print('safe')\n")
        self.assertFalse(self.bm._source_valid())
        self.assertFalse(self.bm._syntax_check()[0])

        self.write(
            os.path.join(self.root, "aside_session.py"),
            "def broken(:\n",
        )
        self.assertFalse(self.bm._source_valid())
        self.assertFalse(self.bm._syntax_check()[0])

        with open(os.path.join(self.root, "aside_session.py"), "wb") as handle:
            handle.write(b"# invalid utf-8: \xff\nVALUE = 1\n")
        self.assertFalse(self.bm._source_valid())
        self.assertFalse(self.bm._syntax_check()[0])

    def test_health_keeps_process_endpoint_and_product_separate(self):
        health = self.bm.DeploymentHealth(
            source_valid=True,
            installed_paths_valid=False,
            process_running=True,
            endpoint_healthy=True,
            product_ready=False,
            reasons=("installed Mini App paths are stale",),
        )
        self.assertTrue(health.process_running)
        self.assertTrue(health.endpoint_healthy)
        self.assertFalse(health.product_ready)

    def test_doctor_json_is_small_and_does_not_emit_secrets(self):
        sentinel = "SECRET-TOKEN-DO-NOT-PRINT"
        self.write(self.bm.BRIDGE_PY, "print('safe')\n")
        self.write(self.bm.LAST_GOOD, "print('older')\n")
        self.write_config({"state_dir": self.root, "port": 8790})
        with open(self.bm.config_path(), encoding="utf-8") as handle:
            config = json.load(handle)
        config["token"] = sentinel
        self.write(self.bm.config_path(), json.dumps(config))
        self.bm.is_running = lambda *_args: (True, "42")
        self.bm.service_state = lambda _label: (False, None)
        self.bm.miniapp_health = lambda _port: False

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = self.bm.cmd_doctor(["--json"])

        self.assertEqual(code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertNotIn(sentinel, stdout.getvalue())
        self.assertEqual(
            set(payload),
            {"repository", "bridge", "miniapp", "product_ready", "reasons"},
        )
        self.assertNotIn("config", payload)
        self.assertNotIn("environment", payload)

    def test_current_plist_paths_can_make_product_ready(self):
        self.write(self.bm.BRIDGE_PY, "print('safe')\n")
        self.write(os.path.join(self.root, "aside_session.py"), "VALUE = 1\n")
        self.write_config({"state_dir": self.root, "port": 8790})
        node = self.write(os.path.join(self.root, "bin", "node"), "node\n")
        entry = self.write(
            os.path.join(
                self.bm.MINIAPP_DIR, "server", "dist", "index.js"
            ),
            "// built\n",
        )
        plist = {
            "ProgramArguments": [node, entry],
            "WorkingDirectory": self.bm.MINIAPP_DIR,
            "StandardOutPath": os.path.join(self.root, "miniapp.out.log"),
            "StandardErrorPath": os.path.join(self.root, "miniapp.err.log"),
            "EnvironmentVariables": {"MINIAPP_CONFIG": self.bm.config_path()},
        }
        path = self.bm.plist_path(self.bm.MINIAPP_LABEL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            plistlib.dump(plist, handle)
        self.bm.is_running = lambda *_args: (True, "41")
        self.bm.service_state = lambda _label: (True, "42")
        self.bm.miniapp_health = lambda _port: True

        health = self.bm.deployment_health()

        self.assertTrue(health.installed_paths_valid)
        self.assertTrue(health.product_ready)

    def test_stale_but_self_consistent_state_paths_are_invalid(self):
        self.write_config({"state_dir": "/obsolete/checkout", "port": 8790})
        node = self.write(os.path.join(self.root, "bin", "node"), "node\n")
        entry = self.write(
            os.path.join(
                self.bm.MINIAPP_DIR, "server", "dist", "index.js"
            ),
            "// built\n",
        )
        plist = {
            "ProgramArguments": [node, entry],
            "WorkingDirectory": self.bm.MINIAPP_DIR,
            "StandardOutPath": "/obsolete/checkout/miniapp.out.log",
            "StandardErrorPath": "/obsolete/checkout/miniapp.err.log",
            "EnvironmentVariables": {"MINIAPP_CONFIG": self.bm.config_path()},
        }
        path = self.bm.plist_path(self.bm.MINIAPP_LABEL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            plistlib.dump(plist, handle)
        self.assertFalse(self.bm._installed_miniapp_paths_valid())

    def test_missing_dist_entry_makes_installed_paths_invalid(self):
        self.write_config({"state_dir": self.root, "port": 8790})
        node = self.write(os.path.join(self.root, "bin", "node"), "node\n")
        missing_entry = os.path.join(
            self.bm.MINIAPP_DIR, "server", "dist", "index.js"
        )
        plist = {
            "ProgramArguments": [node, missing_entry],
            "WorkingDirectory": self.bm.MINIAPP_DIR,
            "StandardOutPath": os.path.join(self.root, "miniapp.out.log"),
            "StandardErrorPath": os.path.join(self.root, "miniapp.err.log"),
            "EnvironmentVariables": {"MINIAPP_CONFIG": self.bm.config_path()},
        }
        path = self.bm.plist_path(self.bm.MINIAPP_LABEL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            plistlib.dump(plist, handle)
        self.assertFalse(self.bm._installed_miniapp_paths_valid())


if __name__ == "__main__":
    unittest.main()
