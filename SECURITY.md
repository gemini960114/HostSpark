# Security Policy

## Supported version

Only the latest commit on the default branch receives security fixes.

## Threat model

This project intentionally lets one allowlisted Telegram user submit tasks to AGY on an Ubuntu VM. `AGY_PERMISSION_MODE=full` is equivalent to trusting that Telegram account, Bot Token, model inputs and model decisions with the service user's VM permissions.

The following are outside the project's security guarantees:

- A compromised Telegram account, Bot Token or AGY account.
- Prompt injection contained in websites, logs, repositories or files read by AGY.
- Host privilege escalation available to the service user, including Docker group access.
- Secrets intentionally requested from AGY or sent through Telegram.
- Instructions and secrets intentionally stored in scheduled-task prompts.

## Deployment requirements

- Set `ALLOWED_USER_ID` before startup; there is no first-user auto-binding.
- Keep `.env` at mode `600` and never commit it.
- Prefer Safe mode. Use Full only on a dedicated, recoverable VM.
- Remember that scheduled tasks run unattended. In Full mode they inherit automatic tool approval, so review the generated prompt before confirming a schedule.
- Never place tokens, passwords, private keys or other credentials in a scheduled-task prompt; prompts are persisted in SQLite.
- Do not grant `NOPASSWD:ALL` to the service user.
- Keep snapshots or tested backups and minimize credentials stored on the VM.
- Rotate the Bot Token immediately if it may have leaked.

## Reporting a vulnerability

Do not open a public issue containing exploit details or secrets. Contact the repository owner privately through the security reporting channel configured on the hosting platform. Include affected commit, impact and minimal reproduction steps without real credentials.
