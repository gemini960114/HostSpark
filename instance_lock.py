import atexit
import logging
import os
from pathlib import Path


logger = logging.getLogger(__name__)


class InstanceLockError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path.expanduser().resolve()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._acquired = False

    def acquire(self) -> None:
        if self.lock_path.exists():
            try:
                content = self.lock_path.read_text().strip()
                old_pid = int(content)
            except Exception:
                old_pid = None

            if old_pid is not None:
                is_alive = False
                try:
                    os.kill(old_pid, 0)
                    is_alive = True
                except PermissionError:
                    # Process exists but owned by different user (e.g. root/systemd)
                    is_alive = True
                except (ProcessLookupError, OSError):
                    is_alive = False

                if is_alive and old_pid != os.getpid():
                    raise InstanceLockError(
                        f"另一個 Bot 實例正在執行中（PID: {old_pid}）。如確定未運行，請刪除 {self.lock_path}"
                    )
                else:
                    logger.warning("偵測到已終止進程的殘留鎖（PID: %s），自動清除並接管鎖定", old_pid)
                    self.lock_path.unlink(missing_ok=True)
            else:
                self.lock_path.unlink(missing_ok=True)

        my_pid = os.getpid()
        self.lock_path.write_text(str(my_pid))
        self._acquired = True
        atexit.register(self.release)

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            if self.lock_path.exists():
                content = self.lock_path.read_text().strip()
                if content == str(os.getpid()):
                    self.lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        finally:
            self._acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
