"""HTTP transport layer — auth, timeouts, retries, error mapping.

Every API call goes through Transport. client.py never touches httpx directly.
"""

from __future__ import annotations

import time

import httpx

from jarvislabs.constants import (
    HTTP_TIMEOUT_CONNECT_S,
    HTTP_TIMEOUT_READ_S,
    MAX_RETRIES,
    REGION_URLS,
    RETRY_STATUS_CODES,
)
from jarvislabs.exceptions import (
    APIError,
    AuthError,
    InsufficientBalanceError,
    NotFoundError,
)


class Transport:
    """Thin httpx wrapper with auth, retries, and error mapping."""

    def __init__(self, token: str, base_url: str | None = None) -> None:
        self._token = token
        self._base_url = base_url or REGION_URLS["india-01"]
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=HTTP_TIMEOUT_CONNECT_S,
                read=HTTP_TIMEOUT_READ_S,
                write=HTTP_TIMEOUT_READ_S,
                pool=HTTP_TIMEOUT_READ_S,
            ),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        base_url: str | None = None,
    ) -> dict | list:
        """Send an HTTP request with retries on transient failures."""
        url = (base_url or self._base_url).rstrip("/") + "/" + path.lstrip("/")

        # POST/PUT/DELETE are not idempotent — retrying after timeout can cause
        # double operations (e.g. pause already started → retry → "Invalid Machine ID")
        safe_to_retry = method.upper() in {"GET", "HEAD", "OPTIONS"}

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._client.request(method, url, json=json, params=params)
            except httpx.HTTPError as exc:
                if safe_to_retry and attempt < MAX_RETRIES:
                    time.sleep(2**attempt)
                    continue
                label = "timed out" if isinstance(exc, httpx.TimeoutException) else "Connection failed"
                raise APIError(0, f"Request {label}: {method} {path}") from exc

            # Retry on transient HTTP errors (429, 5xx) — only for idempotent methods
            if resp.status_code in RETRY_STATUS_CODES and safe_to_retry and attempt < MAX_RETRIES:
                time.sleep(2**attempt)  # 1s, 2s, 4s
                continue

            self._raise_for_status(resp)
            return resp.json()

        # Unreachable — loop always returns or raises — but keeps the type checker happy
        raise AssertionError("unreachable")

    def _raise_for_status(self, resp: httpx.Response) -> None:
        """Map HTTP status codes to typed SDK exceptions."""
        if resp.is_success:
            return

        try:
            data = resp.json()
        except Exception:
            data = {}

        msg = _extract_error_message(data) or f"HTTP {resp.status_code}"

        if resp.status_code == 401:
            raise AuthError(msg)
        if resp.status_code == 403 and "balance" in msg.lower():
            raise InsufficientBalanceError(msg)
        if resp.status_code == 404:
            raise NotFoundError(msg)
        raise APIError(resp.status_code, msg)

    def close(self) -> None:
        self._client.close()


def _extract_error_message(data: dict | list) -> str:
    """Handle {"message": ...} vs {"detail": ...} vs {"error": ...} response shapes."""
    if not isinstance(data, dict):
        return str(data) or "Unknown error"
    detail = data.get("detail")
    if isinstance(detail, list):
        # FastAPI RequestValidationError: [{"loc": [...], "msg": "...", "type": "..."}]
        return "; ".join(item.get("msg", "") if isinstance(item, dict) else str(item) for item in detail)
    return data.get("message") or data.get("error") or detail or "Unknown error"
