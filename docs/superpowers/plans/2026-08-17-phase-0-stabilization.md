# Phase 0 Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a trustworthy local Mini App deployment and a provider-qualified, truthful new-session workflow without losing the existing bridge recovery state or retaining source-overwriting rollback behavior.

**Architecture:** Add one Python 3.9-compatible `aside_session.py` boundary for model qualification and session evidence. Keep deployment state and migration in the existing operator surfaces: `bridgemon.py` owns diagnosis, Git synchronization, and update policy, while `miniapp/setup-miniapp.py` owns pure plist/config construction plus an explicit non-interactive path migration. Verify with standard-library `unittest`, one Mini App build, one bounded direct session smoke, one focused security review, and one fresh Sol/High audit.

**Tech Stack:** macOS launchd, `/usr/bin/python3` 3.9.6 standard library, `unittest`, Git, Node 24/npm 11, TypeScript Mini App build, Aside CLI.

## Global Constraints

- Execute inline in the primary Sol / High session under Sol Advisor 0.6 `audit`; use no implementation subagent.
- Preserve the existing modified `bridge.py` and `backups/last-good.py` in a recoverable local checkpoint before changing source.
- Never use `git reset --hard`, `git checkout --`, or source-overwriting rollback.
- Do not stage the existing `bridge.py` rollback diff wholesale; inspect and stage only the intentional reconciled Phase 0 result.
- Keep production Python compatible with `/usr/bin/python3` 3.9.6; no `X | None`, `match`, or later runtime-only features.
- Add no Python package dependency; use standard-library `unittest`.
- Read only top-level credential-provider keys; never read, log, or persist credential values.
- Do not log bot tokens, JWTs, prompts, transcript bodies, arbitrary environment values, or config contents.
- Do not change Telegram menus, BotFather, Cloudflare/DNS configuration, Aside permission modes, or external services.
- Do not invoke Telegram `heavy_new()` in the live smoke because its existing post-create path grants full access.
- Do not push, create a pull request, or publish a release.
- Run the full Mini App test suite only if TypeScript source, dependency metadata, or shared Mini App behavior changes.
- Run one focused security review after the final accumulated diff and exactly one fresh Sol / High reviewer after parent verification.

---

## File Structure

- Create `aside_session.py`: pure model qualification, provider-key reading, session-directory evidence, transcript validation, and structured session-creation results.
- Modify `bridge.py`: merge model-ID mappings, route model arguments through `aside_session`, and make `/new` consume `SessionCreationResult` while preserving unrelated recovery behavior.
- Modify `bridgemon.py`: import-safe structured diagnosis, upstream-aware fast-forward synchronization, conservative rollback, and `doctor` output.
- Modify `miniapp/setup-miniapp.py`: pure desired-config/plist builders, owner-only atomic writes, and `--migrate-paths` with backup/restore.
- Convert `tests/test_bridgemon.py`: standard-library `unittest`, isolated module fixture, no real LaunchAgent leakage.
- Create `tests/test_aside_session.py`: model and session boundary tests.
- Create `tests/test_miniapp_migration.py`: config/plist migration and restoration tests.
- Create `tests/__init__.py`: stable `python3 -m unittest` module imports.
- Modify `README.md`: truthful update, rollback, doctor, migration, and `/new` behavior.
- Amend `docs/superpowers/specs/2026-08-17-phase-0-stabilization-design.md`: record the verified no-catalog and Python 3.9 constraints.

---

### Task 1: Preserve the Recovery State and Make Deployment Tests Hermetic

**Files:**
- Create: `tests/__init__.py`
- Modify: `tests/test_bridgemon.py`
- Local ignored checkpoint: `backups/phase0-<timestamp>/`

**Interfaces:**
- Consumes: Current `bridge.py`, `backups/last-good.py`, `config.json`, installed `com.aside.miniapp.plist`, and existing `bridgemon.py` environment seams.
- Produces: Recoverable pre-change bytes and an import-safe `BridgemonTestCase` fixture used by later deployment tests.

- [ ] **Step 1: Capture the pre-change repository evidence**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}'
shasum -a 256 bridge.py backups/last-good.py
cmp -s bridge.py backups/last-good.py
```

Expected:

- `bridge.py` is the only pre-existing tracked modification.
- The two SHA-256 values are recorded in the execution log.
- `cmp` exits zero, preserving the evidence that the working file is the recovery copy.

- [ ] **Step 2: Create an ignored, owner-only recovery checkpoint**

Use an explicit generated directory under the already ignored `backups/` tree:

```bash
phase0_stamp="$(date +%Y%m%d-%H%M%S)"
phase0_checkpoint="backups/phase0-${phase0_stamp}"
mkdir -m 700 "$phase0_checkpoint"
cp -p bridge.py "$phase0_checkpoint/bridge.py"
cp -p backups/last-good.py "$phase0_checkpoint/last-good.py"
cp -p config.json "$phase0_checkpoint/config.json"
cp -p /Users/mosi/Library/LaunchAgents/com.aside.miniapp.plist "$phase0_checkpoint/com.aside.miniapp.plist"
chmod 600 "$phase0_checkpoint"/*
```

Expected: all four files exist under one `0700` directory and are `0600`; `git status --short` is unchanged because `backups/` is ignored.

- [ ] **Step 3: Convert the bridgemon harness fixture before adding new behavior**

Replace module-global temporary state, custom `check()`, and terminal `sys.exit()` with this fixture shape:

```python
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
```

Move every existing assertion into named `test_*` methods. Preserve all current cases: tunnel parsing, last-error parsing, integer arguments, normal/worktree Git detection, live-label precedence, cached label detection, Mini App config defaults, Mini App installed detection, dist snapshot/restore, and command registration.

- [ ] **Step 4: Add the regression that proves no real LaunchAgent leakage**

```python
def test_bare_tree_has_no_miniapp_even_when_user_has_real_plist(self):
    self.write_config(None)
    self.assertFalse(self.bm.miniapp_installed())
```

Name the production change that makes it fail: none; the fixture itself makes `_agents_dir()` remain bound to the temporary directory for the full test. Before conversion, importing the old script under discovery exits and the existing case can observe the real plist.

- [ ] **Step 5: Run the isolated suite**

Run:

```bash
/usr/bin/python3 -m unittest -v tests.test_bridgemon
```

Expected: all converted bridgemon cases pass; no command reads `~/Library/LaunchAgents` or the live config.

- [ ] **Step 6: Commit only the test-foundation change**

```bash
git add tests/__init__.py tests/test_bridgemon.py
git diff --cached --check
git commit -m "test: make bridgemon checks hermetic"
```

Expected: the commit contains no `bridge.py`, config, plist, backup, or runtime-log bytes.

---

### Task 2: Add Explicit Model Qualification

**Files:**
- Create: `aside_session.py`
- Create: `tests/test_aside_session.py`

**Interfaces:**
- Consumes: a selected model string, merged `model_ids`, and the configured credentials file.
- Produces:
  - `DEFAULT_MODEL_IDS: Mapping[str, str]`
  - `ResolvedModel(model_id, provider_id, qualified_model)`
  - `ModelResolutionError(code, user_message, diagnostic)`
  - `merge_model_ids(config: Mapping[str, object]) -> Dict[str, str]`
  - `read_connected_providers(path: str) -> FrozenSet[str]`
  - `resolve_model(selection, model_ids, connected_providers) -> ResolvedModel`

- [ ] **Step 1: Write failing model-qualification tests**

Create `tests/test_aside_session.py` with:

```python
class ModelQualificationTests(unittest.TestCase):
    def test_qualified_connected_model_is_preserved(self):
        resolved = resolve_model(
            "openai-codex/gpt-5.6-sol",
            {},
            frozenset({"openai-codex"}),
        )
        self.assertEqual(resolved.model_id, "gpt-5.6-sol")
        self.assertEqual(resolved.provider_id, "openai-codex")
        self.assertEqual(
            resolved.qualified_model, "openai-codex/gpt-5.6-sol")

    def test_bare_model_uses_merged_mapping(self):
        resolved = resolve_model(
            "gpt-5.6-sol",
            {"gpt-5.6-sol": "openai-codex/gpt-5.6-sol"},
            frozenset({"openai-codex"}),
        )
        self.assertEqual(
            resolved.qualified_model, "openai-codex/gpt-5.6-sol")

    def test_unmapped_bare_model_fails_before_exec(self):
        with self.assertRaises(ModelResolutionError) as caught:
            resolve_model("future-model", {}, frozenset({"openai-codex"}))
        self.assertEqual(caught.exception.code, "configuration")
        self.assertIn("model_ids", caught.exception.user_message)

    def test_unconnected_provider_fails_before_exec(self):
        with self.assertRaises(ModelResolutionError) as caught:
            resolve_model(
                "openai-codex/gpt-5.6-sol", {}, frozenset({"claude-code"}))
        self.assertEqual(caught.exception.code, "configuration")
        self.assertNotIn("token", caught.exception.diagnostic.lower())

    def test_config_override_replaces_builtin_mapping(self):
        merged = merge_model_ids({
            "model_ids": {"gpt-5.6-sol": "test-provider/test-model"}
        })
        self.assertEqual(merged["gpt-5.6-sol"], "test-provider/test-model")
        self.assertIn("claude-sonnet-5", merged)
```

Add credentials tests using a temporary JSON file whose values are sentinel secrets:

```python
def test_provider_reader_returns_only_top_level_keys(self):
    path = self.write_json({
        "openai-codex": {"access": "DO-NOT-RETURN"},
        "claude-code": {"api_key": "DO-NOT-RETURN-EITHER"},
    })
    providers = read_connected_providers(path)
    self.assertEqual(providers, frozenset({"openai-codex", "claude-code"}))
    self.assertNotIn("DO-NOT-RETURN", repr(providers))

def test_malformed_credentials_fail_closed(self):
    path = self.write_text("not-json")
    with self.assertRaises(ModelResolutionError) as caught:
        read_connected_providers(path)
    self.assertEqual(caught.exception.code, "configuration")
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
/usr/bin/python3 -m unittest -v tests.test_aside_session.ModelQualificationTests
```

Expected: import failure because `aside_session.py` does not exist.

- [ ] **Step 3: Implement the minimal Python 3.9-compatible model boundary**

Use these public shapes:

```python
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
    model_id: str
    provider_id: str
    qualified_model: str


class ModelResolutionError(ValueError):
    def __init__(self, code, user_message, diagnostic):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.diagnostic = diagnostic
```

Validation requirements:

- Exactly one slash in a qualified value.
- Nonempty provider and model components.
- No whitespace or control characters.
- A bare value must appear in the merged map.
- The selected provider must appear in `connected_providers`.
- Credentials JSON must be an object; return only `frozenset(parsed.keys())`.
- Exception text must not include file contents or credential values.

- [ ] **Step 4: Run the model tests and verify GREEN**

Run:

```bash
/usr/bin/python3 -m unittest -v tests.test_aside_session.ModelQualificationTests
/usr/bin/python3 -m py_compile aside_session.py
```

Expected: all model tests pass and Python 3.9 compilation succeeds.

- [ ] **Step 5: Commit the model boundary**

```bash
git add aside_session.py tests/test_aside_session.py
git diff --cached --check
git commit -m "feat: qualify Aside models explicitly"
```

---

### Task 3: Require Usable Session Evidence and Integrate `/new`

**Files:**
- Modify: `aside_session.py`
- Modify: `tests/test_aside_session.py`
- Modify: `bridge.py`

**Interfaces:**
- Consumes: `ResolvedModel`, an injected `execute(qualified_model)` callback returning `(returncode, stdout, stderr)`, session directory, marker, timeout, monotonic clock, and sleeper.
- Produces:
  - `SessionCreationResult(status, error_code, model_id, provider_id, qualified_model, session_id, user_message, diagnostic)`
  - `create_new_session(...) -> SessionCreationResult`
  - `main(argv=None) -> int` with a bounded `smoke` subcommand that prints only non-secret result fields
  - Bridge helper `resolve_configured_model(selection) -> ResolvedModel`

- [ ] **Step 1: Write failing session-evidence tests**

Add a deterministic fake clock:

```python
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
```

Add these tests:

```python
class SessionCreationTests(unittest.TestCase):
    def test_exit_zero_without_new_session_is_not_success(self):
        result = create_new_session(
            resolved=self.resolved,
            prompt="marker prompt",
            sessions_dir=self.sessions,
            marker="marker prompt",
            execute=lambda _model: (0, "", ""),
            timeout_seconds=1.0,
            poll_interval=0.25,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
        )
        self.assertEqual(result.status, "timed_out")
        self.assertEqual(result.error_code, "session")

    def test_directory_without_transcript_is_not_success(self):
        os.makedirs(os.path.join(self.sessions, "2026-08-17_missing"))
        result = self.create(execute=lambda _model: (0, "", ""))
        self.assertNotEqual(result.status, "created")

    def test_valid_session_after_more_than_one_second_is_created(self):
        def publish(now):
            if now < 1.5 or os.path.exists(self.session_dir):
                return
            os.makedirs(self.session_dir)
            self.write_transcript([
                {"role": "user", "content": [{"type": "text", "text": "marker prompt"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "stopReason": "end_turn"},
            ])
        self.clock.callbacks.append(publish)
        result = self.create(execute=lambda _model: (0, "", ""))
        self.assertEqual(result.status, "created")
        self.assertEqual(result.session_id, "valid")

    def test_transcript_provider_error_is_availability_rejection(self):
        self.publish_error(
            "Requested model openai-codex/gpt-5.6-sol is not available for this account."
        )
        result = self.create(execute=lambda _model: (0, "", ""))
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.error_code, "availability")
        self.assertIn("not available", result.user_message.lower())

    def test_nonzero_process_error_is_process_rejection(self):
        result = self.create(
            execute=lambda _model: (1, "", "safe process failure"))
        self.assertEqual(result.error_code, "process")
        self.assertNotIn("couldn't find its id", result.user_message)
```

Also test unreadable transcript, multiple candidate sessions, invalid JSON-only transcript, timeout classification, and redaction of the prompt from `diagnostic`.

Add a CLI test that patches `subprocess.run` and `_prepare_new_session`-equivalent behavior does not exist in this module:

```python
def test_smoke_cli_never_calls_aside_repl_or_prints_prompt(self):
    calls = []
    completed = mock.Mock(returncode=0, stdout="", stderr="")

    def fake_run(argv, **kwargs):
        calls.append(argv)
        self.publish_valid_session("Reply exactly: ok")
        return completed

    stdout = io.StringIO()
    with mock.patch("aside_session.subprocess.run", side_effect=fake_run):
        with contextlib.redirect_stdout(stdout):
            code = main([
                "smoke", "--config", self.config_path,
                "--state", self.state_path,
                "--timeout", "2",
            ])

    self.assertEqual(code, 0)
    self.assertTrue(all(call[1] == "exec" for call in calls))
    self.assertTrue(all("repl" not in call for call in calls))
    self.assertNotIn("Reply exactly: ok", stdout.getvalue())
```

- [ ] **Step 2: Run the session tests and verify RED**

Run:

```bash
/usr/bin/python3 -m unittest -v tests.test_aside_session.SessionCreationTests
```

Expected: failures because `SessionCreationResult` and `create_new_session` are absent.

- [ ] **Step 3: Implement structured session creation**

Use Python 3.9-compatible fields:

```python
@dataclass(frozen=True)
class SessionCreationResult:
    status: str
    error_code: Optional[str]
    model_id: str
    provider_id: Optional[str]
    qualified_model: Optional[str]
    session_id: Optional[str]
    user_message: str
    diagnostic: str
```

`create_new_session` must:

1. Snapshot valid session directory names before `execute`.
2. Pass only `resolved.qualified_model` to `execute`.
3. Return `process` on nonzero execution.
4. Poll candidates not in the before-snapshot until timeout.
5. Require exactly one candidate whose `messages.jsonl` contains the marker in a parsed record.
6. Parse assistant records with `stopReason == "error"`; classify the exact unavailable-model phrase as `availability`.
7. Use `errors="replace"` for concurrently written transcripts.
8. Return `created` only after a valid transcript has no decisive error.
9. Never include the prompt or transcript contents in diagnostics.

The `smoke` subcommand must:

- Read `config.json` and optional `state.json` locally.
- Expand configured paths with `os.path.expanduser`.
- Select `state.model`, falling back to `config.default_model`.
- Resolve the model and connected provider using the same public functions as the bridge.
- Execute `aside exec -m <qualified> --effort low -- "Reply exactly: ok"` with an argv array and a bounded timeout.
- Never invoke `aside repl`, Telegram, or the bridge module.
- Print one JSON object containing only `status`, `error_code`, `qualified_model`, `session_id`, and `diagnostic`.
- Return zero only for `created`.

- [ ] **Step 4: Run the session tests and verify GREEN**

Run:

```bash
/usr/bin/python3 -m unittest -v tests.test_aside_session
/usr/bin/python3 -m py_compile aside_session.py
```

Expected: all model and session tests pass.

- [ ] **Step 5: Reconcile `bridge.py` deliberately against the saved recovery copy**

Before editing, inspect both sources:

```bash
git diff -- bridge.py
git show fce3b9f:bridge.py > /tmp/aside-bridge-committed.py
diff -u backups/phase0-*/bridge.py /tmp/aside-bridge-committed.py
```

Apply only the previously committed hardening needed by the Phase 0 integration—account-root/path handling, tolerant transcript reads, merged model maps, and first-session error validation—plus the new `aside_session` calls. Do not restore unrelated code merely to make the diff disappear. The saved checkpoint remains the recovery source of truth.

Before importing config, add one hermetic path seam that defaults to current production behavior:

```python
BRIDGE_DIR = os.path.expanduser(
    os.environ.get("ASIDE_BRIDGE_DIR")
    or os.path.dirname(os.path.abspath(__file__))
)
CONFIG_PATH = os.path.expanduser(
    os.environ.get("ASIDE_BRIDGE_CONFIG")
    or os.path.join(BRIDGE_DIR, "config.json")
)
```

Derive state, log, and media paths from `BRIDGE_DIR`. Restore the committed `detect_aside_root()` and `config_path()` behavior so empty documented config paths resolve to the current Aside account instead of `u/0` or an empty spawn target. Tests must set both environment variables before importing `bridge.py`; production launchd sets neither.

Add imports and one bridge resolver:

```python
from aside_session import (
    ModelResolutionError,
    create_new_session,
    merge_model_ids,
    read_connected_providers,
    resolve_model,
)

MODEL_IDS = merge_model_ids(CONFIG)


def resolve_configured_model(selection):
    providers = read_connected_providers(CREDENTIALS_PATH)
    return resolve_model(selection, MODEL_IDS, providers)
```

Change `run_aside` so every supplied model is resolved to a qualified value. Return a distinct negative local code for `ModelResolutionError` with the safe user message; never execute a bare unmapped model.

Change `heavy_new` to:

1. Resolve the current model.
2. Call `create_new_session` with an executor wrapping `run_aside` and a bounded post-process evidence timeout.
3. Send `result.user_message` on any non-created status.
4. Update state only when `status == "created"`.
5. Preserve the existing `_prepare_new_session` behavior for normal Telegram `/new`; permission redesign remains out of scope.

- [ ] **Step 6: Add a bridge integration regression without invoking Telegram or Aside**

In `tests/test_aside_session.py`, create a temporary bridge directory containing a non-secret fake config/state, set `ASIDE_BRIDGE_DIR` and `ASIDE_BRIDGE_CONFIG`, then load the repository `bridge.py` with Telegram/state functions stubbed. Replace `create_new_session` with a fake result:

```python
def test_heavy_new_does_not_switch_on_rejected_result(self):
    old_session = self.bridge.state.get("session_id")
    self.bridge.create_new_session = lambda **_kwargs: SessionCreationResult(
        status="rejected",
        error_code="availability",
        model_id="gpt-5.6-sol",
        provider_id="openai-codex",
        qualified_model="openai-codex/gpt-5.6-sol",
        session_id=None,
        user_message="model is not available",
        diagnostic="provider rejected model",
    )
    self.bridge.heavy_new()
    self.assertEqual(self.bridge.state.get("session_id"), old_session)
    self.assertIn("not available", self.sent[-1])
```

Add the corresponding created-result test proving state changes only after evidence.

- [ ] **Step 7: Verify bridge integration**

Run:

```bash
/usr/bin/python3 -m unittest -v tests.test_aside_session
/usr/bin/python3 -m py_compile bridge.py aside_session.py
python3 tests/test_approval_gate.py
python3 tests/test_approval_persistence.py
python3 tests/test_question_gate.py
```

Expected: new tests pass; retained approval/question executable tests remain green.

- [ ] **Step 8: Commit only the reconciled intentional bridge change**

Inspect the complete bridge diff against both `HEAD` and the checkpoint. Stage deliberately; do not use a blanket add until every old hunk is classified:

```bash
git diff -- bridge.py
git add -p bridge.py
git add aside_session.py tests/test_aside_session.py
git diff --cached --check
git diff --cached --stat
git commit -m "fix: require qualified usable new sessions"
```

Expected: the commit contains the intentional source reconciliation and session boundary, while the checkpoint remains ignored and recoverable.

---

### Task 4: Make Deployment Diagnosis and Git Updates Fail Closed

**Files:**
- Modify: `bridgemon.py`
- Modify: `tests/test_bridgemon.py`

**Interfaces:**
- Produces:
  - `DeploymentHealth(source_valid, installed_paths_valid, process_running, endpoint_healthy, product_ready, reasons)`
  - `GitSyncResult(status, changed, ok, message, remote, branch)`
  - `_git_upstream() -> Tuple[str, str]`
  - `_git_sync() -> GitSyncResult`
  - `deployment_health() -> DeploymentHealth`
  - `cmd_doctor(args) -> int`
- Replaces: ambiguous `_git_pull() -> (changed, message)` and source-copying `cmd_rollback`.

Use these exact Python 3.9-compatible result shapes:

```python
@dataclass(frozen=True)
class GitSyncResult:
    status: str
    changed: bool
    ok: bool
    message: str
    remote: Optional[str] = None
    branch: Optional[str] = None


@dataclass(frozen=True)
class DeploymentHealth:
    source_valid: bool
    installed_paths_valid: bool
    process_running: bool
    endpoint_healthy: bool
    product_ready: bool
    reasons: Tuple[str, ...]
```

- [ ] **Step 1: Write failing structured Git-sync tests**

Use an injected `_git` fake that records argv:

```python
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
            returncode=99, stderr="unexpected git call: %r" % (args,)))


def test_git_sync_uses_configured_fork_upstream(self):
    fake = GitFake({
        ("status", "--porcelain", "--untracked-files=no"): ok(""),
        ("rev-parse", "--abbrev-ref", "HEAD"): ok("feature\n"),
        ("config", "--get", "branch.feature.remote"): ok("fork\n"),
        ("config", "--get", "branch.feature.merge"): ok("refs/heads/feature\n"),
        ("remote", "get-url", "fork"): ok("git@example/fork.git\n"),
        ("rev-parse", "HEAD"): ok("aaaa\n"),
        ("fetch", "fork", "feature"): ok(""),
        ("rev-parse", "fork/feature"): ok("bbbb\n"),
        ("merge-base", "--is-ancestor", "HEAD", "fork/feature"): ok(""),
        ("merge", "--ff-only", "fork/feature"): ok(""),
    })
    self.bm._git = fake
    result = self.bm._git_sync()
    self.assertTrue(result.ok)
    self.assertEqual((result.remote, result.branch), ("fork", "feature"))
    self.assertIn(("fetch", "fork", "feature"), fake.calls)
    self.assertNotIn(("reset", "--hard", "fork/feature"), fake.calls)
```

Add tests for dirty tracked source, no upstream, missing remote, fetch failure, non-fast-forward ancestry, already-current status, and merge failure. Assert every blocked/failed result has `ok == False` and `cmd_update` returns nonzero before restart.

- [ ] **Step 2: Write failing rollback and health tests**

```python
def test_rollback_never_copies_last_good_over_bridge(self):
    self.write(self.bm.BRIDGE_PY, "current\n")
    self.write(self.bm.LAST_GOOD, "old\n")
    with mock.patch.object(self.bm.shutil, "copy2") as copy2:
        code = self.bm.cmd_rollback([])
    self.assertEqual(code, 1)
    copy2.assert_not_called()
    self.assertEqual(self.read(self.bm.BRIDGE_PY), "current\n")

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
    self.assertFalse(health.product_ready)
```

Add a `cmd_doctor --json` test that parses stdout and proves it contains paths/statuses but no config object, token, JWT, credential contents, or transcript text.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
/usr/bin/python3 -m unittest -v \
  tests.test_bridgemon.GitSyncTests \
  tests.test_bridgemon.RollbackTests \
  tests.test_bridgemon.DeploymentHealthTests
```

Expected: missing `GitSyncResult`, `_git_sync`, `DeploymentHealth`, or `cmd_doctor` failures.

- [ ] **Step 4: Implement structured diagnosis and synchronization**

Use Python 3.9-compatible dataclasses. `_git_upstream` must read:

```text
git config --get branch.<current>.remote
git config --get branch.<current>.merge
git remote get-url <remote>
```

Strip only the `refs/heads/` prefix from the merge ref. `_git_sync` must:

1. Return `skipped, ok=True` for a non-Git local deployment.
2. Return `blocked, ok=False` for tracked dirtiness or missing upstream.
3. Fetch the configured remote/branch.
4. Return `unchanged, ok=True` when `HEAD` equals the remote ref.
5. Require `merge-base --is-ancestor HEAD remote/branch`.
6. Advance only through `git merge --ff-only remote/branch`.
7. Never invoke `reset --hard`.

`cmd_update` must stop immediately on `blocked` or `failed`. On a post-restart health failure it reports the checkpoint and returns one; it does not copy source. `cmd_rollback` refuses source mutation, prints the last-good/checkpoint locations, and returns one until immutable releases exist.

Before rebuilding or restarting an installed Mini App, `cmd_update` must inspect `DeploymentHealth.installed_paths_valid`. If false, it returns one with the exact `bridgemon miniapp migrate-paths` next step; it must not kickstart a plist whose program/config/working-directory paths are already known stale.

Add `doctor` to `COMMANDS` and keep `status` human-readable. `doctor --json` emits only the structured non-secret diagnosis.

The JSON schema is stable and deliberately small:

```json
{
  "repository": {
    "root": "/absolute/path",
    "revision": "full commit id or null",
    "branch": "branch or null",
    "upstream": "remote/branch or null",
    "dirty_tracked": ["relative/path"]
  },
  "bridge": {
    "source_valid": true,
    "last_good_exists": true,
    "process_running": true
  },
  "miniapp": {
    "installed_paths_valid": false,
    "process_running": false,
    "endpoint_healthy": false
  },
  "product_ready": false,
  "reasons": ["non-secret actionable reason"]
}
```

Do not add config objects, environment dumps, command output, logs, transcript excerpts, or credential fields to this schema.

- [ ] **Step 5: Run focused tests and verify GREEN**

```bash
/usr/bin/python3 -m unittest -v tests.test_bridgemon
/usr/bin/python3 -m py_compile bridgemon.py
```

Expected: all bridgemon tests pass and no test touches live launchd/Git/npm.

- [ ] **Step 6: Commit the deployment-control change**

```bash
git add bridgemon.py tests/test_bridgemon.py
git diff --cached --check
git commit -m "fix: make bridge updates non-destructive"
```

---

### Task 5: Add Recoverable Mini App Path Migration

**Files:**
- Modify: `miniapp/setup-miniapp.py`
- Create: `tests/test_miniapp_migration.py`
- Modify: `bridgemon.py`
- Modify: `tests/test_bridgemon.py`

**Interfaces:**
- Produces in `miniapp/setup-miniapp.py`:
  - `desired_miniapp_section(config_path, current_section, repository_root) -> dict`
  - `build_service_plist(config_path, section, repository_root, node_path) -> dict`
  - `atomic_write_json(path, value, mode=0o600)`
  - `atomic_write_plist(path, value, mode=0o600)`
  - `migrate_paths(..., reload_service, check_health) -> MigrationResult`
  - CLI `--migrate-paths --config <absolute path> --repository-root <absolute path>`
- Consumes in `bridgemon.py`: the migration subprocess exit code and its final one-line result; `cmd_update` never silently migrates without an explicit valid diagnosis.

Use this exact result shape:

```python
@dataclass(frozen=True)
class MigrationResult:
    ok: bool
    changed: bool
    restored: bool
    message: str
    backup_dir: Optional[str]
```

- [ ] **Step 1: Write failing pure config/plist tests**

```python
class MiniappPathBuilderTests(unittest.TestCase):
    def test_desired_section_replaces_only_stale_state_path(self):
        current = {
            "port": 8790,
            "tunnel": "cloudflared",
            "auto_register_menu": False,
            "state_dir": "/Users/mosi/aside-telegram-bridge",
            "custom": "preserve-me",
        }
        desired = desired_miniapp_section(
            "/repo/config.json", current, "/repo")
        self.assertEqual(desired["state_dir"], "/repo")
        self.assertEqual(desired["custom"], "preserve-me")
        self.assertFalse(desired["auto_register_menu"])

    def test_plist_uses_current_repository_paths(self):
        plist = build_service_plist(
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
```

Add tests preserving tunnel/menu/custom values, replacing config/program/log/working-directory paths, rejecting a missing dist entry, and never embedding the bot token in the plist.

- [ ] **Step 2: Write failing atomic migration and restoration tests**

```python
def test_failed_health_restores_exact_config_and_plist_bytes(self):
    old_config = b'{"token":"SECRET","miniapp":{"state_dir":"/old"}}\n'
    old_plist = plistlib.dumps({"Label": LABEL, "WorkingDirectory": "/old"})
    self.write_bytes(self.config_path, old_config, 0o600)
    self.write_bytes(self.plist_path, old_plist, 0o600)

    result = migrate_paths(
        config_path=self.config_path,
        plist_path=self.plist_path,
        repository_root=self.repo,
        node_path=self.node,
        reload_service=lambda: True,
        check_health=lambda _port: False,
        backup_root=self.backups,
    )

    self.assertFalse(result.ok)
    self.assertEqual(self.read_bytes(self.config_path), old_config)
    self.assertEqual(self.read_bytes(self.plist_path), old_plist)
    self.assertEqual(stat.S_IMODE(os.stat(self.config_path).st_mode), 0o600)
    self.assertEqual(stat.S_IMODE(os.stat(self.plist_path).st_mode), 0o600)
```

Add success, reload failure, backup mode, malformed config, missing node, and missing entry tests. All reload and health functions are injected; tests must not call launchctl or HTTP.

- [ ] **Step 3: Run migration tests and verify RED**

```bash
/usr/bin/python3 -m unittest -v tests.test_miniapp_migration
```

Expected: missing builder/migration functions.

- [ ] **Step 4: Implement pure builders and recoverable migration**

Refactor existing interactive `configure()` and `install_service()` to consume the same builders used by `--migrate-paths`; do not maintain a second plist schema.

Atomic writes must:

1. Create the temporary file in the target directory.
2. Open it with `os.open(..., 0o600)`.
3. Flush and `os.fsync` before `os.replace`.
4. `chmod(0o600)` after replacement.
5. Preserve a byte-exact `0600` backup before replacement.

`migrate_paths` must build and validate everything before the first replacement, apply both files, reload once, poll health, and restore both exact backups plus reload once on failure. It must not call the interactive questionnaire, `npm install`, Telegram menu registration, or tunnel configuration.

- [ ] **Step 5: Add bridgemon diagnosis and explicit migration command coverage**

Add `bridgemon miniapp migrate-paths` that invokes:

```text
/usr/bin/python3 miniapp/setup-miniapp.py --migrate-paths \
  --config <resolved config> --repository-root <BRIDGE_DIR>
```

The command prints the pre-migration diagnosis, requires the current dist entry, returns the child exit code, and prints the post-migration diagnosis. It is explicit; `status` remains read-only.

Test the exact argv and nonzero propagation in `tests/test_bridgemon.py` with `subprocess.run` stubbed.

- [ ] **Step 6: Run migration tests and verify GREEN**

```bash
/usr/bin/python3 -m unittest -v \
  tests.test_miniapp_migration \
  tests.test_bridgemon
/usr/bin/python3 -m py_compile miniapp/setup-miniapp.py bridgemon.py
```

Expected: all tests pass under system Python 3.9.

- [ ] **Step 7: Commit the migration change**

```bash
git add miniapp/setup-miniapp.py tests/test_miniapp_migration.py \
  bridgemon.py tests/test_bridgemon.py
git diff --cached --check
git commit -m "fix: migrate Mini App paths safely"
```

---

### Task 6: Document, Verify, Migrate, and Audit Phase 0

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-17-phase-0-stabilization-design.md`
- Verify: all accumulated Phase 0 files and local installed state

**Interfaces:**
- Consumes: completed Tasks 1–5, ignored recovery checkpoint, current LaunchAgent/config, and exact Sol reviewer role.
- Produces: truthful operator instructions, static verification evidence, one recoverable live migration, one direct harmless session-boundary smoke, focused security findings, and final Sol verdict.

- [ ] **Step 1: Update operator documentation**

Document exact commands and guarantees:

```text
python3 bridgemon.py doctor
python3 bridgemon.py doctor --json
python3 bridgemon.py miniapp migrate-paths
python3 bridgemon.py update
python3 bridgemon.py rollback
```

State explicitly:

- `update` follows the configured upstream and fast-forwards only.
- Dirty tracked source blocks update.
- `rollback` no longer overwrites source and points to recovery evidence until immutable releases exist.
- `miniapp migrate-paths` backs up, migrates, health-checks, and restores on failure.
- `/new` rejects unmapped/unconnected models and requires usable transcript evidence.
- Phase 0 does not repair durable delivery, cancellation, callback routing, or approval enforcement.

- [ ] **Step 2: Run the complete accumulated Python gate**

```bash
/usr/bin/python3 -m unittest -v \
  tests.test_aside_session \
  tests.test_bridgemon \
  tests.test_miniapp_migration
python3 tests/test_approval_gate.py
python3 tests/test_approval_persistence.py
python3 tests/test_question_gate.py
/usr/bin/python3 -m py_compile \
  aside_session.py bridge.py bridgemon.py miniapp/setup-miniapp.py
git diff --check
```

Expected: zero failures; all production Python compiles under launchd's Python 3.9; no whitespace errors.

- [ ] **Step 3: Run the single Mini App artifact gate**

Because no TypeScript or dependency source is planned to change, run one build and do not rerun the full 602-test suite:

```bash
npm run build --prefix miniapp
test -s miniapp/server/dist/index.js
test -s miniapp/web/dist/index.html
```

Expected: build exit zero and both artifacts exist.

- [ ] **Step 4: Run the pre-migration doctor and compare the checkpoint**

```bash
/usr/bin/python3 bridgemon.py doctor --json
shasum -a 256 backups/phase0-*/bridge.py backups/phase0-*/last-good.py
git status --short
```

Expected: doctor reports stale installed Mini App paths before migration; checkpoint digests remain stable; no config or credential values appear.

- [ ] **Step 5: Perform the recoverable live Mini App migration**

This mutates the user LaunchAgent/config and therefore requires the explicit local execution approval at tool time:

```bash
/usr/bin/python3 bridgemon.py miniapp migrate-paths
/usr/bin/python3 bridgemon.py doctor --json
launchctl print gui/$(id -u)/com.aside.miniapp
curl -fsS http://127.0.0.1:8790/api/health
```

Expected:

- Installed config/program/working-directory/log/state paths use the active checkout.
- The job is loaded with a live PID and no continuing crash loop.
- Health returns JSON with `ok: true`.
- On any failure, restored old bytes and resulting service state are reported; do not continue to session smoke.

- [ ] **Step 6: Preflight the harmless direct session smoke**

Resolve and display only non-secret model/provider evidence:

```bash
/usr/bin/python3 -c 'from aside_session import merge_model_ids, read_connected_providers, resolve_model; import json; c=json.load(open("config.json")); m=(json.load(open("state.json")).get("model") or c.get("default_model")); r=resolve_model(m, merge_model_ids(c), read_connected_providers(c.get("credentials_path", "~/.aside/u/0/credentials.json"))); print(r.qualified_model)'
```

Before executing the smoke, report the qualified model and that one minimal model turn may count toward Aside/model usage. Do not print credentials, prompt content, or state.

- [ ] **Step 7: Run one direct session-boundary smoke without permission mutation**

Run the tested boundary CLI:

```bash
/usr/bin/python3 aside_session.py smoke \
  --config config.json \
  --state state.json \
  --timeout 15
```

It executes the smallest fixed prompt (`Reply exactly: ok`) and prints only status, error category, qualified model, session ID, and safe diagnostics. It must not import or call `bridge.heavy_new`, `_prepare_new_session`, Aside `repl`, Telegram, browser, filesystem tools, or external APIs beyond the model call inherent in session creation.

Expected: `status=created`, qualified provider/model preserved, one session ID, valid transcript evidence. If the provider rejects the model, stop and report the exact availability result; do not substitute another model without user direction.

- [ ] **Step 8: Run the required focused security review once**

Invoke `security-review` against only the accumulated Phase 0 diff, focusing on:

- Credential-key-only reads and redaction.
- Model/input validation and subprocess argv.
- Atomic config/plist writes and owner-only modes.
- Backup/restore integrity.
- Git command construction and remote/ref validation.
- Launchctl subprocess boundaries.
- No automatic source overwrite.

Resolve every required finding, rerun affected targeted tests, then rerun Steps 2–3 once if source changes.

- [ ] **Step 9: Perform the primary code review and final verification**

Invoke `code-review` inline as the primary diff inspection. Then run:

```bash
git status --short --branch
git diff --check
git diff --stat fork/fix/new-session-model-resolution...HEAD
git diff fork/fix/new-session-model-resolution...HEAD -- \
  aside_session.py bridge.py bridgemon.py miniapp/setup-miniapp.py \
  tests README.md docs/superpowers
```

Confirm line-by-line acceptance criteria and distinguish static, build, installed-runtime, and direct-session evidence.

- [ ] **Step 10: Obtain exactly one fresh Sol / High audit**

Preflight only the selected reviewer:

```bash
sh /Users/mosi/.codex/plugins/cache/sol-advisor/sol-advisor/0.6.0/scripts/install-agents.sh \
  --check --check-role sol
```

Spawn `sol_advisor_sol_reviewer` with `fork_turns: none`, the exact accumulated diff, constraints, and primary verification evidence. Require:

```text
SOL REVIEW
VERDICT: ship | fix-first | rethink
REASON: ...
FINDINGS: ...
RESIDUAL RISK: ...
```

On `fix-first`, implement only required corrections inline, rerun affected tests plus the accumulated gate, discard the old verdict, and obtain a new fresh reviewer. On `rethink`, stop and revise the architecture.

- [ ] **Step 11: Commit documentation or review corrections and report the handoff**

```bash
git add README.md \
  docs/superpowers/specs/2026-08-17-phase-0-stabilization-design.md \
  docs/superpowers/plans/2026-08-17-phase-0-stabilization.md
git diff --cached --check
git commit -m "docs: document Phase 0 operations"
```

Final report must include:

- Exact commits and changed files.
- Original checkpoint location and whether it contains recoverable secrets.
- Test/build commands and counts.
- Installed LaunchAgent and endpoint evidence.
- Direct session smoke result and whether it incurred model usage.
- Security review result and Sol verdict.
- Whether `bridge.py` remains modified or has been deliberately reconciled.
- Residual risks explicitly deferred to later phases.
