# wrm_pipeline

This is a [Dagster](https://dagster.io/) project scaffolded with [`dagster project scaffold`](https://docs.dagster.io/guides/build/projects/creating-a-new-project).

## Getting started

First, install your Dagster code location as a Python package. By using the --editable flag, pip will install your Python package in ["editable mode"](https://pip.pypa.io/en/latest/topics/local-project-installs/#editable-installs) so that as you develop, local code changes will automatically apply.

```bash
pip install -e ".[dev]"
```

Then, start the Dagster UI web server:

```bash
dagster dev
```

Open http://localhost:3000 with your browser to see the project.

You can start writing assets in `wrm_pipeline/assets.py`. The assets are automatically loaded into the Dagster code location as you define them.

## HashiCorp Vault Integration

This project includes comprehensive HashiCorp Vault integration for secrets management. The Vault module provides secure storage and retrieval of sensitive configuration such as database credentials, API keys, and application secrets.

### Features

- **Secure Secret Storage**: All sensitive configuration is stored in HashiCorp Vault instead of environment variables
- **Dagster Integration**: Native `VaultSecretsResource` for seamless use in Dagster assets and jobs
- **Secret Caching**: Built-in caching with configurable TTL to reduce Vault API calls
- **Automatic Authentication**: AppRole authentication with role ID and secret ID
- **Secret Rotation**: Support for automated secret rotation policies
- **Audit Logging**: Comprehensive audit logging for compliance and security monitoring
- **Backup & Recovery**: Automated backup scheduling with Hetzner Object Storage support

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Dagster Pipeline                        │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────────────────────┐ │
│  │  Assets/Ops     │───▶│    VaultSecretsResource         │ │
│  └─────────────────┘    │  - Secret caching               │ │
│                         │  - Automatic authentication      │ │
│                         │  - Error handling                │ │
│                         └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      HashiCorp Vault                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  KV v2      │  │  AppRole    │  │  Raft Storage       │  │
│  │  Secrets    │  │  Auth       │  │  (or file-based)    │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Quick Start

#### 1. Configure Environment Variables

Copy `.env.example` to `.env` and configure your Vault connection:

```bash
# Required Vault settings
VAULT_ENABLED=true
VAULT_ADDR=https://vault.example.com:8200
VAULT_ROLE_ID=your-role-id
VAULT_SECRET_ID=your-secret-id

# Optional: Custom namespace for Vault Enterprise
# VAULT_NAMESPACE=admin
```

#### 2. Create the Vault Resource in Definitions

Update your `definitions.py` to include the Vault resource:

```python
from dagster import Definitions, EnvVar
from wrm_pipeline.vault import VaultSecretsResource

defs = Definitions(
    resources={
        "vault": VaultSecretsResource.configured(
            {
                "vault_addr": EnvVar("VAULT_ADDR"),
                "namespace": EnvVar.get("VAULT_NAMESPACE"),
                "role_id": EnvVar("VAULT_ROLE_ID"),
                "secret_id": EnvVar("VAULT_SECRET_ID"),
                "cache_ttl": 300,  # Cache for 5 minutes
            }
        ),
    },
    assets=[...],
    jobs=[...],
)
```

#### 3. Use Secrets in Assets

Access secrets in your Dagster assets:

```python
from dagster import asset
from wrm_pipeline.vault import VaultSecret

@asset
def processed_stations(context):
    vault = context.resources.vault
    
    # Get database credentials from Vault
    db_creds = vault.get_secret(
        "bike-data-flow/production/database",
        mount_point="secret"
    )
    
    # Use credentials to connect to database
    connection = create_database_connection(
        host=db_creds["host"],
        port=db_creds["port"],
        user=db_creds["username"],
        password=db_creds["password"],
        database=db_creds["database"]
    )
    
    return process_stations(connection)
```

### Secret Paths

Secrets are organized under the following paths in Vault:

| Path | Description |
|------|-------------|
| `secret/data/bike-data-flow/production/database` | Database credentials |
| `secret/data/bike-data-flow/production/api-keys/*` | External API keys |
| `secret/data/bike-data-flow/production/app` | Application secrets |
| `secret/data/bike-data-flow/staging/*` | Staging environment secrets |
| `secret/data/bike-data-flow/test/*` | Test environment secrets |

### Migration from Environment Variables

To migrate from environment variables to Vault:

1. **Scan existing environment variables** for secrets:
   ```python
   from wrm_pipeline.migration import scan_for_secrets
   
   # Scan .env file for potential secrets
   secrets = scan_for_secrets(".env")
   print(secrets)
   ```

2. **Write secrets to Vault**:
   ```python
   from wrm_pipeline.migration import write_to_vault
   
   # write_to_file secrets to Vault
   write_to_vault(
       path="bike-data-flow/production/database",
       secrets={
           "username": "db_user",
           "password": "secure_password",
           "host": "db.example.com",
           "port": "5432"
       }
   )
   ```

3. **Update application code** to use `VaultSecretsResource` instead of direct environment variable access.

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `vault_addr` | `str` | Required | Vault server address |
| `namespace` | `Optional[str]` | `None` | Vault Enterprise namespace |
| `role_id` | `str` | Required | AppRole role ID |
| `secret_id` | `str` | Required | AppRole secret ID |
| `cache_ttl` | `int` | `300` | Cache TTL in seconds |
| `timeout` | `int` | `30` | Request timeout in seconds |
| `verify` | `bool` | `True` | Verify TLS certificates |

### Common Patterns

#### Database Credentials with Automatic Rotation

```python
from wrm_pipeline.vault import get_database_credentials

@asset
def my_asset(context):
    # Get credentials with automatic refresh
    creds = get_database_credentials(
        vault=context.resources.vault,
        path="bike-data-flow/production/database"
    )
    
    # Credentials are automatically refreshed if expired
    return use_credentials(creds)
```

#### API Key Management

```python
from wrm_pipeline.vault import VaultSecret

# Get API key for external service
api_key = vault.get_secret(
    "bike-data-flow/production/api-keys/openweather"
)["api_key"]

# List available API keys
api_keys = vault.list_secrets(
    "bike-data-flow/production/api-keys"
)
```

#### Health Checks

```python
from wrm_pipeline.vault import VaultHealthStatus

# Check Vault health
health = vault.health_check()

if health.status == VaultHealthStatus.ACTIVE:
    print("Vault is healthy and active")
elif health.status == VaultHealthStatus.SEALED:
    print("Vault is sealed - requires unsealing")
```

### Development

#### Running Tests

```bash
# Run all tests
pytest wrm_pipeline_tests

# Run vault-specific tests
pytest wrm_pipeline_tests -k vault
```

#### Local Development with Vault

For local development, you can use:

1. **Dev Vault Server**:
   ```bash
   vault server -dev
   export VAULT_ADDR=http://127.0.0.1:8200
   export VAULT_TOKEN=root-token
   ```

2. **Mock Vault Client** (for unit tests):
   ```python
   from wrm_pipeline.vault.client import VaultClient
   from wrm_pipeline.vault.models import VaultConnectionConfig
   
   # Create a mock client for testing
   config = VaultConnectionConfig(
       vault_addr="http://localhost:8200",
       auth_method="token",
       token="test-token"
   )
   client = VaultClient(config)
   ```

### Documentation

- [Vault API Documentation](docs/vault-api.md) - Complete API reference
- [Vault Server Configuration](docs/vault-server-config.md) - Server setup guide
- [Vault TLS Configuration](docs/vault-tls-config.md) - TLS certificates
- [Vault Unseal Management](docs/vault-unseal-management.md) - Unsealing procedures
- [Vault Policies](docs/vault-policies.md) - Access control policies
- [Vault Audit Logging](docs/vault-audit-logging.md) - Audit configuration
- [Vault Backup & Recovery](docs/vault-backup-recovery.md) - Backup procedures
- [Vault Troubleshooting](docs/vault-troubleshooting.md) - Common issues and solutions
- [Secret Migration Guide](docs/secret-migration-guide.md) - Migration from env vars

### Troubleshooting

See [Vault Troubleshooting](docs/vault-troubleshooting.md) for common issues and solutions.

### Schedules and sensors

If you want to enable Dagster [Schedules](https://docs.dagster.io/guides/automate/schedules/) or [Sensors](https://docs.dagster.io/guides/automate/sensors/) for your jobs, the [Dagster Daemon](https://docs.dagster.io/guides/deploy/execution/dagster-daemon) process must be running. This is done automatically when you run `dagster dev`.

Once your Dagster Daemon is running, you can start turning on schedules and sensors for your jobs.

## Deploy on Dagster+

The easiest way to deploy your Dagster project is to use Dagster+.

Check out the [Dagster+ documentation](https://docs.dagster.io/dagster-plus/) to learn more.
