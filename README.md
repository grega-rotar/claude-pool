# claude-pool

Run Claude Code on whichever of your accounts has usage left. When one account hits its usage limit, the session moves to the next one and keeps going. You don't have to do anything.

```sh
claude-pool                       # an interactive session, like `claude`
claude-pool -p "fix the tests"    # print mode, like `claude -p`
```

It takes the same arguments as `claude` and prints the same output. With no accounts set up it just runs `claude` on your normal login, so it's safe to install first and set up later.

> **Before you use it:** using several accounts to get around usage limits breaks [Anthropic's terms](https://www.anthropic.com/legal/consumer-terms), and so does sharing one account between people. For a team, the supported route is one seat per person (Team or Enterprise plan) or an API key with org billing. claude-pool works with API keys too.

## What it does

- **Interactive sessions switch on their own.** When the account hits its limit, claude-pool stops claude and resumes the same conversation on the next free account. Then it sends *"Continue where you left off"*. The history and every tool result carry over, so nothing runs twice. A refused token (revoked, org access off) is handled the same way.
- **Print mode reruns.** A `-p` run that a limit cuts off is rerun on the next account, but only if it hadn't used tools yet.
- **It picks well.** It uses the account whose weekly window resets soonest first, since leftover usage is lost at the reset. It moves on at 90% of the 5-hour limit.
- **It sees all use.** Every account's usage is rechecked every 5 minutes with a tiny Haiku call (about $0.0001), so use from other machines and apps counts too.

## Install

You need **Python 3.8+** and **[Claude Code](https://code.claude.com/docs/en/setup)** (`claude --version` must work).

### macOS

```sh
mkdir -p ~/.local/bin
curl -fsSL https://github.com/grega-rotar/claude-pool/releases/latest/download/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
claude-pool --pool-version
```

macOS asks you to install the Command Line Tools the first time `python3` runs. Say yes.

### Linux and WSL

```sh
mkdir -p ~/.local/bin
curl -fsSL https://github.com/grega-rotar/claude-pool/releases/latest/download/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
claude-pool --pool-version
```

If `claude-pool` isn't found, add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` and open a new terminal. On WSL, install Claude Code inside WSL, not on the Windows side.

### Windows (PowerShell)

```powershell
py -m pip install --user https://github.com/grega-rotar/claude-pool/archive/refs/heads/main.zip
claude-pool --pool-version
```

If `claude-pool` isn't found, pip printed the folder it put it in (`...\Python3xx\Scripts`): add that folder to your `PATH`. Interactive switching needs **Git for Windows**, because Claude Code runs hooks in Git Bash. Without it, sessions still run, but they stay on their first account.

### With pipx (any OS)

```sh
pipx install git+https://github.com/grega-rotar/claude-pool
```

## Set up accounts

For each account, run `claude setup-token`, log in as that account in the browser it opens, and copy the token. Then:

```sh
claude-pool --pool-add work       # paste the token; it's checked straight away
claude-pool --pool-add personal
claude-pool --pool-check          # every line should say OK
```

The name is up to you. Or put the tokens in a `.env` instead (claude-pool reads the nearest one, and only its `CLAUDE_POOL_*` lines):

```sh
CLAUDE_POOL_TOKEN_WORK=sk-ant-oat01-...
CLAUDE_POOL_KEY_BACKUP=sk-ant-api03-...   # an API key works too
```

Tokens from `claude setup-token` last a year.

## Use it as `claude`

To make it your everyday `claude`:

```sh
alias claude=claude-pool                      # add to ~/.zshrc or ~/.bashrc
```

```powershell
Set-Alias claude claude-pool                  # add to $PROFILE
```

claude-pool still finds the real `claude` on your `PATH`, so the alias is safe.

## Commands

| Command | What it does |
|---|---|
| `claude-pool --pool-status` | Every account with its usage, when it resets, and which one runs next |
| `claude-pool --pool-watch [minutes]` | The same table, checked again every 5 minutes (or the minutes given) until Ctrl+C |
| `claude-pool --pool-refresh [name..]` | Check the usage of every account (or the named ones) now |
| `claude-pool --pool-check` | Check the setup: claude, folders, permissions, every token |
| `claude-pool --pool-add <name>` | Add an account and paste its token, or pipe it in: `echo "$TOKEN" \| claude-pool --pool-add work` |
| `claude-pool --pool-off <name>` | Take an account out (renames its file to `.off`) |
| `claude-pool --pool-on <name>` | Put it back |
| `claude-pool --pool-log [N]` | The last N events (default 20): sessions, switches, errors |
| `claude-pool --pool-version` | The version |
| `claude-pool --pool-help` | All commands and settings |

Anything else goes to `claude` unchanged.

## Update and uninstall

Update: run the install line again. Your accounts, usage and settings stay. (With pip, add `--upgrade`.)

Uninstall: delete `~/.local/bin/claude-pool` (or `pip uninstall claude-pool`). Your accounts are in `~/.config/claude-pool/` (on Windows, `%APPDATA%\claude-pool\`).

## Good to know

- **On a switch** the session restarts on the new account in about two seconds. Anything you had typed or queued in the prompt box at that moment is lost. Background tasks the session started are stopped.
- **The first turn after a switch costs more,** since the new account has no prompt cache yet.
- **When every account is full,** the session waits for the first reset, as `claude` does on its own.
- **When claude exits** it prints `claude --resume <id>`. Run `claude-pool --resume <id>` to stay in the pool.
- **Switching needs hooks.** It does nothing with `--bare`, `--safe-mode`, `disableAllHooks`, or a managed policy that allows only managed hooks. Those sessions run normally on one account.
- **To stay on one account,** set `CLAUDE_POOL_ACCOUNT=work` (or `CLAUDE_POOL_SWITCH=off` to keep picking but never switch).

## More

- [How it works](docs/how-it-works.md): picking, switching, reruns, the log
- [Settings](docs/settings.md): every option
- [Files and doing it by hand](docs/manual.md)
- [Servers and shared pools](docs/server.md)
- [Changelog](CHANGELOG.md)

MIT licensed.
