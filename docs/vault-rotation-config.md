# Vault Secret Rotation Configuration

This document describes how to configure and manage secret rotation policies for the HashiCorp Vault integration.

## Overview

Secret rotation is a critical security practice that regularly changes credentials, API keys, and certificates to minimize the impact of potential compromises. The bike-data-flow project provides automated secret rotation with support for multiple secret types and flexible scheduling options.

## Rotation Types

### Manual Rotation
Manual rotation requires explicit user action to rotate a secret. Use this for:
- Emergency credential rotation after a potential breach
- One-time rotation during maintenance windows
- Secrets that should not be rotated automatically

### Automatic Rotation
Automatic rotation occurs on a fixed schedule without user intervention. Use this for:
- Long-term credentials that need regular rotation
- Service accounts with defined rotation periods
- Any secret where automation improves security posture

### Scheduled Rotation
Scheduled rotation uses cron expressions for flexible scheduling. Use this for:
- Rotation during off-peak hours
- Rotation aligned with business schedules
- Complex rotation requirements (e.g., first Sunday of each quarter)

## Rotation Policy Configuration

### Basic Configuration

```python
from wrm_pipeline.wrm_pipeline.vault.models import SecretRotationPolicy, RotationType

# Create a rotation policy
policy = SecretRotationPolicy(
    secret_path="secret/data/bike-data-flow/production/database",
    rotation_type=RotationType.SCHEDULED,
    rotation_period_days=90,
    is_active=True,
    notify_on_failure=True,
    notify_emails=["admin@bike-data-flow"],
    max_retries=3,
    rollback_on_failure=True,
)
```

### Fields Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `secret_path` | str | Yes | Path to the secret in Vault |
| `rotation_type` | RotationType | Yes | Type of rotation (manual, automatic, scheduled) |
| `rotation_period_days` | int | No* | Days between rotations (*required for scheduled) |
| `rotation_script_path` | str | No | Path to custom rotation script |
| `cron_schedule` | str | No* | Cron expression (*alternative to rotation_period_days) |
| `is_active` | bool | No | Whether rotation is active (default: True) |
| `notify_on_failure` | bool | No | Send notifications on failure (default: True) |
| `notify_emails` | list[str] | No | Email addresses for notifications |
| `max_retries` | int | No | Maximum retry attempts (default: 3) |
| `rollback_on_failure` | bool | No | Revert to previous version on failure (default: True) |

### Cron Expression Format

The scheduler supports standard 5-field cron expressions:

```
┌───────────── minute (0 - 59)
│ ┌───────────── hour (0 - 23)
│ │ ┌───────────── day of month (1 - 31)
│ │ │ ┌───────────── month (1 - 12)
│ │ │ │ ┌───────────── day of week (0 - 6) (Sunday = 0)
│ │ │ │ │
* * * * *
```

#### Common Cron Examples

| Schedule | Cron Expression | Description |
|----------|-----------------|-------------|
| Daily at 2 AM | `0 2 * * *` | Every day at 2:00 AM |
| Weekly on Sunday | `0 2 * * 0` | Every Sunday at 2:00 AM |
| Monthly on 1st | `0 2 1 * *` | First day of each month at 2:00 AM |
| Quarterly | `0 2 1 1,4,7,10 *` | First day of each quarter at 2:00 AM |
| Every 6 hours | `0 */6 * * *` | Every 6 hours |

## Secret Type Rotation Procedures

### Database Credentials

Database credentials require special handling to ensure applications can continue functioning during rotation.

#### Configuration Example

```python
from wrm_pipeline.wrm_pipeline.vault.models import SecretRotationPolicy, RotationType

policy = SecretRotationPolicy(
    secret_path="secret/data/bike-data-flow/production/database",
    rotation_type=RotationType.SCHEDULED,
    rotation_period_days=90,
    rotation_script_path="/opt/vault/scripts/rotate-db-credentials.sh",
    is_active=True,
    notify_on_failure=True,
    notify_emails=["dba@bike-data-flow", "admin@bike-data-flow"],
    rollback_on_failure=True,
)
```

#### Custom Rotation Script

For database credentials, a custom rotation script may be required to handle:
- Generating new credentials
- Updating the database user
- Updating Vault with new credentials
- Revoking old credentials after grace period

Example script (`/opt/vault/scripts/rotate-db-credentials.sh`):

```bash
#!/bin/bash
# Database credential rotation script

SECRET_PATH="$1"
DB_HOST=$(vault kv get -field=host "$SECRET_PATH")
DB_USER=$(vault kv get -field=username "$SECRET_PATH")
DB_NAME=$(vault kv get -field=database "$SECRET_PATH")

# Generate new password
NEW_PASSWORD=$(openssl rand -base64 32)

# Update database user
psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" \
    -c "ALTER USER $DB_USER WITH PASSWORD '$NEW_PASSWORD';"

# Update Vault
vault kv put "$SECRET_PATH" \
    password="$NEW_PASSWORD" \
    previous_password="$OLD_PASSWORD" \
    rotated_at="$(date -Iseconds)"

echo "Database credentials rotated successfully"
```

### API Keys

API key rotation needs careful coordination to avoid service disruption.

#### Configuration Example

```python
policy = SecretRotationPolicy(
    secret_path="secret/data/bike-data-flow/production/api-key",
    rotation_type=RotationType.SCHEDULED,
    rotation_period_days=30,
    rotation_script_path="/opt/vault/scripts/rotate-api-key.sh",
    is_active=True,
    notify_on_failure=True,
    rollback_on_failure=True,
)
```

#### Rotation Behavior

1. Generate new API key
2. Store new key in Vault
3. Update application configuration with new key
4. Revoke old key after grace period (typically 24-48 hours)
5. Notify dependent services of key change

### Certificates

Certificate rotation uses the built-in certificate handler.

#### Configuration Example

```python
policy = SecretRotationPolicy(
    secret_path="secret/data/bike-data-flow/production/tls-cert",
    rotation_type=RotationType.SCHEDULED,
    rotation_period_days=365,
    cron_schedule="0 3 1 * *",  # First of month at 3 AM
    is_active=True,
    notify_on_failure=True,
    notify_emails=["security@bike-data-flow"],
)
```

#### Certificate Renewal Process

1. Trigger certificate renewal via ACME or CA
2. Validate new certificate
3. Deploy new certificate to services
4. Monitor for issues
5. Revoke old certificate after successful deployment

### Generic Secrets

Generic secrets use a simple rotation mechanism.

```python
policy = SecretRotationPolicy(
    secret_path="secret/data/bike-data-flow/production/iot-api-key",
    rotation_type=RotationType.SCHEDULED,
    rotation_period_days=60,
    is_active=True,
)
```

## Using the Rotation API

### Initialize Vault Client with Rotation

```python
from wrm_pipeline.wrm_pipeline.vault.client import VaultClient
from wrm_pipeline.wrm_pipeline.vault.models import VaultConnectionConfig, SecretRotationPolicy, RotationType

# Configure Vault connection
config = VaultConnectionConfig(
    vault_addr="https://vault.example.com:8200",
    auth_method="approle",
    role_id="your-role-id",
    secret_id="your-secret-id",
)

# Create client
client = VaultClient(config)
```

### Configure Rotation Policy

```python
# Create and configure rotation policy
policy = SecretRotationPolicy(
    secret_path="bike-data-flow/production/database",
    rotation_type=RotationType.SCHEDULED,
    rotation_period_days=90,
    is_active=True,
)

# Configure the policy
client.configure_rotation_policy(policy)
```

### Perform Immediate Rotation

```python
# Rotate a secret immediately
history = client.rotate_secret(
    secret_path="bike-data-flow/production/database",
    rotation_type=RotationType.MANUAL,
    secret_type="database",
)

print(f"Rotation status: {history.status}")
print(f"Rotation timestamp: {history.timestamp}")
```

### Check Rotation Status

```python
# Get rotation status for a secret
status = client.get_rotation_status("bike-data-flow/production/database")

print(f"Scheduled: {status['scheduled']}")
print(f"Last rotated: {status.get('last_rotated')}")
print(f"Next rotation: {status.get('next_rotation')}")
```

### List Secrets with Rotation

```python
# List all secrets with rotation configured
secrets = client.list_secrets_for_rotation()

for secret_path in secrets:
    print(f"- {secret_path}")
```

### Get Rotation History

```python
from wrm_pipeline.wrm_pipeline.vault.models import RotationStatus

# Get rotation history
history = client.get_rotation_history(
    secret_path="bike-data-flow/production/database",
    limit=10,
)

for entry in history:
    print(f"{entry.timestamp}: {entry.status.value} - {entry.error_message or 'OK'}")
```

### Get Rotation Statistics

```python
# Get rotation statistics
stats = client.get_rotation_stats()

print(f"Total rotations: {stats['total_rotations']}")
print(f"Success rate: {stats['success_rate'] * 100:.1f}%")
print(f"Average duration: {stats['avg_duration_seconds']:.2f}s")
```

## Monitoring Rotation Status

### Health Checks

Regularly check rotation status to ensure policies are functioning:

```bash
# Check all scheduled rotations
python -c "
from wrm_pipeline.wrm_pipeline.vault.client import VaultClient
from wrm_pipeline.wrm_pipeline.vault.models import VaultConnectionConfig

client = VaultClient(VaultConnectionConfig(...))

# Check due rotations
due = client._rotation_scheduler.get_due_rotations()
print(f'Due rotations: {len(due)}')

for policy in due:
    print(f'  - {policy.secret_path}')
"
```

### Rotation Alerts

Configure notifications for rotation failures:

```python
policy = SecretRotationPolicy(
    secret_path="secret/data/bike-data-flow/production/database",
    rotation_type=RotationType.SCHEDULED,
    rotation_period_days=90,
    notify_on_failure=True,
    notify_emails=["admin@bike-data-flow", "alerts@bike-data-flow"],
)
```

### Metrics

Track rotation metrics for observability:

```python
# Get comprehensive stats
stats = client.get_rotation_stats()

# Log or export to monitoring system
print({
    "total_rotations": stats["total_rotations"],
    "success_count": stats["success_count"],
    "failed_count": stats["failed_count"],
    "success_rate": stats["success_rate"],
    "avg_duration_seconds": stats["avg_duration_seconds"],
})
```

## Troubleshooting Rotation Failures

### Common Issues

#### 1. Rotation Script Not Found

**Symptom**: Rotation fails with "Rotation script not found"

**Solution**: Verify the script path exists and is executable:
```bash
ls -la /opt/vault/scripts/rotate-db-credentials.sh
chmod +x /opt/vault/scripts/rotate-db-credentials.sh
```

#### 2. Database Connection Failed

**Symptom**: Rotation fails with database connection error

**Solution**: Check database connectivity and credentials:
```bash
vault kv get secret/data/bike-data-flow/production/database
psql -h <host> -U <user> -d <database> -c "SELECT 1;"
```

#### 3. Vault Write Permission Denied

**Symptom**: Rotation fails with permission error

**Solution**: Verify Vault policy allows writes:
```bash
vault policy read bike-data-flow-secrets
```

#### 4. Rollback Triggered

**Symptom**: Rotation shows "rolled_back" status

**Solution**: Check rotation history for original error:
```python
history = client.get_rotation_history(secret_path="...")
for entry in history:
    if entry.status.value == "rolled_back":
        print(f"Original error: {entry.error_message}")
```

### Debug Mode

Enable debug logging for rotation operations:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Rotation operations will now log detailed information
client.rotate_secret("path", secret_type="database")
```

### Manual Recovery

If automatic rotation fails repeatedly:

```python
# Perform manual rotation with detailed error handling
try:
    history = client.rotate_secret(
        secret_path="path",
        rotation_type=RotationType.MANUAL,
        secret_type="database",
    )
except Exception as e:
    print(f"Rotation failed: {e}")
    # Manual intervention required
```

## Best Practices

1. **Test Rotation Policies**: Always test rotation in non-production first
2. **Monitor Closely**: Watch logs during initial rotation schedule
3. **Grace Periods**: Allow time for applications to pick up new credentials
4. **Rollback Strategy**: Ensure rollback works before enabling automatic rotation
5. **Documentation**: Document custom rotation scripts and procedures
6. **Regular Reviews**: Periodically review rotation policies and schedules
7. **Access Control**: Limit who can configure rotation policies
8. **Notifications**: Configure alerts for rotation failures

## Security Considerations

- **Rotation Script Security**: Rotation scripts should be owned by vault user
- **Credential Storage**: Never store rotation scripts with embedded credentials
- **Audit Logging**: All rotation operations are logged in Vault audit logs
- **Access Control**: Only authorized principals can configure rotation
- **Network Security**: Rotation scripts should run over secure connections
