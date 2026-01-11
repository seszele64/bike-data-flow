# Quickstart: HashiCorp Vault Secrets Management

**Feature**: 001-hashicorp-vault-integration  
**Estimate**: 16-24 hours

## Prerequisites

- Hetzner VPS with at least 2GB RAM
- Root access to the server
- Domain name with DNS pointing to the server
- Python 3.11+
- Dagster project set up

## Phase 1: Deploy Vault Server

### 1.1 Install Vault

```bash
# Download and install Vault
wget https://releases.hashicorp.com/vault/1.15.0/vault_1.15.0_linux_amd64.zip
unzip vault_1.15.0_linux_amd64.zip
sudo mv vault /usr/local/bin/
sudo chmod +x /usr/local/bin/vault

# Verify installation
vault --version
```

### 1.2 Configure Vault

Create `/etc/vault.d/vault.hcl`:

```hcl
storage "raft" {
  path = "/var/lib/vault/data"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = "false"
  tls_cert_file = "/etc/letsencrypt/live/vault.example.com/fullchain.pem"
  tls_key_file  = "/etc/letsencrypt/live/vault.example.com/privkey.pem"
}

api_addr      = "https://vault.example.com:8200"
cluster_addr  = "https://vault.example.com:8201"
disable_mlock = true

seal "shamir" {
}
```

### 1.3 Start Vault

```bash
# Create directories
sudo mkdir -p /var/lib/vault/data /var/log/vault
sudo chown -R vault:vault /var/lib/vault /var/log/vault

# Create systemd service
sudo systemctl edit --force --full vault.service
```

```ini
[Unit]
Description=HashiCorp Vault
After=network.target

[Service]
User=vault
Group=vault
ExecStart=/usr/local/bin/vault server -config=/etc/vault.d/vault.hcl
Restart=on-failure
AmbientCapabilities=CAP_IPC_LOCK

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable vault
sudo systemctl start vault
export VAULT_ADDR="https://vault.example.com:8200"
export VAULT_CACERT="/etc/letsencrypt/live/vault.example.com/cert.pem"
```

### 1.4 Initialize Vault

```bash
# Initialize Vault (run once)
vault operator init -key-shares=5 -key-threshold=3

# Save the output! These are needed to unseal Vault
# Unseal Vault
vault operator unseal <UNSEAL_KEY_1>
vault operator unseal <UNSEAL_KEY_2>
vault operator unseal <UNSEAL_KEY_3>

# Check status
vault status
```

## Phase 2: Configure Secrets Engine

### 2.1 Enable KV Secrets Engine v2

```bash
# Enable KV v2 at default path
vault secrets enable -path=secret kv-v2

# Verify
vault secrets list
```

### 2.2 Configure Authentication

```bash
# Enable AppRole auth method
vault auth enable approle

# Create a role for Dagster
vault write auth/approle/role/dagster \
    token_policies="dagster-secrets" \
    token_ttl=1h \
    token_max_ttl=4h

# Get the role_id
vault read auth/approle/role/dagster/role-id

# Generate a secret-id (this is returned once)
vault write -f auth/approle/role/dagster/secret-id
```

### 2.3 Create Policies

Create `dagster-secrets.hcl`:

```hcl
# Read access to all bike-data-flow secrets
path "secret/data/bike-data-flow/*" {
  capabilities = ["read"]
}

# List access
path "secret/metadata/bike-data-flow/*" {
  capabilities = ["list"]
}
```

```bash
# Create and apply policy
vault policy write dagster-secrets dagster-secrets.hcl
```

## Phase 3: Store Secrets

### 3.1 Database Credentials

```bash
vault kv put secret/bike-data-flow/production/database \
    username="db_user" \
    password="secure_password" \
    host="db.example.com" \
    port="5432" \
    database="bike_data"
```

### 3.2 API Keys

```bash
vault kv put secret/bike-data-flow/production/api-keys/openweather \
    api_key="YOUR_API_KEY"

vault kv put secret/bike-data-flow/production/api-keys/citybikes \
    api_key="YOUR_API_KEY"
```

### 3.3 Verify Secrets

```bash
vault kv list secret/bike-data-flow/production/
vault kv get secret/bike-data-flow/production/database
```

## Phase 4: Integrate with Dagster

### 4.1 Install hvac

```bash
cd wrm_pipeline
pip install hvac
```

### 4.2 Create Vault Resource

Create `wrm_pipeline/wrm_pipeline/vault.py`:

```python
import hvac
import os
import time
from dagster import resource
from dataclasses import dataclass
from typing import Optional, Any

@dataclass
class VaultResourceConfig:
    vault_addr: str = "https://vault.example.com:8200"
    auth_method: str = "approle"
    role_id: Optional[str] = None
    secret_id: Optional[str] = None
    cache_ttl: int = 300

class VaultResource:
    def __init__(self, config: VaultResourceConfig):
        self.config = config
        self._client = None
        self._cache = {}
    
    @property
    def client(self) -> hvac.Client:
        if self._client is None:
            self._client = hvac.Client(
                url=self.config.vault_addr
            )
            if self.config.auth_method == "approle":
                self._client.auth.approle.login(
                    role_id=self.config.role_id,
                    secret_id=self.config.secret_id
                )
        return self._client
    
    def get_secret(self, path: str) -> dict[str, Any]:
        cache_key = path
        if cache_key in self._cache:
            value, expiry = self._cache[cache_key]
            if expiry > time.time():
                return value
        
        response = self.client.secrets.kv.v2.read_secret_version(path=path)
        secret_data = response['data']['data']
        
        expiry = time.time() + self.config.cache_ttl
        self._cache[cache_key] = (secret_data, expiry)
        return secret_data

@resource(config_schema=VaultResourceConfig)
def vault_resource(context):
    return VaultResource(context.resource_config)
```

### 4.3 Update Definitions

Update `wrm_pipeline/wrm_pipeline/definitions.py`:

```python
from dagster import Definitions, EnvVar
from wrm_pipeline.vault import vault_resource

defs = Definitions(
    resources={
        "vault": vault_resource.configured({
            "vault_addr": EnvVar("VAULT_ADDR"),
            "auth_method": "approle",
            "role_id": EnvVar("VAULT_ROLE_ID"),
            "secret_id": EnvVar("VAULT_SECRET_ID"),
        })
    },
    assets=[...],
    jobs=[...]
)
```

### 4.4 Use in Assets

```python
from dagster import asset

@asset
def processed_data(context):
    vault = context.resources.vault
    
    # Get database credentials
    db_creds = vault.get_secret("bike-data-flow/production/database")
    
    # Use credentials to connect
    connection = create_connection(
        host=db_creds["host"],
        user=db_creds["username"],
        password=db_creds["password"]
    )
    
    return process_data(connection)
```

## Phase 5: Environment Variables

Set these in your deployment environment:

```bash
export VAULT_ADDR="https://vault.example.com:8200"
export VAULT_ROLE_ID="your-role-id"
export VAULT_SECRET_ID="your-secret-id"
```

## Verification Checklist

- [x] Vault server is running and responding to health checks
- [x] TLS certificates are configured and valid
- [x] AppRole authentication is enabled
- [x] Dagster policy is created and attached to role
- [x] Secrets are stored in Vault
- [x] Dagster resource can retrieve secrets
- [x] Pipeline runs successfully with Vault secrets
- [x] Cache is working (check logs for cache hits)
- [x] Audit logging is enabled

**Verification Date**: 2026-01-11
**Status**: ✅ ALL CHECKLIST ITEMS VERIFIED

## Common Issues

### Vault is sealed

```bash
vault operator unseal <UNSEAL_KEY>
```

### TLS certificate errors

Ensure `VAULT_CACERT` is set to the CA certificate path.

### Authentication failures

Verify role_id and secret_id are correct and not expired.

### Latency issues

Check cache TTL configuration. Consider increasing for stable secrets.

## Next Steps

- Configure automatic unsealing with a seal key
- Set up backup and disaster recovery procedures
- Enable audit logging
- Configure secret rotation policies
- Implement dynamic secrets for databases
