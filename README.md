# claude-pool

Runs `claude` on whichever of your accounts has usage left. Use it exactly like `claude`.

## Install

```sh
curl -fsSL https://github.com/grega-rotar/claude-pool/releases/latest/download/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
```

## Add accounts

Run `claude setup-token` for each account, then:

```sh
claude-pool --pool-add work
```

Or put them in a `.env`:

```sh
CLAUDE_POOL_TOKEN_WORK=sk-ant-oat01-...
```

## Use

```sh
claude-pool -p "hi"
```

`claude-pool --pool-watch` shows every account's usage and keeps it up to date. `claude-pool --pool-help` lists all commands. More: [settings](docs/settings.md), [how it works](docs/how-it-works.md), [servers](docs/server.md).

Using several accounts to get around usage limits breaks Anthropic's terms. MIT.
