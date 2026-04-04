"""
Base class for ShellFrame communication bridges.
Extend this to add support for Telegram, Discord, WhatsApp, LINE, etc.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class BridgeConfigBase:
    """Base configuration shared by all bridges."""
    prefix_enabled: bool = True
    initial_prompt: str = ""
    auto_resume_on_message: bool = True
    allowed_users: list = field(default_factory=list)


class BridgeBase(ABC):
    """Abstract base for all communication bridges."""

    PLATFORM = "unknown"  # Override in subclass: "telegram", "discord", "line", etc.

    def __init__(self, bridge_id: str, config, write_fn, on_status_change=None):
        self.bridge_id = bridge_id
        self.config = config
        self.write_fn = write_fn
        self.on_status_change = on_status_change
        self.active = False
        self.paused = False
        self.connected = False

    @abstractmethod
    def start(self):
        """Start the bridge (verify credentials + begin listening)."""
        ...

    @abstractmethod
    def stop(self):
        """Stop the bridge."""
        ...

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def toggle_pause(self) -> bool:
        if self.paused:
            self.resume()
        else:
            self.pause()
        return not self.paused

    @abstractmethod
    def feed_output(self, raw_text: str):
        """Feed PTY output to the bridge for forwarding to the platform."""
        ...

    @abstractmethod
    def get_status(self) -> dict:
        """Return current bridge status."""
        ...

    def _emit_status(self, status: dict):
        if self.on_status_change:
            try:
                self.on_status_change(self.bridge_id, status)
            except:
                pass
