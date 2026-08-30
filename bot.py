import asyncio
import logging
import os
import shutil
import sys
from contextlib import suppress
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agy_bot_core import (
    BotConfig,
    ConfigError,
    ProcessResult,
    compose_agy_prompt,
    load_config,
    md_to_telegram_html,
    redact_sensitive,
    run_process,
    split_markdown_into_chunks,
)


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = Path(os.getenv("AGY_ENV_FILE", str(BASE_DIR / ".env"))).expanduser()
load_dotenv(ENV_PATH)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CONFIG: BotConfig | None = None
agy_lock = asyncio.Lock()


def get_config() -> BotConfig:
    if CONFIG is None:
        raise RuntimeError("Bot 尚未載入設定")
    return CONFIG


def is_authorized(user_id: int) -> bool:
    return user_id == get_config().allowed_user_id


async def run_agy(user_text: str, *, continue_conversation: bool) -> ProcessResult:
    config = get_config()
    prompt = compose_agy_prompt(user_text, config.rule_prompt)
    args = [str(config.agy_bin), "-p", prompt]
    if continue_conversation:
        args.append("--continue")
    if config.permission_mode == "full":
        args.append("--dangerously-skip-permissions")
    args.extend(["--print-timeout", f"{config.timeout_seconds}s"])

    env = os.environ.copy()
    env["PATH"] = f"{config.agy_bin.parent}{os.pathsep}{env.get('PATH', '')}"
    return await run_process(
        args,
        cwd=config.agy_workdir,
        env=env,
        timeout_seconds=config.timeout_seconds + 10,
        max_output_bytes=config.max_output_bytes,
    )


def result_message(result: ProcessResult) -> str:
    truncation_note = "\n\n⚠️ 輸出過長，僅顯示前段內容。" if (
        result.stdout_truncated or result.stderr_truncated
    ) else ""
    if result.timed_out:
        return "⚠️ **AGY 執行逾時，程序已停止。**" + truncation_note
    if result.returncode != 0:
        details = result.stderr or result.stdout or "沒有錯誤詳情"
        return f"❌ **AGY 執行失敗（exit {result.returncode}）**\n\n{details}{truncation_note}"
    if result.stdout:
        return result.stdout + truncation_note
    if result.stderr:
        return f"⚠️ AGY 沒有標準輸出：\n\n{result.stderr}{truncation_note}"
    return "✅ 執行完成。"


async def send_formatted_response(message, text: str) -> None:
    for chunk in split_markdown_into_chunks(text, max_chunk_size=3500):
        try:
            await message.reply_text(
                md_to_telegram_html(chunk),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("HTML 格式傳送失敗，改用純文字：%s", exc)
            try:
                await message.reply_text(chunk)
            except Exception:
                logger.exception("Telegram 訊息傳送失敗")


async def reject_unauthorized(update: Update) -> bool:
    user = update.effective_user
    if user and is_authorized(user.id):
        return False
    if user:
        logger.warning("未授權訪問：%s", user.id)
    if update.message:
        await update.message.reply_text("⛔ 您沒有權限使用此機器人。")
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    mode = get_config().permission_mode
    mode_text = "Full（不逐次審核）" if mode == "full" else "Safe（遵循 AGY 權限規則）"
    msg = (
        "🤖 <b>Antigravity CLI (agy) 助手在線中！</b>\n\n"
        f"• 執行模式：<b>{mode_text}</b>\n"
        "• <code>/status</code> - 查看 VM 健康狀況\n"
        "• <code>/clear</code> - 開啟全新工作階段\n\n"
        "請直接傳送您要執行的文字任務。"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def _status_section(title: str, args: list[str]) -> str:
    config = get_config()
    env = os.environ.copy()
    result = await run_process(
        args,
        cwd=config.agy_workdir,
        env=env,
        timeout_seconds=15,
        max_output_bytes=200_000,
    )
    if result.timed_out:
        body = "查詢逾時"
    elif result.returncode != 0:
        body = result.stderr or f"命令失敗（exit {result.returncode}）"
    else:
        body = result.stdout or "沒有輸出"
    return f"**{title}**\n```\n{body}\n```"


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    checks = [
        ("系統負載與運行時間", ["uptime"]),
        ("根目錄磁碟", ["df", "-h", "/"]),
        ("記憶體", ["free", "-h"]),
    ]
    docker_bin = shutil.which("docker")
    if docker_bin:
        checks.append(("Docker 容器", [docker_bin, "ps"]))

    sections = []
    for title, args in checks:
        try:
            sections.append(await _status_section(title, args))
        except FileNotFoundError:
            sections.append(f"**{title}**\n```\n命令不存在\n```")
        except Exception as exc:
            logger.exception("狀態查詢失敗：%s", title)
            sections.append(f"**{title}**\n```\n查詢失敗：{redact_sensitive(str(exc))}\n```")
    await send_formatted_response(update.message, "📊 **VM 即時健康狀態**\n\n" + "\n\n".join(sections))


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    status_message = await update.message.reply_text("🔄 正在建立新的對話工作階段...")
    try:
        async with agy_lock:
            result = await run_agy("已開啟新對話，請簡短確認。", continue_conversation=False)
        if result.returncode == 0 and not result.timed_out:
            await status_message.edit_text("✅ 已建立新的對話工作階段。")
        else:
            await status_message.edit_text(result_message(result))
    except Exception as exc:
        logger.exception("重置工作階段失敗")
        await status_message.edit_text(f"❌ 重置失敗：{redact_sensitive(str(exc))}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not update.message or not update.message.text:
        return

    status_message = await update.message.reply_text(
        "⏳ <code>agy</code> 正在思考與執行中，請稍候...",
        parse_mode=ParseMode.HTML,
    )
    chat_id = update.effective_chat.id

    async def keep_typing() -> None:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        async with agy_lock:
            result = await run_agy(update.message.text, continue_conversation=True)
        with suppress(Exception):
            await status_message.delete()
        await send_formatted_response(update.message, result_message(result))
    except Exception as exc:
        logger.exception("AGY 執行異常")
        await status_message.edit_text(f"❌ 執行異常：{redact_sensitive(str(exc))}")
    finally:
        typing_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await typing_task


def main() -> None:
    global CONFIG
    try:
        CONFIG = load_config()
    except ConfigError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if "--check-config" in sys.argv:
        print("設定驗證成功")
        return

    if CONFIG.permission_mode == "full":
        logger.warning("AGY 目前使用 Full 模式：所有工具權限將自動核准")
    logger.info("載入設定：workdir=%s, mode=%s", CONFIG.agy_workdir, CONFIG.permission_mode)

    app = ApplicationBuilder().token(CONFIG.bot_token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Telegram AGY Bot 正在啟動長輪詢")
    app.run_polling()


if __name__ == "__main__":
    main()
