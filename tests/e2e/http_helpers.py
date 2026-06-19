"""HTTP client helpers for Bridge API operations.

This module provides:
- Bridge HTTP client class with convenience methods
- Retry logic for transient failures
- Default values for common operations
"""

import time
import requests
from typing import Optional, Dict, List


class BridgeHttpClient:
    """HTTP client for Bridge API operations."""
    
    def __init__(self, base_url: str = "http://localhost:8655", auth_key: str = "hermes-bridge-dev-key"):
        self.base_url = base_url.rstrip("/")
        self.auth_key = auth_key
        self.auth_headers = {"Authorization": f"Bearer {auth_key}"}
        self.max_retries = 3
        self.retry_delay = 1.0
    
    def _request_with_retry(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make HTTP request with retry logic."""
        url = f"{self.base_url}{endpoint}"
        
        for attempt in range(self.max_retries):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=self.auth_headers,
                    timeout=5,
                    **kwargs
                )
                return response
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))  # Exponential backoff
                    continue
                raise e
        
        raise RuntimeError("Unexpected retry failure")
    
    def push(self, title: str, body: str, **kwargs) -> Dict:
        """POST /push - Send a push message."""
        payload = {
            "title": title,
            "body": body,
            **kwargs
        }
        response = self._request_with_retry("POST", "/push", json=payload)
        response.raise_for_status()
        return response.json()
    
    def get_messages(self, limit: int = 50, offset: int = 0, voice: bool = False) -> List[Dict]:
        """GET /messages - Get recent messages."""
        params = {"limit": limit, "offset": offset}
        if voice:
            params["voice"] = "true"
        
        response = self._request_with_retry("GET", "/messages", params=params)
        response.raise_for_status()
        return response.json()
    
    def get_message(self, msg_id: str) -> Dict:
        """GET /messages/{id} - Get a single message."""
        response = self._request_with_retry("GET", f"/messages/{msg_id}")
        response.raise_for_status()
        return response.json()
    
    def mark_read(self, msg_id: str) -> Dict:
        """POST /messages/{id}/read - Mark message as read."""
        response = self._request_with_retry("POST", f"/messages/{msg_id}/read")
        response.raise_for_status()
        return response.json()
    
    def get_stats(self) -> Dict:
        """GET /stats - Get bridge statistics."""
        response = self._request_with_retry("GET", "/stats")
        response.raise_for_status()
        return response.json()
    
    def get_last_delivery(self) -> Dict:
        """GET /test/last-delivery - Test endpoint for last delivery."""
        response = self._request_with_retry("GET", "/test/last-delivery")
        response.raise_for_status()
        return response.json()
    
    def reset_test(self) -> Dict:
        """POST /test/reset - Clear all messages and clients."""
        response = self._request_with_retry("POST", "/test/reset")
        response.raise_for_status()
        return response.json()
    
    def health_check(self) -> bool:
        """Check if bridge is healthy."""
        try:
            response = self._request_with_retry("GET", "/health")
            return response.status_code == 200
        except Exception:
            return False


def wait_for_bridge_ready(host: str = "localhost", port: int = 8655, timeout: int = 30) -> bool:
    """Wait for the Bridge to be ready."""
    client = BridgeHttpClient(base_url=f"http://{host}:{port}")
    
    start = time.time()
    while time.time() - start < timeout:
        if client.health_check():
            return True
        time.sleep(0.5)
    
    return False


def wait_for_message_delivery(client: BridgeHttpClient, msg_id: str, timeout: int = 10) -> bool:
    """Wait for a message to be delivered (tracked in delivered_to)."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            msg = client.get_message(msg_id)
            if msg.get("delivered_to"):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    
    return False
