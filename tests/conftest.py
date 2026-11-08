import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeOps:
    """Scripted herdr: statuses is consumed one per status() call; last value repeats."""
    def __init__(self, statuses, screen="❯ \n", send_ok=True):
        self.statuses = list(statuses)
        self.screen = screen
        self.send_ok = send_ok
        self.sent = []

    def status(self, pane):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def read(self, pane):
        return self.screen

    def send_text(self, pane, text):
        self.sent.append(("text", text))
        return self.send_ok

    def send_enter(self, pane):
        self.sent.append(("enter",))
        return self.send_ok


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeRestartOps(FakeOps):
    """Models a pane: claude running -> /exit -> shell -> relaunch -> claude ready."""
    def __init__(self, argv=("claude", "--settings", "/s.json"), sid="sess-1", cwd="/proj dir",
                 exits=True, relaunch_ok=True, screen="❯ \n", starts=True):
        super().__init__(["idle"], screen=screen)
        self.argv, self.sid, self.cwd = list(argv), sid, cwd
        self.exits, self.relaunch_ok, self.starts = exits, relaunch_ok, starts
        self.agent, self.ran = "claude", []
        self.has_session = True

    def status(self, pane):
        return "idle" if self.agent == "claude" else None

    def pane_info(self, pane):
        if self.agent == "gone":
            return None
        return {"agent": self.agent, "agent_status": "idle", "cwd": self.cwd, "foreground_cwd": self.cwd,
                "agent_session": {"value": self.sid} if self.has_session else None}

    def process_info(self, pane):
        procs = [{"argv": ["caffeinate", "-i"], "cwd": self.cwd}] if self.agent == "claude" else []
        if self.agent == "claude":
            procs.append({"argv": self.argv, "cwd": self.cwd})
        return {"foreground_processes": procs}

    def send_enter(self, pane):
        self.sent.append(("enter",))
        if self.exits and ("text", "/exit") in self.sent:
            self.agent = "shell"
        return True

    def run_command(self, pane, command):
        self.ran.append(command)
        if self.relaunch_ok and self.starts:
            self.agent = "claude"
        return self.relaunch_ok
