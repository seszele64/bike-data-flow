# Vault API Documentation

**Feature**: 001-hashicorp-vault-integration  
**Last Updated**: 2026-01-11

This document provides comprehensive API documentation for the HashiCorp Vault integration module.

---

## Table of Contents

1. [VaultClient](#vaultclient)
2. [VaultSecretsResource](#vaultsecretsresource)
3. [Models](#models)
4. [Exceptions](#exceptions)
5. [Secret Rotation](#secret-rotation)
6. [Audit Logging](#audit-logging)
7. [Backup & Recovery](#backup--recovery)
8. [Migration Utilities](#migration-utilities)
9. [Error Handling](#error-handling)

---

## VaultClient

The `VaultClient` class is the main interface for interacting with HashiCorp Vault.

### Constructor

```python
from wrm_pipeline.vault import VaultClient
from wrm_pipeline.vault.models import VaultConnectionConfig

config = VaultConnectionConfig(
    vault_addr="https://vault.example.com:8200",
    auth_method="approle",
    role_id="your-role-id",
    secret_id="your-secret-id",
    namespace=None,  # Optional: for Vault Enterprise
    cache_ttl=300,   # Cache TTL in seconds
    timeout=30,      # Request timeout in seconds
    verify=True      # Verify TLS certificates
)

client = VaultClient(config)
```

### Methods

#### `get_secret(path, mount_point='secret')`

Retrieve a secret from Vault.

```python
secret = client.get_secret("bike-data-flow/production/database")

# Returns: VaultSecret
# {
#     "path": "bike-data-flow/production/database",
#     "data": {
#         "username": "db_user",
#         "password": "secure_password",
#         "host": "db.example.com",
#         "port": "5432"
#     },
#     "metadata": {
#         "created_time": "2024-01-01T00:00:00.000Z",
#         "version": 1
#     }
# }
```

**Parameters:**
- `path` (str): The path to the secret (without mount_point prefix)
- `mount_point` (str): The secrets engine mount point (default: "secret")

**Returns:** `VaultSecret` - The secret data

**Raises:**
- `VaultConnectionError` - If Vault is unreachable
- `VaultAuthenticationError` - If authentication fails
- `VaultSecretNotFoundError` - If the secret doesn't exist

---

#### `write_secret(path, data, mount_point='secret')`

Write a secret to Vault.

```python
client.write_secret(
    path="bike-data-flow/production/database",
    data={
        "username": "new_user",
        "password": "new_password",
        "host": "db.example.com",
        "port": "5432"
    },
    mount_point="secret"
)
```

**Parameters:**
- `path` (str): The path to store the secret
- `data` (dict): The secret data to store
- `mount_point` (str): The secrets engine mount point

**Returns:** `dict` - The write response with version info

---

#### `delete_secret(path, mount_point='secret')`

Delete a secret from Vault.

```python
client.delete_secret(
    path="bike-data-flow/production/old-secret",
    mount_point="secret"
)
```

**Parameters:**
- `path` (str): The path to the secret to delete
- `mount_point` (str): The secrets engine mount point

---

#### `list_secrets(path, mount_point='secret')`

List secrets at a given path.

```python
secrets = client.list_secrets(
    path="bike-data-flow/production",
    mount_point="secret"
)

# Returns: list[str]
# ["database", "api-keys", "app"]
```

**Parameters:**
- `path` (str): The path to list
- `mount_point` (str): The secrets engine mount point

**Returns:** `list[str]` - List of secret names

---

#### `get_secret_version(path, version, mount_point='secret')`

Get a specific version of a secret.

```python
secret_v1 = client.get_secret_version(
    path="bike-data-flow/production/database",
    version=1,
    mount_point="secret"
)
```

**Parameters:**
- `path` (str): The path to the secret
- `version` (int): The version number to retrieve
- `mount_point` (str): The secrets engine mount point

**Returns:** `VaultSecret` - The secret data

---

#### `get_health()`

Check the health status of Vault.

```python
health = client.get_health()

# Returns: VaultHealth
# {
#     "status": <VaultHealthStatus.ACTIVE>,
#     "version": "1.15.0",
#     "cluster_id": "abc123",
#     "is_sealed": False,
#     "is_initialized": True
# }
```

**Returns:** `VaultHealth` - Health status information

---

#### `get_connection_config()`

Get the current connection configuration.

```python
config = client.get_connection_config()
# Returns: VaultConnectionConfig
```

**Returns:** `VaultConnectionConfig` - The connection configuration

---

## VaultSecretsResource

The `VaultSecretsResource` is a Dagster resource for integrating Vault with Dagster pipelines.

### Configuration

```python
from dagster import EnvVar
from wrm_pipeline.vault import VaultSecretsResource

resources = {
    "vault": VaultSecretsResource.configured({
        "vault_addr": EnvVar("VAULT_ADDR"),
        "namespace": EnvVar.get("VAULT_NAMESPACE"),
        "role_id": EnvVar("VAULT_ROLE_ID"),
        "secret_id": EnvVar("VAULT_SECRET_ID"),
        "cache_ttl": 300,
        "timeout": 30,
        "verify": True,
    })
}
```

### Configuration Schema

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `vault_addr` | `str` | Yes | - | Vault server address |
| `namespace` | `str` | No | `None` | Vault Enterprise namespace |
| `role_id` | `str` | Yes | - | AppRole role ID |
| `secret_id` | `str` | Yes | - | AppRole secret ID |
| `cache_ttl` | `int` | No | `300` | Cache TTL in seconds |
| `timeout` | `int` | No | `30` | Request timeout |
| `verify` | `bool` | No | `True` | Verify TLS certificates |

### Resource Methods

#### `get_secret(path, mount_point='secret')`

Retrieve a secret from Vault.

```python
@asset
def my_asset(context):
    vault = context.resources.vault
    
    secret = vault.get_secret(
        "bike-data-flow/production/database",
        mount_point="secret"
    )
    
    return secret.data
```

#### `get_database_credentials(path, mount_point='secret')`

Get database credentials with automatic parsing.

```python
@asset
def my_asset(context):
    vault = context.resources.vault
    
    creds = vault.get_database_credentials(
        "bike-data-flow/production/database"
    )
    
    # Returns: DatabaseCredentials
    # {
    #     "username": "db_user",
    #     "password": "secure_password",
    #     "host": "db.example.com",
    #     "port": "5432",
    #     "database": "bike_data"
    # }
```

#### `get_api_key(service_name, path='api-keys')`

Get an API key for a specific service.

```python
@asset
def my_asset(context):
    vault = context.resources.vault
    
    api_key = vault.get_api_key(
        "openweather",
        path="bike-data-flow/production/api-keys"
    )
    
    # Returns: str - The API key value
```

#### `list_secrets(path, mount_point='secret')`

List secrets at a given path.

```python
@asset
def my_asset(context):
    vault = context.resources.vault
    
    secrets = vault.list_secrets(
        "bike-data-flow/production",
        mount_point="secret"
    )
```

#### `health_check()`

Check Vault health status.

```python
@asset
def my_asset(context):
    vault = context.resources.vault
    
    health = vault.health_check()
    
    if health.status.value != "active":
        raise Exception("Vault is not healthy!")
```

---

## Models

### VaultConnectionConfig

```python
from wrm_pipeline.vault.models import VaultConnectionConfig

config = VaultConnectionConfig(
    vault_addr=str,
    auth_method=str,  # "approle", "token", "kubernetes"
    role_id=Optional[str],
    secret_id=Optional[str],
    token=Optional[str],
    namespace=Optional[str],
    cache_ttl=int,
    timeout=int,
    verify=bool,
)
```

### VaultSecret

```python
from wrm_pipeline.vault.models import VaultSecret

secret = VaultSecret(
    path=str,
    data=dict,
    metadata=SecretMetadata
)
```

**Attributes:**
- `path` (str): The secret path
- `data` (dict): The secret data
- `metadata` (SecretMetadata): Metadata about the secret

### VaultHealth

```python
from wrm_pipeline.vault.models import VaultHealth

health = VaultHealth(
    status=VaultHealthStatus,  # ACTIVE, STANDBY, SEALED, UNINITIALIZED, ERROR
    version=str,
    cluster_id=str,
    is_sealed=bool,
    is_initialized=bool,
    # For Raft storage:
    leader_address=Optional[str],
    performance_standby=bool,
)
```

### SecretRotationPolicy

```python
from wrm_pipeline.vault.models import SecretRotationPolicy, RotationType

policy = SecretRotationPolicy(
    name="daily-rotation",
    path="bike-data-flow/production/database",
    rotation_type=RotationType.TIME_BASED,
    interval_days=90,
    grace_period_hours=24,
    auto_rotate=True,
)
```

### AccessPolicy

```python
from wrm_pipeline.vault.models import AccessPolicy, PolicyRule

policy = AccessPolicy(
    name="dagster-secrets",
    rules=[
        PolicyRule(
            path="secret/data/bike-data-flow/*",
            capabilities=["read", "list"],
        ),
    ],
)
```

---

## Exceptions

### Exception Hierarchy

```
VaultError (base)
├── VaultConnectionError
├── VaultAuthenticationError
│   └── VaultInvalidTokenError
├── VaultSecretNotFoundError
├── VaultPermissionError
├── VaultSealedError
├── VaultUninitializedError
├── VaultVersionError
├── VaultCacheError
└── VaultValidationError
```

### Usage

```python
from wrm_pipeline.vault import (
    VaultClient,
    VaultAuthenticationError,
    VaultSecretNotFoundError,
    VaultSealedError,
)

try:
    client = VaultClient(config)
    secret = client.get_secret("bike-data-flow/production/database")
except VaultAuthenticationError as e:
    print(f"Authentication failed: {e}")
    # Handle invalid credentials
except VaultSecretNotFoundError as e:
    print(f"Secret not found: {e}")
    # Handle missing secret
except VaultSealedError as e:
    print(f"Vault is sealed: {e}")
    # Handle sealed state
except VaultError as e:
    print(f"Vault error: {e}")
    # Handle other errors
```

---

## Secret Rotation

### RotationScheduler

```python
from wrm_pipeline.vault.rotation import RotationScheduler, RotationOrchestrator

# Create scheduler
scheduler = RotationScheduler(
    vault_client=client,
    check_interval=3600,  # Check every hour
)

# Schedule a rotation
scheduler.schedule_rotation(
    secret_path="bike-data-flow/production/database",
    rotation_type=RotationType.TIME_BASED,
    interval_days=90,
)

# Run scheduled rotations
orchestrator = RotationOrchestrator(scheduler)
orchestrator.run_rotations()
```

### RotationHistoryTracker

```python
from wrm_pipeline.vault.rotation import RotationHistoryTracker

tracker = RotationHistoryTracker(vault_client=client)

# Get rotation history
history = tracker.get_rotation_history(
    secret_path="bike-data-flow/production/database"
)

# Get rotation status
status = tracker.get_rotation_status(
    secret_path="bike-data-flow/production/database"
)
```

---

## Audit Logging

### AuditLogParser

```python
from wrm_pipeline.vault.audit import AuditLogParser, AuditLogCollection

parser = AuditLogParser()

# Parse from file
collection = parser.parse_from_file("/var/log/vault/audit.log")

# Parse from directory
collection = parser.parse_from_directory("/var/log/vault/")

# Filter logs
read_logs = collection.filter_by_operation(AuditOperation.READ)
failed_logs = collection.filter_by_success(False)
recent_logs = collection.filter_by_time(
    start=datetime.utcnow() - timedelta(days=1)
)

# Get access patterns
patterns = collection.get_access_patterns()

# Detect anomalies
anomalies = collection.detect_anomalies(
    threshold=100,
    time_window_minutes=60
)
```

### AuditLogCollection Methods

| Method | Description |
|--------|-------------|
| `filter_by_time(start, end)` | Filter by time range |
| `filter_by_operation(*ops)` | Filter by operation types |
| `filter_by_path(pattern)` | Filter by path pattern |
| `filter_by_actor(actor)` | Filter by actor ID |
| `filter_by_success(success)` | Filter by success/failure |
| `filter_by_client_ip(ip)` | Filter by client IP |
| `search_logs(query)` | Search by text content |
| `aggregate_by_operation()` | Group by operation type |
| `aggregate_by_time(interval)` | Group by time window |
| `get_access_patterns()` | Analyze access patterns |
| `detect_anomalies(threshold, window)` | Detect unusual activity |
| `sort_by_timestamp(ascending)` | Sort by timestamp |
| `statistics()` | Get summary statistics |

---

## Backup & Recovery

### SnapshotManager

```python
from wrm_pipeline.vault.backup import SnapshotManager, BackupType

manager = SnapshotManager(
    vault_client=client,
    backup_dir="/var/backups/vault"
)

# Create snapshot
snapshot = manager.create_snapshot(
    backup_type=BackupType.FULL,
    encrypt=True,
    encryption_key="gpg-key-id",
    upload=True,  # Upload to object storage
    retention_days=30,
)

# List snapshots
snapshots = manager.list_snapshots(
    backup_type=BackupType.FULL,
    status=BackupStatus.COMPLETED,
)

# Verify snapshot
manager.verify_snapshot(snapshot_id)

# Restore snapshot
result = manager.restore_snapshot(
    snapshot_id,
    dry_run=False,
)

# Delete old snapshots
manager.cleanup_expired(retention_policy)
```

### BackupScheduler

```python
from wrm_pipeline.vault.backup import BackupScheduler, ScheduleFrequency

scheduler = BackupScheduler(
    snapshot_manager=manager,
    notify_on_failure=True,
    notify_emails=["admin@example.com"]
)

# Schedule backups
scheduler.schedule_daily(hour=2, minute=0)
scheduler.schedule_weekly(day_of_week=0, hour=3)
scheduler.schedule_monthly(day_of_month=1, hour=3)

# Run backup manually
result = scheduler.run_backup(
    backup_type=BackupType.FULL,
    encrypt=True,
    upload=True,
)

# Get statistics
stats = scheduler.get_backup_stats()
```

---

## Migration Utilities

### Scan Secrets

```python
from wrm_pipeline.migration import scan_for_secrets

# Scan .env file
secrets = scan_for_secrets(".env")

# Scan for patterns
secrets = scan_for_secrets(
    ".env",
    patterns=[
        r"API_KEY.*=.*",
        r"PASSWORD.*=.*",
        r"SECRET.*=.*",
    ]
)
```

### Write to Vault

```python
from wrm_pipeline.migration import write_to_vault

# Write secrets to Vault
write_to_vault(
    vault_client=client,
    path="bike-data-flow/production/database",
    secrets={
        "username": "db_user",
        "password": "secure_password",
    },
    mount_point="secret"
)
```

### Validate Paths

```python
from wrm_pipeline.migration import validate_paths

# Validate secret paths
results = validate_paths(
    vault_client=client,
    paths=[
        "bike-data-flow/production/database",
        "bike-data-flow/production/api-keys",
    ],
    mount_point="secret"
)

for result in results:
    print(f"{result.path}: {'Valid' if result.exists else 'Not found'}")
```

---

## Error Handling

### Best Practices

```python
from wrm_pipeline.vault import VaultClient, VaultError
from wrm_pipeline.vault.exceptions import (
    VaultConnectionError,
    VaultAuthenticationError,
    VaultSecretNotFoundError,
)

def get_secret_safely(client: VaultClient, path: str, max_retries: int = 3) -> dict:
    """Get a secret with retry logic."""
    
    for attempt in range(max_retries):
        try:
            return client.get_secret(path)
            
        except VaultConnectionError as e:
            if attempt == max_retries - 1:
                raise
            # Wait before retry
            time.sleep(2 ** attempt)  # Exponential backoff
            
        except VaultAuthenticationError as e:
            # Re-authenticate
            client._ensure_authenticated()
            
        except VaultSecretNotFoundError as e:
            # Secret doesn't exist - return default or raise
            raise ValueError(f"Secret not found: {path}")
            
    raise VaultError(f"Failed after {max_retries} attempts")
```

### Retry Configuration

```python
from wrm_pipeline.vault.models import VaultConnectionConfig

config = VaultConnectionConfig(
    vault_addr="https://vault.example.com:8200",
    auth_method="approle",
    role_id="role-id",
    secret_id="secret-id",
    timeout=30,
    # Custom retry configuration
    retry_max_attempts=3,
    retry_base_delay=1.0,
    retry_max_delay=30.0,
)
```

---

## Additional Resources

- [Vault Server Configuration](vault-server-config.md)
- [Vault TLS Configuration](vault-tls-config.md)
- [Vault Unseal Management](vault-unseal-management.md)
- [Vault Policies](vault-policies.md)
- [Vault Audit Logging](vault-audit-logging.md)
- [Vault Backup & Recovery](vault-backup-recovery.md)
- [Vault Troubleshooting](vault-troubleshooting.md)
- [Secret Migration Guide](secret-migration-guide.md)
