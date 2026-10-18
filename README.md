# claude-pool

Runs `claude` on whichever of your accounts has usage left. It takes the same arguments as `claude` and prints the same output.

```sh
claude-pool -p "hi" --output-format json
```

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/grega-rotar/claude-pool/main/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
```

## Accounts

Put one file per account in `~/.config/claude-pool/`:

- `<name>.token`: a token from `claude setup-token`
- `<name>.key`: an API key

Rename a file to `.off` to disable it. With no files, `claude` runs with your normal login.

```sh
claude-pool --pool-status
```

## How it picks

1. The account whose weekly window resets soonest (its unused room expires first).
2. Past 90% of a 5-hour window, the next account.
3. Unused accounts last.

If a limit cuts off a `-p` run, it reruns on the next account. If every account is full, it exits with code 75. JSON output gets `pool_account` and `pool_switched`.

## Settings

| Variable | Default |
|---|---|
| `CLAUDE_POOL_DIR` | `~/.config/claude-pool` |
| `CLAUDE_POOL_STATE` | `~/.local/state/claude-pool/usage.json` |
| `CLAUDE_POOL_BINARY` | `claude` |
| `CLAUDE_POOL_ACCOUNT` | pin one account |
| `CLAUDE_POOL_STRATEGY` | `drain` (or `balance`: lowest usage first) |
| `CLAUDE_POOL_SWITCH_AT` | `90` |
| `CLAUDE_POOL_LIMIT` | `100` |

## Notes

- A rerun starts the task over, so its tool calls run again.
- Using several accounts to get around usage limits breaks Anthropic's terms.

MIT
