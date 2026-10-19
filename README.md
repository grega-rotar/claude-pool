# claude-pool

You have more than one Claude account. claude-pool runs Claude Code on the one that still has usage left. When that one hits its limit, your session moves to the next account and keeps going on its own. You use it exactly like `claude`.

```sh
claude-pool                       # an interactive session, like `claude`
claude-pool -p "fix the tests"    # print mode, like `claude -p`
```

> **Before you use it:** using several accounts to get around usage limits breaks [Anthropic's terms](https://www.anthropic.com/legal/consumer-terms), and so does sharing one account between people. For a team, the supported route is one seat per person (Team or Enterprise plan) or an API key with org billing. claude-pool works with API keys too.

## What it does

- **Sessions switch on their own.** When the account hits its limit, claude-pool resumes the same conversation on the next free account and tells it to carry on. The history and every tool result come along, so nothing runs twice. A refused token is handled the same way.
- **Print mode reruns.** A `-p` run cut off by a limit is rerun on the next account, if it hadn't used tools yet.
- **It picks well.** The account whose week resets soonest goes first, since leftover usage is lost at the reset. It moves on at 90% of the 5-hour limit.
- **It's safe to install first.** With no accounts set up it just runs `claude` on your normal login.

## Install

You need **Python 3.8+** and **[Claude Code](https://code.claude.com/docs/en/setup)** (`claude --version` must work).

**macOS**

```sh
mkdir -p ~/.local/bin
curl -fsSL https://github.com/grega-rotar/claude-pool/releases/latest/download/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

The first time `python3` runs, macOS offers to install the Command Line Tools. Say yes.

**Linux and WSL**

```sh
mkdir -p ~/.local/bin
curl -fsSL https://github.com/grega-rotar/claude-pool/releases/latest/download/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
```

If `claude-pool` isn't found, add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` and open a new terminal. On WSL, install Claude Code inside WSL, not on the Windows side.

**Windows (PowerShell)**

```powershell
py -m pip install --user https://github.com/grega-rotar/claude-pool/archive/refs/heads/main.zip
```

If `claude-pool` isn't found, add the `Scripts` folder that pip names to your `PATH`. Sessions switch accounts only with **[Git for Windows](https://git-scm.com/download/win)** installed, because Claude Code runs hooks in Git Bash. Without it, a session stays on its first account.

**Any OS, with pipx:** `pipx install git+https://github.com/grega-rotar/claude-pool`

Check it: `claude-pool --pool-version`

## Start

1. **Add your accounts.** For each one, run `claude setup-token`, log in as that account, copy the token, then:

   ```sh
   claude-pool --pool-add work
   ```

   The name (`work`) is only a label.

2. **Check the setup:** `claude-pool --pool-check`. Every line should say OK.

3. **Use it** like `claude`. To make it your everyday `claude`:

   ```sh
   alias claude=claude-pool          # in ~/.zshrc or ~/.bashrc
   ```

   ```powershell
   Set-Alias claude claude-pool      # in $PROFILE
   ```

   claude-pool still finds the real `claude` on your `PATH`.

See your accounts and their usage: `claude-pool --pool-status`

## More

| Page | For |
|---|---|
| [How it works](docs/how-it-works.md) | picking an account, switching sessions, reruns, what to expect on a switch |
| [Accounts](docs/accounts.md) | add, renew, take out, remove, by hand |
| [Commands](docs/commands.md) | every `--pool-*` command |
| [Settings](docs/settings.md) | `CLAUDE_POOL_*` variables and `.env` |
| [Files](docs/files.md) | where tokens, usage and the log live; update and uninstall |
| [Servers](docs/server.md) | systemd, shared pools, several servers |
| [Changelog](CHANGELOG.md) | what changed in each version |

MIT licensed.
