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
claude-pool --pool-status    # usage per account
claude-pool --pool-refresh   # check every account now
```

## How it knows usage

Every Claude run reports the account's real usage, and claude-pool saves it. If an account's numbers are older than 5 minutes (other apps may have used it), it checks that account first with a tiny Haiku call. An account whose token fails is skipped.

## How it picks

It uses up the account whose week ends soonest, because leftover usage is lost at the reset. When that account's 5-hour window is at 90%, it moves to the next one. Fresh accounts are saved for last.

If a run hits a limit, it starts over on the next account. If every account is full, it exits with code 75.

## Cost

- **Time:** about 60 ms per call, plus 2-3 s when it checks accounts (all at once, at most every 5 minutes).
- **Usage:** each check is one tiny Haiku call (about $0.0001 per account).
- **Reruns:** a run cut off by a limit is wasted, since it starts again from scratch.

## Settings

| Variable | Default |
|---|---|
| `CLAUDE_POOL_DIR` | `~/.config/claude-pool` |
| `CLAUDE_POOL_STATE` | `~/.local/state/claude-pool/usage.json` |
| `CLAUDE_POOL_BINARY` | `claude` |
| `CLAUDE_POOL_ACCOUNT` | pin one account |
| `CLAUDE_POOL_STRATEGY` | `drain` (or `balance`: lowest usage first) |
| `CLAUDE_POOL_SWITCH_AT` | `90` |
| `CLAUDE_POOL_FRESH` | `300` seconds (`0` turns checks off) |
| `CLAUDE_POOL_LIMIT` | `100` |

## Notes

- Using several accounts to get around usage limits breaks Anthropic's terms.

MIT
