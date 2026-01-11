"""Custom exceptions for HashiCorp Vault integration.

This module defines all custom exceptions used by the Vault client
for consistent error handling.
"""

from typing import Optional


class VaultError(Exception):
    """Base exception for Vault-related errors."""

    def __init__(self, message: str, details: Optional[dict] = None):
        """Initialize Vault error.

        Args:
            message: Error message
            details: Additional error details
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """Return error string representation."""
        if self.details:
            return f"{self.message} (details: {self.details})"
        return self.message


class VaultConnectionError(VaultError):
    """Raised when connection to Vault fails."""

    def __init__(
        self,
        message: str = "Failed to connect to Vault server",
        details: Optional[dict] = None,
    ):
        super().__init__(message, details)


class VaultAuthenticationError(VaultError):
    """Raised when authentication to Vault fails."""

    def __init__(
        self,
        message: str = "Vault authentication failed",
        details: Optional[dict] = None,
    ):
        super().__init__(message, details)


class VaultSecretNotFoundError(VaultError):
    """Raised when a secret is not found in Vault."""

    def __init__(
        self,
        path: str,
        message: Optional[str] = None,
        details: Optional[dict] = None,
    ):
        full_message = message or f"Secret not found at path: {path}"
        super().__init__(full_message, details)
        self.path = path


class VaultPermissionError(VaultError):
    """Raised when permission is denied to access a secret."""

    def __init__(
        self,
        path: str,
        operation: str,
        message: Optional[str] = None,
        details: Optional[dict] = None,
    ):
        full_message = (
            message or f"Permission denied for {operation} on path: {path}"
        )
        super().__init__(full_message, details)
        self.path = path
        self.operation = operation


class VaultSealedError(VaultError):
    """Raised when Vault is sealed and cannot serve requests."""

    def __init__(
        self,
        message: str = "Vault is sealed. Unseal required.",
        details: Optional[dict] = None,
    ):
        super().__init__(message, details)


class VaultUninitializedError(VaultError):
    """Raised when Vault is not initialized."""

    def __init__(
        self,
        message: str = "Vault is not initialized. Initialization required.",
        details: Optional[dict] = None,
    ):
        super().__init__(message, details)


class VaultVersionError(VaultError):
    """Raised when Vault version is incompatible."""

    def __init__(
        self,
        message: str = "Incompatible Vault version",
        details: Optional[dict] = None,
    ):
        super().__init__(message, details)


class VaultCacheError(VaultError):
    """Raised when cache operations fail."""

    def __init__(
        self,
        message: str = "Cache operation failed",
        details: Optional[dict] = None,
    ):
        super().__init__(message, details)


class VaultValidationError(VaultError):
    """Raised when configuration validation fails."""

    def __init__(
        self,
        message: str,
        details: Optional[dict] = None,
    ):
        super().__init__(message, details)
