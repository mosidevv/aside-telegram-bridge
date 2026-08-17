# Phase 0 Stabilization Design

**Date:** 2026-08-17

**Status:** Implementation complete; live acceptance and final audit pending

**Orchestration:** Sol Advisor 0.6 `audit` route, native-first
**Primary objective:** Restore a trustworthy local deployment and a truthful `/new` workflow without discarding the existing recovery state or expanding into the later durable-queue redesign.

**Implementation checkpoint:** The static Phase 0 implementation now uses a
single `aside_session.py` trust boundary, structured fail-closed deployment
results, and one explicit recoverable Mini App path migration. Hermetic Python
tests replace the prior live-state bridgemon test script. Acceptance criteria
that require launchd, loopback health, model usage, security review, or the
fresh Sol verdict remain evidence gates rather than implementation claims.

## 1. Context

The repository contains two separately deployed products:

- A Python Telegram bridge started by `com.aside.telegram-bridge`.
- A TypeScript Mini App started by `com.aside.miniapp` and exposed through a local server and optional Cloudflare quick tunnel.

The source and installed runtime currently disagree:

- `bridge.py` has an existing working-tree modification that is byte-identical to `backups/last-good.py` and substantially different from `HEAD`.
- The updater can create that condition by copying `last-good.py` over tracked source during rollback.
- The Telegram bridge process is running, but `/new` passes a bare model identifier that Aside resolves through the wrong provider.
- Aside may exit zero without creating a usable session, and the bridge then reports a misleading session-ID discovery error.
- The Mini App source builds and its test suite passes, but its LaunchAgent points to an obsolete checkout and is crash-looping.
- Mini App configuration also retains an obsolete state-directory path that the current setup script does not replace.
- The current branch tracks `fork`, while the updater assumes `origin`.

Phase 0 addresses these deployment and session-creation failures. Durable Telegram ingestion, cancellation, queue isolation, permission brokering, and the broader security findings remain later phases except where a Phase 0 change must avoid worsening them.

## 2. Settled Decisions

### 2.1 Preserve existing state

- Treat the current modified `bridge.py` as user-owned recovery state.
- Do not reset, restore, overwrite, reformat, or commit that file merely to make the worktree clean.
- Do not delete or replace `backups/last-good.py`.
- Do not expose configuration secrets, bot tokens, JWT material, or transcript bodies in checkpoints or logs.
- Create recoverable backups before changing LaunchAgent or configuration files.
- Do not push, open a pull request, or alter external services during Phase 0.

### 2.2 Use native-first selective orchestration

- The primary Sol / High session owns design, implementation, verification, and acceptance.
- No implementation subagent is used.
- Do not install Luna for this phase.
- Do not overwrite or depend on the conflicting older Terra profile.
- After the complete accumulated diff passes parent verification, use exactly one fresh `sol_advisor_sol_reviewer` audit.
- Preflight only the Sol reviewer role; do not run the all-role check.
- A reviewer correction invalidates the prior verdict and requires fresh verification plus a new reviewer.

### 2.3 Keep each verification gate distinct

- Targeted red/green tests prove individual behavior changes.
- One accumulated Python suite proves repository-level Python compatibility.
- One Mini App build proves deployable artifacts exist.
- One live smoke proves the installed launchd/runtime path actually works.
- One focused security review covers the changed filesystem, process, model, and external-API boundaries.
- One fresh Sol audit reviews the final diff and evidence.
- Do not add another implementation agent or overlapping fresh code-review agent.
- Do not rerun the full 602-test Mini App suite when no TypeScript source or dependency changed.

## 3. Scope

### 3.1 Included

1. A non-secret Phase 0 checkpoint and diagnostic packet.
2. Provider-aware Aside model resolution for `/new`.
3. Reliable session-creation result handling and error propagation.
4. Mini App LaunchAgent/config path migration to the current checkout.
5. Explicit migration of the Mini App state directory.
6. Deployment diagnostics shared by status, migration, update, and rollback paths.
7. Tracking-remote-aware Git update checks.
8. Removal of automatic rollback behavior that overwrites tracked source.
9. Hermetic tests for the changed Python behavior.
10. One bounded live smoke after static verification.

### 3.2 Excluded

- SQLite durable Telegram inbox and transactional offset acknowledgement.
- Per-task immutable queue envelopes and cross-session callback repair.
- `/cancel`, `/queue`, or process-group cancellation.
- Full permission/approval broker redesign.
- Mini App JWT URL tickets, rate-limit redesign, upload quotas, or transcript-cache redesign.
- Broad Mini App TypeScript refactoring.
- Immutable release directories and an atomic `current` release pointer.
- General consolidation of every Python test or every model catalog.
- Dependency upgrades unrelated to a Phase 0 build failure.
- Removal, replacement, or migration of the old Terra agent profile.

## 4. Architecture

### 4.1 Aside session boundary

Create a small Python module, `aside_session.py`, that owns the unreliable boundary between the Telegram bridge and the Aside CLI.

It provides pure or dependency-injected functions for:

- Normalizing a configured model selection.
- Resolving a bare model through the explicit `model_ids` configuration.
- Checking that a configured provider appears among the connected credential-provider keys.
- Rejecting unmapped bare identifiers rather than relying on Aside's implicit provider.
- Constructing provider-qualified CLI arguments.
- Taking a before-snapshot of session directories.
- Polling for a newly created session using a bounded timeout and interval.
- Validating that the session has usable transcript state.
- Converting process output, timeout, and session evidence into one structured result.

The bridge consumes a result rather than independently interpreting exit status and filesystem timing:

```python
from dataclasses import dataclass
from typing import Literal, Optional

SessionCreationStatus = Literal[
    "created",
    "rejected",
    "timed_out",
    "invalid",
]

SessionCreationError = Literal[
    "configuration",
    "availability",
    "process",
    "session",
]


@dataclass(frozen=True)
class SessionCreationResult:
    status: SessionCreationStatus
    error_code: Optional[SessionCreationError]
    model_id: str
    provider_id: Optional[str]
    qualified_model: Optional[str]
    session_id: Optional[str]
    user_message: str
    diagnostic: str
```

The boundary must not treat process exit code zero as proof of session creation. `created` requires all of:

1. A single new session can be identified relative to the before-snapshot.
2. The new session satisfies the agreed transcript/session validity check.
3. No decisive unavailable-model or initialization error is present.

If the configured model is unavailable, the user-facing message names that exact condition rather than reporting that the session ID could not be found. Diagnostics may include provider and model names but not credentials or prompt/transcript content.

### 4.2 Model qualification and availability proof

Aside accepts `provider/model` but exposes no command that enumerates the models an account can actually use. The Mini App's existing catalog is curated display/configuration data seeded by credential-provider keys; it is not live availability proof. Phase 0 must not introduce a fictitious catalog check.

Qualification rules:

1. A configured `provider/model` value is syntactically validated and used as written.
2. A bare model is accepted only when the merged `model_ids` map—small built-in compatibility mappings plus explicit configuration overrides—maps it to one exact `provider/model` value.
3. A bare model with no explicit mapping is rejected before `aside exec`; the error names the missing `model_ids` entry instead of allowing Aside to select an implicit provider.
4. The qualified provider must appear among the top-level keys of the configured Aside credentials file. Credential values are never read.
5. An unreadable or malformed credentials file fails closed for new-session creation and reports that provider connectivity could not be verified.
6. Static aliases may map friendly names to model IDs, but aliases do not select providers.
7. No new Python copy of the Mini App's curated model catalog is introduced.

Actual model availability is proven only by the bounded session-creation handshake: Aside must accept the qualified identifier and create one usable session. Provider rejection is parsed and surfaced as `availability`; it is not replaced with a generic session-ID error. Configuration and credential-key readers must be dependency-injected for tests.

### 4.3 Deployment diagnostics

Add one structured deployment diagnosis inside `bridgemon.py` and use it consistently across `status`, migration, update, and rollback decisions.

The diagnosis records only non-secret values:

- Repository root and current revision.
- Current branch, configured upstream remote/ref, and dirty tracked paths.
- Bridge source and last-good digests.
- LaunchAgent label, program, config, working directory, and log paths.
- Whether each required path exists and has the expected type.
- Configured Mini App state directory, without reading secret fields.
- Mini App build artifact existence.
- Launchd process state.
- Loopback health result.
- Product-readiness checks separately from process-running checks.

Represent health as separate evidence rather than one optimistic boolean:

```python
@dataclass(frozen=True)
class DeploymentHealth:
    source_valid: bool
    installed_paths_valid: bool
    process_running: bool
    endpoint_healthy: bool
    product_ready: bool
    reasons: tuple[str, ...]
```

`process_running` cannot imply `endpoint_healthy`, and `endpoint_healthy` cannot imply model/session readiness.

### 4.4 Mini App path migration

The migration computes expected paths from the active repository root:

- `config.json`
- `miniapp/server/dist/index.js`
- repository working directory
- standard-output and standard-error log paths
- Mini App state directory

Migration sequence:

1. Read the installed plist and the relevant non-secret config keys.
2. Build the complete desired replacement in memory.
3. Validate the current build artifact and every parent directory.
4. Write timestamped recoverable backups with owner-only permissions.
5. Write replacement files atomically with restrictive modes.
6. Reload the Mini App LaunchAgent once.
7. Poll the actual loopback health endpoint within a bounded timeout.
8. On failure, restore the backed-up plist/config, reload once, and report both migration and restoration evidence.
9. On success, verify launchd is no longer crash-looping and that the live process uses the expected program and working directory.

The stale `miniapp.state_dir` value must be explicitly migrated. `setdefault` is not sufficient for a known obsolete path.

No Telegram menu, BotFather, tunnel account, DNS, or external Cloudflare configuration is changed in this migration.

### 4.5 Git update and rollback behavior

Update behavior derives its remote/ref from the branch's configured upstream. It does not assume `origin`.

Before fetch or deployment it must reject:

- No configured upstream.
- Missing remote.
- Fetch failure.
- Non-fast-forward ancestry.
- A dirty tracked path that the deployment would overwrite.
- A missing source or build artifact.

Rollback must not copy `last-good.py` over tracked `bridge.py`.

For Phase 0, rollback is deliberately conservative:

- LaunchAgent/config migration can restore its explicit timestamped backup.
- Source deployment failure stops and reports the recovery checkpoint.
- `last-good.py` remains an operator-visible recovery artifact.
- Source rollback to an immutable release is deferred until the release-directory design is implemented.

The CLI must return a nonzero exit code whenever the requested update or migration did not complete successfully, even if a prior deployment remains running.

## 5. Error Handling and User Messages

Errors are categorized rather than collapsed into generic health or ID failures:

- `configuration`: missing or stale required path, missing upstream, invalid model value.
- `availability`: provider/model not available to the active Aside account.
- `process`: Aside or launchd process failed or timed out.
- `session`: process returned but no single usable new session appeared.
- `build`: Mini App artifact could not be produced or is absent.
- `migration`: desired installation could not be applied.
- `restoration`: prior installation could not be restored after migration failure.

Every operator-facing failure includes:

- What failed.
- The non-secret target involved.
- Whether existing service/state was preserved.
- The exact next diagnostic command.

Normal logs must not include bot tokens, JWTs, full prompts, transcript bodies, or arbitrary environment values.

## 6. Testing Strategy

### 6.1 Test framework

- Production Python must remain compatible with launchd's `/usr/bin/python3` 3.9.6; do not use Python 3.10 union syntax or newer runtime-only features.
- Use the Python standard library's `unittest` framework so Phase 0 does not add a package solely for test discovery; `pytest` is not currently installed in this project.
- Convert only `tests/test_bridgemon.py`, the directly affected legacy executable test, into an import-safe `unittest` module.
- Do not perform a broad test-suite rewrite in this phase.
- Tests use temporary directories and dependency injection for repository roots, user state, launchd paths, model mappings, credential-provider keys, process results, clocks, and polling intervals.
- Tests must not read or mutate the real `~/Library/LaunchAgents`, `~/.aside`, Telegram, launchd services, or Cloudflare state.

### 6.2 Required model/session tests

- Exact qualified model is accepted when its provider is connected.
- Bare model with one explicit `model_ids` entry is qualified deterministically.
- Bare model without a `model_ids` entry is rejected before process execution.
- Qualified model with an unconnected provider is rejected before process execution.
- Unreadable or malformed credentials do not fall back to an implicit provider.
- Process exit zero with no new session is not success.
- Process exit zero with a directory but no usable transcript is not success.
- A valid session appearing after more than one second is accepted within the configured timeout.
- Unavailable-model output is surfaced as an availability error.
- A timeout reports timeout rather than session-ID lookup failure.
- Diagnostics redact configured secrets and prompts.

### 6.3 Required deployment tests

- Diagnosis detects every stale plist/config path independently.
- Diagnosis distinguishes running, endpoint healthy, and product ready.
- Migration rewrites all required current-root paths.
- Migration explicitly replaces an obsolete state directory.
- Migration preserves unrelated plist/config values.
- Failed health polling restores the exact prior plist/config bytes.
- Backup and replacement modes are owner-only.
- Tests do not consult the real user LaunchAgent directory.
- Upstream `fork/branch` is used when configured.
- Missing upstream and fetch failure stop before deployment.
- A dirty tracked `bridge.py` prevents source overwrite.
- Rollback never copies `last-good.py` over tracked source.
- Failure returns nonzero even when the prior service remains running.

### 6.4 Gate matrix

| Gate | Trigger | Required evidence |
|---|---|---|
| Targeted red | Before each production behavior change | Test fails for the intended missing behavior, not an import/setup error |
| Targeted green | After minimal implementation | The exact test passes |
| Accumulated Python | Once before live mutation | New Phase 0 suite plus retained applicable Python tests pass |
| Python static | Once before live mutation | All touched Python files compile; diff check is clean |
| Mini App build | Once before migration | Production build exits zero and expected server entry exists |
| TypeScript tests | Only if TypeScript/dependency source changes | Relevant workspace tests pass; full suite once if shared behavior changed |
| Live smoke | Once after migration | Correct launchd paths, stable process, healthy loopback endpoint, qualified model, harmless usable `/new` |
| Focused security review | Once on final accumulated diff | No unresolved issue in filesystem modes, process execution, model input, external API, or recovery paths |
| Fresh Sol audit | Once after all prior gates | `ship`, or correction followed by fresh verification and a new review |

## 7. Live Smoke Boundaries

The live smoke is authorized only after static verification and recoverable backups exist.

It may:

- Reload the Mini App LaunchAgent using the corrected local plist.
- Query the local health endpoint.
- Inspect launchd process metadata and non-secret logs.
- Resolve the configured model through explicit provider qualification and connected-provider evidence.
- Invoke the new session-creation boundary directly to create one harmless session whose prompt performs no filesystem, browser, network, account, or external-service action.
- Verify that the new session has valid local transcript state.

It may not:

- Send privileged commands through the new session.
- Change Aside permissions or grant full access.
- Post messages to third parties.
- Modify Telegram menu configuration.
- Create billable cloud resources.
- Push Git state or publish a release.

If harmless session creation itself could incur model usage, report that fact before the live smoke and use the smallest available validation prompt.

The live smoke does not invoke Telegram `heavy_new()` because the current post-create path grants full access. Phase 0 proves the same qualified model and session-creation boundary directly, while unit/integration tests prove that `/new` consumes its structured result. Live permission behavior remains deferred to the approval/permission phase.

## 8. File-Level Design

Expected repository changes:

- Create `aside_session.py`: model qualification, session discovery, validity, and structured creation results.
- Modify `bridge.py`: call the new boundary while preserving unrelated current recovery edits.
- Modify `bridgemon.py`: structured diagnosis, upstream resolution, path migration, and non-overwriting rollback behavior.
- Modify `miniapp/setup-miniapp.py`: explicit stale-state-directory migration and reusable atomic configuration helpers, only if `bridgemon.py` should delegate the migration rather than own it.
- Create or convert focused `unittest` modules under `tests/` for Aside session and deployment behavior.
- Update `README.md` only where operator commands or recovery guarantees materially change.

Files outside this list require an explicit design amendment before modification.

## 9. Acceptance Criteria

Phase 0 is accepted only when all of the following are evidenced:

1. The original modified `bridge.py` and `last-good.py` digests were recorded without exposing secrets.
2. No existing user-owned recovery file was discarded.
3. Update and rollback logic cannot overwrite dirty tracked bridge source.
4. Update logic uses the configured tracking remote/ref.
5. Mini App installed program, config, working directory, logs, and state directory point to the active checkout or the explicitly selected runtime directory.
6. The Mini App LaunchAgent remains stable after reload.
7. The actual loopback Mini App health endpoint responds successfully.
8. The selected model resolves to one explicitly configured provider-qualified identifier whose provider is connected, and the session handshake proves actual availability.
9. `/new` creates one usable session or returns the exact model/process/session failure category.
10. Process exit zero without a valid session is never reported as success.
11. Phase 0 Python tests pass hermetically.
12. The Mini App production build passes.
13. The bounded direct session-boundary live smoke passes without granting full access.
14. The focused security review has no unresolved required correction.
15. The fresh Sol / High reviewer returns `ship` on the final verified diff.

## 10. Residual Risk After Phase 0

Phase 0 does not make Telegram task delivery durable and does not enforce approvals before privileged execution. Until later phases are complete:

- A crash can still lose an already acknowledged Telegram update.
- Long turns can still block the global worker.
- Queued work and callbacks can still be affected by mutable global session state.
- Telegram approval remains a prompt convention rather than a hard broker.
- The Mini App retains the separately documented JWT URL, resource-limit, and public-admission risks.

These limitations must remain visible in the final Phase 0 handoff. A healthy deployment and truthful `/new` workflow must not be described as complete product hardening.

## 11. Implementation Order

1. Add the hermetic `unittest` foundation and checkpoint tests.
2. Add failing model/session boundary tests.
3. Implement `aside_session.py` and integrate `/new` minimally.
4. Add failing deployment/upstream/rollback tests.
5. Implement structured diagnosis and non-overwriting update behavior.
6. Add failing Mini App path migration tests.
7. Implement atomic, recoverable path and state-directory migration.
8. Update operator documentation.
9. Run accumulated static and build verification.
10. Create the live checkpoint and perform the bounded migration/smoke.
11. Run focused security review and primary diff review.
12. Obtain one fresh Sol / High audit verdict.
