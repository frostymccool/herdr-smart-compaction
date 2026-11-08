"""Opt-in installer: edits a Claude Code settings.json to add/remove smart compaction."""
import copy
import json
import os
import shlex
import shutil
import tempfile
import time

PRE_KEY = "HSC_PRE_COMPACT_SKILL"
ENV_KEY = "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"
WRAP_VAR = "HSC_WRAP_CMD"


def bin_dir():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")


def _is_ours(cmd):
    return isinstance(cmd, str) and ("hsc-stop-hook" in cmd or "hsc-statusline" in cmd)


def _stop_entry(bd):
    return {"hooks": [{"type": "command", "command": os.path.join(bd, "hsc-stop-hook"), "timeout": 10}]}


def _has_our_stop(settings):
    for grp in (settings.get("hooks") or {}).get("Stop") or []:
        for h in (grp.get("hooks") or []) if isinstance(grp, dict) else []:
            if _is_ours(h.get("command")):
                return True
    return False


def install(settings, bd=None, backstop=60, wrap=None, pre_skill=None):
    """Return (new_settings, notes). Never mutates the input."""
    bd = bd or bin_dir()
    s = copy.deepcopy(settings)
    notes = []
    hooks = s.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings.hooks is not an object")
    stop = hooks.setdefault("Stop", [])
    if _has_our_stop(s):
        notes.append("Stop hook: already installed")
    else:
        stop.append(_stop_entry(bd))
        notes.append("Stop hook: added")
    sl = s.get("statusLine")
    ours = os.path.join(bd, "hsc-statusline")
    if isinstance(sl, dict) and _is_ours(sl.get("command")):
        notes.append("statusLine: already installed")
    elif isinstance(sl, dict) and sl.get("command"):
        s["statusLine"] = dict(sl, command="%s=%s %s" % (WRAP_VAR, shlex.quote(sl["command"]), ours))
        notes.append("statusLine: wrapped your existing command (its output is preserved)")
    elif wrap:
        s["statusLine"] = {"type": "command", "command": "%s=%s %s" % (WRAP_VAR, shlex.quote(wrap), ours)}
        notes.append("statusLine: added, wrapping the given command (output preserved)")
    else:
        s["statusLine"] = {"type": "command", "command": ours}
        notes.append("statusLine: added")
    if backstop:
        env = s.setdefault("env", {})
        cur = env.get(ENV_KEY)
        if cur is None:
            env[ENV_KEY] = str(backstop)
            notes.append("%s=%s: added (backstop)" % (ENV_KEY, backstop))
        else:
            notes.append("%s already set to %s: left alone" % (ENV_KEY, cur))
    if pre_skill:
        env = s.setdefault("env", {})
        cur = env.get(PRE_KEY)
        if cur is None:
            env[PRE_KEY] = pre_skill
            notes.append("%s=%s: added (skill the agent runs before compacting)" % (PRE_KEY, pre_skill))
        else:
            notes.append("%s already set to %s: left alone" % (PRE_KEY, cur))
    return s, notes


def uninstall(settings, backstop=60):
    s = copy.deepcopy(settings)
    notes = []
    hooks = s.get("hooks")
    if isinstance(hooks, dict) and isinstance(hooks.get("Stop"), list):
        kept = []
        for grp in hooks["Stop"]:
            hs = [h for h in (grp.get("hooks") or []) if not _is_ours(h.get("command"))] if isinstance(grp, dict) else None
            if hs is None:
                kept.append(grp)
            elif hs:
                kept.append(dict(grp, hooks=hs))
        if len(kept) != len(hooks["Stop"]) or any(_has_ours_in(g) for g in hooks["Stop"]):
            notes.append("Stop hook: removed")
        hooks["Stop"] = kept
        if not kept:
            del hooks["Stop"]
        if not hooks:
            del s["hooks"]
    sl = s.get("statusLine")
    if isinstance(sl, dict) and _is_ours(sl.get("command")):
        try:
            toks = shlex.split(sl["command"])
        except ValueError:
            toks = []
        if toks and toks[0].startswith(WRAP_VAR + "="):
            s["statusLine"] = dict(sl, command=toks[0].split("=", 1)[1])
            notes.append("statusLine: restored your original command")
        else:
            del s["statusLine"]
            notes.append("statusLine: removed")
    env = s.get("env")
    if isinstance(env, dict) and env.get(ENV_KEY) == str(backstop):
        del env[ENV_KEY]
        if not env:
            del s["env"]
        notes.append("%s: removed" % ENV_KEY)
    if isinstance(env, dict) and PRE_KEY in env:
        del env[PRE_KEY]
        if not env:
            del s["env"]
        notes.append("%s: removed" % PRE_KEY)
    return s, notes


def _has_ours_in(grp):
    return isinstance(grp, dict) and any(_is_ours(h.get("command")) for h in grp.get("hooks") or [])


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError("settings file is not a JSON object")
    return d


def save(path, settings):
    """Backup then atomically write. Returns backup path (or None if file was new)."""
    backup = None
    if os.path.exists(path):
        backup = "%s.hsc-backup-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(prefix=".hsc-settings.", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return backup
