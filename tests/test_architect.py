"""API contracts and structural correctness."""
import json
from hsc import core
from conftest import FakeOps, Clock


def test_parse_statusline_extracts_fields():
    d = {"session_id": "s", "context_window": {"used_percentage": 42.5, "context_window_size": 200000}}
    assert core.parse_statusline(d) == {"session_id": "s", "pct": 42.5, "window": 200000}


def test_state_roundtrip_and_merge(tmp_path):
    core.write_state(str(tmp_path), "s1", pct=50)
    s = core.write_state(str(tmp_path), "s1", asked_pct=50)
    assert s["pct"] == 50 and s["asked_pct"] == 50 and "updated" in s
    assert core.read_state(str(tmp_path), "s1")["pct"] == 50


def test_decide_stop_below_threshold_allows():
    assert core.decide_stop({"pct": 39.9}, False, pane="p") is None


def test_decide_stop_in_band_blocks_with_reason():
    out = core.decide_stop({"pct": 47}, False, pane="p")
    assert out["decision"] == "block" and "hsc-compact" in out["reason"] and "47%" in out["reason"]
    json.dumps(out)


def test_stop_hook_active_never_blocks():
    assert core.decide_stop({"pct": 80}, True, pane="p") is None


def test_does_not_reask_until_pct_grows():
    assert core.decide_stop({"pct": 52, "asked_pct": 50}, False, pane="p") is None
    assert core.decide_stop({"pct": 55, "asked_pct": 50}, False, pane="p") is not None


def test_compact_sends_text_then_enter():
    ops, c = FakeOps(["idle"]), Clock()
    assert core.compact_when_idle("p", ops, sleep=c.sleep, now=c.now) == "sent"
    assert ops.sent == [("text", "/compact"), ("enter",)]


def test_focus_text_appended():
    ops, c = FakeOps(["idle"]), Clock()
    core.compact_when_idle("p", ops, focus="keep the TODO list", sleep=c.sleep, now=c.now)
    assert ops.sent[0] == ("text", "/compact keep the TODO list")


# ---- installer contracts ----
from hsc import installer

BD = "/x/bin"


def test_install_adds_stop_statusline_and_backstop():
    s, notes = installer.install({}, bd=BD)
    assert s["hooks"]["Stop"][0]["hooks"][0]["command"] == "/x/bin/hsc-stop-hook"
    assert s["statusLine"] == {"type": "command", "command": "/x/bin/hsc-statusline"}
    assert s["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] == "60"
    assert len(notes) == 3


def test_install_is_idempotent():
    once, _ = installer.install({}, bd=BD)
    twice, notes = installer.install(once, bd=BD)
    assert once == twice and any("already" in n for n in notes)


def test_install_does_not_mutate_input():
    src = {"hooks": {"Stop": []}}
    installer.install(src, bd=BD)
    assert src == {"hooks": {"Stop": []}}


def test_backstop_zero_skips_env():
    s, _ = installer.install({}, bd=BD, backstop=0)
    assert "env" not in s


def test_uninstall_of_clean_install_is_empty():
    s, _ = installer.install({}, bd=BD)
    assert installer.uninstall(s)[0] == {}


# ---- restart contracts ----
from hsc import restart
from conftest import FakeRestartOps


def test_resume_argv_replaces_old_resume_and_continue():
    assert restart.resume_argv(["claude", "--resume", "old", "--settings", "x"], "new") == ["claude", "--settings", "x", "--resume", "new"]
    assert restart.resume_argv(["claude", "-c", "--model", "m"], "n") == ["claude", "--model", "m", "--resume", "n"]
    assert restart.resume_argv(["claude", "--resume=old"], "n") == ["claude", "--resume", "n"]
    assert restart.resume_argv(["claude"], "n") == ["claude", "--resume", "n"]


def test_plan_builds_cd_and_resume_command_keeping_flags():
    ok, d = restart.plan("p", FakeRestartOps())
    assert ok and d["command"] == "cd '/proj dir' && claude --settings /s.json --resume sess-1"


def test_restart_sequence_exit_then_relaunch():
    ops, c = FakeRestartOps(), Clock()
    assert restart.restart_when_ready("p", ops, sleep=c.sleep, now=c.now) == "restarted"
    assert ops.sent[:2] == [("text", "/exit"), ("enter",)] and len(ops.ran) == 1


def test_reason_has_no_skill_step_by_default():
    r = core.decide_stop({"pct": 47}, False, pane="p")["reason"]
    assert "skill" not in r and core.PRE_COMPACT_SKILL == ""


def test_reason_runs_configured_skill_before_compact():
    r = core.decide_stop({"pct": 47}, False, pane="p", compact_cmd="/x/hsc-compact", pre_skill="my-sync")["reason"]
    assert "`my-sync`" in r
    assert r.index("my-sync") < r.index("/x/hsc-compact")


def test_installer_pre_skill_added_and_removed_cleanly():
    from hsc import installer
    new, notes = installer.install({}, bd="/b", pre_skill="my-sync")
    assert new["env"]["HSC_PRE_COMPACT_SKILL"] == "my-sync"
    back, _ = installer.uninstall(new)
    assert back == {}
