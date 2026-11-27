from __future__ import annotations

from dataclasses import dataclass

from collector.platforms import PlatformProvider, create_platform_provider


@dataclass(frozen=True)
class IdleState:
    is_idle: bool
    idle_seconds: int
    available: bool = True


class IdleTracker:
    def __init__(
        self,
        idle_threshold_seconds: int = 300,
        provider: PlatformProvider | None = None,
    ) -> None:
        self.idle_threshold_seconds = idle_threshold_seconds
        self.provider = provider or create_platform_provider()

    def get_idle_state(self) -> IdleState:
        seconds = self.provider.get_idle_seconds()
        return IdleState(
            is_idle=seconds is not None and seconds >= self.idle_threshold_seconds,
            idle_seconds=seconds if seconds is not None else 0,
            available=seconds is not None,
        )
