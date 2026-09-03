import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


UTC = timezone.utc


@dataclass(frozen=True)
class PendingAction:
    token: str
    kind: str
    user_id: int
    payload: Any
    created_at: datetime
    expires_at: datetime


class PendingActionStore:
    def __init__(self) -> None:
        self._actions: dict[str, PendingAction] = {}
        self._lock = threading.Lock()

    def purge_expired(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        with self._lock:
            expired = [k for k, v in self._actions.items() if v.expires_at <= current]
            for k in expired:
                del self._actions[k]

    def put(
        self,
        kind: str,
        user_id: int,
        payload: Any,
        ttl_minutes: int = 15,
        now: datetime | None = None,
    ) -> str:
        current = now or datetime.now(UTC)
        self.purge_expired(current)
        token = secrets.token_urlsafe(8)
        action = PendingAction(
            token=token,
            kind=kind,
            user_id=user_id,
            payload=payload,
            created_at=current,
            expires_at=current + timedelta(minutes=ttl_minutes),
        )
        with self._lock:
            self._actions[token] = action
        return token

    def get(self, token: str, user_id: int | None = None, now: datetime | None = None) -> PendingAction | None:
        current = now or datetime.now(UTC)
        with self._lock:
            action = self._actions.get(token)
            if action is None:
                return None
            if action.expires_at <= current:
                del self._actions[token]
                return None
            if user_id is not None and action.user_id != user_id:
                return None
            return action

    def pop(self, token: str, user_id: int | None = None, now: datetime | None = None) -> PendingAction | None:
        current = now or datetime.now(UTC)
        with self._lock:
            action = self._actions.get(token)
            if action is None:
                return None
            if action.expires_at <= current:
                del self._actions[token]
                return None
            if user_id is not None and action.user_id != user_id:
                return None
            del self._actions[token]
            return action

    def count(self, kind: str | None = None, user_id: int | None = None) -> int:
        self.purge_expired()
        with self._lock:
            actions = list(self._actions.values())
            if kind is not None:
                actions = [a for a in actions if a.kind == kind]
            if user_id is not None:
                actions = [a for a in actions if a.user_id == user_id]
            return len(actions)
