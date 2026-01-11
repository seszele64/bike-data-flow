# Implementation Plan: HashiCorp Vault Secrets Management

**Branch**: `001-hashicorp-vault-integration` | **Date**: 2026-01-10 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-hashicorp-vault-integration/spec.md`

## Summary

Implement HashiCorp Vault for secure secrets management on Hetzner VPS, replacing environment variables and .env files with centralized Vault storage. Integrate with Dagster resources for secure secret retrieval in pipelines. All secrets stored in KV v2 secrets engine with AppRole authentication, <500ms retrieval latency, and single-node deployment.

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: hvac (Vault Python client), Dagster resources  
**Storage**: Vault integrated storage (raft), Hetzner Object Storage for backups  
**Testing**: pytest, integration tests for Vault connectivity  
**Target Platform**: Linux server (Hetzner VPS)  
**Project Type**: Infrastructure/Resource integration  
**Performance Goals**: <500ms secret retrieval latency (p95), >80% cache hit rate  
**Constraints**: Single-node deployment, TLS required, AppRole authentication  
**Scale/Scope**: 16-24 hours estimate, 7 user stories (3 P1, 3 P2, 1 P3)

## Constitution Check

✅ All constitution requirements satisfied:
- Secrets management follows security principles (VIII)
- Performance targets defined (<500ms latency)
- Structured logging in Vault audit logs
- No authentication required for public data access

## Project Structure

### Documentation (this feature)

```
specs/001-hashicorp-vault-integration/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output - Research findings
├── data-model.md        # Phase 1 output - Entity definitions
├── quickstart.md        # Phase 1 output - Getting started guide
├── contracts/           # Phase 1 output - API specifications
│   ├── vault-api.yaml   # OpenAPI spec for Vault service
│   └── dagster-resource.md  # Dagster resource interface
├── spec.md              # Feature specification
└── checklists/
    └── requirements.md  # Requirements checklist
```

### Source Code (repository root)

```
backend/
├── src/
│   ├── vault/           # Vault integration module
│   │   ├── __init__.py
│   │   ├── client.py    # Vault client wrapper
│   │   ├── resource.py  # Dagster resource
│   │   ├── models.py    # Pydantic models
│   │   └── exceptions.py
│   └── migration/       # Secret migration tools
└── tests/
    ├── unit/
    └── integration/
        └── vault/       # Vault integration tests
```

**Structure Decision**: Vault integration follows backend structure with dedicated `vault/` module for secrets management, separate `migration/` module for .env to Vault migration tools.

## Complexity Tracking

No constitution violations - all design decisions align with established principles.

## Research Summary

Key decisions from research (see [research.md](research.md)):

| Decision | Rationale |
|----------|-----------|
| Integrated storage | Single-node deployment, no external storage needed |
| Let's Encrypt TLS | Free, automated certificates, industry standard |
| hvac library | Official Python client, well-maintained |
| KV v2 secrets | Version support, metadata, efficient |
| AppRole auth | Machine-to-machine, least-privilege, secure |
| 5-minute cache TTL | Balance freshness and performance |
| Dynamic secrets for DB | Automatic rotation, short-lived credentials |
| Integrated storage snapshots | Simple backup/restore for single-node |

## Design Summary

Key entities defined (see [data-model.md](data-model.md)):

- **Secret**: Stored credential with metadata, version, and custom metadata
- **AccessPolicy**: Fine-grained permissions for secret paths
- **VaultConfig**: Server configuration (storage, listener, TLS, seal)
- **VaultClientConfig**: Client connection configuration
- **SecretRotationPolicy**: Automated rotation configuration
- **AuditLog**: Secret access records for compliance
- **VaultHealth**: Server health status monitoring

## Contracts Summary

API contracts defined (see [contracts/](contracts/)):

- **vault-api.yaml**: OpenAPI 3.1 spec for Vault REST API
  - Secrets CRUD operations
  - Policy management
  - AppRole authentication
  - Health checks

- **dagster-resource.md**: Dagster resource interface
  - `VaultSecretsResource` class
  - Configuration schema
  - Exception hierarchy
  - Performance requirements

## Quickstart Summary

Step-by-step guide (see [quickstart.md](quickstart.md)):

1. Deploy Vault server on Hetzner VPS
2. Configure TLS and initialize
3. Enable KV v2 and AppRole auth
4. Create policies and store secrets
5. Implement Dagster resource
6. Update pipeline definitions
7. Verify integration

## Remaining Items for Phase 2

Phase 2 tasks (to be created by `/speckit.tasks` command):

1. Create `backend/src/vault/` module structure
2. Implement `VaultClient` wrapper class with caching
3. Create `VaultSecretsResource` for Dagster
4. Write Pydantic models for all entities
5. Implement secret migration script
6. Create unit tests for vault module
7. Write integration tests for Dagster resource
8. Update existing Dagster definitions
9. Document deployment procedures
10. Create backup and restore scripts
