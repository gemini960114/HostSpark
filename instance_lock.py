"""Backward compatibility adapter for instance_lock."""
from hostspark.runtime.instance_lock import InstanceLock, InstanceLockError

__all__ = ["InstanceLock", "InstanceLockError"]
