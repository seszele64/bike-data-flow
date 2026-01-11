# Secret Migration Guide

This guide provides step-by-step procedures for migrating secrets from environment variables to HashiCorp Vault in the bike-data-flow project.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Pre-Migration Checklist](#pre-migration-checklist)
4. [Phase 1: Scan and Identify Secrets](#phase-1-scan-and-identify-secrets)
5. [Phase 2: Write Secrets to Vault](#phase-2-write-secrets-to-vault)
6. [Phase 3: Update Application Code](#phase-3-update-application-code)
7. [Phase 4: Verify Migration](#phase-4-verify-migration)
8. [Phase 5: Clean Up Environment Files](#phase-5-clean-up-environment-files)
9. [Rollback Procedures](#rollback-procedures)
10. [Troubleshooting](#troubleshooting)

## Overview

This migration moves all sensitive configuration (API keys, database credentials, tokens, etc.) from environment variables and `.env` files into HashiCorp Vault. Benefits include:

- **Centralized secret management**
- **Audit logging of secret access**
- **Automated secret rotation capabilities**
- **Access control policies**
- **No hardcoded secrets in the codebase**

### Migration Strategy

The migration is performed in phases to minimize risk:

1. **Scan**: Identify all secrets in the codebase and environment
2. **write_to_file**: Securely transfer secrets to Vault
3. **Update**: Modify application code to use Vault
4. **Verify**: Confirm the migration works correctly
5. **Clean**: Remove sensitive data from `.env` files

## Prerequisites

### 1. Vault Server

Ensure your Vault server is running and accessible:

```bash
# Check Vault health
curl https://vault.internal.bike-data-flow.com:8200/v1/sys/health

# Expected response format:
# {
#   "initialized": true,
#   "sealed": false,
#   "standby": false,
#   "version": "1.15.0"
# }
```

### 2. AppRole Authentication

Ensure AppRole is enabled and you have credentials:

```bash
# Enable AppRole auth method (if not already enabled)
vault auth enable approle

# Create a role for the application
vault write auth/approle/role/bike-data-flow \
    token_policies="bike-data-flow-policy" \
    token_ttl=1h \
    token_max_ttl=4h

# Get role_id and secret_id
vault read auth/approle/role/bike-data-flow/role-id
vault write -f auth/approle/role/bike-data-flow/secret-id
```

### 3. Policies Created

Ensure the required Vault policies are in place. See [docs/vault-policies.md](vault-policies.md) for details.

### 4. Required Permissions

You need:
- `write` access to `secret/data/bike-data-flow/*`
- `read` access to verify secrets after writing

### 5. Environment Variables

Set these environment variables for the migration scripts:

```bash
export VAULT_ADDR="https://vault.internal.bike-data-flow.com:8200"
export VAULT_ROLE_ID="your-role-id"
export VAULT_SECRET_ID="your-secret-id"
```

## Pre-Migration Checklist

Before starting the migration, complete these steps:

- [ ] Vault server is running and healthy
- [ ] AppRole is configured with appropriate policies
- [ ] Backup of current `.env` files created
- [ ] Development/Testing environment available
- [ ] Rollback plan documented
- [ ] Team notified of migration window

### Create Backup

```bash
# Backup all .env files
cp .env .env.backup.$(date +%Y%m%d)
cp .env.local .env.local.backup.$(date +%Y%m%d)
cp .env.production .env.production.backup.$(date +%Y%m%d)
```

## Phase 1: Scan and Identify Secrets

Use the secret scanner to identify all secrets in the codebase and environment files.

### Run Secret Scanner

```bash
cd wrm_pipeline
python -m wrm_pipeline.wrm_pipeline.migration.scan_secrets --path . --output scan_results.json
```

### Run Environment Variable Detector

```bash
python -m wrm_pipeline.wrm_pipeline.migration.detect_env --path . --output env_detection.json
```

### Review Results

The scan results will show:
- Hardcoded secrets in source code
- Sensitive environment variables
- Files containing secrets

Example output:
```
SCAN SUMMARY:
  - Hardcoded secrets in code: 5
  - Sensitive env variables: 12
  - Total env variables to migrate: 25
```

### Document Secrets to Migrate

Review the JSON output and document secrets by category:

| Category | Count | Examples |
|----------|-------|----------|
| Database | 3 | POSTGRES_PASSWORD, DB_HOST |
| API Keys | 5 | API_KEY, AUTH_TOKEN |
| Storage | 2 | S3_ACCESS_KEY, MINIO_SECRET |
| App Config | 15 | Various |

## Phase 2: Write Secrets to Vault

Write the identified secrets to Vault using the secret writer.

### Validate Vault Paths

First, validate your intended path structure:

```bash
python -m wrm_pipeline.wrm_pipeline.migration.validate_paths \
    --paths bike-data-flow/production/database/postgres_password \
    --paths bike-data-flow/production/api/external_api_key \
    --vault-addr $VAULT_ADDR \
    --auth-method approle \
    --role-id $VAULT_ROLE_ID \
    --secret-id $VAULT_SECRET_ID \
    --check-permissions
```

### Create Secrets JSON File

Create a JSON file with the secrets to migrate:

```json
{
  "database": {
    "postgres_host": "localhost",
    "postgres_port": "5432",
    "postgres_user": "bike_data_flow",
    "postgres_password": "${POSTGRES_PASSWORD}"
  },
  "api": {
    "external_api_key": "${API_KEY}",
    "auth_token": "${AUTH_TOKEN}"
  },
  "storage": {
    "s3_access_key": "${S3_ACCESS_KEY}",
    "s3_secret_key": "${S3_SECRET_KEY}"
  }
}
```

### Dry Run First

Always run a dry run first:

```bash
python -m wrm_pipeline.wrm_pipeline.migration.write_to_vault \
    --vault-addr $VAULT_ADDR \
    --auth-method approle \
    --role-id $VAULT_ROLE_ID \
    --secret-id $VAULT_SECRET_ID \
    --secrets secrets.json \
    --dry-run
```

### Write Secrets

After confirming the dry run looks correct:

```bash
python -m wrm_pipeline.wrm_pipeline.migration.write_to_vault \
    --vault-addr $VAULT_ADDR \
    --auth-method approle \
    --role-id $VAULT_ROLE_ID \
    --secret-id $VAULT_SECRET_ID \
    --secrets secrets.json \
    --verify
```

### Verify Secrets in Vault

```bash
# List secrets
vault kv list secret/bike-data-flow/production/

# Read a specific secret
vault kv get secret/bike-data-flow/production/database/postgres_password
```

## Phase 3: Update Application Code

Update the application code to retrieve secrets from Vault instead of environment variables.

### Update Configuration Module

Modify [`wrm_pipeline/wrm_pipeline/config.py`](../wrm_pipeline/wrm_pipeline/config.py):

```python
import os
from wrm_pipeline.wrm_pipeline.vault.client import VaultClient
from wrm_pipeline.wrm_pipeline.vault.models import VaultConnectionConfig

# Initialize Vault client
vault_config = VaultConnectionConfig(
    vault_addr=os.environ.get("VAULT_ADDR", "https://vault.internal.bike-data-flow.com:8200"),
    auth_method="approle",
    role_id=os.environ.get("VAULT_ROLE_ID"),
    secret_id=os.environ.get("VAULT_SECRET_ID"),
)

vault_client = VaultClient(vault_config)

# Retrieve secrets from Vault
def get_vault_secret(path: str) -> dict:
    """Get a secret from Vault."""
    secret = vault_client.get_secret(path)
    return secret.data

# Database configuration from Vault
DB_CONFIG = get_vault_secret("bike-data-flow/production/database")

# Non-sensitive config can remain as environment variables
S3_ENDPOINT_URL = os.environ.get('S3_ENDPOINT_URL')
BUCKET_NAME = os.environ.get('BUCKET_NAME')
```

### Update Dagster Resources

The [`wrm_pipeline/wrm_pipeline/definitions.py`](../wrm_pipeline/wrm_pipeline/definitions.py) already has Vault integration. Ensure it's properly configured:

```python
from dagster import Definitions, env_var
from wrm_pipeline.wrm_pipeline.vault import vault_secrets_resource

defs = Definitions(
    resources={
        "vault": vault_secrets_resource.configured({
            "vault_addr": env_var("VAULT_ADDR"),
            "auth_method": "approle",
            "role_id": env_var("VAULT_ROLE_ID"),
            "secret_id": env_var("VAULT_SECRET_ID"),
        }),
        # ... other resources
    },
    # ... assets and jobs
)
```

### Update Assets to Use Vault

For assets that need secrets, use the `vault` resource:

```python
from dagster import asset, AssetExecutionContext

@asset(
    required_resource_keys={"vault", "s3_resource"},
    group_name="data_processing"
)
def processed_data_asset(context: AssetExecutionContext) -> None:
    # Get database credentials from Vault
    db_config = context.resources.vault.get_database_credentials(
        "bike-data-flow/production/database"
    )
    
    # Use the credentials
    connection = connect_to_database(
        host=db_config["host"],
        user=db_config["username"],
        password=db_config["password"],
    )
```

## Phase 4: Verify Migration

Run verification checks to ensure everything works correctly.

### Run Verification Script

```bash
python -m wrm_pipeline.wrm_pipeline.migration.migrate \
    --vault-addr $VAULT_ADDR \
    --auth-method approle \
    --role-id $VAULT_ROLE_ID \
    --secret-id $VAULT_SECRET_ID \
    --phase verify \
    --confirm
```

### Manual Verification Checklist

- [ ] Vault health check passes
- [ ] All secrets readable from Vault
- [ ] Application starts without errors
- [ ] Data pipelines execute successfully
- [ ] No secrets found in codebase scan
- [ ] Environment variables cleaned up

### Test Secret Retrieval

```python
# Test in Python
from wrm_pipeline.wrm_pipeline.vault.client import VaultClient

client = VaultClient(vault_config)
secret = client.get_secret("bike-data-flow/production/database")
print("Successfully retrieved secret from Vault")
```

## Phase 5: Clean Up Environment Files

After verification, clean up sensitive values from `.env` files.

### Create .env.example

Create a template file without sensitive values:

```bash
# Copy original
cp .env .env.backup

# Create .env.example with placeholders
grep -v "^#" .env | while IFS= read -r line; do
    if [[ $line == *"="* ]]; then
        key="${line%%=*}"
        if [[ $key == *"PASSWORD"* || $key == *"SECRET"* || $key == *"API_KEY"* || $key == *"TOKEN"* ]]; then
            echo "${key}=#MIGRATED_TO_VAULT_${key}"
        else
            echo "$line"
        fi
    fi
done > .env.example
```

### Example .env.example

```bash
# Database Configuration
# All credentials moved to Vault at bike-data-flow/production/database/
POSTGRES_HOST=#MIGRATED_TO_VAULT_POSTGRES_HOST
POSTGRES_PORT=#MIGRATED_TO_VAULT_POSTGRES_PORT
POSTGRES_USER=#MIGRATED_TO_VAULT_POSTGRES_USER
POSTGRES_PASSWORD=#MIGRATED_TO_VAULT_POSTGRES_PASSWORD

# Non-sensitive configuration
S3_ENDPOINT_URL=https://s3.hetzner.example.com
BUCKET_NAME=bike-data
WRM_STATIONS_S3_PREFIX=bike-data/
```

### Update .env Files

Replace sensitive values with comments indicating migration:

```bash
# For each sensitive variable, replace the value
# BEFORE: POSTGRES_PASSWORD=mysecretpassword
# AFTER:  POSTGRES_PASSWORD=#MIGRATED_TO_VAULT_POSTGRES_PASSWORD
```

## Rollback Procedures

If issues arise, use the rollback procedure.

### 1. Stop Application

```bash
# Stop any running Dagster instances
pkill -f dagster
```

### 2. Restore Environment Variables

```bash
# Restore from backup
cp .env.backup.$(date +%Y%m%d) .env
source .env
```

### 3. Rollback Vault Secrets

```bash
python -m wrm_pipeline.wrm_pipeline.migration.migrate \
    --vault-addr $VAULT_ADDR \
    --auth-method approle \
    --role-id $VAULT_ROLE_ID \
    --secret-id $VAULT_SECRET_ID \
    --rollback \
    --confirm
```

### 4. Revert Code Changes

```bash
# Revert to previous version
git checkout HEAD~1 -- wrm_pipeline/wrm_pipeline/config.py
```

### 5. Verify Rollback

```bash
# Confirm application starts
python -m wrm_pipeline.wrm_pipeline.main

# Check logs for errors
tail -f logs/app.log
```

## Troubleshooting

### Common Issues

#### 1. Vault Connection Errors

**Symptom**: `VaultConnectionError: Connection refused`

**Solution**:
```bash
# Verify Vault is running
curl https://vault.internal.bike-data-flow.com:8200/v1/sys/health

# Check TLS certificates
openssl s_client -connect vault.internal.bike-data-flow.com:8200
```

#### 2. Authentication Failures

**Symptom**: `VaultAuthenticationError: invalid role ID or secret ID`

**Solution**:
```bash
# Verify AppRole credentials
vault read auth/approle/role/bike-data-flow/role-id

# Check policy is attached
vault read auth/approle/role/bike-data-flow
```

#### 3. Permission Denied

**Symptom**: `Permission denied` when writing secrets

**Solution**:
```bash
# Check policy
vault policy read bike-data-flow-policy

# Verify policy is attached to the role
vault read auth/approle/role/bike-data-flow
```

#### 4. Secret Not Found

**Symptom**: `VaultSecretNotFoundError` after migration

**Solution**:
```bash
# List secrets at path
vault kv list secret/bike-data-flow/production/

# Verify exact path
vault kv get secret/bike-data-flow/production/database/postgres_password
```

### Debug Commands

```bash
# List all policies
vault policy list

# Read policy
vault policy read bike-data-flow-policy

# Check token capabilities
vault token capabilities <token> secret/bike-data-flow/*

# Enable audit logging
vault audit list
```

### Getting Help

If issues persist:

1. Check Vault server logs: `journalctl -u vault`
2. Review audit logs in `/var/log/vault/audit.log`
3. Contact the infrastructure team
4. Refer to [docs/vault-troubleshooting.md](vault-troubleshooting.md)

## Quick Reference

### Migration Commands

| Action | Command |
|--------|---------|
| Scan secrets | `python -m wrm_pipeline.wrm_pipeline.migration.scan_secrets` |
| Detect env vars | `python -m wrm_pipeline.wrm_pipeline.migration.detect_env` |
| Write to Vault | `python -m wrm_pipeline.wrm_pipeline.migration.write_to_vault` |
| Validate paths | `python -m wrm_pipeline.wrm_pipeline.migration.validate_paths` |
| Run full migration | `python -m wrm_pipeline.wrm_pipeline.migration.migrate --phase all --confirm` |
| Rollback | `python -m wrm_pipeline.wrm_pipeline.migration.migrate --rollback --confirm` |

### Vault Path Structure

```
secret/
└── bike-data-flow/
    ├── production/
    │   ├── database/
    │   │   ├── postgres_host
    │   │   ├── postgres_password
    │   │   └── postgres_port
    │   ├── api/
    │   │   ├── external_api_key
    │   │   └── auth_token
    │   └── storage/
    │       ├── s3_access_key
    │       └── s3_secret_key
    └── staging/
        └── ...
```

### Environment Variables Required

| Variable | Description |
|----------|-------------|
| `VAULT_ADDR` | Vault server URL |
| `VAULT_ROLE_ID` | AppRole role ID |
| `VAULT_SECRET_ID` | AppRole secret ID |
| `VAULT_NAMESPACE` | Enterprise namespace (optional) |
