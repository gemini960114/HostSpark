import logging
from telegram import Update
import hostspark.state as state

logger = logging.getLogger(__name__)


def is_authorized(
    user_id: int,
    chat_id: int | None = None,
    chat_type: str | None = None,
) -> bool:
    config = state.get_config()
    if user_id not in config.allowed_user_ids:
        return False
    if config.allowed_chat_ids and chat_id is not None and chat_id not in config.allowed_chat_ids:
        return False
    if config.private_only and chat_type is not None and chat_type != "private":
        return False
    return True


async def reject_unauthorized(update: Update) -> bool:
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    user_id = getattr(user, "id", None) if user else None
    chat_id = getattr(chat, "id", None) if chat else None
    chat_type = getattr(chat, "type", None) if chat else None

    if user_id is not None and is_authorized(user_id, chat_id, chat_type):
        return False
    if user_id is not None:
        logger.warning("未授權訪問：user_id=%s, chat_id=%s", user_id, chat_id)
    if getattr(update, "message", None) and hasattr(update.message, "reply_text"):
        await update.message.reply_text("⛔ 您沒有權限使用此機器人。")
    return True


def _get_chat_id(update: Update, default: int = 0) -> int:
    chat = getattr(update, "effective_chat", None)
    if chat and hasattr(chat, "id"):
        return chat.id
    user = getattr(update, "effective_user", None)
    if user and hasattr(user, "id"):
        return user.id
    query = getattr(update, "callback_query", None)
    if query and getattr(query, "message", None) and getattr(query.message, "chat", None):
        return query.message.chat.id
    return default
