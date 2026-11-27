from __future__ import annotations

from collector.platforms import PlatformProvider, create_platform_provider
from workgraph.models import ActiveWindow


class WindowTracker:
    """Reads the currently focused application and window title."""

    def __init__(self, provider: PlatformProvider | None = None) -> None:
        self.provider = provider or create_platform_provider()

    def get_active_window(self) -> ActiveWindow:
        return self.provider.get_active_window()
