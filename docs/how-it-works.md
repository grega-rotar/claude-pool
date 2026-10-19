# How it works

- **Usage:** each run reports the account's usage and claude-pool saves it. Numbers older than 5 minutes are rechecked with a tiny Haiku call (~$0.0001), so other apps' use counts too.
- **Picking:** it uses the account whose week resets soonest first, since leftover usage is lost at the reset. At 90% of the 5-hour limit it moves on. Unused accounts go last.
- **Limits:** a run cut off by a limit is rerun on the next account, but only if it hadn't used tools yet, so nothing happens twice.
- **Output:** JSON gets a `pool_account` field; stream-json starts each run with a `{"type": "system", "subtype": "pool_account", "account": ...}` line. Exit code `75` means every account is full.
- **Log:** runs, switches and errors go to `events.log`. See them with `claude-pool --pool-log`.
