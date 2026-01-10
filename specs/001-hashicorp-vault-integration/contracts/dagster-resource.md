# Contract: Dagster VaultSecretsResource

**Type**: Dagster Resource  
**Feature**: 001-hashicorp-vault-integration

## Interface Definition

```python
from dagster import ResourceDefinition
from dataclasses import dataclass
from typing import Optional, Any
import hvac

@dataclass
class VaultSecretsResourceConfig:
    """Configuration for the Vault secrets resource."""
    
    vault_addr: str = "https://vault.internal.bike-data-flow.com:8200"
    auth_method: str = "approle"
    role_id: Optional[str] = None
    secret_id: Optional[str] = None
    token: Optional[str] = None
    namespace: Optional[str] = None
    timeout: int = 30
    retries: int = 3
    cache_ttl: int = 300  # 5 minutes cache


class VaultSecretsResource:
    """
    Dagster resource for retrieving secrets from HashiCorp Vault.
    
    This resource provides secure access to secrets stored in Vault,
    with caching and error handling for reliable pipeline execution.
    """
    
    def __init__(self, config: VaultSecretsResourceConfig):
        """Initialize the Vault secrets resource."""
        self.config = config
        self._client: Optional[hvac.Client] = None
        self._cache: dict[str, tuple[Any, float]] = {}
    
    @property
    def client(self) -> hvac.Client:
        """Get or create the Vault client."""
        if self._client is None:
            self._client = self._create_client()
        return self._client
    
    def _create_client(self) -> hvac.Client:
        """Create and authenticate the Vault client."""
        client = hvac.Client(
            url=self.config.vault_addr,
            namespace=self.config.namespace,
            timeout=self.config.timeout,
            retries=self.config.retries
        )
        
        if self.config.auth_method == "approle":
            if not self.config.role_id or not self.config.secret_id:
                raise ValueError("role_id and secret_id required for AppRole auth")
            client.auth.approle.login(
                role_id=self.config.role_id,
                secret_id=self.config.secret_id
            )
        elif self.config.auth_method == "token":
            if not self.config.token:
                raise ValueError("token required for token auth")
            client.token = self.config.token
        
        return client
    
    # =====================================================================
    # Public API
    # =====================================================================
    
    def get_secret(
        self,
        path: str,
        version: Optional[int] = None,
        use_cache: bool = True
    ) -> dict[str, Any]:
        """
        Retrieve a secret from Vault.
        
        Args:
            path: Path to the secret (e.g., 'bike-data-flow/production/database')
            version: Optional specific version to retrieve
            use_cache: Whether to use cached value
        
        Returns:
            Dictionary containing secret data
        
        Raises:
            VaultSecretNotFoundError: If secret doesn't exist
            VaultAuthenticationError: If authentication fails
            VaultConnectionError: If Vault is unreachable
        """
        cache_key = f"{path}:{version}"
        
        if use_cache and cache_key in self._cache:
            value, expiry = self._cache[cache_key]
            if expiry > __import__('time').time():
                return value
        
        try:
            if version:
                response = self.client.secrets.kv.v2.read_secret_version(
                    path=path,
                    version=version
                )
            else:
                response = self.client.secrets.kv.v2.read_secret_version(
                    path=path
                )
            
            secret_data = response['data']['data']
            
            if use_cache:
                expiry = __import__('time').time() + self.config.cache_ttl
                self._cache[cache_key] = (secret_data, expiry)
            
            return secret_data
        
        except hvac.InvalidPath:
            raise VaultSecretNotFoundError(f"Secret not found: {path}")
        except hvac.Unauthorized:
            raise VaultAuthenticationError("Vault authentication failed")
        except hvac.VaultError as e:
            raise VaultConnectionError(f"Vault error: {str(e)}")
    
    def get_database_credentials(
        self,
        database_path: str = "bike-data-flow/production/database"
    ) -> dict[str, str]:
        """
        Convenience method for getting database credentials.
        
        Args:
            database_path: Path to database credentials secret
        
        Returns:
            Dictionary with 'username' and 'password' keys
        """
        credentials = self.get_secret(database_path)
        return {
            "username": credentials.get("username"),
            "password": credentials.get("password"),
            "host": credentials.get("host"),
            "port": credentials.get("port", "5432"),
            "database": credentials.get("database")
        }
    
    def get_api_key(self, provider: str) -> str:
        """
        Get an API key for a specific provider.
        
        Args:
            provider: Provider name (e.g., 'openweather', 'citybikes')
        
        Returns:
            The API key string
        """
        path = f"bike-data-flow/production/api-keys/{provider}"
        secret = self.get_secret(path)
        return secret.get("api_key")
    
    def list_secrets(self, path: str) -> list[str]:
        """
        List secret paths under a given path.
        
        Args:
            path: Parent path to list
        
        Returns:
            List of secret names/paths
        """
        response = self.client.secrets.kv.v2.list_secrets(path=path)
        return response.get('data', {}).get('keys', [])
    
    def health_check(self) -> bool:
        """
        Perform a health check on the Vault server.
        
        Returns:
            True if Vault is healthy and unsealed
        """
        try:
            health = self.client.sys.read_health_status()
            return health.get('initialized', False) and not health.get('sealed', False)
        except Exception:
            return False
    
    def clear_cache(self):
        """Clear the secret cache."""
        self._cache.clear()


# =====================================================================
# Resource Definition
# =====================================================================

vault_secrets_resource = ResourceDefinition(
    resource_def=VaultSecretsResource,
    config_schema=VaultSecretsResourceConfig,
    description="Resource for retrieving secrets from HashiCorp Vault"
)


# =====================================================================
# Exceptions
# =====================================================================

class VaultSecretsError(Exception):
    """Base exception for Vault secrets errors."""
    pass


class VaultSecretNotFoundError(VaultSecretsError):
    """Raised when a secret is not found in Vault."""
    pass


class VaultAuthenticationError(VaultSecretsError):
    """Raised when Vault authentication fails."""
    pass


class VaultConnectionError(VaultSecretsError):
    """Raised when connection to Vault fails."""
    pass
```

## Usage in Dagster

```python
from dagster import Definitions, env_var
from wrm_pipeline.vault import vault_secrets_resource, VaultSecretsResourceConfig

defs = Definitions(
    resources={
        "vault": vault_secrets_resource.configured(
            {
                "vault_addr": env_var("VAULT_ADDR"),
                "auth_method": "approle",
                "role_id": env_var("VAULT_ROLE_ID"),
                "secret_id": env_var("VAULT_SECRET_ID"),
                "cache_ttl": 300
            }
        )
    },
    assets=[...],
    jobs=[...]
)
```

## Performance Requirements

| Metric | Target | Measurement |
|--------|--------|-------------|
| Secret read latency | <500ms p95 | Per-request timing |
| Cache hit rate | >80% | Cache statistics |
| Error rate | <0.1% | Failed requests |

## Error Handling

The resource must handle the following scenarios gracefully:

1. **Vault Unreachable**: Retry with exponential backoff (max 3 retries)
2. **Authentication Failure**: Raise `VaultAuthenticationError` with details
3. **Secret Not Found**: Raise `VaultSecretNotFoundError` with path
4. **Cache Miss**: Fetch from Vault, update cache, return value
5. **Invalid Response**: Validate response structure, raise `VaultSecretsError`
