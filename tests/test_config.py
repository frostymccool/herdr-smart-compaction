"""Range config: architect (contract), pessimist (bad input), user (workflows)."""
import json, os, subprocess, sys
import pytest
from conftest import *  # noqa
from hsc import config, installer

ROOT = os.path.join(os.path.dirname(__file__), "..")
CFG = os.path.join(ROOT, "bin", "hsc-config")


def run(tmp, *args, env=None):
    e = dict(os.environ, HSC_CONFIG_DIR=str(tmp / "cfg"), HSC_STATE_DIR=str(tmp / "st"))
    e.pop("HSC_LOW_PCT", None)
    e.update(env or {})
    return subprocess.run([sys.executable, CFG, "--project", str(tmp / "proj"), *args], capture_output=True, text=True, env=e)


# --- architect
def test_defaults_and_presets():
    assert config.resolve(None, env={}, gpath="/nonexistent") == {"low": 40, "backstop": 60, "source": "default"}
    assert config.PRESETS == {"eager": (30, 50), "balanced": (40, 60), "relaxed": (50, 70)}


def test_precedence_env_over_project_over_global(tmp_path):
    g = tmp_path / "g.json"; config.write(str(g), 30, 50)
    proj = tmp_path / "p"; config.write(config.project_path(str(proj)), 50, 70)
    assert config.resolve(str(proj), env={}, gpath=str(g))["source"] == "project"
    assert config.resolve(str(tmp_path / "other"), env={}, gpath=str(g)) == {"low": 30, "backstop": 50, "source": "global"}
    assert config.resolve(str(proj), env={"HSC_LOW_PCT": "35"}, gpath=str(g))["low"] == 35


def test_installer_set_env_replaces_existing():
    new, changed = installer.set_env({"env": {installer.ENV_KEY: "60"}}, installer.ENV_KEY, 50)
    assert changed and new["env"][installer.ENV_KEY] == "50"
    assert installer.set_env(new, installer.ENV_KEY, 50)[1] is False


# --- pessimist
@pytest.mark.parametrize("low,back", [(5, 60), (40, 95), (55, 60), (40, 40), (True, 60), (40.5, 60), ("40", 60), (None, 60)])
def test_validate_rejects_bad_pairs(low, back):
    with pytest.raises(ValueError):
        config.validate(low, back)


def test_corrupt_or_invalid_files_fall_back_to_defaults(tmp_path):
    p = tmp_path / "proj" / ".claude"; p.mkdir(parents=True)
    for bad in ("{not json", "[]", '{"low": 90, "backstop": 20}', ""):
        (p / "hsc.json").write_text(bad)
        assert config.resolve(str(tmp_path / "proj"), env={}, gpath="/nonexistent")["source"] == "default"


def test_cli_rejects_bad_values_without_writing(tmp_path):
    (tmp_path / "proj").mkdir()
    r = run(tmp_path, "--low", "58", "--backstop", "60")
    assert r.returncode != 0 and not (tmp_path / "proj" / ".claude").exists()
    assert run(tmp_path).returncode != 0   # nothing asked for


# --- user workflows
def test_set_preset_for_project_writes_file_and_backstop_env(tmp_path):
    (tmp_path / "proj").mkdir()
    r = run(tmp_path, "--preset", "eager")
    assert r.returncode == 0 and "restart" in r.stdout
    assert json.load(open(tmp_path / "proj" / ".claude" / "hsc.json")) == {"low": 30, "backstop": 50}
    assert json.load(open(tmp_path / "proj" / ".claude" / "settings.local.json"))["env"][installer.ENV_KEY] == "50"
    assert "30%" in run(tmp_path, "--show").stdout


def test_changing_only_low_keeps_backstop_and_needs_no_restart(tmp_path):
    (tmp_path / "proj").mkdir()
    run(tmp_path, "--preset", "balanced")
    r = run(tmp_path, "--low", "45")
    assert "45%" in r.stdout and "backstop 60%" in r.stdout and "restart" not in r.stdout


def test_project_change_does_not_touch_global(tmp_path):
    (tmp_path / "proj").mkdir()
    run(tmp_path, "--preset", "relaxed")
    assert not (tmp_path / "cfg").exists()


def test_global_requires_confirmation_and_lists_sessions(tmp_path, monkeypatch):
    (tmp_path / "proj").mkdir()
    fake = tmp_path / "bin"; fake.mkdir()
    (fake / "herdr").write_text('#!/bin/sh\necho \'{"result":{"panes":[{"agent":"claude","terminal_title_stripped":"alpha","cwd":"/x/alpha","agent_session":{"value":"s1"}}]}}\'\n')
    (fake / "herdr").chmod(0o755)
    env = {"PATH": "%s:%s" % (fake, os.environ["PATH"]), "HOME": str(tmp_path)}
    r = run(tmp_path, "--global", "--preset", "relaxed", env=env)
    assert r.returncode == 3 and "alpha" in r.stdout and "NOT applied" in r.stdout
    assert not (tmp_path / "cfg" / "config.json").exists()
    r = run(tmp_path, "--global", "--preset", "relaxed", "--confirmed", env=env)
    assert r.returncode == 0 and json.load(open(tmp_path / "cfg" / "config.json")) == {"low": 50, "backstop": 70}


def test_badge_shows_range(tmp_path):
    env = dict(os.environ, HSC_STATE_DIR=str(tmp_path / "st"), HSC_CONFIG_DIR=str(tmp_path / "cfg"))
    env.pop("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", None); env.pop("HSC_LOW_PCT", None)
    payload = json.dumps({"session_id": "s", "context_window": {"used_percentage": 42}, "cwd": str(tmp_path)})
    r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "hsc-statusline")], input=payload, capture_output=True, text=True, env=env)
    assert "SC 42% (40-60)" in r.stdout
