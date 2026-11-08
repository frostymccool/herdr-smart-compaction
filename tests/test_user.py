"""Real-world workflow scenarios, end to end through the scripts' logic."""
from hsc import core
from conftest import FakeOps, Clock


def session_turn(tmp_path, pct, stop_active=False, pane="wM:p3"):
    """What happens when the agent stops: statusline ran earlier, then the Stop hook decides."""
    sd = str(tmp_path)
    core.write_state(sd, "sess", pct=pct)
    state = core.read_state(sd, "sess")
    out = core.decide_stop(state, stop_active, pane=pane)
    if out:
        core.write_state(sd, "sess", asked_pct=state["pct"])
    return out


def test_light_session_is_left_alone(tmp_path):
    assert session_turn(tmp_path, 12) is None


def test_busy_session_in_band_is_asked_once_then_continues(tmp_path):
    first = session_turn(tmp_path, 46)
    assert first and first["decision"] == "block"
    # agent decides more work is coming, just stops: hook fires again with stop_hook_active
    assert session_turn(tmp_path, 46, stop_active=True) is None
    # next unrelated stop at nearly the same pct is not nagged
    assert session_turn(tmp_path, 48) is None
    # much later, usage has grown: ask again
    assert session_turn(tmp_path, 56) is not None


def test_agent_chooses_compact_and_it_happens_after_idle(tmp_path):
    assert session_turn(tmp_path, 52)
    # agent runs hsc-compact during the blocked turn, then ends it: working -> working -> idle
    ops, c = FakeOps(["working", "working", "idle"]), Clock()
    assert core.compact_when_idle("wM:p3", ops, sleep=c.sleep, now=c.now) == "sent"
    assert ops.sent == [("text", "/compact"), ("enter",)]


def test_outside_herdr_nothing_happens(tmp_path):
    assert session_turn(tmp_path, 55, pane=None) is None


# ---- installer workflow via the real CLI ----
import json, os, subprocess, sys

CLI = os.path.join(os.path.dirname(__file__), "..", "bin", "hsc-install")
STATUS = os.path.join(os.path.dirname(__file__), "..", "bin", "hsc-statusline")


def run(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True)


def test_dry_run_by_default_changes_nothing(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"model": "x"}')
    r = run("--settings", str(p))
    assert r.returncode == 0 and "dry run" in r.stdout
    assert json.load(open(p)) == {"model": "x"}


def test_apply_then_uninstall_restores_user_settings(tmp_path):
    p = tmp_path / "settings.json"
    orig = {"model": "x", "statusLine": {"type": "command", "command": "bash mine.sh"}}
    p.write_text(json.dumps(orig))
    assert run("--settings", str(p), "--apply").returncode == 0
    assert "hsc-stop-hook" in p.read_text()
    assert run("--settings", str(p), "--uninstall", "--apply").returncode == 0
    assert json.load(open(p)) == orig


def test_cli_refuses_corrupt_settings_without_touching_it(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{broken")
    r = run("--settings", str(p), "--apply")
    assert r.returncode != 0 and p.read_text() == "{broken"


def test_wrapped_statusline_shows_users_own_output_and_records_state(tmp_path):
    env = dict(os.environ, HSC_STATE_DIR=str(tmp_path), HSC_WRAP_CMD="echo MY-STATUS")
    r = subprocess.run([sys.executable, STATUS], input='{"session_id":"s9","context_window":{"used_percentage":33}}',
                       capture_output=True, text=True, env=env)
    assert r.stdout.startswith("MY-STATUS ") and "SC 33%" in r.stdout and r.stdout.count("\n") == 1
    assert json.load(open(tmp_path / "s9.json"))["pct"] == 33


def test_statusline_survives_garbage_stdin(tmp_path):
    env = dict(os.environ, HSC_STATE_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, STATUS], input="not json", capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "SC --" in r.stdout


# ---- restart approval gate via the real CLI ----
RESTART = os.path.join(os.path.dirname(__file__), "..", "bin", "hsc-restart")


def run_restart(*args, pane="wZ:p1"):
    env = dict(os.environ, HERDR_PANE_ID=pane, HSC_STATE_DIR="/tmp/hsc-test-none")
    return subprocess.run([sys.executable, RESTART, *args], capture_output=True, text=True, env=env)


def test_restarting_another_session_without_user_override_is_refused():
    r = run_restart("--pane", "wQ:p9", "--dry-run")
    assert r.returncode != 0 and "--user-override" in r.stderr and "NOT approval" in r.stderr


def test_blank_override_is_refused_too():
    assert run_restart("--pane", "wQ:p9", "--user-override", "   ", "--dry-run").returncode != 0


def test_outside_herdr_and_no_pane_is_refused():
    env = {k: v for k, v in os.environ.items() if k != "HERDR_PANE_ID"}
    r = subprocess.run([sys.executable, RESTART, "--dry-run"], capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "not in a herdr pane" in r.stderr


def test_multiline_wrapped_status_gets_badge_on_last_line_only(tmp_path):
    env = dict(os.environ, HSC_STATE_DIR=str(tmp_path), HSC_WRAP_CMD="printf 'line1\\nline2\\n'")
    r = subprocess.run([sys.executable, STATUS], input='{"session_id":"m","context_window":{"used_percentage":47}}',
                       capture_output=True, text=True, env=env)
    lines = r.stdout.splitlines()
    assert lines[0] == "line1" and lines[1].startswith("line2 ") and "SC 47%" in lines[1]


def test_failing_wrapped_command_still_shows_badge(tmp_path):
    env = dict(os.environ, HSC_STATE_DIR=str(tmp_path), HSC_WRAP_CMD="exit 3")
    r = subprocess.run([sys.executable, STATUS], input='{"session_id":"f","context_window":{"used_percentage":8}}',
                       capture_output=True, text=True, env=env)
    assert "SC 8%" in r.stdout
