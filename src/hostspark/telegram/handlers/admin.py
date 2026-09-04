from __future__ import annotations

import asyncio
import html
import logging
import shutil
import sys
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import hostspark.state as state
import hostspark.core.executor as executor
from hostspark.constants import DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
from hostspark.core.sanitizer import build_safe_subprocess_env
from hostspark.telegram.auth import reject_unauthorized

logger = logging.getLogger(__name__)


async def _perform_bot_restart(delay_seconds: float) -> None:
    """觸發服務重啟。

    優先嘗試 `systemctl restart`；但該指令通常需要 polkit 授權，一般（非
    root）服務使用者常會被拒絕（`Interactive authentication required`）。
    因此一律準備非零結束碼作為保底手段——systemd unit 設有
    `Restart=on-failure`，只有非零 exit code 才會被視為失敗並觸發自動重啟；
    `sys.exit(0)` 屬於「正常結束」，systemd 不會重啟，會導致服務整個停擺。
    """
    await asyncio.sleep(delay_seconds)
    if shutil.which("systemctl"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "restart", "agy-telegram.service",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                return
            logger.warning(
                "systemctl restart 未成功（exit %s，可能缺少 polkit 授權）：%s；改用結束碼觸發 Restart=on-failure",
                proc.returncode,
                stderr.decode("utf-8", errors="replace").strip(),
            )
        except Exception as exc:
            logger.warning("呼叫 systemctl restart 失敗：%s；改用結束碼觸發 Restart=on-failure", exc)
    sys.exit(1)


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = state.get_config()
    if not config.allow_bot_update:
        await update.message.reply_text("❌ 未啟用遠端重啟。請在 `.env` 設定 `ALLOW_BOT_UPDATE=1`。")
        return

    await update.message.reply_text("🔄 正在重新啟動 HostSpark 服務...")
    asyncio.create_task(_perform_bot_restart(1.0))


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = state.get_config()
    if not config.allow_bot_update:
        await update.message.reply_text("❌ 未啟用遠端更新。請在 `.env` 設定 `ALLOW_BOT_UPDATE=1`。")
        return

    status_msg = await update.message.reply_text("⬇️ 正在自 GitHub 拉取最新更新...")
    env = build_safe_subprocess_env()
    git_bin = shutil.which("git") or "git"
    result = await executor.run_process(
        [git_bin, "pull", "origin", "main"],
        cwd=state.BASE_DIR,
        env=env,
        timeout_seconds=DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
        max_output_bytes=100_000,
    )

    if result.returncode != 0:
        await status_msg.edit_text(f"❌ 更新失敗（exit {result.returncode}）：\n\n{result.stderr or result.stdout}")
        return

    await status_msg.edit_text(f"✅ 更新成功：\n\n<pre>{html.escape(result.stdout)}</pre>\n\n🔄 正在自動重啟服務...", parse_mode=ParseMode.HTML)
    asyncio.create_task(_perform_bot_restart(1.5))
