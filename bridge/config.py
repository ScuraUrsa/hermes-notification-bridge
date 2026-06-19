"""Configuration for the Notification Bridge service."""

import os
from pathlib import Path

# Default paths
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DEFAULT_DB_PATH = HERMES_HOME / "notification-bridge" / "messages.db"


class BridgeConfig:
    """Configuration loaded from environment variables."""

    def __init__(self):
        self.port = int(os.environ.get("BRIDGE_PORT", "8655"))
        self.host = os.environ.get("BRIDGE_HOST", "0.0.0.0")
        self.auth_key = os.environ.get("BRIDGE_AUTH_KEY", "hermes-bridge-dev-key")
        self.db_path = Path(os.environ.get("BRIDGE_DB_PATH", str(DEFAULT_DB_PATH)))
        self.heartbeat_interval = int(os.environ.get("BRIDGE_HEARTBEAT_INTERVAL", "30"))
        self.max_messages = int(os.environ.get("BRIDGE_MAX_MESSAGES", "1000"))

    @property
    def db_dir(self) -> Path:
        return self.db_path.parent


# Global singleton
config = BridgeConfig()
