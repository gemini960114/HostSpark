*English | [繁體中文](INSTALL_BY_AI.zh-TW.md)*

# Ubuntu VM Installation Instructions for AI Agents

This project only officially supports a regular Ubuntu user paired with systemd. Do not run the bot as root, and do not proactively grant the user `NOPASSWD:ALL` (if the user enables it themselves for maintenance, remind them to revert it afterward with `sudo rm -f /etc/sudoers.d/$USER`).

## Installation SOP

1. Confirm you are the regular user that will run the service, and that the following commands succeed:

   ```bash
   agy --version
   agy -p "reply ok"
   ```

2. Create the config file in the repo root:

   ```bash
   cp -n .env.example .env
   chmod 600 .env
   ```

3. Write the values the user provides into `.env`:
   - `TELEGRAM_BOT_TOKEN`
   - `ALLOWED_USER_IDS` (or `ALLOWED_USER_ID`)
   - `ALLOWED_CHAT_IDS` (optional), `TELEGRAM_PRIVATE_ONLY` (optional, default 1)
   - `AGY_PERMISSION_MODE=safe` or `full`
   - Only set `AGY_BIN`, `AGY_WORKDIR`, `AGY_WORKSPACE_ROOT`, `AGY_RULE_PROMPT` if needed
   - Confirm `AGY_SCHEDULE_TIMEZONE` matches the user's location

4. **`AGY_ALLOWED_MODELS` must use the real model list — do not copy the example value verbatim.** Run `agy models` first to get the model IDs actually available for this VM/account, then fill the result into `AGY_ALLOWED_MODELS` (comma-separated). Skipping this step, leaving it blank, or copying the doc's example as-is can make the `/models` menu show model names that don't exist; selecting one will fail outright.

5. Explicitly confirm the following two decisions with the user — neither may be assumed:
   - `AGY_PERMISSION_MODE`: `full` lets AGY auto-approve every tool operation; a rule prompt is not a substitute for real permission isolation.
   - `ALLOW_BOT_UPDATE` (default `0`): once enabled, any authorized user can trigger `git pull` and an automatic service restart via Telegram's `/update`, or a restart via `/restart`. This is additional attack surface (a compromised Telegram account becomes equivalent to being able to trigger code updates and restarts) — only enable it when the user explicitly wants this capability.

6. Run the install script. It uses uv (if installed) or a Python venv, syncs dependencies (including `pexpect`, `httpx`, etc.), validates the configuration, and generates a systemd service based on the current user and the repo's actual path:

   ```bash
   chmod +x install.sh
   ./install.sh
   ```

7. Verify the service and recent logs:

   ```bash
   sudo systemctl status agy-telegram.service --no-pager
   sudo journalctl -u agy-telegram.service -n 50 --no-pager
   ```

8. If `ALLOW_BOT_UPDATE=1`, explain to the user: `/restart`/`/update` will first attempt `systemctl restart`, but a regular service user typically has no polkit authorization to run that command; on failure they instead exit with a non-zero status code, letting systemd's `Restart=on-failure` policy bring the service back up automatically (this is already configured in the unit the install script generates — no extra authorization is needed). This means both commands still work correctly; the restart will simply leave what looks like a "crash" entry in `journalctl`, which is expected.

9. Do not commit `.env`, tokens, AGY login credentials, or secrets from logs to Git.
