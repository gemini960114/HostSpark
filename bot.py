import os
import sys
import subprocess
import asyncio
import logging
import re
import html
from dotenv import load_dotenv, set_key
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

ENV_PATH = "/home/ubuntu/telegram_agy_bot/.env"
load_dotenv(ENV_PATH)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

if not BOT_TOKEN:
    print("❌ 錯誤: 未設定 TELEGRAM_BOT_TOKEN")
    sys.exit(1)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# 確保同一時間只有一個 agy 在執行，避免 SQLite 鎖定與 timeout 衝突
agy_lock = asyncio.Lock()

def save_allowed_user(user_id: int):
    global ALLOWED_USER_ID
    ALLOWED_USER_ID = str(user_id)
    try:
        set_key(ENV_PATH, "ALLOWED_USER_ID", str(user_id))
        os.chmod(ENV_PATH, 0o600)
        logger.info(f"✅ 管理員 ID 已自動綁定: {user_id}")
    except Exception as e:
        logger.error(f"寫入 .env 失敗: {e}")

def is_authorized(user_id: int) -> bool:
    global ALLOWED_USER_ID
    if not ALLOWED_USER_ID or ALLOWED_USER_ID == "":
        return False
    return str(user_id) == str(ALLOWED_USER_ID)

def md_to_telegram_html(text: str) -> str:
    # 1. 提取並保護多行 Code Blocks ```lang\ncode\n```
    code_blocks = []
    def save_code_block(match):
        lang = match.group(1) or ""
        code = match.group(2)
        code_blocks.append((lang, code))
        return f"\x00CB_{len(code_blocks)-1}\x00"

    text = re.sub(r"```(\w*)\n([\s\S]*?)```", save_code_block, text)

    # 2. 提取並保護行內 Code `code`
    inline_codes = []
    def save_inline_code(match):
        code = match.group(1)
        inline_codes.append(code)
        return f"\x00IC_{len(inline_codes)-1}\x00"

    text = re.sub(r"`([^`\n]+)`", save_inline_code, text)

    # 3. 轉義一般 HTML 特殊字元
    text = html.escape(text)

    # 4. 轉換常用的 Markdown 標記為 HTML
    def replace_link(match):
        link_text = match.group(1)
        link_url = match.group(2)
        return f'<a href="{link_url}">{link_text}</a>'

    text = re.sub(r"\[(.*?)\]\((https?://[^\s\)]+)\)", replace_link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    # 5. 還原行內 code
    for idx, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC_{idx}\x00", f"<code>{html.escape(code)}</code>")

    # 6. 還原 code blocks
    for idx, (lang, code) in enumerate(code_blocks):
        escaped_code = html.escape(code.rstrip())
        if lang:
            tag = f'<pre><code class="language-{html.escape(lang)}">{escaped_code}</code></pre>'
        else:
            tag = f'<pre>{escaped_code}</pre>'
        text = text.replace(f"\x00CB_{idx}\x00", tag)

    return text

def split_markdown_into_chunks(text: str, max_chunk_size: int = 3500) -> list:
    if len(text) <= max_chunk_size:
        return [text]

    chunks = []
    current_chunk = []
    current_length = 0

    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if current_length + len(para) + 2 > max_chunk_size:
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_length = 0

            if len(para) > max_chunk_size:
                lines = para.split("\n")
                for line in lines:
                    if current_length + len(line) + 1 > max_chunk_size:
                        if current_chunk:
                            chunks.append("\n".join(current_chunk))
                            current_chunk = []
                            current_length = 0
                    current_chunk.append(line)
                    current_length += len(line) + 1
            else:
                current_chunk.append(para)
                current_length += len(para) + 2
        else:
            current_chunk.append(para)
            current_length += len(para) + 2

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks

async def send_formatted_response(message, text: str):
    chunks = split_markdown_into_chunks(text, max_chunk_size=3500)
    for chunk in chunks:
        formatted = md_to_telegram_html(chunk)
        try:
            await message.reply_text(
                formatted,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.warning(f"HTML parse fallback: {e}")
            try:
                await message.reply_text(chunk)
            except Exception as e2:
                logger.error(f"Failed to send chunk: {e2}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ALLOWED_USER_ID
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    if not ALLOWED_USER_ID or ALLOWED_USER_ID == "":
        save_allowed_user(user_id)
        msg = (
            f"🎉 <b>管理員身分綁定成功！</b>\n\n"
            f"• 帳號：<code>{html.escape(str(username))}</code>\n"
            f"• 專屬 User ID：<code>{user_id}</code>\n"
            f"• 狀態：<b>已鎖定為唯一白名單，其他帳號無法使用此機器人</b>。\n\n"
            "🤖 <b>Antigravity CLI (agy) 已就緒！</b>\n"
            "您可以在這裡直接傳送文字訊息，我會在伺服器上執行 <code>agy</code> 並即時回覆。\n\n"
            "📌 <b>快捷指令：</b>\n"
            "• <code>/status</code> - 查看伺服器與 Docker 容器狀態\n"
            "• <code>/clear</code> - 重置對話工作階段\n"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return

    if not is_authorized(user_id):
        logger.warning(f"未授權訪問: {user_id}")
        await update.message.reply_text("⛔ 您沒有權限使用此機器人。")
        return

    msg = (
        f"🤖 <b>Antigravity CLI (agy) 助手在線中！</b>\n\n"
        "請直接傳送訊息（例如：「<i>檢查 HMP 網站運作情況</i>」或「<i>查看目前的磁碟空間</i>」）。\n\n"
        "• <code>/status</code> - 快速查看伺服器健康狀況\n"
        "• <code>/clear</code> - 開啟全新工作階段\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        cmd = "docker compose -f /home/ubuntu/dkan_hmp_backup/docker_setup/docker-compose.yml ps && echo '' && df -h / && echo '' && free -h"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        output = f"📊 <b>伺服器即時健康狀態：</b>\n<pre>{html.escape(res.stdout.strip())}</pre>"
        await update.message.reply_text(output, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ 查詢失敗: {html.escape(str(e))}")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    await update.message.reply_text("🔄 正在開闢全新的對話工作階段...")
    try:
        env = os.environ.copy()
        env["PATH"] = f"/home/ubuntu/.local/bin:{env.get('PATH', '')}"
        subprocess.run(
            ["/home/ubuntu/.local/bin/agy", "-p", "已開啟新對話", "--dangerously-skip-permissions"],
            cwd="/home/ubuntu",
            env=env,
            timeout=15
        )
        await update.message.reply_text("✅ 對話上下文已重置，已為您建立全新的乾淨工作階段！")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 重置提示: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logger.warning(f"未授權發送訊息: {user_id}")
        return

    user_text = update.message.text
    if not user_text:
        return

    chat_id = update.effective_chat.id
    status_msg = await update.message.reply_text("⏳ <code>agy</code> 正在思考與執行中，請稍候...", parse_mode=ParseMode.HTML)

    async def keep_typing():
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    typing_task = asyncio.create_task(keep_typing())

    try:
        # 使用 lock 防止多次快速點擊造成資料庫競爭超時
        async with agy_lock:
            env = os.environ.copy()
            env["PATH"] = f"/home/ubuntu/.local/bin:{env.get('PATH', '')}"

            # 加入 --print-timeout 10m 給予足夠分析時間
            process = await asyncio.create_subprocess_exec(
                "/home/ubuntu/.local/bin/agy",
                "-p", user_text,
                "-c",
                "--dangerously-skip-permissions",
                "--print-timeout", "10m",
                cwd="/home/ubuntu",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )

            stdout, stderr = await process.communicate()

        typing_task.cancel()

        response_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()

        if not response_text:
            if "timeout waiting for response" in err_text:
                response_text = "⚠️ **模型連線暫時逾時**：先前的複雜運算或網路延遲導致超時，請重新傳送一次或使用 `/clear` 重置後再試。"
            elif err_text:
                response_text = f"⚠️ 執行完成但無標準輸出。\n{err_text}"
            else:
                response_text = "✅ 執行完成。"

        await status_msg.delete()
        await send_formatted_response(update.message, response_text)

    except Exception as e:
        typing_task.cancel()
        await status_msg.edit_text(f"❌ 執行異常: {html.escape(str(e))}", parse_mode=ParseMode.HTML)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Telegram AGY Bot 正在啟動監聽 (支援佇列鎖與超時防護)...")
    app.run_polling()

if __name__ == "__main__":
    main()
