"""Shared test fixtures for the Notification Bridge test suite."""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
