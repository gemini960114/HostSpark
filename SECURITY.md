# Security Policy

## Supported version

Only the latest commit on the default branch receives security fixes.

## Threat model

This project allows allowlisted Telegram users (`ALLOWED_USER_IDS`, `ALLOWED_CHAT_IDS`) to submit tasks to AGY on an Ubuntu VM. Setting `AGY_PERMISSION_MODE=full` is equivalent to trusting the allowed Telegram accounts, Bot Token, model inputs and model decisions with the service user's VM permissions.

The following are outside the project's security guarantees:

- A compromised Telegram account, Bot Token or AGY account.
- Prompt injection contained in websites, logs, repositories or files read by AGY.
- Host privilege escalation available to the service user, including Docker group access.
- Secrets intentionally requested from AGY or sent through Telegram.
- Instructions and secrets intentionally stored in scheduled-task prompts.

## Deployment requirements

- Set `ALLOWED_USER_IDS` (or `ALLOWED_USER_ID`) before startup; there is no first-user auto-binding.
- Keep `.env` at mode `600` and never commit it. `chat_state.db`, `schedules.db`, and the instance-lock `bot.pid` file are all created at mode `600` automatically; do not loosen them.
- Uploaded attachments and per-chat/schedule working directories are automatically purged once a day when older than 30 days (`cleanup_expired_workspaces_and_uploads`, run from the schedule loop). This is a disk-hygiene measure, not a data-retention guarantee — do not rely on it for compliance purposes, and do not assume sensitive content is gone before 30 days have passed.
- Prefer Safe mode. Use Full only on a dedicated, recoverable VM.
- Remember that scheduled tasks run unattended. In Full mode they inherit automatic tool approval, so review the generated prompt before confirming a schedule.
- Never place tokens, passwords, private keys or other credentials in a scheduled-task prompt; prompts are persisted in SQLite.
- Avoid granting `NOPASSWD:ALL` to the service user. If temporarily enabled for root-level VM maintenance, revoke it immediately after use (`sudo rm -f /etc/sudoers.d/$USER`).
- Keep snapshots or tested backups and minimize credentials stored on the VM.
- Rotate the Bot Token immediately if it may have leaked.

## CLI Passthrough Security (`/agy`)

- Interactive flags (`-i`, `--prompt-interactive`) are strictly prohibited to prevent unmonitored deadlocks in headless environments.
- Dangerous operations (including `--dangerously-skip-permissions`, `update`, `install`, `plugin install/uninstall/enable/disable/import/link`) require explicit two-phase confirmation (`/agy_confirm`) with a 15-minute TTL token.
- All CLI executions bypass the shell and invoke `asyncio.create_subprocess_exec` directly.
- Subprocess environments are strictly sanitized: `TELEGRAM_BOT_TOKEN`, `ALLOWED_USER_IDS`, and other credentials are automatically scrubbed, with `NO_COLOR=1` and `TERM=dumb` enforced.

## Attachment & Workspace Path Containment

- All user-uploaded attachments and `/add_dir` directory inclusions are strictly validated with `safe_join` to prevent path traversal outside `AGY_WORKSPACE_ROOT`.
- File extensions are restricted to an allowlist of safe text, source code, data, and image formats.

## SSRF Defense & Media Validation

- When AGY outputs media URLs or file links to be forwarded to Telegram, all remote URLs undergo pre-flight SSRF inspection.
- The host domain is resolved via DNS, and all target IPs (IPv4 and IPv6) are validated to ensure they are public. Private IP ranges (e.g. RFC 1918, link-local, loopback, AWS metadata `169.254.169.254`) are strictly blocked to prevent DNS rebinding and internal network scanning.
- The file is fetched directly by the bot process itself (`httpx`, redirects disabled) rather than handed to Telegram as a bare URL, so the validated IP is the one actually connected to.

## Per-Chat Conversation Isolation

- AGY's `--continue` flag resumes "the most recently active conversation in the current working directory" — this scope is tied to the process's cwd, not to any notion of Telegram chat or user (confirmed empirically: two unrelated `--continue` calls issued from the same cwd share conversation history; from different cwds they do not).
- Every normal per-chat turn therefore runs in a dedicated working directory (`<state dir>/workspaces/chat-<chat_id>/`), created on first use, with the real `AGY_WORKDIR` reachable via `--add-dir`. Without this, two authorized chats issuing plain messages close together could have one silently resume and see conversation history from the other.
- Explicit `--conversation <UUID>` binding (when supported by a future feature) is unaffected by this — it works by ID regardless of cwd.
- Scheduled task execution and the `/schedule_add` prompt-rewrite step already used their own isolated per-run working directories before this was generalized to normal chat turns.

## Remote Restart & Self-Update (`/restart`, `/update`)

- Disabled by default (`ALLOW_BOT_UPDATE=0`). Enabling it means any allowlisted Telegram user can trigger `git pull origin main` against the deployed repo and force a service restart — treat this as equivalent to granting that user deploy access to the VM.
- `/update` runs `git pull` with no shell interpolation (argv array) and a bounded timeout; it does not accept user-supplied arguments.
- Both commands attempt `systemctl restart agy-telegram.service` first. Since the service normally runs as an unprivileged user, this call is commonly rejected by polkit (`Interactive authentication required`). The fallback path exits the process with a non-zero status code specifically so systemd's `Restart=on-failure` policy (configured by `install.sh`) brings it back up — no additional sudo/polkit grant is required or recommended for this feature to function.
- Only enable `ALLOW_BOT_UPDATE` if you trust every allowlisted user with the ability to deploy new code to this VM.

## Running the Security Scanner

`security-scan.sh` runs a battery of open-source scanners against this repo and writes their reports to `security-reports/<timestamp>/`:

- **pip-audit** — known-CVE scan of `requirements.lock`.
- **bandit** — static analysis for common Python security anti-patterns.
- **semgrep** — flexible static analysis using the public `p/python` and `p/owasp-top-ten` rulesets.
- **gitleaks** — scans the full git history for committed secrets and tokens.

```bash
./security-scan.sh
```

The script is self-contained: it bootstraps its own dependencies on first run (a dedicated `.security-tools-venv/`, kept separate from the app's own `venv/`, plus `gitleaks` via `apt` if missing) and does not require anything to be installed beforehand.

**It also installs a git pre-commit hook** (`.git/hooks/pre-commit`) that runs `gitleaks protect --staged` before every commit, blocking it if the staged changes contain a secret. This catches leaks before they land instead of relying solely on a later manual scan of git history. The hook is local to your checkout (`.git/hooks/` isn't version-controlled), so it's reinstalled automatically the next time `security-scan.sh` runs on a fresh clone. To bypass it for a confirmed false positive, use `git commit --no-verify`.

Not every finding these tools report is a real vulnerability — bandit and semgrep in particular flag patterns (e.g. any f-string used to build SQL) without understanding surrounding validation logic, so always read a flagged line in context before acting on it. A finding that's confirmed to be a false positive is still worth a one-line comment at the flagged code (why it's safe) so the next person — or the next scan — doesn't have to re-derive the same reasoning.

**Do not commit `security-reports/`.** It is already excluded via `.gitignore`: a gitleaks report in particular can contain the actual secret snippets it found, and even a stale bandit/semgrep report tied to a specific commit can read as a map of "here's what to check first" if the underlying code regresses later. Treat scan output as a local, disposable artifact — re-run the scanner instead of trying to keep an old report current.

## Reporting a vulnerability

Do not open a public issue containing exploit details or secrets. Contact the repository owner privately through the security reporting channel configured on the hosting platform. Include affected commit, impact and minimal reproduction steps without real credentials.
