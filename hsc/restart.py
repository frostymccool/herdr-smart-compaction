"""Restart a Claude Code session in its herdr pane: /exit, then relaunch with --resume."""
import shlex
import time

from . import core


BANNER = "[herdr-smart-compaction] restarting this session..."
NOTICE = ("[herdr-smart-compaction] This session was just restarted to load updated settings. "
          "Reply with only: ⟳ Session restarted - ready.")


def resume_argv(argv, session_id):
    """Original claude argv with any prior resume/continue flags replaced by --resume <id>."""
    out, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a in ("--resume", "-r"):
            skip = True
            continue
        if a in ("--continue", "-c") or a.startswith("--resume="):
            continue
        out.append(a)
    return out + ["--resume", session_id]


def find_claude(process_info):
    for p in (process_info or {}).get("foreground_processes") or []:
        argv = p.get("argv") or []
        if argv and argv[0].split("/")[-1] == "claude":
            return argv, p.get("cwd")
    return None, None


def plan(pane, ops):
    """Return (ok, detail). detail is a dict with the relaunch command or a reason string."""
    info = ops.pane_info(pane)
    if not info:
        return False, "pane-gone"
    sess = (info.get("agent_session") or {}).get("value")
    if info.get("agent") != "claude" or not sess:
        return False, "no-claude-session"
    argv, cwd = find_claude(ops.process_info(pane))
    if not argv:
        return False, "no-claude-process"
    cwd = cwd or info.get("foreground_cwd") or info.get("cwd")
    cmd = "cd %s && %s" % (shlex.quote(cwd), " ".join(shlex.quote(a) for a in resume_argv(argv, sess)))
    return True, {"session_id": sess, "cwd": cwd, "command": cmd}


def _announce(pane, ops, notice, sleep):
    """Best-effort visible sign of the restart: a one-line prompt the resumed session answers."""
    sleep(2.0)
    if core.pane_prompt_empty(ops.read(pane)) and ops.send_text(pane, notice):
        sleep(0.5)
        ops.send_enter(pane)


def restart_when_ready(pane, ops, timeout=300, exit_timeout=60, start_timeout=90, poll=1.0, notice=NOTICE,
                       sleep=time.sleep, now=time.monotonic):
    ok, detail = plan(pane, ops)
    if not ok:
        return detail
    w = core.wait_ready(pane, ops, timeout=timeout, poll=poll, sleep=sleep, now=now)
    if w != "ready":
        return w
    if not core.pane_prompt_empty(ops.read(pane)):
        return "user-typing"
    if not (ops.send_text(pane, "/exit") and (sleep(0.5) or True) and ops.send_enter(pane)):
        return "send-failed"
    deadline = now() + exit_timeout
    while now() < deadline:
        info = ops.pane_info(pane)
        if info is None:
            return "pane-gone"
        if info.get("agent") != "claude":
            break
        sleep(poll)
    else:
        return "exit-timeout"
    sleep(1.0)
    if not ops.run_command(pane, "printf '%%s\\n' %s && %s" % (shlex.quote(BANNER), detail["command"])):
        return "relaunch-failed"
    deadline = now() + start_timeout
    while now() < deadline:
        info = ops.pane_info(pane) or {}
        if info.get("agent") == "claude" and info.get("agent_status") in core.READY_STATES:
            if notice:
                _announce(pane, ops, notice, sleep)
            return "restarted"
        sleep(poll)
    return "resume-timeout"
