# Settings

All optional. Set them as environment variables or as lines in a `.env`.

| Variable | Default | |
|---|---|---|
| `CLAUDE_POOL_TOKEN_<NAME>` | | Account token without a file (`CLAUDE_POOL_KEY_<NAME>` for an API key) |
| `CLAUDE_POOL_DIR` | `~/.config/claude-pool` | Account folder |
| `CLAUDE_POOL_STATE` | `~/.local/state/claude-pool/usage.json` | Usage file |
| `CLAUDE_POOL_ENV_FILE` | nearest `.env` going up | `.env` to read |
| `CLAUDE_POOL_BINARY` | `claude` | The real claude |
| `CLAUDE_POOL_ACCOUNT` | | Always use this account |
| `CLAUDE_POOL_STRATEGY` | `drain` | `balance` = least used first |
| `CLAUDE_POOL_SWITCH_AT` | `90` | 5-hour % to move on |
| `CLAUDE_POOL_SPREAD_AT` | `75` | 5-hour % after which a busy account gets no second run |
| `CLAUDE_POOL_FRESH` | `300` | Seconds between usage checks, `0` = off |
| `CLAUDE_POOL_LIMIT` | `100` | % that counts as full |
| `CLAUDE_POOL_RERUN` | `safe` | After a limit cut-off: `safe` (only if no tools ran), `always`, `never` |
| `CLAUDE_POOL_SWITCH` | `on` | `off` = an interactive session stays on its first account |
| `CLAUDE_POOL_ORCA` | `auto` | Orca's logins: `auto` = in interactive sessions and `--pool-*` commands, `on` = in print mode too, `off`, or the path of Orca's data folder (as `on`) |
| `CLAUDE_POOL_RESUME_PROMPT` | Continue where you left off. … | What a switched session is told first; empty = nothing, you type |
| `CLAUDE_POOL_ALERT` | | Command run on problems, gets `$CLAUDE_POOL_MESSAGE` |
| `CLAUDE_POOL_LOG` | next to the usage file | Event log |

## Where to set them

Set it where the caller runs:

- a shell: `export CLAUDE_POOL_SWITCH_AT=80`, or add it to `~/.bashrc`
- one project: a `CLAUDE_POOL_SWITCH_AT=80` line in its `.env`
- a systemd service: `Environment=CLAUDE_POOL_SWITCH_AT=80`, or a line in its `EnvironmentFile`

## .env

claude-pool reads the first `.env` it finds, from the current folder up. Only `CLAUDE_POOL_*` lines are used, so an app's other secrets never reach claude.

- `CLAUDE_POOL_TOKEN_WORK_2` is account `work-2`.
- A real environment variable wins over the `.env`.
- A `.token` file wins over a variable with the same name.

## Alerts

`CLAUDE_POOL_ALERT` runs when every account is full, a token is refused, a session can't move on from a refused token, claude stops reporting usage, or a cut-off run isn't rerun. Each problem alerts at most once an hour.

```sh
CLAUDE_POOL_ALERT='curl -s -d "{\"text\": \"$CLAUDE_POOL_MESSAGE\"}" https://hooks.slack.com/services/...'
```
