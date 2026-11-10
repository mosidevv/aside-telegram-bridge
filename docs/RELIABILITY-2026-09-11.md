# Bridge reliability investigation — 2026-09-11

## Evidence and repairs

The installed bridge and Mini App were both running; `bridgemon doctor --json`
reported healthy local processes, paths, and endpoint. This is infrastructure
evidence, not a complete Telegram-to-Aside acceptance test.

Recent logs contain Telegram HTTP 502 responses, read timeouts, and transient
network/DNS failures. The configured owner name contained pasted setup guidance
rather than a name. It was corrected to Mosi after an owner-only backup. This
changes future persona priming; it does not rewrite an existing transcript.

Regression tests reproduced and now cover:

- FIFO violation: messages around `/new` executed as message, message, `/new`.
- Waiting tasks lost on restart because Telegram offsets were acknowledged
  before tasks were persisted. Task enqueue and offset now share a state write.
- Replies discarded after three failed sends. Plain-text replies now persist
  before sending and retry in order, including after process restart.
- Approval/question responses associated with a different selected session.
  Responses carry their originating session through the queue, and old-session
  buttons require switching back before answering.
- Negative question indexes and invalid effort callback values accepted.
- Errors hidden when a turn had already streamed progress.
- A missing session starting an untracked execution.

The durable queue records waiting and in-flight work separately. Interrupted
work is retained in `state.json` under `interrupted_tasks` and is not automatically
re-executed. `/status` shows its count and the undelivered text count. Review the
session before resending an interrupted request. Startup no longer kills any
Aside process merely because its command line mentions the selected session.
A dead task worker causes the process to exit so launchd can restart it.

## Validation

- 87 Python unittest cases passed under `/usr/bin/python3`, including 19 new
  reliability cases.
- Offline approval/question/persistence harnesses: 89 assertions passed.
- Mini App TypeScript checks passed for server and web.
- Mini App baseline: 458 server and 144 web tests passed.
- Source compilation and `git diff --check` passed.
- Code and security review performed against the patch.

Live Telegram sends and the existing live approval end-to-end harness were not
run by this investigation. Acceptance still requires a user-originated message,
a `/new` boundary followed by another message, and a representative button tap.

## Live activation

The idle bridge service was restarted through launchd after the tests and
backups completed. Its PID changed from 12109 to 67720. The fresh startup log
confirms owner Mosi was loaded; no post-start errors were observed. The local
doctor again reported the bridge and Mini App healthy. Read-only Telegram
`getMe` and `getWebhookInfo` calls succeeded, with no webhook configured and
zero pending updates. No Telegram messages were sent by the test procedure.
The Mini App service was not restarted.

## Limits and separate findings

- Text delivery is at least once. An accepted Telegram send whose HTTP response
  is lost can be retried and duplicated. Telegram sends have no bridge-supplied
  idempotency key here.
- Photo sends, status edits, and button presentation do not use the text outbox;
  their existing fallback/error handling remains in place.
- The bot worker and Mini App runner do not share a cross-process execution
  lock. Simultaneously driving the same Aside session from both surfaces is a
  remaining concurrency risk; local health checks cannot rule it out.
- An already-running Aside task is not forcibly stopped during startup recovery.
  Review its transcript/status before repeating the interrupted request.
- Existing transcript persona text remains historical; a fresh `/new` session
  uses the corrected owner name.
- `npm audit --omit=dev` reported three existing production dependency findings:
  high advisories in brace-expansion and fast-uri, and moderate advisories in
  fastify. No dependencies were changed in this repair.
- Changes are local and uncommitted on `fix/new-session-model-resolution`.
  The existing untracked `miniapp-soft-confirm.json` was preserved.

## Recovery artifacts

Owner-only local files, excluded from source control:

- `backups/bridge-before-reliability-20260911.py`
- `backups/config-before-owner-repair-20260911T021109.json`

Do not overwrite a live state file or roll back while work is queued. The older
bridge does not understand the durable task queue or outbox fields.
