"""Backward compatibility adapter for pending_actions."""
from hostspark.runtime.pending_actions import (
    UTC,
    PendingAction,
    PendingActionStore,
)

__all__ = ["UTC", "PendingAction", "PendingActionStore"]
