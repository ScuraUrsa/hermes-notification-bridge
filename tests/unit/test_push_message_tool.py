"""Unit tests for the push_message Hermes tool.

Tests all parameter combinations, error handling, and the check function.
Uses unittest.mock to mock urllib.request — no running Bridge required.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add hermes-tool to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hermes-tool"))

from push_message_tool import (
    push_message,
    _check_push_message,
    VALID_PRIORITIES,
    VALID_SOURCES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_urlopen_mock(response_body: dict) -> MagicMock:
    """Create a mock for urllib.request.urlopen that returns a context manager
    yielding a response with the given JSON body."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(response_body).encode("utf-8")
    # urlopen is used as a context manager: `with urlopen(...) as resp:`
    # So urlopen() must return an object whose __enter__ returns the mock_response
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_response
    return mock_ctx


# ---------------------------------------------------------------------------
# Success cases
# ---------------------------------------------------------------------------

class TestPushMessageSuccess:
    def test_basic_push(self):
        """Basic push with title and body returns success."""
        mock = _make_urlopen_mock({"status": "delivered", "id": "msg-test-123"})

        with patch("urllib.request.urlopen", return_value=mock):
            result = json.loads(push_message(
                title="Test Title",
                body="Test body content",
            ))

        assert result["status"] == "delivered"
        assert result["id"] == "msg-test-123"

    def test_push_with_all_params(self):
        """All optional parameters are sent correctly."""
        captured_data = {}

        def capture_request(req, timeout=None):
            captured_data["url"] = req.full_url
            captured_data["headers"] = dict(req.headers)
            captured_data["body"] = json.loads(req.data.decode())
            return _make_urlopen_mock({"status": "delivered", "id": "x"})

        with patch("urllib.request.urlopen", side_effect=capture_request):
            push_message(
                title="Full Test",
                body="All params",
                priority="urgent",
                source="github-watcher",
                action_url="http://example.com/alert",
                voice=True,
                tts_text="Short TTS version",
                requires_reply=True,
            )

        body = captured_data["body"]
        assert body["title"] == "Full Test"
        assert body["body"] == "All params"
        assert body["priority"] == "urgent"
        assert body["source"] == "github-watcher"
        assert body["voice"] is True
        assert body["tts_text"] == "Short TTS version"
        assert body["metadata"]["requires_reply"] is True
        assert body["action"]["url"] == "http://example.com/alert"

    def test_push_without_action_url(self):
        """No action field when action_url is empty."""
        captured_data = {}

        def capture_request(req, timeout=None):
            captured_data["body"] = json.loads(req.data.decode())
            return _make_urlopen_mock({"status": "delivered", "id": "x"})

        with patch("urllib.request.urlopen", side_effect=capture_request):
            push_message(title="T", body="B")

        assert "action" not in captured_data["body"]

    def test_push_default_priority(self):
        """Default priority is 'normal'."""
        captured_data = {}

        def capture_request(req, timeout=None):
            captured_data["body"] = json.loads(req.data.decode())
            return _make_urlopen_mock({"status": "delivered", "id": "x"})

        with patch("urllib.request.urlopen", side_effect=capture_request):
            push_message(title="T", body="B")

        assert captured_data["body"]["priority"] == "normal"

    def test_push_default_source(self):
        """Default source is 'system'."""
        captured_data = {}

        def capture_request(req, timeout=None):
            captured_data["body"] = json.loads(req.data.decode())
            return _make_urlopen_mock({"status": "delivered", "id": "x"})

        with patch("urllib.request.urlopen", side_effect=capture_request):
            push_message(title="T", body="B")

        assert captured_data["body"]["source"] == "system"

    def test_push_auth_header(self):
        """Authorization header is sent correctly."""
        captured_headers = {}

        def capture_request(req, timeout=None):
            captured_headers["auth"] = req.headers.get("Authorization")
            return _make_urlopen_mock({"status": "delivered", "id": "x"})

        with patch("urllib.request.urlopen", side_effect=capture_request):
            push_message(title="T", body="B")

        assert captured_headers["auth"] == "Bearer hermes-bridge-dev-key"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestPushMessageErrors:
    def test_missing_body(self):
        """Empty body returns error."""
        result = json.loads(push_message(title="T", body=""))
        assert "error" in result
        assert "body is required" in result["error"]

    def test_whitespace_only_body(self):
        """Whitespace-only body returns error."""
        result = json.loads(push_message(title="T", body="   "))
        assert "error" in result

    def test_invalid_priority(self):
        """Invalid priority returns error with valid options."""
        result = json.loads(push_message(title="T", body="B", priority="super-high"))
        assert "error" in result
        assert "Invalid priority" in result["error"]

    def test_invalid_source(self):
        """Invalid source returns error with valid options."""
        result = json.loads(push_message(title="T", body="B", source="telegram"))
        assert "error" in result
        assert "Invalid source" in result["error"]

    def test_bridge_unreachable(self):
        """URLError returns connection error."""
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            result = json.loads(push_message(title="T", body="B"))

        assert "error" in result
        assert "Cannot reach" in result["error"]

    def test_bridge_401(self):
        """HTTP 401 returns auth error."""
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "http://bridge:8655/push", 401, "Unauthorized", {}, None
        )):
            result = json.loads(push_message(title="T", body="B"))

        assert "error" in result
        assert "401" in result["error"]

    def test_bridge_500(self):
        """HTTP 500 returns server error."""
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "http://bridge:8655/push", 500, "Internal Server Error", {}, None
        )):
            result = json.loads(push_message(title="T", body="B"))

        assert "error" in result
        assert "500" in result["error"]

    def test_unexpected_exception(self):
        """Unexpected exceptions are caught and returned as error."""
        with patch("urllib.request.urlopen", side_effect=ValueError("Something broke")):
            result = json.loads(push_message(title="T", body="B"))

        assert "error" in result
        assert "ValueError" in result["error"]


# ---------------------------------------------------------------------------
# Check function tests
# ---------------------------------------------------------------------------

class TestCheckFunction:
    def test_bridge_up(self):
        """_check_push_message returns True when /health returns ok."""
        mock = _make_urlopen_mock({"status": "ok"})

        with patch("urllib.request.urlopen", return_value=mock):
            assert _check_push_message() is True

    def test_bridge_down(self):
        """_check_push_message returns False when /health fails."""
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            assert _check_push_message() is False

    def test_bridge_health_not_ok(self):
        """_check_push_message returns False when /health returns non-ok status."""
        mock = _make_urlopen_mock({"status": "degraded"})

        with patch("urllib.request.urlopen", return_value=mock):
            assert _check_push_message() is False

    def test_bridge_health_timeout(self):
        """_check_push_message returns False on timeout."""
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            assert _check_push_message() is False


# ---------------------------------------------------------------------------
# Valid values
# ---------------------------------------------------------------------------

class TestValidValues:
    def test_all_priorities_accepted(self):
        """All valid priorities are accepted."""
        for priority in VALID_PRIORITIES:
            mock = _make_urlopen_mock({"status": "delivered", "id": "x"})
            with patch("urllib.request.urlopen", return_value=mock):
                result = json.loads(push_message(title="T", body="B", priority=priority))
                assert "error" not in result, f"Priority '{priority}' should be valid"

    def test_all_sources_accepted(self):
        """All valid sources are accepted."""
        for source in VALID_SOURCES:
            mock = _make_urlopen_mock({"status": "delivered", "id": "x"})
            with patch("urllib.request.urlopen", return_value=mock):
                result = json.loads(push_message(title="T", body="B", source=source))
                assert "error" not in result, f"Source '{source}' should be valid"
