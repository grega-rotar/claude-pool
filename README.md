# claude-pool

You have more than one Claude account. claude-pool runs Claude Code on the one that still has usage left. When that one hits its limit, your session moves to the next account and keeps going on its own. You use it exactly like `claude`.

```sh
claude-pool                       # an interactive session, like `claude`
claude-pool -p "fix the tests"    # print mode, like `claude -p`
```

> **Before you use it:** using several accounts to get around usage limits breaks [Anthropic's terms](https://www.anthropic.com/legal/consumer-terms), and so does sharing one account between people. For a team, the supported route is one seat per person (Team or Enterprise plan) or an API key with org billing. claude-pool works with API keys too.

## What it does

- **Sessions switch on their own.** When the account hits its limit, claude-pool resumes the same conversation on the next free account and tells it to carry on. The history and every tool result come along, so nothing runs twice. A refused token is handled the same way.
- **You always see where you are.** A line in the bottom-right corner, under the prompt, shows the account, how much of its 5 hours is used, when that resets, and where the session goes next. Usage is green while low, yellow from 70% and red from 90%, and a red warning shows when there's nowhere left to go:

  ```
  pool: editors2 · 85% · resets 23:10 · next: factory 12%
  pool: editors2 · 95% · resets 23:10 · ⚠ no next account until 20:20
  ```

- **Or switch when you want.** Type `/pool:switch work` in the session to move it to `work`, or just `/pool:switch` to move it to the best free account. Same conversation, no model call for the command.
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

## With Orca

If you use [Orca](https://github.com/stablyai/orca), the Claude accounts you're logged in to there join the pool by themselves, as `orca-<name>`. No `setup-token` step is needed. Put this in `~/.bashrc` (or `~/.zshrc`) so every Orca pane runs through claude-pool:

```sh
[ -n "$ORCA_PANE_KEY" ] && alias claude=claude-pool
```

- **A new pane starts on its own account** while that account has room, so Orca's label is right until a switch. When Orca reopens and resumes its panes, they are picked in the pool's order instead.
- **At the limit, the session moves to another Orca account** (or a token you added) and carries on. All Orca accounts share `~/.claude/projects`, so the conversation is there. After a switch, Orca's pane still shows the first account's name.
- **Logins stay Orca's.** claude-pool never reads or copies them: it starts claude in that account's Orca folder, and claude keeps the login fresh there, as it does in an Orca pane.
- **Only interactive sessions and `--pool-*` commands use them.** Scripts and services on `claude-pool -p` keep to the accounts set up for them. Set `CLAUDE_POOL_ORCA=on` to change that, or `off` to never use Orca's accounts.
- `claude-pool --pool-off orca-<name>` leaves one out.

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
