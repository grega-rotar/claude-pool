# Commands

Anything that isn't a `--pool-*` command goes to `claude` unchanged.

## Look

| Command | What it does |
|---|---|
| `claude-pool --pool-status` | Every account with its usage and when it resets, numbered in the order the pool uses them (1 = next), with its weekly pace, learned `r` and 5-hour windows left (see [How it works](how-it-works.md#weekly-pace)) |
| `claude-pool --pool-same <a> <b>..` | These accounts are one Anthropic account (a token and the same login in Orca): they share their usage numbers from now on. With no names, lists the groups. `--pool-status` suggests it when two accounts' 5-hour windows reset at the same second |
| `claude-pool --pool-capacity <r>` | What 1, 2, 3, 4, 5, 6, 10 and 100 identical accounts would hold if one full 5-hour window took `r`% of a week. Hypothetical: the pool's own estimates are in `--pool-status` |
| `claude-pool --pool-watch [minutes]` | The same table, checked again every 5 minutes (or the minutes given) until Ctrl+C |
| `claude-pool --pool-refresh [name..]` | Check the usage of every account (or the named ones) now |
| `claude-pool --pool-log [N]` | The last N events (default 20): runs, sessions, switches, errors |

## Set up

| Command | What it does |
|---|---|
| `claude-pool --pool-add <name>` | Add an account and paste its token, or pipe it in: `echo "$TOKEN" \| claude-pool --pool-add work` |
| `claude-pool --pool-off <name>` | Take an account out (renames its file to `.off`) |
| `claude-pool --pool-on <name>` | Put it back |
| `claude-pool --pool-check` | Check the setup: claude, folders, permissions, every token |

## In a session

| Command | What it does |
|---|---|
| `/pool:switch [name]` | Typed in the session: move it to the named account, or the best free one ([more](how-it-works.md#switching-by-hand)) |
| `! claude-pool --pool-switch [name]` | The same, from the session's shell |
| `/pool:status` | The `--pool-status` table in the session, in colour, with its own account marked "this session" |
| `/pool:log [N]` | The last N events (default 20), as `--pool-log` |

## Other

| Command | What it does |
|---|---|
| `claude-pool --pool-version` | The version |
| `claude-pool --pool-help` | All commands and settings, and the folders in use right now |
