# Research: HashiCorp Vault Secrets Management Integration

**Feature**: 001-hashicorp-vault-integration  
**Date**: 2026-01-10  
**Status**: In Progress

## Unknowns Identified

### U1: Hetzner VPS Deployment
- Optimal Vault deployment configuration for Hetzner VPS (storage backend, resource allocation)
- TLS certificate configuration for Vault
- Firewall and network security requirements

### U2: Dagster Integration
- Python `hvac` library usage patterns
- Custom Dagster resource for Vault secrets
- Connection pooling and caching for secrets

### U3: Secret Migration
- Automated .env to Vault migration tools
- Secret path structure conventions
- Application configuration patterns

### U4: Secret Rotation
- Vault dynamic secrets vs static secrets
- Rotation policies and automation
- Integration with database credential rotation

### U5: Backup & Recovery
- Vault snapshot/restore procedures
- Disaster recovery planning
- Unseal key management

### U6: Authentication
- AppRole vs Token authentication
- Least-privilege policy design
- Token lifecycle management

---

## Research Findings

### R1: Hetzner VPS Deployment

**Recommended Configuration**:
- Storage Backend: Integrated (file-based) storage for single-node deployment
- Resources: 2GB RAM minimum, 20GB storage
- Network: Configure listener on private IP or behind reverse proxy
- TLS: Use Let's Encrypt certificates via cert-manager or ACME

**Key Resources**:
- [Vault Production Hardening Guide](https://developer.hashicorp.com/vault/docs/internals/security)
- [Vault HA with Integrated Storage](https://developer.hashicorp.com/vault/docs/concepts/integrated-storage)

**Decisions**:
1. Use integrated storage (no separate storage backend needed for single-node)
2. Configure TLS with Let's Encrypt certificates
3. Use systemd service for auto-restart

---

### R2: Dagster Integration with hvac

**hvac Library Usage**:
```python
import hvac
import os

client = hvac.Client(
    url=os.environ.get('VAULT_ADDR'),
    token=os.environ.get('VAULT_TOKEN')
)

# Read secret
secret = client.secrets.kv.v2.read_secret_version(path='database/credentials')

# Write secret
client.secrets.kv.v2.write_secret_version(
    path='database/credentials',
    secret={'username': 'user', 'password': 'pass'}
)
```

**Dagster Resource Pattern**:
```python
from dagster import resource
import hvac

@resource
def vault_resource(context):
    return hvac.Client(
        url=context.resource_config['vault_addr'],
        token=context.resource_config['vault_token']
    )
```

**Performance**: Implement caching layer with TTL to meet <500ms latency requirement

**Key Resources**:
- [hvac PyPI](https://pypi.org/project/hvac/)
- [Dagster Resources Documentation](https://docs.dagster.io/concepts/resources)

**Decisions**:
1. Create custom `VaultSecretsResource` with caching
2. Use KV secrets engine v2
3. Implement connection retry logic

---

### R3: Secret Migration

**Migration Strategy**:
1. Scan codebase for environment variable patterns
2. Create Vault path structure: `secret/{app}/{environment}/{secret-name}`
3. Write_to_file migration script to transfer secrets
4. Update application code to use Vault client
5. Remove secrets from .env files

**Path Structure Convention**:
```
secret/
├── bike-data-flow/
│   ├── production/
│   │   ├── database-url
│   │   ├── api-keys/
│   │   │   ├── provider-a
│   │   │   └── provider-b
│   │   └── aws-credentials
│   └── staging/
└── dagster/
    └── vault-token
```

**Key Resources**:
- [KV Secrets Engine](https://developer.hashicorp.com/vault/docs/secrets/kv)

**Decisions**:
1. Use hierarchical path structure by application/environment
2. Implement migration script in Python
3. Version secrets in Vault for rollback capability

---

### R4: Secret Rotation

**Rotation Options**:

| Type | Use Case | Rotation Method |
|------|----------|-----------------|
| Static Secrets | API keys, passwords | Manual or scheduled |
| Dynamic Secrets | Database credentials | Automatic with TTL |
| Database Secrets | PostgreSQL, MySQL | Vault database secrets engine |

**Dynamic Secrets Benefits**:
- Automatic rotation on configurable interval
- Short-lived credentials reduce exposure
- Automatic revocation on lease expiry

**Key Resources**:
- [Vault Dynamic Secrets](https://developer.hashicorp.com/vault/docs/secrets/dynamicsecrets)
- [Database Secrets Engine](https://developer.hashicorp.com/vault/docs/secrets/databases)

**Decisions**:
1. Use dynamic secrets for database connections
2. Configure 90-day rotation for API keys
3. Use Vault's database secrets engine for PostgreSQL

---

### R5: Backup & Recovery

**Backup Methods**:
1. `vault operator snapshot save` - Snapshots of integrated storage
2. Export secrets via API (for specific paths)
3. Replication for cross-datacenter redundancy

**Disaster Recovery**:
- Store snapshots in secure offsite location
- Document restore procedures
- Test restore quarterly

**Unseal Key Management**:
- Use Vault Seal (Auto-unseal) with cloud KMS
- For Hetzner: Use seal with master key encrypted
- Store unseal keys in separate secure location

**Key Resources**:
- [Vault Snapshot Guide](https://developer.hashicorp.com/vault/docs/commands/operator/snapshot)
- [Vault Seal Configuration](https://developer.hashicorp.com/vault/docs/configuration/seal)

**Decisions**:
1. Use integrated storage snapshots
2. Configure auto-unseal with master key
3. Store snapshots in encrypted Hetzner Object Storage

---

### R6: Authentication Methods

| Method | Best For | Security |
|--------|----------|----------|
| Token | Human access, scripts | Requires token management |
| AppRole | Machine-to-machine | More secure, role-based |
| Kubernetes | Container workloads | Pod identity |
| LDAP/AD | Enterprise users | Existing identity provider |

**AppRole Configuration**:
```hcl
# Enable AppRole auth
vault auth enable approle

# Create role
vault write auth/approle/role/dagster \
    token_policies="dagster-secrets" \
    token_ttl=1h \
    token_max_ttl=4h
```

**Policy Design**:
```hcl
# dagster-secrets.hcl
path "secret/data/bike-data-flow/production/*" {
  capabilities = ["read"]
}
path "secret/data/bike-data-flow/staging/*" {
  capabilities = ["read"]
}
```

**Decisions**:
1. Use AppRole for Dagster integration (machine authentication)
2. Use short-lived tokens with renewal
3. Implement least-privilege policies by environment

---

## Consolidated Decisions

| Decision | Status |
|----------|--------|
| Use integrated storage for single-node | ✅ Decided |
| TLS with Let's Encrypt certificates | ✅ Decided |
| hvac library for Python integration | ✅ Decided |
| Custom Dagster VaultSecretsResource | ✅ Decided |
| Hierarchical path structure | ✅ Decided |
| Dynamic secrets for databases | ✅ Decided |
| 90-day rotation for API keys | ✅ Decided |
| AppRole authentication for machines | ✅ Decided |
| Integrated storage snapshots | ✅ Decided |
| Auto-unseal with master key | ✅ Decided |

## Open Items for Phase 1

None - all unknowns resolved through research.

## References

1. [Vault Documentation](https://developer.hashicorp.com/vault/docs)
2. [hvac PyPI](https://pypi.org/project/hvac/)
3. [Dagster Resources](https://docs.dagster.io/concepts/resources)
4. [KV Secrets Engine v2](https://developer.hashicorp.com/vault/docs/secrets/kv/kv-v2)
5. [Database Secrets Engine](https://developer.hashicorp.com/vault/docs/secrets/databases)
