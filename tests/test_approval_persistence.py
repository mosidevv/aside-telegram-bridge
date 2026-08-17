"""A pending approval must survive a bridge restart: it is persisted
in state.json (real save_json/load_json round-trip) and the tap still
resolves after a fresh load. Uses a temp state file; never touches the
live state.json."""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location(
    "bridgemod", os.path.join(HERE, "bridge.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
tmp.close()
b.STATE_PATH = tmp.name
b.tg = lambda m, p=None, timeout=65: {"ok": True, "result": {"message_id": 7}}
SENT_TASKS = []
b.TASKS.put = lambda item: SENT_TASKS.append(item)

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# record a pending approval + persist it (as present_approval does)
b.state["approval"] = {"token": "abc123", "action": "send email to x",
                       "details": "", "session_id": "S1",
                       "message_id": 7, "ts": 0}
b.save_json(b.STATE_PATH, b.state)

# simulate a restart: reload the persisted state from disk
reloaded = b.load_json(b.STATE_PATH, {})
check("persist: approval written to disk", reloaded.get("approval") is not None)
check("persist: token preserved",
      reloaded["approval"].get("token") == "abc123")

# after "restart", the callback resolves against the reloaded state
b.state = reloaded
b.state.setdefault("approval", None)
b._handle_approval_tap("apv:approve:abc123", 7)
check("persist: tap after restart resolves", b.state.get("approval") is None)
check("persist: proceed turn injected",
      any(k == "msg" and "GRANTED" in p for k, p in SENT_TASKS))

os.unlink(tmp.name)
print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
