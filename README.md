# claude-pool

A drop-in wrapper for the [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI that runs each call on whichever of your accounts has the most usage left.

```sh
claude-pool -p "summarize this repo" --output-format json   # same args, same output as claude
```

- **Same interface.** It takes the same arguments as `claude` and prints the same output (`text`, `json`, `stream-json`). Any script or app that runs `claude -p` can switch by changing one word, or by pointing its "claude binary" setting at `claude-pool`.
- **Picks the account.** By default it drains first the usage that expires first (see [Picking an account](#picking-an-account)).
- **Reruns on limits.** In print mode (`-p`), a run cut off by a usage limit is rerun right away on the next free account.
- **Shared usage.** Usage numbers live in one file under a lock, so parallel callers and different programs all see the same numbers.
- **One file.** It's Python 3.8+ with the standard library only. With no accounts set up, it just runs `claude` with your normal login.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/grega-rotar/claude-pool/main/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
# or: pipx install git+https://github.com/grega-rotar/claude-pool
```

`claude` itself must be installed and on your `PATH`, or set `CLAUDE_POOL_BINARY`.

## Add accounts

Put one file per account in `~/.config/claude-pool/`:

| File | Contents |
|---|---|
| `<name>.token` | an OAuth token from `claude setup-token` (a Claude subscription) |
| `<name>.key` | an Anthropic API key |

```sh
mkdir -p ~/.config/claude-pool && chmod 700 ~/.config/claude-pool
claude setup-token            # log in as the account, copy the token
printf '%s' 'sk-ant-oat01-…' > ~/.config/claude-pool/work.token && chmod 600 ~/.config/claude-pool/work.token
```

To take an account out, rename it to `<name>.token.off`. The folder is reread on every run.

```sh
claude-pool --pool-status
account              kind     5h %   7d %  5h reset              7d reset              runs
personal             token    22.0   23.0  2026-10-18 21:30 UTC  2026-10-22 11:00 UTC  14 <- next
work                 token    81.0   64.0  2026-10-18 19:00 UTC  2026-10-21 08:00 UTC  9
```

## How it works

`claude -p --output-format stream-json --verbose` emits a `rate_limit_event` with the account's 5-hour and 7-day utilization. In print mode, claude-pool always runs `claude` that way, records the numbers, and turns the output back into the format you asked for:

- **`text`:** the result text.
- **`json`:** the result object. With `--verbose`, the array of all events, as `claude` prints it.
- **`stream-json`:** every line as it arrives. A `{"type":"system","subtype":"pool_switch"}` line marks a rerun.

The JSON result gets two extra fields:

- **`pool_account`:** the account that ran it.
- **`pool_switched`:** reruns, for example `["a -> b"]`.

An account counts as full when a window is at 100% (`CLAUDE_POOL_LIMIT`), or when its last run was rejected, until that window resets. When every account is full, claude-pool doesn't start `claude`. It prints an `error_pool_exhausted` result and exits with code **75** (`EX_TEMPFAIL`), so callers can wait until the reset.

### Picking an account

Usage you don't spend before a window resets is lost. So the default strategy, **`drain`**, gets the most out of your accounts:

1. **Soonest reset first.** It takes the account whose 7-day window resets soonest, because that account's leftover room expires first. If two reset at the same time, it takes the more used one.
2. **Unused accounts last.** Accounts with no live window (never used, or reset since) wait until the others are full. That way a new window doesn't start before it's needed.
3. **The 5-hour margin.** Once an account passes `CLAUDE_POOL_SWITCH_AT` (90%) of its 5-hour window, it's used only when every free account has passed it too. This makes a cut-off in the middle of a run less likely.

`CLAUDE_POOL_STRATEGY=balance` uses the lowest 7-day usage instead, which spreads the load evenly.

A piped prompt (`echo hi | claude-pool -p`) is read once and sent again on a rerun. If a caller leaves stdin open without writing to it, claude-pool stops waiting after 3 seconds, as `claude` does. A stream-json input conversation (`--input-format stream-json`) can't be replayed, so it's never rerun.

Interactive sessions and subcommands (`claude-pool`, `claude-pool mcp list`) pick the account and then hand straight over to `claude`.

**Reruns repeat side effects.** A rerun starts the task again from the beginning, so any tool calls the cut-off run made (file edits, commands) happen a second time. Use `CLAUDE_POOL_ACCOUNT` to pin one account when that isn't safe.

## Settings

| Variable | Default | |
|---|---|---|
| `CLAUDE_POOL_DIR` | `~/.config/claude-pool` | account files |
| `CLAUDE_POOL_STATE` | `$XDG_STATE_HOME/claude-pool/usage.json` | shared usage file |
| `CLAUDE_POOL_BINARY` | `claude` | the real CLI |
| `CLAUDE_POOL_ACCOUNT` | | always use this account, no rotation |
| `CLAUDE_POOL_LIMIT` | `100` | percent of a window that counts as full |
| `CLAUDE_POOL_STRATEGY` | `drain` | `drain` (soonest weekly reset first) or `balance` (lowest usage first) |
| `CLAUDE_POOL_SWITCH_AT` | `90` | drain: 5-hour percent after which another account is preferred |

## Using it from code

```python
subprocess.run(["claude-pool", "-p", prompt, "--output-format", "json"], capture_output=True, text=True)
```

```php
// Laravel: config('services.claude.binary') → CLAUDE_BINARY=claude-pool
Process::run([config('services.claude.binary'), '-p', $prompt, '--output-format', 'json']);
```

## Terms

Use this only within the terms of your plan. Anthropic's Consumer Terms and Usage Policy don't allow using several accounts to get around usage limits. The intended uses are separate paid seats, each used by its own team or workload, and API keys. You're responsible for how you use it.

## Tests

```sh
python3 -m unittest discover tests
```

The tests run against a fake `claude`, so they need no network or account.

## License

MIT
