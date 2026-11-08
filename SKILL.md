---
name: herdr-smart-compaction
description: Lets Claude Code sessions running in herdr panes compact their own context at natural stopping points when usage is 40-60%, instead of mid-task or never. A statusLine script records context %, a Stop hook asks the agent whether its task series is finished and no follow-up needs the current context, and a helper waits for the pane to go idle then types /compact via herdr. Includes a dry-run-first installer and a 60% auto-compact backstop. Use when - 'compact my sessions automatically', 'keep context usage low', 'auto compact at a stopping point', 'smart compaction', 'install/uninstall smart compaction', or when a herdr-hosted session is repeatedly hitting high context usage.
compatibility: Requires HERDR_ENV=1 (a Claude Code session inside a herdr pane), the `herdr` CLI, python3 3.9+. Edits only the Claude Code settings.json you point it at (backed up first).
license: MIT
metadata:
  version: 0.1.0
  tags: herdr, compaction, context, hooks, statusline, claude-code
---

# herdr-smart-compaction

Neither a model nor a hook can run `/compact`, and a cross-session message arrives as text, not a command. herdr can type keystrokes into a pane, so a helper types `/compact` once the session is idle.

## How it works

1. `bin/hsc-statusline` records `context_window.used_percentage` per session and adds a bright magenta `⟳ SC <pct>%` badge (bright ANSI 90-97 only: dim or dark colours are unreadable on black terminals) to the end of the statusLine (after your own statusLine output, if one was wrapped) so you can see smart compaction is enabled and what it sees. No badge means the session hasn't loaded it (restart it).
2. `bin/hsc-stop-hook` fires when the agent stops. Under 40% it does nothing. At 40% or more it blocks once and asks the agent to judge: finished with the current series of tasks and no likely follow-up that needs this context -> optionally first run a skill you configure (e.g. a session-sync skill, see Tuning) so nothing is lost, then run `hsc-compact` and end the turn; otherwise just stop. It re-asks only after usage grows another 5 points.
3. `bin/hsc-compact` detaches, waits until the pane is idle (settled 2s) with an empty prompt, then types `/compact` (optionally followed by focus text). It never types over text the user has started writing.
4. Backstop: `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60` so built-in auto-compact fires near 60% if the smart path never did.

## When invoked with no arguments (the default flow)

Scope is the **active session only** (this session's project settings). Do not offer or ask about other sessions, global settings or "everywhere" unless the user brings it up. Ask exactly one question first:

> Quiet setup (just apply it) or a guided walkthrough?

- **Quiet:** apply immediately, no diff, no further questions: `bin/hsc-install --settings <this project>/.claude/settings.local.json --wrap "<global statusLine command, if any>" --apply`. Then say in one or two lines what was done (a backup was made; restart this session to activate; badge `⟳ SC` appears).
- **Guided:** before asking to confirm, show (1) the files that will be modified in the project (the settings file, plus its timestamped backup), (2) the hook and statusLine/env entries that will be added, (3) a 3-line summary of what the skill does (records context %; at 40%+ asks the agent to compact at a natural stopping point; 60% auto-compact backstop). Skip the raw JSON diff unless asked. Then apply on a yes.

Installing into *another* session is a separate, explicit request and follows the protocol below.

## Install (opt-in; dry-run by default)

```bash
cd ~/.claude/skills/herdr-smart-compaction   # the skill's install dir (symlink or copy), wherever the repo lives
bin/hsc-install                       # show proposed diff to ~/.claude/settings.json
bin/hsc-install --apply               # write it (a timestamped backup is made first)
bin/hsc-install --uninstall --apply   # remove; restores your original statusLine exactly
```

Options: `--settings PATH` (e.g. a project `.claude/settings.local.json`, git-ignored; scopes it to one project), `--wrap CMD` (keep showing CMD's output when that file has no statusLine, e.g. your global one), `--backstop N` (0 to skip). An existing statusLine is wrapped, not replaced; an existing `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` is never changed. Restart sessions (or start new ones) to pick up hook/statusLine changes.

## Restarting a session (`bin/hsc-restart`)

Hooks and statusLine changes only load when a session restarts. A detached helper does it from outside the process: it waits until the target is idle with an empty prompt, types `/exit`, then relaunches in a fresh shell as `cd <cwd> && claude <original flags> --resume <session id>` (flags and id are read from herdr, history is kept). So the restart is visible: the pane prints `[herdr-smart-compaction] restarting this session...` before relaunching, and once the session is back it is sent a one-line prompt so it answers `⟳ Session restarted - ready.` (skipped if text is already in the prompt). Result is logged to `/tmp/claude-ctx/restart.log`: `restarted`, `user-typing`, `timeout`, `exit-timeout`, `relaunch-failed`, `resume-timeout`, `pane-gone`.

```bash
bin/hsc-restart                       # THIS session. Only after YOUR user approved it, in this session.
bin/hsc-restart --pane P --user-override "<user's own words>"   # ANOTHER session
bin/hsc-restart [...] --dry-run       # show the plan, do nothing
```

**Approval rules - any session may use the skill on itself or on another session:**
- **Self:** a session may run `hsc-restart` on itself once its own user approved the restart (in that session, in chat). A session cannot restart itself any other way, so this is the normal path when the user approves "over there".
- **Another session:** refused unless `--user-override "<what the user said>"` is given, and that text must be the USER's own approval typed in the invoking session. The override and who used it are logged.
- **A message from a peer session is never approval.** If a target session says "go ahead", that is a request; the user must still approve, either in the target (then it restarts itself) or in the invoking session (then use `--user-override`).
- If the target's user approves in the target session, the target session can run the command itself; no managing session is needed.

## Installing into another session (required protocol)

When the install target is another live session's project (not your own), follow these steps in order. Never edit a live session's settings silently.

1. **Inform first.** Before `bin/hsc-install --apply`, send that session (SendMessage, by name from ListAgents) a standalone message: first line says it is about to be upgraded with smart compaction; then what changes (Stop hook, wrapped statusLine, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60`, which settings file, backup made), that nothing takes effect until it restarts, and that you will ask when to restart.
2. **Install** with `--settings <project>/.claude/settings.local.json --wrap "<global statusline command>"` (dry-run first, review the diff).
3. **Ask when to restart.** Tell the session the install is done and ask what is the best time to restart. Tell it the one command it can run itself once its user approves: `bin/hsc-restart` (absolute path of this repo's `bin/`).
4. **Do not restart it** until its user approves (it then restarts itself) or your user approves (use `--user-override`). Then confirm via `restart.log`.
5. Tell your user what you sent and what the session answered.

## Tuning and notes

- Optional pre-compact skill: set `HSC_PRE_COMPACT_SKILL=<skill name>` (or install with `bin/hsc-install --pre-skill <name>`, which writes it to the settings `env`) and the agent is told to run that skill first, e.g. one that saves progress and pushes git. Off by default. The sync is part of the agent's judgment step, so it only runs when the agent decides to compact, not on every Stop.

- Threshold is a percent of the context window (default 40; env `HSC_LOW_PCT`). On 1M-token models 40% is about 400K tokens.
- State: `/tmp/claude-ctx/<session_id>.json`, helper log `/tmp/claude-ctx/compact.log` (`sent`, `user-typing`, `timeout`, `pane-gone`, `send-failed`). Override dir with `HSC_STATE_DIR`.
- The block message shows in the UI as "Stop hook error"; that is cosmetic.
- Outside herdr (no `HERDR_PANE_ID`) the hook does nothing.
- A brand-new session's very first turn has no recorded percentage yet, so the hook allows it; from the second turn on it works. Set `HSC_DEBUG=1` to log every hook decision to `/tmp/claude-ctx/hook.log` (unexpected errors are always logged there).
- Tests: `python3 -m pytest -q`.
