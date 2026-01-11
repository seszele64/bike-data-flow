# Data Model: HashiCorp Vault Secrets Management

**Feature**: 001-hashicorp-vault-integration  
**Date**: 2026-01-10

## Entities

### E1: Secret

Represents a stored credential or configuration value.

```python
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Any

class Secret(BaseModel):
    """Represents a secret stored in Vault."""
    
    path: str = Field(..., description="Full path to the secret in Vault KV v2")
    data: dict[str, Any] = Field(..., description="Secret data key-value pairs")
    version: int = Field(default=1, description="Current version number")
    created_time: datetime = Field(..., description="Creation timestamp")
    custom_metadata: Optional[dict[str, Any]] = Field(default=None, description="Custom metadata")
    
    class Config:
        json_schema_extra = {
            "example": {
                "path": "secret/data/bike-data-flow/production/database",
                "data": {
                    "username": "db_user",
                    "host": "db.example.com"
                },
                "version": 3,
                "created_time": "2026-01-10T10:00:00Z"
            }
        }
```

**Validation Rules**:
- `path` must be valid Vault path format (alphanumeric, hyphens, underscores, slashes)
- `data` must be serializable to JSON
- `version` is read-only, assigned by Vault

---

### E2: AccessPolicy

Defines permissions for entities to access or modify specific secret paths.

```python
class AccessPolicy(BaseModel):
    """Defines access control policy for Vault secrets."""
    
    name: str = Field(..., description="Policy name (e.g., 'dagster-secrets')")
    rules: list[PolicyRule] = Field(default_factory=list, description="Policy rules")

class PolicyRule(BaseModel):
    """Single policy rule defining access to a path."""
    
    path: str = Field(..., description="Secret path pattern (supports wildcards)")
    capabilities: list[str] = Field(
        ...,
        description="Allowed capabilities: create, read, update, delete, list, patch, sudo"
    )
    description: Optional[str] = Field(None, description="Human-readable description")

    class Config:
        json_schema_extra = {
            "example": {
                "path": "secret/data/bike-data-flow/production/*",
                "capabilities": ["read"],
                "description": "Read access to all production secrets"
            }
        }
```

**Capability Meanings**:
- `create`: Write new secrets
- `read`: Read secret values
- `update`: Modify existing secrets
- `delete`: Remove secrets
- `list`: List secret paths
- `sudo`: Access sensitive endpoints

---

### E3: VaultConfig

Server configuration for Vault deployment.

```python
class VaultConfig(BaseModel):
    """Vault server configuration."""
    
    storage_backend: str = Field(default="raft", description="Storage backend type")
    listener: ListenerConfig = Field(..., description="Listener configuration")
    tls: TLSConfig = Field(..., description="TLS configuration")
    seal: SealConfig = Field(default=None, description="Seal configuration for auto-unseal")
    telemetry: TelemetryConfig = Field(default=None, description="Telemetry settings")

class ListenerConfig(BaseModel):
    """Network listener configuration."""
    
    type: str = Field(default="tcp", description="Listener type")
    address: str = Field(default="0.0.0.0:8200", description="Bind address")
    cluster_address: str = Field(default="0.0.0.0:8201", description="Cluster communication address")

class TLSConfig(BaseModel):
    """TLS certificate configuration."""
    
    enabled: bool = Field(default=True, description="Enable TLS")
    cert_file: Optional[str] = Field(None, path="Path to TLS certificate")
    key_file: Optional[str] = Field(None, path="Path to TLS private key")
    min_version: str = Field(default="tls12", description="Minimum TLS version")

class SealConfig(BaseModel):
    """Seal configuration for key management."""
    
    type: str = Field(default="shamir", description="Seal type: shamir or awskms, etc.")
    disabled: bool = Field(default=False, description="Disable seal (not recommended)")

class TelemetryConfig(BaseModel):
    """Telemetry and monitoring configuration."""
    
    statsite_address: Optional[str] = Field(None, description="StatsD/Statsite address")
    disable_hostname: bool = Field(default=False, description="Disable hostname prefix")
```

---

### E4: VaultClientConfig

Configuration for applications connecting to Vault.

```python
class VaultClientConfig(BaseModel):
    """Configuration for Vault client connections."""
    
    vault_addr: str = Field(..., description="Vault server address (https://...)")
    auth_method: str = Field(default="approle", description="Authentication method")
    role_id: Optional[str] = Field(None, description="AppRole role ID (if approle)")
    secret_id: Optional[str] = Field(None, description="AppRole secret ID (if approle)")
    token: Optional[str] = Field(None, description="Vault token (if token auth)")
    namespace: Optional[str] = Field(None, description="Enterprise namespace")
    timeout: int = Field(default=30, description="Request timeout in seconds")
    retries: int = Field(default=3, description="Number of retry attempts")
    cache_ttl: int = Field(default=300, description="Secret cache TTL in seconds")

    def validate(self) -> bool:
        """Validate configuration has required auth credentials."""
        if self.auth_method == "approle":
            return bool(self.role_id)
        elif self.auth_method == "token":
            return bool(self.token)
        else:
            return False
```

---

### E5: SecretRotationPolicy

Configuration for automated secret rotation.

```python
from enum import Enum

class RotationType(str, Enum):
    """Types of secret rotation."""
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    DYNAMIC = "dynamic"

class SecretRotationPolicy(BaseModel):
    """Configuration for secret rotation behavior."""
    
    secret_path: str = Field(..., description="Path to secret being rotated")
    rotation_type: RotationType = Field(..., description="Type of rotation")
    rotation_period_days: Optional[int] = Field(None, description="Days between rotations")
    rotation_function: Optional[str] = Field(None, description="Custom rotation function name")
    last_rotated: Optional[datetime] = Field(None, description="Last rotation timestamp")
    next_rotation: Optional[datetime] = Field(None, description="Next scheduled rotation")
    
    class Config:
        json_schema_extra = {
            "example": {
                "secret_path": "secret/data/bike-data-flow/production/api-key",
                "rotation_type": "scheduled",
                "rotation_period_days": 90,
                "last_rotated": "2025-10-12T10:00:00Z",
                "next_rotation": "2026-01-10T10:00:00Z"
            }
        }
```

---

### E6: AuditLog

Record of secret access and management operations.

```python
from enum import Enum

class AuditOperation(str, Enum):
    """Types of audit operations."""
    READ = "read"
    WRITE = "write"
    UPDATE = "update"
    DELETE = "delete"
    LIST = "list"
    DENY = "deny"

class AuditLog(BaseModel):
    """Audit log entry for Vault operations."""
    
    timestamp: datetime = Field(..., description="Operation timestamp")
    accessor: str = Field(..., description="Entity that performed the operation")
    operation: AuditOperation = Field(..., description="Type of operation")
    path: str = Field(..., description="Secret path accessed")
    success: bool = Field(..., description="Whether operation succeeded")
    error: Optional[str] = Field(None, description="Error message if failed")
    client_ip: Optional[str] = Field(None, description="Client IP address")
    request_id: Optional[str] = Field(None, description="Vault request ID")
    
    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2026-01-10T10:30:00Z",
                "accessor": "token-abcd1234",
                "operation": "read",
                "path": "secret/data/bike-data-flow/production/database",
                "success": True,
                "client_ip": "10.0.0.5",
                "request_id": "req-xyz789"
            }
        }
```

---

### E7: VaultHealth

Health status of Vault server.

```python
from enum import Enum

class VaultHealthStatus(str, Enum):
    """Vault health status values."""
    INITIALIZED = "initialized"
    SEALED = "sealed"
    UNSEALED = "unsealed"
    STANDBY = "standby"
    MAINTENANCE = "maintenance"
    DISABLED = "disabled"

class VaultHealth(BaseModel):
    """Vault server health status."""
    
    status: VaultHealthStatus = Field(..., description="Current status")
    version: str = Field(..., description="Vault version")
    cluster_id: Optional[str] = Field(None, description="Cluster identifier")
    cluster_name: Optional[str] = Field(None, description="Cluster name")
    replication_mode: Optional[str] = Field(None, description="Replication mode")
    server_time_utc: datetime = Field(..., description="Server time")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "unsealed",
                "version": "1.15.0",
                "cluster_id": "vault-cluster-1234",
                "cluster_name": "bike-data-flow-vault",
                "server_time_utc": "2026-01-10T10:30:00Z"
            }
        }
```

---

## Entity Relationships

```
VaultConfig ──────► VaultServer
      │                   │
      │                   ▼
      │            ┌──────┴──────┐
      │            │             │
      ▼            ▼             ▼
VaultClientConfig  Secret    AccessPolicy
      │                        │
      │                        ▼
      ▼                   Authentication
   DAGSTER ◄─────────────► AppRole
   Resource                   │
                              ▼
                         SecretRotationPolicy
```

---

## Usage Examples

### Reading a Secret from Dagster Resource

```python
from wrm_pipeline.vault import VaultSecretsResource

# Get configured resource
vault_resource = context.resources.vault

# Read secret
database_creds = vault_resource.get_secret(
    path="bike-data-flow/production/database"
)

# Access values
username = database_creds.data["username"]
password = database_creds.data["password"]
```

### Creating an Access Policy

```python
from wrm_pipeline.vault.models import AccessPolicy, PolicyRule

dagster_policy = AccessPolicy(
    name="dagster-secrets",
    rules=[
        PolicyRule(
            path="secret/data/bike-data-flow/production/*",
            capabilities=["read"],
            description="Read access to production secrets"
        ),
        PolicyRule(
            path="secret/data/bike-data-flow/staging/*",
            capabilities=["read"],
            description="Read access to staging secrets"
        )
    ]
)
```
