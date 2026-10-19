# claude-pool

You have more than one Claude account. claude-pool runs `claude` on the one that still has usage left, and moves to the next when one is full. You use it exactly like `claude`.

## Start in 3 steps

1. **Install**

   ```sh
   curl -fsSL https://github.com/grega-rotar/claude-pool/releases/latest/download/claude_pool.py -o ~/.local/bin/claude-pool
   chmod +x ~/.local/bin/claude-pool
   ```

2. **Add an account.** Run `claude setup-token`, log in, copy the token, then:

   ```sh
   claude-pool --pool-add work
   ```

   Do this once per account. The name (`work`) is only a label.

3. **Use it** like `claude`:

   ```sh
   claude-pool -p "hi"
   ```

See your accounts and their usage: `claude-pool --pool-status`

## More

| Page | For |
|---|---|
| [Accounts](docs/accounts.md) | add, renew, take out, remove, by hand |
| [Commands](docs/commands.md) | every `--pool-*` command |
| [Settings](docs/settings.md) | `CLAUDE_POOL_*` variables and `.env` |
| [Files](docs/files.md) | where tokens, usage and the log live |
| [Servers](docs/server.md) | systemd, shared pools, several servers, Windows |
| [How it works](docs/how-it-works.md) | how it picks an account and handles limits |

Using several accounts to get around usage limits breaks Anthropic's terms. MIT.
