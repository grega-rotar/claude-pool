# Changelog

## 0.11.1

- **Status line on the right, in colour:** the pool line sits on the right side of the terminal. Usage (5-hour, week and the next account's) is green while low, yellow from 70% and red from `CLAUDE_POOL_SWITCH_AT` (90%). When the terminal width can't be found, the line stays on the left.

## 0.11.0

- **Status line:** every interactive session shows `pool: <account> · <5-hour %> · resets <time> · next: <account>` under the prompt. The % turns yellow from 90%, and a red `⚠ no next account until <time>` appears when no other account is free. It uses the live numbers Claude Code gives its status line and saves them for the pool. Any status line you had, Orca's included, runs first and stays. `CLAUDE_POOL_BANNER=off` turns it off.

## 0.10.0

- **Orca's accounts join the pool:** every Claude account logged in to [Orca](https://github.com/stablyai/orca) is an account named `orca-<name>`, with no `setup-token` step. claude runs in that account's Orca folder, and claude keeps the login fresh there. They're used in interactive sessions and `--pool-*` commands. Print mode leaves them out unless `CLAUDE_POOL_ORCA=on`, so scripts and services keep to their own accounts.
- **`.dir` accounts:** a `<name>.dir` file names any logged-in claude config folder.
- **Your own login first:** in an Orca pane, the pane's account is used first while it has room.
- **Safer switches:** before a session moves on, the next account's usage is checked again unless it was checked in the last minute, and only accounts that can see the conversation are picked.

## 0.9.0

- **Interactive sessions switch accounts:** when a session hits its usage limit, or its token is refused, claude-pool resumes the same conversation on the next free account and tells it to continue. Your flags carry over, and the restart happens in the session's own folder (its worktree with `-w`). It works through a `StopFailure` hook passed with `--settings`, merged into your own `--settings` if you pass one. `CLAUDE_POOL_SWITCH=off` turns it off, and `CLAUDE_POOL_RESUME_PROMPT` changes the nudge.
- Commands (`claude mcp`...), `--version`, `--bg`, `--bare` and `--safe-mode` go to claude unchanged.
- On Windows, switching needs Git Bash, which Claude Code runs hooks in. Without it the session runs on one account and says so.
- New events: `session`, `switch_failed` (a refused token with no other account, alerted).
- README: install for macOS, Linux, WSL and Windows; use it as `claude`; update and uninstall.

## 0.8.2

- **Readable status table:** `--pool-status` and `--pool-watch` show usage as `67%`, resets in local time with how long is left (`20:20 (in 2h55m)`), and a status column (`next`, `full until 20:20`, `2 running`, `broken: <reason>`). Column widths fit the content, and a broken token's reason is cut to one line; the full error stays in `--pool-check` and `--pool-log`.

## 0.8.1

- **Refused tokens:** a token claude refuses with a 401/403 (for example "Your organization has disabled Claude subscription access") counts as broken, at a check and in a run. It's skipped until rechecked, and a run refused that way is rerun on the next account. Before, a check took it for a CLI change and kept picking it.

## 0.8.0

- **Account in stream-json:** each run starts with a `pool_account` system line naming the account, so a caller reading the stream knows which account it ran on.

## 0.7.1

- **Usage without accounts:** with no accounts set up, `--pool-status`, `--pool-refresh` and `--pool-watch` show the normal claude login as a row named `login`, with its usage.

## 0.7.0

- **Live usage:** `--pool-watch [minutes]` checks every account and redraws the table every 5 minutes (or the minutes given, at least 1) until Ctrl+C.

## 0.6.0

- **.env support:** settings and accounts can live in a `.env` file, the nearest one from the current folder up or `CLAUDE_POOL_ENV_FILE`. Only `CLAUDE_POOL_*` lines are read; a variable that is already set wins.
- **Accounts in variables:** `CLAUDE_POOL_TOKEN_<NAME>` and `CLAUDE_POOL_KEY_<NAME>` add accounts without files. They are taken out of the environment claude and alert commands run in.

## 0.5.0

- **Safe reruns:** a run cut off by a limit is rerun only if it used no tools yet, so nothing happens twice. `CLAUDE_POOL_RERUN=always|never` changes this.
- **Event log:** runs, switches, cut-offs and errors go to `events.log`, including the raw output of every failed run. `--pool-log` shows them.
- **Alerts:** `CLAUDE_POOL_ALERT` runs a command when every account is full, a token is refused, the CLI stops reporting usage, or a cut-off run isn't rerun. The same alert goes out at most once an hour.
- **Parallel runs:** past 75% of its 5-hour window (`CLAUDE_POOL_SPREAD_AT`), an account that's already busy gets no second run while another has room.
- **Faster checks:** a check times out after 15 s. Other callers wait at most 5 s for it, then go on with the saved numbers. A network error no longer marks a token as broken.
- **New commands:** `--pool-help`, `--pool-check`, `--pool-add`, `--pool-off`, `--pool-on`, `--pool-log`.
- **Warnings:** a token file that every user can read gets a warning. A user who can't read the account folder gets a clear message instead of a traceback.
- **Tests:** CI on Linux, macOS and Windows with Python 3.8-3.13, plus a test with two real users sharing one pool.

## 0.4.1

- Several users on one server: files in a shared folder are group-writable. A token file the user can't read is skipped.

## 0.4.0

- Windows support.

## 0.3.0

- Accounts whose numbers are older than 5 minutes are checked first, so use by other apps shows up.

## 0.2.0

- Drain: the account whose weekly window resets soonest is used first.

## 0.1.0

- First version.
