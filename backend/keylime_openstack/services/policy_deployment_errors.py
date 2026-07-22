"""Policy deployment control-flow exceptions."""

from __future__ import annotations

__all__ = ["AwaitingReboot"]


class AwaitingReboot(RuntimeError):
    """Raised when a policy is installed but needs a controlled node reboot."""
