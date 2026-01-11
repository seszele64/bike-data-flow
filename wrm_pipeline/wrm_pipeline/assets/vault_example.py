"""Example assets demonstrating Vault secret integration.

This module provides example assets that show how to use the VaultSecretsResource
to securely retrieve secrets from HashiCorp Vault in Dagster pipelines.
"""

import logging
from typing import Any, Dict, Optional

from dagster import (
    AssetExecutionContext,
    AssetKey,
    MaterializeResult,
    ConfigurableResource,
    asset,
    get_dagster_logger,
)
from pydantic import Field

from wrm_pipeline.wrm_pipeline.vault.resource import (
    VaultSecretsResource,
    VaultSecretsResourceConfig,
)

logger = logging.getLogger(__name__)


class VaultExampleConfig(ConfigurableResource):
    """Configuration for Vault example assets."""

    database_secret_path: str = Field(
        default="bike-data-flow/production/database",
        description="Path to database credentials in Vault",
    )
    api_keys_path: str = Field(
        default="bike-data-flow/production/api-keys",
        description="Base path for API keys in Vault",
    )
    cache_ttl: int = Field(
        default=300,
        description="Cache TTL in seconds for Vault secrets",
    )


@asset(
    name="vault_health_check",
    description="Check Vault server health and report status",
    required_resource_keys={"vault"},
    tags={"vault", "health", "monitoring"},
)
def vault_health_check(context: AssetExecutionContext) -> Dict[str, Any]:
    """Check Vault server health and return status information.

    This asset performs a health check on the Vault server and reports
    whether it is reachable, initialized, and unsealed.

    Returns:
        Dictionary containing:
            - healthy: Whether Vault is healthy
            - status: Vault status string
            - version: Vault version
            - latency_ms: Response latency in milliseconds

    Requires:
        - vault resource configured with valid credentials
    """
    vault_resource: VaultSecretsResource = context.resources.vault

    health = vault_resource.health_check()

    logger.info(
        f"Vault health check: status={health['status']}, "
        f"latency={health['latency_ms']}ms"
    )

    return health


@asset(
    name="database_credentials",
    description="Retrieve database credentials from Vault",
    required_resource_keys={"vault"},
    tags={"vault", "database", "credentials"},
)
def database_credentials(
    context: AssetExecutionContext,
    config: VaultExampleConfig,
) -> Dict[str, str]:
    """Retrieve database credentials from Vault.

    This asset retrieves database connection credentials stored in Vault
    and returns them in a structured format suitable for establishing
    database connections.

    Args:
        config: Configuration for the example assets.

    Returns:
        Dictionary containing:
            - username: Database username
            - password: Database password
            - host: Database host address
            - port: Database port
            - database: Database name

    Requires:
        - vault resource configured with valid credentials
        - Secret at path 'bike-data-flow/production/database' with fields:
            username, password, host, port, database
    """
    vault_resource: VaultSecretsResource = context.resources.vault

    credentials = vault_resource.get_database_credentials(
        database_path=config.database_secret_path
    )

    logger.info(
        f"Retrieved database credentials for host: {credentials['host']}"
    )

    return credentials


@asset(
    name="api_credentials",
    description="Retrieve API credentials from Vault for all providers",
    required_resource_keys={"vault"},
    tags={"vault", "api", "credentials"},
)
def api_credentials(
    context: AssetExecutionContext,
    config: VaultExampleConfig,
) -> Dict[str, Dict[str, Any]]:
    """Retrieve API credentials for all providers from Vault.

    This asset lists available API keys in Vault and retrieves them
    for use in external API calls.

    Args:
        config: Configuration for the example assets.

    Returns:
        Dictionary mapping provider names to their credentials:
            - api_key: The API key
            - service_name: Service name
            - expiry_date: Expiry date if applicable

    Requires:
        - vault resource configured with valid credentials
    """
    vault_resource: VaultSecretsResource = context.resources.vault

    # List all API key providers
    providers = vault_resource.list_secrets(config.api_keys_path)

    logger.info(f"Found {len(providers)} API key providers: {providers}")

    # Retrieve credentials for each provider
    all_credentials = {}
    for provider in providers:
        # Remove trailing slash if present
        provider = provider.rstrip("/")
        credentials = vault_resource.get_api_key(
            provider=provider,
            api_key_path=config.api_keys_path,
        )
        all_credentials[provider] = credentials
        logger.info(f"Retrieved API key for {provider}")

    return all_credentials


@asset(
    name="vault_secret_reader",
    description="Read a specific secret from Vault",
    required_resource_keys={"vault"},
    tags={"vault", "secret", "read"},
)
def vault_secret_reader(
    context: AssetExecutionContext,
    secret_path: str,
) -> Dict[str, Any]:
    """Read a specific secret from Vault.

    This asset demonstrates reading an arbitrary secret from Vault
    by specifying its path.

    Args:
        context: Dagster asset execution context.
        secret_path: Path to the secret in Vault.

    Returns:
        Secret data as a dictionary.

    Requires:
        - vault resource configured with valid credentials
    """
    vault_resource: VaultSecretsResource = context.resources.vault

    secret = vault_resource.get_secret(secret_path)

    logger.info(f"Retrieved secret from path: {secret_path}")

    return secret


@asset(
    name="vault_list_secrets",
    description="List all secrets at a given path in Vault",
    required_resource_keys={"vault"},
    tags={"vault", "list", "secrets"},
)
def vault_list_secrets(
    context: AssetExecutionContext,
    path: str,
) -> list[str]:
    """List secrets at a given path in Vault.

    This asset demonstrates listing secrets at a specific path
    to discover available secrets.

    Args:
        context: Dagster asset execution context.
        path: Parent path to list secrets under.

    Returns:
        List of secret names/paths.

    Requires:
        - vault resource configured with valid credentials
    """
    vault_resource: VaultSecretsResource = context.resources.vault

    secrets = vault_resource.list_secrets(path)

    logger.info(f"Found {len(secrets)} secrets at path {path}: {secrets}")

    return secrets


def create_vault_example_job():
    """Create a job that runs all Vault example assets.

    Returns:
        Job definition for testing Vault integration.
    """
    from dagster import JobDefinition, define_asset_job

    return define_asset_job(
        name="vault_example_job",
        description="Job to test Vault integration",
        selection=["vault_health_check", "database_credentials", "api_credentials"],
    )


# =====================================================================
# Standalone Usage Example
# =====================================================================

"""
Example usage outside of Dagster's asset framework:

```python
from wrm_pipeline.wrm_pipeline.vault.resource import (
    VaultSecretsResource,
    VaultSecretsResourceConfig,
)

# Create config
config = VaultSecretsResourceConfig(
    vault_addr="https://vault.example.com:8200",
    auth_method="approle",
    role_id="my-role-id",
    secret_id="my-secret-id",
    cache_ttl=300,
)

# Create resource
vault_resource = VaultSecretsResource(config)

# Use the resource
try:
    # Check health
    health = vault_resource.health_check()
    print(f"Vault healthy: {health['healthy']}")

    # Get database credentials
    db_creds = vault_resource.get_database_credentials()
    print(f"Database: {db_creds['host']}:{db_creds['port']}")

    # Get API key
    api_key = vault_resource.get_api_key("openweather")
    print(f"API Key: {api_key['api_key']}")

finally:
    vault_resource.close()
```
"""
