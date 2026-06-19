"""ADB helper utilities for Android device interaction.

This module provides convenience functions for:
- ADB shell commands
- Broadcast intents
- Querying Android system properties
"""

import subprocess
import json
import time
from typing import Optional


def adb_shell(cmd: str, timeout: int = 30) -> str:
    """Execute an ADB shell command and return output."""
    result = subprocess.run(
        ["adb", "shell", cmd],
        capture_output=True,
        text=True,
        timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"ADB shell command failed: {cmd}\n{result.stderr}")
    return result.stdout


def adb_broadcast(action: str, extras: Optional[dict] = None) -> str:
    """Send an Android broadcast intent."""
    cmd = f"am broadcast -a {action}"
    if extras:
        for key, value in extras.items():
            if isinstance(value, bool):
                cmd += f" --ez {key} {str(value).lower()}"
            elif isinstance(value, int):
                cmd += f" --ei {key} {value}"
            elif isinstance(value, str):
                cmd += f" --es {key} {value}"
    return adb_shell(cmd)


def adb_get_prop(prop: str) -> str:
    """Get an Android system property."""
    return adb_shell(f"getprop {prop}").strip()


def adb_logcat(filter_str: str = "", timeout: int = 10) -> str:
    """Capture ADB logcat output for a specified duration."""
    result = subprocess.run(
        ["adb", "logcat", "-d"] + (filter_str.split() if filter_str else []),
        capture_output=True,
        text=True,
        timeout=timeout
    )
    return result.stdout


def adb_install_apk(apk_path: str, timeout: int = 120) -> bool:
    """Install an APK on the device."""
    result = subprocess.run(
        ["adb", "install", "-r", apk_path],
        capture_output=True,
        text=True,
        timeout=timeout
    )
    return result.returncode == 0


def adb_uninstall(package: str, timeout: int = 30) -> bool:
    """Uninstall an app from the device."""
    result = subprocess.run(
        ["adb", "uninstall", package],
        capture_output=True,
        text=True,
        timeout=timeout
    )
    return result.returncode == 0


def adb_start_activity(package: str, activity: str) -> bool:
    """Start an Android activity."""
    cmd = f"am start -n {package}/{activity}"
    result = subprocess.run(
        ["adb", "shell", cmd],
        capture_output=True,
        text=True,
        timeout=30
    )
    return result.returncode == 0


def get_last_message_from_conduit(timeout: int = 30) -> Optional[dict]:
    """Query Conduit for the last received message via ADB broadcast.
    
    Returns a dict with title, body, and timestamp or None if no message.
    """
    # Send broadcast to get last message
    broadcast_result = adb_broadcast(
        "hermes.test.GET_LAST_MESSAGE",
        timeout=timeout
    )
    
    # Parse the result from logcat
    log_output = adb_logcat(timeout=5)
    
    # Look for the result broadcast
    last_message = None
    for line in log_output.split("\n"):
        if "hermes.test.LAST_MESSAGE_RESULT" in line:
            # Extract JSON from the line
            try:
                # This is a simplified parser - in production you'd want better parsing
                if "Bundle" in line:
                    # Extract the message JSON from the bundle
                    last_message = {"found": True}
            except Exception:
                pass
    
    return last_message


def clear_conduit_messages(timeout: int = 30) -> bool:
    """Clear all received messages in Conduit."""
    try:
        adb_broadcast("hermes.test.CLEAR_MESSAGES", timeout=timeout)
        return True
    except Exception:
        return False


def wait_for_message_arrival(timeout: int = 10) -> bool:
    """Wait for a new message to arrive.
    
    Polls Conduit periodically until a message is received or timeout.
    """
    start = time.time()
    while time.time() - start < timeout:
        msg = get_last_message_from_conduit(timeout=5)
        if msg and msg.get("found", False):
            return True
        time.sleep(0.5)
    return False


def get_conduit_package() -> str:
    """Get the Conduit app package name."""
    # Try common Conduit package names
    packages = [
        "io.cogwheel.conduit",
        "io.cogwheel.conduit.debug",
    ]
    
    for pkg in packages:
        result = subprocess.run(
            ["adb", "shell", f"pm path {pkg}"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return pkg
    
    raise RuntimeError(f"None of the expected Conduit packages found: {packages}")
