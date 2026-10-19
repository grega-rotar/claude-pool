# How it works

## Picking an account

- **Usage:** each print-mode run reports the account's usage and claude-pool saves it. Numbers older than 5 minutes are rechecked with a tiny Haiku call (~$0.0001), so other apps' use counts too.
- **Order:** it uses the account whose week resets soonest first, since leftover usage is lost at the reset. At 90% of the 5-hour limit it moves on. Unused accounts go last.
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
