"""Pure logic for herdr-smart-compaction. All I/O goes through injected ops."""
import json
import os
import re
import subprocess
import tempfile
import time

LOW_PCT = 40
ASK_STEP = 5
PRE_COMPACT_SKILL = ""  # opt-in: name of a skill the agent runs before compacting
READY_STATES = ("idle", "done")
STATE_DIR = os.environ.get("HSC_STATE_DIR", "/tmp/claude-ctx")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _state_path(state_dir, session_id):
    if not isinstance(session_id, str) or not SAFE_ID.match(session_id):
        raise ValueError("bad session id")
    return os.path.join(state_dir, session_id + ".json")


def parse_statusline(data):
    """Extract context usage from statusLine stdin JSON; None pct if unknown."""
    cw = data.get("context_window") if isinstance(data, dict) else None
    cw = cw if isinstance(cw, dict) else {}
    pct = cw.get("used_percentage")
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        pct = None
    return {
        "session_id": data.get("session_id") if isinstance(data, dict) else None,
        "pct": pct,
        "window": cw.get("context_window_size"),
    }


def read_state(state_dir, session_id):
    try:
        with open(_state_path(state_dir, session_id)) as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state_dir, session_id, **fields):
    """Merge fields into the session's state file atomically."""
    os.makedirs(state_dir, exist_ok=True)
    state = read_state(state_dir, session_id)
    state.update(fields)
    state["updated"] = time.time()
    path = _state_path(state_dir, session_id)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=state_dir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return state


def decide_stop(state, stop_hook_active, low=LOW_PCT, step=ASK_STEP, pane=None, compact_cmd="hsc-compact",
                pre_skill=PRE_COMPACT_SKILL):
    """Return a Stop-hook JSON dict that asks the agent to judge, or None to allow stop."""
    if stop_hook_active or not pane:
        return None
    pct = state.get("pct")
    if pct is None or pct < low:
        return None
    last = state.get("asked_pct")
    if last is not None and pct < last + step:
        return None
    first = ("first invoke the `%s` skill (Skill tool) and let it finish so progress is saved "
             "before the context is lost, then " % pre_skill) if pre_skill else ""
    reason = (
        "Context is about %d%% used. If your current series of tasks is finished and no likely "
        "follow-up work would really benefit from the context you hold now, %srun `%s` "
        "(Bash) and then end your turn; it will compact this session once you go idle. "
        "If more related work is probably coming, just stop normally - nothing else is needed%s. "
        "Do not start any new work because of this message." % (round(pct), first, compact_cmd,
                                                                " (do not run the skill either)" if pre_skill else "")
    )
    return {"decision": "block", "reason": reason}


def pane_prompt_empty(pane_text):
    """True if the input box shows no typed text (safe to type a command)."""
    lines = [l for l in pane_text.splitlines() if l.strip()]
    for line in reversed(lines):
        s = line.strip()
        if s.startswith("❯") or s.startswith(">"):
            return s.lstrip("❯> ").strip() == ""
    return True


class HerdrOps:
    def __init__(self, herdr="herdr", runner=subprocess.run):
        self.herdr = herdr
        self.run = runner

    def _call(self, *args, timeout=None):
        return self.run([self.herdr, *args], capture_output=True, text=True, timeout=timeout)

    def status(self, pane):
        r = self._call("pane", "get", pane, timeout=10)
        try:
            return json.loads(r.stdout)["result"]["pane"].get("agent_status")
        except (ValueError, KeyError, TypeError):
            return None

    def pane_info(self, pane):
        r = self._call("pane", "get", pane, timeout=10)
        try:
            return json.loads(r.stdout)["result"]["pane"]
        except (ValueError, KeyError, TypeError):
            return None

    def process_info(self, pane):
        r = self._call("pane", "process-info", "--pane", pane, timeout=10)
        try:
            return json.loads(r.stdout)["result"]["process_info"]
        except (ValueError, KeyError, TypeError):
            return None

    def run_command(self, pane, command):
        return self._call("pane", "run", pane, command, timeout=10).returncode == 0

    def read(self, pane):
        return self._call("pane", "read", pane, "--source", "visible", timeout=10).stdout

    def send_text(self, pane, text):
        return self._call("pane", "send-text", pane, text, timeout=10).returncode == 0

    def send_enter(self, pane):
        return self._call("pane", "send-keys", pane, "enter", timeout=10).returncode == 0


def wait_ready(pane, ops, timeout=300, poll=1.0, settle=2.0, sleep=time.sleep, now=time.monotonic):
    """Wait until the pane has been idle/done for `settle` seconds. Returns 'ready'|'timeout'|'pane-gone'."""
    deadline = now() + timeout
    idle_since = None
    while now() < deadline:
        st = ops.status(pane)
        if st is None:
            return "pane-gone"
        if st in READY_STATES:
            idle_since = now() if idle_since is None else idle_since
            if now() - idle_since >= settle:
                return "ready"
        else:
            idle_since = None
        sleep(poll)
    return "timeout"


def compact_when_idle(pane, ops, focus="", timeout=300, poll=1.0, settle=2.0,
                      sleep=time.sleep, now=time.monotonic):
    """Wait until pane is idle with an empty prompt, then type /compact. Returns a status string."""
    w = wait_ready(pane, ops, timeout, poll, settle, sleep, now)
    if w != "ready":
        return w
    if not pane_prompt_empty(ops.read(pane)):
        return "user-typing"
    cmd = "/compact" + (" " + focus.strip() if focus.strip() else "")
    if not ops.send_text(pane, cmd):
        return "send-failed"
    sleep(0.5)
    return "sent" if ops.send_enter(pane) else "send-failed"
