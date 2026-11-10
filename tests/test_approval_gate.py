"""Offline harness for the approval-gate. Stubs Telegram + state I/O,
never touches the network or the live state.json."""
import importlib.util
import json
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

# ---- stub out all side effects -------------------------------------
SENT = []           # tg() calls
BUBBLES = []        # send_bubbles()
TEXTS = []          # send_text()


def fake_tg(method, params=None, timeout=65):
    SENT.append((method, params or {}))
    return {"ok": True, "result": {"message_id": 4242}}


b.tg = fake_tg
b.save_json = lambda *a, **k: None
b.send_bubbles = lambda t: BUBBLES.append(t)
b.send_text = lambda t: TEXTS.append(t)
b.tg_send_status = lambda t: None
b.tg_edit = lambda *a, **k: True
b.tg_delete = lambda *a, **k: None
b.state["session_id"] = "TESTSESS"
b.state["approval"] = None

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# ---- 1. parse_approval ---------------------------------------------
txt = ("Sure, here's what I'll do.\n\n[[APPROVAL]]\n"
       "Action: Send an email to sam@example.com\n"
       "Details: subject 'Hi', 2 lines\n[[/APPROVAL]]")
ap = b.parse_approval(txt)
check("parse: found", ap is not None)
check("parse: action", ap and ap["action"] == "Send an email to sam@example.com")
check("parse: details", ap and ap["details"].startswith("subject 'Hi'"))
check("parse: none when absent", b.parse_approval("just a normal reply") is None)
check("parse: case-insensitive markers",
      b.parse_approval("[[approval]]\nAction: X\n[[/approval]]") is not None)

# ---- 2. present_approval sends buttons + records state -------------
SENT.clear()
b.present_approval(ap)
send = [s for s in SENT if s[0] == "sendMessage"]
check("present: sent a message", len(send) == 1)
km = json.loads(send[0][1]["reply_markup"])["inline_keyboard"][0]
cbs = [btn["callback_data"] for btn in km]
check("present: approve+deny buttons",
      any(c.startswith("apv:approve:") for c in cbs) and
      any(c.startswith("apv:deny:") for c in cbs))
tok = b.state["approval"]["token"]
check("present: token stored", bool(tok) and ("apv:approve:" + tok) in cbs)
check("present: action stored",
      b.state["approval"]["action"] == ap["action"])

# ---- 3. approve tap injects a proceed turn ------------------------
SENT.clear()
while not b.TASKS.empty():
    b.TASKS.get_nowait()
b._handle_approval_tap("apv:approve:" + tok, 4242)
check("approve: state cleared", b.state["approval"] is None)
check("approve: one task queued", b.TASKS.qsize() == 1)
kind, payload = b.TASKS.get_nowait()
check("approve: inject is a proceed msg",
      kind == "msg" and "APPROVAL GRANTED" in payload["text"] and
      "Proceed" in payload["text"])
check("approve: edited the buttons message",
      any(s[0] == "editMessageText" for s in SENT))

# ---- 4. deny tap injects an abort turn ----------------------------
b.state["approval"] = {"token": "t2", "action": "delete prod db",
                       "details": "", "session_id": "TESTSESS",
                       "message_id": 1, "ts": 0}
while not b.TASKS.empty():
    b.TASKS.get_nowait()
b._handle_approval_tap("apv:deny:t2", 1)
kind, payload = b.TASKS.get_nowait()
check("deny: inject is an abort msg",
      kind == "msg" and "APPROVAL DENIED" in payload["text"] and
      "Do not perform" in payload["text"])
check("deny: state cleared", b.state["approval"] is None)

# ---- 5. stale/duplicate tap is graceful ---------------------------
TEXTS.clear()
b.state["approval"] = {"token": "fresh", "action": "x", "details": "",
                       "session_id": "s", "message_id": 1, "ts": 0}
b._handle_approval_tap("apv:approve:STALE", 1)
check("stale: not consumed", b.state["approval"] is not None)
check("stale: user told", any("isn't active" in t for t in TEXTS))

# ---- 6. finish() suppresses the final block when flagged ----------
BUBBLES.clear()
turn = b.TurnStream()
turn.turn_mode = "pending"
turn.pending_block = "[[APPROVAL]]\nAction: X\n[[/APPROVAL]]"
turn.suppress_final = True
turn.finish()
check("suppress: no bubble sent for approval block", BUBBLES == [])

BUBBLES.clear()
turn2 = b.TurnStream()
turn2.turn_mode = "pending"
turn2.pending_block = "normal reply"
turn2.finish()
check("no-suppress: normal reply still sent", BUBBLES == ["normal reply"])

# ---- 7. stream_new detects the native browser confirmation --------
TEXTS.clear()
tf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
tf.write(json.dumps({
    "role": "assistant",
    "content": [{"type": "toolCall", "id": "tc1",
                 "name": "request_action_confirmation",
                 "arguments": {"title": "Send email", "message": "ok?"}}],
}) + "\n")
tf.close()
turn3 = b.TurnStream()
b.stream_new(tf.name, 0, turn3)
os.unlink(tf.name)
check("native: notified once", turn3.native_confirm_notified and
      any("browser-level confirmation" in t for t in TEXTS))

print("\n%d checks, %d failed" % (
    7 + 20 - len(fails) if False else 0, len(fails)))
sys.exit(1 if fails else 0)
