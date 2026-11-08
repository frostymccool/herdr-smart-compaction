"""Adversarial: corrupt data, odd inputs, timeouts, unicode, concurrency."""
import os, threading
import pytest
from hsc import core
from conftest import FakeOps, Clock


def test_corrupt_state_file_reads_as_empty(tmp_path):
    (tmp_path / "s.json").write_text("{not json")
    assert core.read_state(str(tmp_path), "s") == {}
    assert core.decide_stop({}, False, pane="p") is None


def test_state_file_with_non_dict_json(tmp_path):
    (tmp_path / "s.json").write_text("[1,2]")
    assert core.read_state(str(tmp_path), "s") == {}


@pytest.mark.parametrize("sid", ["../etc/passwd", "a/b", "", None, 5, "x" * 500, "a b"])
def test_session_id_path_traversal_rejected(tmp_path, sid):
    with pytest.raises(ValueError):
        core.write_state(str(tmp_path), sid, pct=1)


@pytest.mark.parametrize("bad", [None, "47", True, [], {}])
def test_non_numeric_pct_is_unknown(bad):
    assert core.parse_statusline({"session_id": "s", "context_window": {"used_percentage": bad}})["pct"] is None


@pytest.mark.parametrize("data", [None, [], "x", {"context_window": None}, {"context_window": "x"}])
def test_parse_statusline_survives_garbage(data):
    core.parse_statusline(data)


def test_no_pane_means_no_block():
    assert core.decide_stop({"pct": 70}, False, pane=None) is None


def test_busy_forever_times_out_without_typing():
    ops, c = FakeOps(["working"]), Clock()
    assert core.compact_when_idle("p", ops, timeout=30, sleep=c.sleep, now=c.now) == "timeout"
    assert ops.sent == []


def test_pane_gone_returns_without_typing():
    ops, c = FakeOps([None]), Clock()
    assert core.compact_when_idle("p", ops, sleep=c.sleep, now=c.now) == "pane-gone"
    assert ops.sent == []


def test_user_typing_in_prompt_is_not_clobbered():
    ops, c = FakeOps(["idle"], screen="❯ half-written message\n"), Clock()
    assert core.compact_when_idle("p", ops, sleep=c.sleep, now=c.now) == "user-typing"
    assert ops.sent == []


def test_flapping_idle_must_hold_for_settle_period():
    ops, c = FakeOps(["idle", "working", "idle", "working", "idle"]), Clock()
    assert core.compact_when_idle("p", ops, settle=2.0, sleep=c.sleep, now=c.now) == "sent"
    assert c.t >= 6.0


def test_send_failure_reported():
    ops, c = FakeOps(["idle"], send_ok=False), Clock()
    assert core.compact_when_idle("p", ops, sleep=c.sleep, now=c.now) == "send-failed"


def test_unicode_focus_text_passes_through():
    ops, c = FakeOps(["idle"]), Clock()
    core.compact_when_idle("p", ops, focus="保留 TODO — café", sleep=c.sleep, now=c.now)
    assert "café" in ops.sent[0][1]


def test_concurrent_writers_never_leave_corrupt_file(tmp_path):
    def w(i):
        for n in range(30):
            core.write_state(str(tmp_path), "s", pct=i * 100 + n)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert isinstance(core.read_state(str(tmp_path), "s").get("pct"), int)
    assert not [f for f in os.listdir(tmp_path) if ".tmp." in f]


def test_prompt_detection_ignores_blank_trailing_lines():
    assert core.pane_prompt_empty("stuff\n❯\n\n\n") is True
    assert core.pane_prompt_empty("stuff\n❯ hi\n\n") is False
    assert core.pane_prompt_empty("") is True


def test_done_status_counts_as_ready():
    ops, c = FakeOps(["working", "done"]), Clock()
    assert core.compact_when_idle("p", ops, sleep=c.sleep, now=c.now) == "sent"


@pytest.mark.parametrize("st", ["working", "blocked", "unknown"])
def test_non_ready_statuses_never_type(st):
    ops, c = FakeOps([st]), Clock()
    assert core.compact_when_idle("p", ops, timeout=20, sleep=c.sleep, now=c.now) == "timeout"
    assert ops.sent == []


# ---- installer adversarial ----
import json
from hsc import installer

BD = "/x/bin"


def test_existing_statusline_is_wrapped_and_restored_exactly():
    orig = {"statusLine": {"type": "command", "command": "bash ~/it's \"weird\" $x.sh", "padding": 2}}
    s, _ = installer.install(orig, bd=BD)
    assert "HSC_WRAP_CMD=" in s["statusLine"]["command"] and s["statusLine"]["padding"] == 2
    assert installer.uninstall(s)[0]["statusLine"] == orig["statusLine"]


def test_other_hooks_and_keys_survive_roundtrip():
    orig = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                      "SessionStart": [{"hooks": []}]},
            "permissions": {"allow": ["Bash(ls)"]}, "model": "x"}
    s, _ = installer.install(orig, bd=BD)
    assert s["hooks"]["Stop"][0] == orig["hooks"]["Stop"][0] and len(s["hooks"]["Stop"]) == 2
    assert installer.uninstall(s)[0] == orig


def test_existing_env_override_is_never_changed_or_removed():
    orig = {"env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "75"}}
    s, notes = installer.install(orig, bd=BD)
    assert s["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] == "75" and any("left alone" in n for n in notes)
    assert installer.uninstall(s)[0]["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] == "75"


def test_uninstall_when_never_installed_is_noop():
    orig = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    assert installer.uninstall(orig)[0] == orig


def test_malformed_hooks_value_rejected_not_clobbered():
    with pytest.raises(ValueError):
        installer.install({"hooks": []}, bd=BD)


def test_load_rejects_corrupt_and_non_object(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{oops")
    with pytest.raises(ValueError):
        installer.load(str(p))
    p.write_text("[1]")
    with pytest.raises(ValueError):
        installer.load(str(p))


def test_load_missing_file_is_empty(tmp_path):
    assert installer.load(str(tmp_path / "nope.json")) == {}


def test_save_makes_backup_and_leaves_no_temp(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"a": 1}')
    b = installer.save(str(p), {"a": 2})
    assert json.load(open(b)) == {"a": 1} and json.load(open(p)) == {"a": 2}
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".hsc-settings")]


def test_unicode_survives_save(tmp_path):
    p = tmp_path / "s.json"
    installer.save(str(p), {"note": "café — 保留"})
    assert installer.load(str(p))["note"] == "café — 保留"


def test_wrap_option_used_only_when_no_statusline():
    s, _ = installer.install({}, bd=BD, wrap="bash g.sh")
    assert s["statusLine"]["command"].startswith("HSC_WRAP_CMD='bash g.sh' ")
    s2, _ = installer.install({"statusLine": {"type": "command", "command": "mine"}}, bd=BD, wrap="bash g.sh")
    assert "HSC_WRAP_CMD=mine " in s2["statusLine"]["command"]


# ---- restart adversarial ----
from hsc import restart
from conftest import FakeRestartOps


def test_restart_refuses_non_claude_pane():
    ops = FakeRestartOps()
    ops.agent = "shell"
    assert restart.plan("p", ops) == (False, "no-claude-session")


def test_restart_refuses_when_no_session_id():
    ops = FakeRestartOps()
    ops.has_session = False
    assert restart.plan("p", ops) == (False, "no-claude-session")


def test_restart_does_not_clobber_user_typing():
    ops, c = FakeRestartOps(screen="❯ half typed\n"), Clock()
    assert restart.restart_when_ready("p", ops, sleep=c.sleep, now=c.now) == "user-typing"
    assert ops.sent == [] and ops.ran == []


def test_exit_that_never_completes_times_out_without_relaunch():
    ops, c = FakeRestartOps(exits=False), Clock()
    assert restart.restart_when_ready("p", ops, exit_timeout=10, sleep=c.sleep, now=c.now) == "exit-timeout"
    assert ops.ran == []


def test_relaunch_failure_and_slow_start_reported():
    ops, c = FakeRestartOps(relaunch_ok=False), Clock()
    assert restart.restart_when_ready("p", ops, sleep=c.sleep, now=c.now) == "relaunch-failed"
    ops, c = FakeRestartOps(starts=False), Clock()
    assert restart.restart_when_ready("p", ops, start_timeout=5, sleep=c.sleep, now=c.now) == "resume-timeout"


def test_cwd_with_spaces_and_quotes_is_shell_safe():
    ops = FakeRestartOps(cwd="/a b/it's")
    _, d = restart.plan("p", ops)
    import shlex
    assert shlex.split(d["command"].split(" && ")[0])[1] == "/a b/it's"


def test_restart_notice_skipped_when_user_already_typing_or_disabled():
    ops, c = FakeRestartOps(), Clock()
    assert restart.restart_when_ready("p", ops, notice=None, sleep=c.sleep, now=c.now) == "restarted"
    assert all(t[0] != "text" or t[1] == "/exit" for t in ops.sent)
    ops, c = FakeRestartOps(), Clock()
    orig = ops.read
    ops.read = lambda p: "❯ \n" if ("text", "/exit") not in ops.sent or ops.agent != "claude" or not ops.ran else "❯ half\n"
    assert restart.restart_when_ready("p", ops, sleep=c.sleep, now=c.now) == "restarted"
    assert ("text", restart.NOTICE) not in ops.sent
