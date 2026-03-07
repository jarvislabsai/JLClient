"""Typed exception hierarchy for the SDK.

CLI maps these to exit codes + human-readable messages.
SDK users catch specific types without knowing HTTP internals.
"""


class JarvislabsError(Exception):
    """Base for all SDK errors."""


class AuthError(JarvislabsError):
    """401 — invalid or missing API token."""


class NotFoundError(JarvislabsError):
    """404 — instance, SSH key, or resource not found."""


class InsufficientBalanceError(JarvislabsError):
    """403 — not enough balance to perform the action."""


class ValidationError(JarvislabsError):
    """Client-side validation failure."""


class SSHError(JarvislabsError):
    """Base error for local SSH command parsing/execution failures."""


class SSHConnectionError(SSHError):
    """SSH transport/connectivity failure."""


class SSHAuthError(SSHError):
    """SSH authentication failure."""


class APIError(JarvislabsError):
    """HTTP error from the backend that doesn't fit a specific category."""

    def __init__(self, status_code: int, message: str, error_code: str | None = None):
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        super().__init__(message)
