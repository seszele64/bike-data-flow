"""VaultClient wrapper for HashiCorp Vault.

This module provides a high-level client for interacting with HashiCorp Vault,
including authentication, secret operations, and caching support.
"""

import logging
from datetime import datetime, timedelta
from functools import lru_cache
from threading import Lock
from typing import Any, Optional

import hvac
from hvac.exceptions import (
    InvalidPath,
    VaultDown,
    VaultNotInitialized,
)

from wrm_pipeline.wrm_pipeline.vault.exceptions import (
    VaultAuthenticationError,
    VaultConnectionError,
    VaultError,
    VaultSecretNotFoundError,
    VaultSealedError,
    VaultUninitializedError,
)
from wrm_pipeline.wrm_pipeline.vault.models import (
    AuditLog,
    AuditOperation,
    Secret,
    SecretMetadata,
    VaultConnectionConfig,
    VaultHealth,
    VaultHealthStatus,
    VaultSecret,
)

logger = logging.getLogger(__name__)


class CacheEntry:
    """Represents a cached secret entry."""

    def __init__(self, secret: VaultSecret, expires_at: datetime):
        self.secret = secret
        self.expires_at = expires_at

    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        return datetime.utcnow() > self.expires_at


class VaultClient:
    """High-level client for HashiCorp Vault operations.

    This client provides:
    - Automatic authentication and token management
    - Secret caching with TTL
    - Connection pooling and retry logic
    - Health checking

    Example:
        >>> config = VaultConnectionConfig(
        ...     vault_addr="https://vault.example.com:8200",
        ...     auth_method="approle",
        ...     role_id="my-role-id",
        ...     secret_id="my-secret-id",
        ... )
        >>> client = VaultClient(config)
        >>> secret = client.get_secret("bike-data-flow/production/database")
    """

    def __init__(
        self,
        config: VaultConnectionConfig,
        verify: Optional[bool] = None,
    ):
        """Initialize Vault client.

        Args:
            config: Connection configuration
            verify: TLS verification (overrides config if provided)
        """
        self.config = config
        self._verify = verify if verify is not None else config.verify
        self._client: Optional[hvac.Client] = None
        self._auth_lock = Lock()
        self._cache: dict[str, CacheEntry] = {}
        self._cache_lock = Lock()
        self._initialized = False

    def _get_client(self) -> hvac.Client:
        """Get or create the underlying hvac client."""
        if self._client is None:
            with self._auth_lock:
                if self._client is None:
                    self._client = hvac.Client(
                        url=self.config.vault_addr,
                        verify=self._verify,
                        timeout=self.config.timeout,
                    )
        return self._client

    def _authenticate(self) -> None:
        """Authenticate with Vault using the configured method."""
        client = self._get_client()

        if self.config.auth_method == "approle":
            if not self.config.role_id:
                raise VaultAuthenticationError(
                    "AppRole authentication requires role_id"
                )
            if not self.config.secret_id:
                raise VaultAuthenticationError(
                    "AppRole authentication requires secret_id"
                )
            response = client.auth.approle.login(
                role_id=self.config.role_id,
                secret_id=self.config.secret_id,
            )
            if "auth" not in response or "client_token" not in response.get(
                "auth", {}
            ):
                raise VaultAuthenticationError(
                    "AppRole login did not return a token"
                )
            client.token = response["auth"]["client_token"]

        elif self.config.auth_method == "token":
            if not self.config.token:
                raise VaultAuthenticationError(
                    "Token authentication requires token"
                )
            client.token = self.config.token

        elif self.config.auth_method == "kubernetes":
            # Kubernetes auth uses the service account token automatically
            # mounted at /var/run/secrets/kubernetes.io/serviceaccount/token
            response = client.auth.kubernetes.login(role=self.config.role_id or "default")
            if "auth" not in response or "client_token" not in response.get(
                "auth", {}
            ):
                raise VaultAuthenticationError(
                    "Kubernetes login did not return a token"
                )
            client.token = response["auth"]["client_token"]

        else:
            raise VaultAuthenticationError(
                f"Unsupported authentication method: {self.config.auth_method}"
            )

        self._initialized = True
        logger.info("Successfully authenticated to Vault")

    def _ensure_authenticated(self) -> None:
        """Ensure the client is authenticated."""
        if not self._initialized:
            self._authenticate()

    def _get_cache_key(self, path: str) -> str:
        """Generate cache key for a secret path."""
        return f"secret:{path}"

    def _get_from_cache(self, path: str) -> Optional[VaultSecret]:
        """Get secret from cache if not expired."""
        cache_key = self._get_cache_key(path)
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry is not None and not entry.is_expired():
                logger.debug(f"Cache hit for {path}")
                return entry.secret
            elif entry is not None:
                logger.debug(f"Cache expired for {path}")
                del self._cache[cache_key]
        return None

    def _set_cache(self, secret: VaultSecret) -> None:
        """Cache a secret with TTL."""
        cache_key = self._get_cache_key(secret.path)
        expires_at = datetime.utcnow() + timedelta(
            seconds=self.config.cache_ttl
        )
        entry = CacheEntry(secret, expires_at)
        with self._cache_lock:
            self._cache[cache_key] = entry
        logger.debug(f"Cached secret: {secret.path}")

    def _clear_cache(self, path: Optional[str] = None) -> None:
        """Clear cache entries.

        Args:
            path: Specific path to clear, or None to clear all
        """
        with self._cache_lock:
            if path is None:
                self._cache.clear()
                logger.info("Cleared all cached secrets")
            else:
                cache_key = self._get_cache_key(path)
                if cache_key in self._cache:
                    del self._cache[cache_key]
                    logger.info(f"Cleared cache for {path}")

    def is_initialized(self) -> bool:
        """Check if Vault is initialized.

        Returns:
            True if Vault is initialized, False otherwise
        """
        try:
            client = self._get_client()
            health = client.sys.read_health_status()
            return health.get("initialized", False)
        except VaultNotInitialized:
            return False
        except Exception as e:
            logger.error(f"Failed to check Vault initialization: {e}")
            return False

    def get_health(self) -> VaultHealth:
        """Get Vault server health status.

        Returns:
            VaultHealth status information

        Raises:
            VaultConnectionError: If Vault is unreachable
        """
        try:
            client = self._get_client()
            status = client.sys.read_health_status()

            # Map status to enum
            status_str = status.get("status", "unknown")
            try:
                vault_status = VaultHealthStatus(status_str)
            except ValueError:
                vault_status = VaultHealthStatus.STANDBY

            # Parse server time
            server_time_str = status.get("server_time_utc", "")
            try:
                server_time = datetime.fromisoformat(
                    server_time_str.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                server_time = datetime.utcnow()

            return VaultHealth(
                status=vault_status,
                version=status.get("version", "unknown"),
                cluster_id=status.get("cluster_id"),
                cluster_name=status.get("cluster_name"),
                replication_mode=status.get("replication_mode"),
                server_time_utc=server_time,
            )

        except VaultDown as e:
            raise VaultConnectionError(
                f"Vault server is unreachable: {e}"
            ) from e
        except VaultNotInitialized as e:
            raise VaultUninitializedError(
                "Vault is not initialized"
            ) from e

    def get_secret(
        self,
        path: str,
        version: Optional[int] = None,
        use_cache: bool = True,
    ) -> VaultSecret:
        """Read a secret from Vault.

        Args:
            path: Secret path (e.g., "bike-data-flow/production/database")
            version: Optional specific version to retrieve
            use_cache: Whether to use cached value (default: True)

        Returns:
            VaultSecret with the secret data

        Raises:
            VaultSecretNotFoundError: If secret doesn't exist
            VaultAuthenticationError: If authentication fails
            VaultConnectionError: If Vault is unreachable
        """
        # Check cache first
        if use_cache:
            cached = self._get_from_cache(path)
            if cached is not None:
                return cached

        self._ensure_authenticated()

        try:
            client = self._get_client()

            # Convert path format for KV v2
            # Input: "bike-data-flow/production/database"
            # Vault: "secret/data/bike-data-flow/production/database"
            mount_point = "secret"

            response = client.secrets.kv.v2.read_secret_version(
                path=path,
                version=version,
                mount_point=mount_point,
            )

            data = response.get("data", {}).get("data", {})
            metadata = response.get("data", {}).get("metadata", {})

            secret = VaultSecret(
                path=path,
                data=data,
                version=metadata.get("version"),
            )

            # Cache the result
            if use_cache:
                self._set_cache(secret)

            return secret

        except InvalidPath as e:
            raise VaultSecretNotFoundError(
                path=path,
                message=f"Secret not found at path: {path}",
            ) from e
        except VaultDown as e:
            raise VaultConnectionError(
                f"Vault server is unreachable: {e}"
            ) from e

    def write_secret(
        self,
        path: str,
        data: dict[str, Any],
        options: Optional[dict] = None,
    ) -> VaultSecret:
        """Write a secret to Vault.

        Args:
            path: Secret path (e.g., "bike-data-flow/production/database")
            data: Secret data to store
            options: Optional write options (cas, etc.)

        Returns:
            VaultSecret with the written data

        Raises:
            VaultError: If write fails
        """
        self._ensure_authenticated()

        try:
            client = self._get_client()

            mount_point = "secret"

            # Strip "secret/data/" prefix if present
            if path.startswith("secret/data/"):
                full_path = path.replace("secret/data/", "", 1)
            elif path.startswith("secret/"):
                full_path = path.replace("secret/", "", 1)
            else:
                full_path = path

            client.secrets.kv.v2.create_or_update_secret(
                path=full_path,
                secret=data,
                mount_point=mount_point,
                options=options,
            )

            # Invalidate cache
            self._clear_cache(path)

            return VaultSecret(
                path=path,
                data=data,
                version=None,  # Will be retrieved on read
            )

        except Exception as e:
            raise VaultError(f"Failed to write secret: {e}") from e

    def delete_secret(
        self,
        path: str,
        versions: Optional[list[int]] = None,
    ) -> None:
        """Delete a secret from Vault.

        Args:
            path: Secret path
            versions: Specific versions to delete (default: all)

        Raises:
            VaultError: If delete fails
        """
        self._ensure_authenticated()

        try:
            client = self._get_client()

            mount_point = "secret"

            if path.startswith("secret/data/"):
                full_path = path.replace("secret/data/", "", 1)
            elif path.startswith("secret/"):
                full_path = path.replace("secret/", "", 1)
            else:
                full_path = path

            if versions:
                # Delete specific versions
                client.secrets.kv.v2.delete_versions(
                    path=full_path,
                    versions=versions,
                    mount_point=mount_point,
                )
            else:
                # Delete all versions and the secret
                client.secrets.kv.v2.delete_metadata_and_all_versions(
                    path=full_path,
                    mount_point=mount_point,
                )

            # Invalidate cache
            self._clear_cache(path)

        except Exception as e:
            raise VaultError(f"Failed to delete secret: {e}") from e

    def list_secrets(self, path: str) -> list[str]:
        """List secret paths at a given path.

        Args:
            path: Parent path to list (e.g., "bike-data-flow/production/")

        Returns:
            List of secret names/paths

        Raises:
            VaultError: If list fails
        """
        self._ensure_authenticated()

        try:
            client = self._get_client()

            mount_point = "secret"

            # Ensure path ends with /
            if not path.endswith("/"):
                path = path + "/"

            # Strip "secret/data/" prefix if present
            if path.startswith("secret/data/"):
                full_path = path.replace("secret/data/", "", 1)
            else:
                full_path = path

            response = client.secrets.kv.v2.list_secrets(
                path=full_path,
                mount_point=mount_point,
            )

            keys = response.get("data", {}).get("keys", [])
            return keys

        except InvalidPath:
            return []
        except Exception as e:
            raise VaultError(f"Failed to list secrets: {e}") from e

    def get_secret_metadata(self, path: str) -> SecretMetadata:
        """Get metadata for a secret.

        Args:
            path: Secret path

        Returns:
            SecretMetadata with version and timing info

        Raises:
            VaultSecretNotFoundError: If secret doesn't exist
        """
        self._ensure_authenticated()

        try:
            client = self._get_client()

            mount_point = "secret"

            if path.startswith("secret/data/"):
                full_path = path.replace("secret/data/", "", 1)
            elif path.startswith("secret/"):
                full_path = path.replace("secret/", "", 1)
            else:
                full_path = path

            response = client.secrets.kv.v2.read_secret_metadata(
                path=full_path,
                mount_point=mount_point,
            )

            metadata = response.get("data", {})

            # Parse creation time
            created_time_str = metadata.get("created_time", "")
            try:
                created_time = datetime.fromisoformat(
                    created_time_str.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                created_time = datetime.utcnow()

            return SecretMetadata(
                secret_path=path,
                created_time=created_time,
                deletion_time=None,
                destroyed=metadata.get("destroyed", False),
                version=metadata.get("version", 1),
            )

        except InvalidPath as e:
            raise VaultSecretNotFoundError(
                path=path,
                message=f"Secret metadata not found for path: {path}",
            ) from e

    def invalidate_cache(self, path: Optional[str] = None) -> None:
        """Invalidate cached secrets.

        Args:
            path: Specific path to invalidate, or None for all
        """
        self._clear_cache(path)
        logger.info(f"Cache invalidated for: {path or 'all'}")

    def close(self) -> None:
        """Close the Vault client and cleanup resources."""
        if self._client is not None:
            # Revoke the token if we have one
            try:
                self._client.auth.token.self_revoke()
            except Exception:
                pass  # Ignore revocation errors
            self._client = None
        self._initialized = False
        self._cache.clear()
        logger.info("Vault client closed")

    def __enter__(self) -> "VaultClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    def __del__(self) -> None:
        """Destructor to cleanup."""
        try:
            self.close()
        except Exception:
            pass


@lru_cache(maxsize=32)
def get_cached_client(config_tuple: tuple) -> VaultClient:
    """Get a cached Vault client instance.

    This is a convenience function for creating clients that can be
    cached by their configuration.

    Args:
        config_tuple: Serialized configuration tuple

    Returns:
        VaultClient instance
    """
    # Note: This is a simple implementation. For production, consider
    # using a proper caching mechanism with expiration.
    config = VaultConnectionConfig(
        vault_addr=config_tuple[0],
        auth_method=config_tuple[1],
        role_id=config_tuple[2] if len(config_tuple) > 2 else None,
        secret_id=config_tuple[3] if len(config_tuple) > 3 else None,
        token=config_tuple[4] if len(config_tuple) > 4 else None,
        namespace=config_tuple[5] if len(config_tuple) > 5 else None,
        timeout=config_tuple[6] if len(config_tuple) > 6 else 30,
        retries=config_tuple[7] if len(config_tuple) > 7 else 3,
        cache_ttl=config_tuple[8] if len(config_tuple) > 8 else 300,
    )
    return VaultClient(config)
