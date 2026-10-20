# How it works

## Picking an account

- **Usage:** each print-mode run reports the account's usage and claude-pool saves it. Numbers older than 5 minutes are rechecked with a tiny Haiku call (~$0.0001), so other apps' use counts too. Print mode waits for that check before it picks. An interactive session doesn't: it starts on the saved numbers and the check runs in the background (only with no saved numbers yet does it check first).
- **Order:** it uses the account whose week resets soonest first, since leftover usage is lost at the reset. At 90% of the 5-hour limit it moves on. When the weeks tie, the 5-hour window decides the same way. Unused accounts go last.
- **Your own login first:** when the caller's claude folder is one of the pool's accounts (an Orca pane on its account), that account goes first while it's under 90%.
- **Busy accounts:** past 75% of its 5-hour window, an account that's already running something gets no second run while another has room.

## Interactive sessions

claude-pool starts `claude` with one extra hook, a `StopFailure` hook passed through `--settings`. If you pass your own `--settings`, the hook is merged into it. Your own hooks and settings files are untouched.

When a turn ends on an error, the hook hands the error to claude-pool:

1. **Usage limit** (`rate_limit` with "hit your … limit", or the account is full when rechecked): the account is marked full until its reset.
2. **Refused** (token revoked, org access off, account on hold, no credit): the account is marked broken and skipped until it's rechecked.
3. **Anything else**, such as a short rate limit or an overloaded API: nothing happens. claude retries on its own.

For 1 and 2, claude-pool picks the next free account that can see the same conversation. With Orca that's every account; a `.dir` account counts only if its folder shares `projects/`. That account's usage is checked again first, unless it was checked in the last minute, so a session never moves to an account that is full too. This matters when one account is in the pool twice, as a token and in Orca. Then claude-pool stops claude (SIGTERM, so claude saves the conversation and restores the terminal). Then it starts it again on the new account:

```
claude <your flags> --resume <session id> "Continue where you left off. Your last turn was cut off by a usage limit."
```

- **Your flags carry over:** `--model`, `--add-dir`, `--mcp-config`, `--settings` and so on, since a resume doesn't keep them. The first prompt and the flags that chose the session (`-c`, `--resume`, `--session-id`, `--fork-session`, `-w`) are left out, so nothing is asked twice and no new worktree is made. To tell these apart, claude-pool reads `claude --help` once per claude version.
- **Same folder:** the session restarts in its own folder, the worktree's when you used `-w`.
- **Nothing runs twice:** the transcript holds every tool call and result up to the limit, so the model sees what it already did.
- **No free account:** claude stays as it is and waits for the reset, and an `exhausted` event is logged.
- **It repeats:** a session can move through every account in turn.

Ctrl+C goes to claude, not claude-pool. Closing the terminal or a `kill` reaches claude too.

### What to expect on a switch

- The session restarts on the new account in about two seconds, and prints a line naming it.
- Anything you had typed or queued in the prompt box at that moment is lost. Background tasks the session started are stopped.
- The first turn after a switch costs more, since the new account has no prompt cache yet.
- When claude exits it prints `claude --resume <id>`. Run `claude-pool --resume <id>` to stay in the pool.
- Switching needs hooks. It does nothing under `--bare`, `--safe-mode`, `disableAllHooks`, or a managed policy that allows only managed hooks. Those sessions run normally on one account.
- To keep a session on one account, set `CLAUDE_POOL_ACCOUNT=work`. To keep picking but never switch, set `CLAUDE_POOL_SWITCH=off`.

### Switching by hand

Type `/pool:switch work` in the session to move it to `work`. Type `/pool:switch` with no name to move it to the account a limit would pick. Typing `/pool` brings it up in the command list.

- claude-pool loads the command as a small plugin (`--plugin-dir`) and catches it in a `UserPromptSubmit` hook, so the prompt never reaches the model and costs nothing. The answer shows up as the hook's message.
- The account must see the same conversation and have room. Its usage is rechecked first, unless it was checked in the last minute. A full, refused or unknown account is turned down with the reason, and the session stays where it is.
- The session restarts with `--resume <id>` as at a limit, but without the "Continue" nudge: it waits for you.
- From the session's shell, `! claude-pool --pool-switch [name]` does the same. So does asking Claude to run it. It needs one message sent in the session first, so it knows the session id.

### The status line

Each claude-pool session gets one line under the prompt, in the bottom-right corner:

```
pool: editors2 · 85% · resets 23:10 · week 40% · next: factory 12%
```

- **The account** the session runs on now.
- **Its 5-hour use** and when that window resets: green while low, yellow from 70%, red from 90% (`CLAUDE_POOL_SWITCH_AT`). The week shows up, in the same colours, when the account has a weekly window.
- **Next:** the account the session would move to at the limit, with its own 5-hour use. It's the same pick a switch would make, from the accounts that can see this conversation.
- **Red ⚠ no next account until 20:20** when every other account is full or broken. The time is when the first one frees up.

The numbers are the live ones Claude Code hands its status line after every reply, and claude-pool saves them, so `--pool-status` and other sessions see them too. Any status line you already had, Orca's included, still runs first, with the same input, and its output stays above the pool line. The width comes from claude's terminal; when it can't be found, the line sits on the left. `CLAUDE_POOL_BANNER=off` turns the line off; `NO_COLOR` leaves out the colors.

## Print mode (`-p`)

- **Limits:** a run cut off by a limit is rerun on the next account, but only if it hadn't used tools yet, so nothing happens twice (`CLAUDE_POOL_RERUN`).
- **Output:** JSON gets a `pool_account` field. stream-json starts each run with a `{"type": "system", "subtype": "pool_account", "account": ...}` line. Exit code `75` means every account is full.

## Commands and other flags

`claude mcp`, `claude update` and other commands, plus `--version`, `--help`, `--bg`, `--bare` and `--safe-mode`, go to claude unchanged on the picked account, without the hook.

## Log

Sessions, switches, refused tokens and errors go to `events.log`. See them with `claude-pool --pool-log`:

```
2026-10-19T16:36:43  session            account=a-factory resumed=False
2026-10-19T16:36:50  switch             account=a-factory to=b-orgoff session=c422b527-… reason=rate_limit
2026-10-19T16:36:53  token_broken       the token of b-orgoff was refused: Your organization has disabled …
2026-10-19T16:36:55  switch             account=b-orgoff to=c-login session=c422b527-… reason=oauth_org_not_allowed
```
