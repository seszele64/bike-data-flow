"""Dagster resource for HashiCorp Vault secrets management.

This module provides a Dagster resource that integrates with HashiCorp Vault
for secure secret retrieval in pipeline execution.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from dagster import ResourceDefinition

from wrm_pipeline.wrm_pipeline.vault.client import VaultClient
from wrm_pipeline.wrm_pipeline.vault.exceptions import (
    VaultAuthenticationError,
    VaultConnectionError,
    VaultError,
    VaultSecretNotFoundError,
)
from wrm_pipeline.wrm_pipeline.vault.models import VaultConnectionConfig, VaultHealth

logger = logging.getLogger(__name__)


@dataclass
class VaultSecretsResourceConfig:
    """Configuration for the Vault secrets resource.

    Attributes:
        vault_addr: Vault server address (e.g., https://vault.example.com:8200)
        auth_method: Authentication method (approle, token, kubernetes)
        role_id: AppRole role ID (if using approle auth)
        secret_id: AppRole secret ID (if using approle auth)
        token: Vault token (if using token auth)
        namespace: Enterprise namespace (optional)
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        cache_ttl: Cache TTL in seconds (default: 300 = 5 minutes)
        verify: Whether to verify TLS certificates
    """

    vault_addr: str = "https://vault.internal.bike-data-flow.com:8200"
    auth_method: str = "approle"
    role_id: Optional[str] = None
    secret_id: Optional[str] = None
    token: Optional[str] = None
    namespace: Optional[str] = None
    timeout: int = 30
    retries: int = 3
    cache_ttl: int = 300
    verify: bool = True

    def to_connection_config(self) -> VaultConnectionConfig:
        """Convert to VaultConnectionConfig for VaultClient."""
        return VaultConnectionConfig(
            vault_addr=self.vault_addr,
            auth_method=self.auth_method,
            role_id=self.role_id,
            secret_id=self.secret_id,
            token=self.token,
            namespace=self.namespace,
            timeout=self.timeout,
            retries=self.retries,
            cache_ttl=self.cache_ttl,
            verify=self.verify,
        )


class VaultSecretsResource:
    """Dagster resource for retrieving secrets from HashiCorp Vault.

    This resource provides secure access to secrets stored in Vault,
    with caching and error handling for reliable pipeline execution.

    Example:
        >>> from dagster import Definitions, env_var
        >>> from wrm_pipeline.wrm_pipeline.vault.resource import (
        ...     VaultSecretsResource,
        ...     VaultSecretsResourceConfig
        ... )
        >>>
        >>> vault_resource = ResourceDefinition(
        ...     resource_def=VaultSecretsResource,
        ...     config_schema=VaultSecretsResourceConfig,
        ...     description="Resource for retrieving secrets from HashiCorp Vault"
        ... )

    Attributes:
        client: The underlying VaultClient instance.
    """

    def __init__(self, config: VaultSecretsResourceConfig):
        """Initialize the Vault secrets resource.

        Args:
            config: Configuration for the Vault connection.
        """
        self.config = config
        self._client: Optional[VaultClient] = None
        self._cache: dict[str, tuple[Any, float]] = {}

    @property
    def client(self) -> VaultClient:
        """Get or create the Vault client.

        Returns:
            VaultClient instance for Vault operations.

        Raises:
            VaultAuthenticationError: If authentication fails.
            VaultConnectionError: If Vault is unreachable.
        """
        if self._client is None:
            connection_config = self.config.to_connection_config()
            self._client = VaultClient(connection_config, verify=self.config.verify)
        return self._client

    def _get_cache_key(self, path: str, version: Optional[int] = None) -> str:
        """Generate a cache key for a secret path.

        Args:
            path: Secret path.
            version: Optional secret version.

        Returns:
            Cache key string.
        """
        return f"{path}:{version}" if version else path

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cached value is still valid.

        Args:
            cache_key: Cache key to check.

        Returns:
            True if cache entry exists and is not expired.
        """
        if cache_key not in self._cache:
            return False
        _, expiry = self._cache[cache_key]
        return expiry > time.time()

    def _get_from_cache(self, cache_key: str) -> Optional[Any]:
        """Get value from cache if valid.

        Args:
            cache_key: Cache key to retrieve.

        Returns:
            Cached value or None if not found/expired.
        """
        if self._is_cache_valid(cache_key):
            value, _ = self._cache[cache_key]
            logger.debug(f"Cache hit for {cache_key}")
            return value
        return None

    def _set_cache(self, cache_key: str, value: Any) -> None:
        """Store value in cache with TTL.

        Args:
            cache_key: Cache key.
            value: Value to cache.
        """
        expiry = time.time() + self.config.cache_ttl
        self._cache[cache_key] = (value, expiry)
        logger.debug(f"Cached {cache_key} with TTL {self.config.cache_ttl}s")

    # =====================================================================
    # Public API
    # =====================================================================

    def get_secret(
        self,
        path: str,
        version: Optional[int] = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Retrieve a secret from Vault.

        Retrieves a secret from the KV v2 secrets engine at the specified path.
        Results are cached according to the configured TTL.

        Args:
            path: Path to the secret (e.g., 'bike-data-flow/production/database').
            version: Optional specific version to retrieve.
            use_cache: Whether to use cached value (default: True).

        Returns:
            Dictionary containing secret data.

        Raises:
            VaultSecretNotFoundError: If secret doesn't exist.
            VaultAuthenticationError: If authentication fails.
            VaultConnectionError: If Vault is unreachable.
        """
        cache_key = self._get_cache_key(path, version)

        if use_cache:
            cached_value = self._get_from_cache(cache_key)
            if cached_value is not None:
                return cached_value

        try:
            vault_secret = self.client.get_secret(
                path=path,
                version=version,
                use_cache=use_cache,
            )

            result = vault_secret.data

            if use_cache:
                self._set_cache(cache_key, result)

            return result

        except VaultSecretNotFoundError:
            raise
        except VaultAuthenticationError:
            raise
        except VaultConnectionError:
            raise
        except VaultError as e:
            raise VaultConnectionError(f"Failed to retrieve secret: {e}") from e

    def get_database_credentials(
        self,
        database_path: str = "bike-data-flow/production/database",
    ) -> dict[str, str]:
        """Convenience method for getting database credentials.

        Retrieves database credentials from Vault and returns them in a
        structured format suitable for connection establishment.

        Args:
            database_path: Path to database credentials secret.
                Default: 'bike-data-flow/production/database'

        Returns:
            Dictionary containing:
                - username: Database username
                - password: Database password
                - host: Database host address
                - port: Database port (defaults to '5432' for PostgreSQL)
                - database: Database name

        Raises:
            VaultSecretNotFoundError: If credentials secret doesn't exist.
            VaultAuthenticationError: If authentication fails.
            VaultConnectionError: If Vault is unreachable.
        """
        credentials = self.get_secret(database_path)

        return {
            "username": credentials.get("username"),
            "password": credentials.get("password"),
            "host": credentials.get("host"),
            "port": str(credentials.get("port", "5432")),
            "database": credentials.get("database"),
        }

    def get_api_key(
        self,
        provider: str,
        api_key_path: str = "bike-data-flow/production/api-keys",
    ) -> dict[str, Any]:
        """Get an API key for a specific provider.

        Retrieves an API key from Vault along with metadata such as
        expiry date and service name.

        Args:
            provider: Provider name (e.g., 'openweather', 'citybikes').
            api_key_path: Base path for API keys (optional).

        Returns:
            Dictionary containing:
                - api_key: The API key string
                - service_name: Name of the service
                - expiry_date: Expiry date if applicable
                - created_date: Creation date

        Raises:
            VaultSecretNotFoundError: If API key doesn't exist.
            VaultAuthenticationError: If authentication fails.
            VaultConnectionError: If Vault is unreachable.
        """
        path = f"{api_key_path}/{provider}"

        secret = self.get_secret(path)

        return {
            "api_key": secret.get("api_key"),
            "service_name": secret.get("service_name", provider),
            "expiry_date": secret.get("expiry_date"),
            "created_date": secret.get("created_date"),
        }

    def list_secrets(
        self,
        path: str,
        use_cache: bool = True,
    ) -> list[str]:
        """List secret paths under a given path.

        Lists all secrets at the specified path in the KV v2 secrets engine.

        Args:
            path: Parent path to list secrets under (e.g., 'bike-data-flow/production/').
            use_cache: Whether to use cached value (default: True).

        Returns:
            List of secret names/paths. Empty list if path doesn't exist.

        Raises:
            VaultAuthenticationError: If authentication fails.
            VaultConnectionError: If Vault is unreachable.
        """
        cache_key = self._get_cache_key(f"list:{path}")

        if use_cache:
            cached_value = self._get_from_cache(cache_key)
            if cached_value is not None:
                return cached_value

        try:
            secrets = self.client.list_secrets(path)

            if use_cache:
                self._set_cache(cache_key, secrets)

            return secrets

        except VaultAuthenticationError:
            raise
        except VaultConnectionError:
            raise
        except VaultError as e:
            raise VaultConnectionError(f"Failed to list secrets: {e}") from e

    def health_check(self) -> dict[str, Any]:
        """Perform a health check on the Vault server.

        Checks the Vault server status including initialization state,
        seal status, and performance metrics.

        Returns:
            Dictionary containing:
                - healthy: True if Vault is healthy and unsealed
                - initialized: Whether Vault is initialized
                - sealed: Whether Vault is sealed
                - status: Health status string
                - version: Vault version
                - latency_ms: Response latency in milliseconds

        Raises:
            VaultConnectionError: If Vault is unreachable.
        """
        start_time = time.time()

        try:
            health: VaultHealth = self.client.get_health()

            latency_ms = (time.time() - start_time) * 1000

            return {
                "healthy": (
                    health.status.value != "sealed"
                    and health.status.value != "disabled"
                ),
                "initialized": health.status.value != "uninitialized",
                "sealed": health.status.value == "sealed",
                "status": health.status.value,
                "version": health.version,
                "cluster_id": health.cluster_id,
                "cluster_name": health.cluster_name,
                "latency_ms": round(latency_ms, 2),
                "server_time_utc": health.server_time_utc.isoformat(),
            }

        except VaultConnectionError:
            latency_ms = (time.time() - start_time) * 1000
            return {
                "healthy": False,
                "initialized": False,
                "sealed": True,
                "status": "unreachable",
                "version": None,
                "cluster_id": None,
                "cluster_name": None,
                "latency_ms": round(latency_ms, 2),
                "server_time_utc": None,
            }

    def clear_cache(self) -> None:
        """Clear the secret cache.

        Invalidates all cached secrets. Use this when secrets are known
        to have been rotated or updated.
        """
        self._cache.clear()
        self.client.invalidate_cache()
        logger.info("Vault resource cache cleared")

    def close(self) -> None:
        """Close the resource and cleanup resources.

        Closes the underlying Vault client and clears the cache.
        """
        if self._client is not None:
            self._client.close()
            self._client = None
        self._cache.clear()
        logger.info("VaultSecretsResource closed")


# =====================================================================
# Resource Definition Factory
# =====================================================================

def vault_secrets_resource(
    config: Optional[VaultSecretsResourceConfig] = None,
) -> ResourceDefinition:
    """Create a Dagster resource definition for Vault secrets.

    This factory function creates a ResourceDefinition that can be used
    in Dagster Definitions.

    Args:
        config: Optional pre-configured configuration. If not provided,
                default configuration will be used.

    Returns:
        ResourceDefinition configured for Vault secrets.

    Example:
        >>> from dagster import Definitions, env_var
        >>> from wrm_pipeline.wrm_pipeline.vault.resource import vault_secrets_resource
        >>>
        >>> defs = Definitions(
        ...     resources={
        ...         "vault": vault_secrets_resource().configured({
        ...             "vault_addr": env_var("VAULT_ADDR"),
        ...             "auth_method": "approle",
        ...             "role_id": env_var("VAULT_ROLE_ID"),
        ...             "secret_id": env_var("VAULT_SECRET_ID"),
        ...         })
        ...     },
        ...     assets=[...],
        ... )
    """
    effective_config = config if config is not None else VaultSecretsResourceConfig()

    def create_resource(context) -> VaultSecretsResource:
        """Resource creation function for Dagster."""
        init_config = effective_config

        # Allow runtime config override from Dagster context
        if context:
            run_config = context.resource_config or {}
            init_config = VaultSecretsResourceConfig(
                vault_addr=run_config.get("vault_addr", effective_config.vault_addr),
                auth_method=run_config.get("auth_method", effective_config.auth_method),
                role_id=run_config.get("role_id", effective_config.role_id),
                secret_id=run_config.get("secret_id", effective_config.secret_id),
                token=run_config.get("token", effective_config.token),
                namespace=run_config.get("namespace", effective_config.namespace),
                timeout=run_config.get("timeout", effective_config.timeout),
                retries=run_config.get("retries", effective_config.retries),
                cache_ttl=run_config.get("cache_ttl", effective_config.cache_ttl),
                verify=run_config.get("verify", effective_config.verify),
            )

        resource = VaultSecretsResource(init_config)
        logger.info(f"VaultSecretsResource initialized with config: {init_config}")
        return resource

    return ResourceDefinition(
        resource_def=create_resource,
        config_schema=VaultSecretsResourceConfig,
        description="Resource for retrieving secrets from HashiCorp Vault with caching",
    )
