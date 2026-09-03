from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

import hostspark.state as state
from hostspark.config import ConfigError, load_config
from hostspark.runtime.instance_lock import InstanceLock, InstanceLockError
from hostspark.storage.chat_state import ChatStateStore
from hostspark.storage.schedule_store import ScheduleStore
from hostspark.telegram.app import build_application

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    # httpx logs the full request URL at INFO level, and python-telegram-bot
    # embeds the bot token directly in the URL path (api.telegram.org/bot<TOKEN>/...)
    # -- letting that through would print the live token in plaintext to the
    # systemd journal on every API call.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(state.ENV_PATH)
    try:
        state.CONFIG = load_config()
    except ConfigError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if "--check-config" in sys.argv:
        print("設定驗證成功")
        return

    # Instance lock
    lock_path = state.CONFIG.state_db_path.parent / "bot.pid"
    state.INSTANCE_LOCK = InstanceLock(lock_path)
    try:
        state.INSTANCE_LOCK.acquire()
    except InstanceLockError as exc:
        print(f"啟動失敗：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    state.SCHEDULE_STORE = ScheduleStore(state.CONFIG.schedule_db_path)
    state.CHAT_STATE_STORE = ChatStateStore(state.CONFIG.state_db_path)

    if state.CONFIG.permission_mode == "full":
        logger.warning("AGY 目前使用 Full 模式：所有工具權限將自動核准")
    logger.info("載入設定：workdir=%s, mode=%s", state.CONFIG.agy_workdir, state.CONFIG.permission_mode)

    app = build_application(state.CONFIG)

    logger.info("Telegram AGY Bot 正在啟動長輪詢")
    app.run_polling()


if __name__ == "__main__":
    main()
