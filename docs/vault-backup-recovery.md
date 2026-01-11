# Vault Backup and Recovery Documentation

This document describes the backup and disaster recovery procedures for HashiCorp Vault in the bike-data-flow project.

## Table of Contents

1. [Overview](#overview)
2. [Backup Types](#backup-types)
3. [Backup Procedures](#backup-procedures)
4. [Restore Procedures](#restore-procedures)
5. [Backup Scheduling](#backup-scheduling)
6. [Retention Policies](#retention-policies)
7. [Remote Storage Configuration](#remote-storage-configuration)
8. [Troubleshooting](#troubleshooting)

## Overview

The vault backup and recovery system provides:

- **Automated backups**: Daily, weekly, and monthly scheduled backups
- **Encrypted backups**: All backups are encrypted with GPG
- **Remote storage**: Backups are stored in Hetzner Object Storage
- **Retention policies**: Configurable backup retention with cold storage archiving
- **Point-in-time recovery**: Restore to any point where a backup exists

### Backup Strategy Summary

| Backup Type | Frequency | Retention | Storage |
|-------------|-----------|-----------|---------|
| Full (Raft Snapshot) | Weekly | 30 days | Local + Remote |
| Incremental | Daily | 7 days | Local + Remote |
| Export | On-demand | As configured | Local |

## Backup Types

### 1. Raft Snapshots (Recommended)

Vault's built-in snapshot functionality for Raft storage backend:

- **Advantages**:
  - Complete cluster state backup
  - Fast restore time
  - Includes all secrets, policies, and configuration
  - Supports point-in-time recovery

- **Usage**:
  ```bash
  # Create full snapshot
  ./scripts/vault/backup-vault.sh --full --encrypt --upload
  
  # Create incremental snapshot
  ./scripts/vault/backup-vault.sh --encrypt --upload
  ```

### 2. Manual Export

JSON export of all secrets (fallback for non-Raft storage):

- **Advantages**:
  - Works with any storage backend
  - Human-readable format
  - Easy to audit

- **Limitations**:
  - Cannot restore policies or configuration
  - May miss metadata
  - Not suitable for complete disaster recovery

### 3. Backup Comparison

| Feature | Raft Snapshot | Manual Export |
|---------|---------------|---------------|
| Restore speed | Fast | Slow |
| Complete recovery | Yes | Partial |
| Policy restore | Yes | No |
| Configuration restore | Yes | No |
| Storage backend support | Raft only | All |

## Backup Procedures

### Using the Backup Script

```bash
# Navigate to scripts directory
cd /home/tr1x/programming/bike-data-flow

# Create encrypted backup and upload to remote storage
./scripts/vault/backup-vault.sh --full --encrypt --upload

# Create incremental backup (faster, smaller)
./scripts/vault/backup-vault.sh --encrypt --upload

# Keep only last 7 backups locally
./scripts/vault/backup-vault.sh --encrypt --upload --keep 7

# Dry run to test configuration
./scripts/vault/backup-vault.sh --full --encrypt --dry-run
```

### Environment Variables

Configure backup behavior with environment variables:

```bash
export VAULT_ADDR="https://vault.bike-data-flow.com:8200"
export VAULT_TOKEN="your-root-token"
export BACKUP_DIR="/var/backups/vault"
export ENCRYPTION_KEY="gpg-key-id"
export ENCRYPTION_RECIPIENT="admin@bike-data-flow"
export HETZNER_ENDPOINT="https://s3.hetzner.com"
export HETZNER_ACCESS_KEY="your-access-key"
export HETZNER_SECRET_KEY="your-secret-key"
export HETZNER_BUCKET="vault-backups"
export RETENTION_COUNT=7
```

### Using Python API

```python
from wrm_pipeline.wrm_pipeline.vault.backup import (
    create_snapshot_manager,
    create_backup_scheduler,
    BackupType,
)

# Create snapshot manager
manager = create_snapshot_manager(
    vault_addr="https://vault.bike-data-flow.com:8200",
    vault_token="your-token",
    storage_backend="hetzner",
    storage_config={
        "endpoint": "https://s3.hetzner.com",
        "access_key": "your-access-key",
        "secret_key": "your-secret-key",
        "bucket": "vault-backups",
    },
)

# Create backup
snapshot = manager.create_snapshot(
    backup_type=BackupType.FULL,
    encrypt=True,
    upload=True,
    retention_days=30,
)

# List snapshots
snapshots = manager.list_snapshots(
    status=BackupStatus.COMPLETED,
    limit=10,
)

# Generate report
report = manager.generate_report()
```

## Restore Procedures

### Using the Restore Script

```bash
# Restore from latest backup
./scripts/vault/restore-vault.sh

# Restore from specific backup file
./scripts/vault/restore-vault.sh --backup /var/backups/vault/vault-backup-full-20240110_020000.snap

# Dry run to preview restore
./scripts/vault/restore-vault.sh --backup /var/backups/vault/vault-backup.snap.gpg --dry-run

# Restore to point-in-time
./scripts/vault/restore-vault.sh --point-in-time "2024-01-10T03:00:00Z"

# Force restore without confirmation
./scripts/vault/restore-vault.sh --backup backup.snap --force
```

### Restore Verification Steps

1. **Check Vault status**:
   ```bash
   curl -s https://vault.bike-data-flow.com:8200/v1/sys/health | jq
   ```

2. **Verify secrets are restored**:
   ```bash
   # List secret paths
   vault kv list secret/
   
   # Check specific secrets
   vault kv get secret/bike-data-flow/production/database
   ```

3. **Verify policies**:
   ```bash
   vault policy list
   vault policy read dagster-secrets
   ```

### Python Restore API

```python
from wrm_pipeline.wrm_pipeline.vault.backup import SnapshotManager

# Create manager
manager = create_snapshot_manager(...)

# Restore from snapshot
result = manager.restore_snapshot(
    snapshot_id="vault-backup-full-20240110_020000-abc123",
    dry_run=False,
    force=False,
)

# Verify restore
is_valid = manager.verify_snapshot("vault-backup-full-20240110_020000-abc123")
```

## Backup Scheduling

### Cron Configuration

Add to crontab (`crontab -e`):

```bash
# Daily incremental backup at 2 AM
0 2 * * * /home/tr1x/programming/bike-data-flow/scripts/vault/backup-vault.sh --encrypt --upload --keep 7

# Weekly full backup on Sunday at 3 AM
0 3 * * 0 /home/tr1x/programming/bike-data-flow/scripts/vault/backup-vault.sh --full --encrypt --upload --keep 4

# Monthly full backup on 1st at 4 AM
0 4 1 * * /home/tr1x/programming/bike-data-flow/scripts/vault/backup-vault.sh --full --encrypt --upload --keep 12
```

### Python Scheduler

```python
from wrm_pipeline.wrm_pipeline.vault.backup import (
    create_backup_scheduler,
    BackupType,
)

# Create scheduler
scheduler = create_backup_scheduler(
    snapshot_manager=manager,
    notify_on_failure=True,
    notify_emails=["admin@bike-data-flow"],
)

# Schedule backups
scheduler.schedule_daily(hour=2, minute=0, retention_days=7)
scheduler.schedule_weekly(day_of_week=0, hour=3, retention_days=30)
scheduler.schedule_monthly(day_of_month=1, hour=4, retention_days=365)

# Manual backup
result = scheduler.run_backup(
    backup_type=BackupType.FULL,
    encrypt=True,
    upload=True,
)
```

### Backup History

```python
# Get backup history
history = scheduler.get_backup_history(limit=50)

# Get statistics
stats = scheduler.get_backup_stats()
print(f"Total backups: {stats['total_backups']}")
print(f"Success rate: {stats['success_rate']:.1f}%")
```

## Retention Policies

### Default Policy

The default retention policy:

- **Daily backups**: Keep last 7
- **Weekly backups**: Keep last 4
- **Monthly backups**: Keep last 12
- **Yearly backups**: Keep last 3
- **Minimum retention**: Always keep 2 backups

### Custom Policy

```python
from wrm_pipeline.wrm_pipeline.vault.backup import RetentionPolicy

custom_policy = RetentionPolicy(
    name="custom-policy",
    daily_backups=14,       # Keep 14 daily backups
    weekly_backups=8,       # Keep 8 weekly backups
    monthly_backups=24,     # Keep 24 monthly backups
    yearly_backups=5,       # Keep 5 yearly backups
    archive_to_cold_storage=True,
    cold_storage_days=90,
    cold_storage_backend=StorageBackend.HETZNER_OBJECT_STORAGE,
    delete_after_archive=True,
    retain_minimum=3,
)

# Apply policy
scheduler.cleanup_old_backups(retention_policy=custom_policy)
```

### Cleanup Old Backups

```python
# Cleanup using default policy
deleted_count = manager.cleanup_expired()

# Cleanup using custom policy
deleted_count = manager.cleanup_expired(retention_policy=custom_policy)
```

## Remote Storage Configuration

### Hetzner Object Storage Setup

1. **Create bucket in Hetzner Robot**:
   - Navigate to Object Storage
   - Create new bucket: `vault-backups`
   - Note the endpoint: `https://s3.hetzner.com`

2. **Create access credentials**:
   - Generate access key and secret key
   - Grant read/write access to the bucket

3. **Configure environment**:
   ```bash
   export HETZNER_ENDPOINT="https://s3.hetzner.com"
   export HETZNER_ACCESS_KEY="your-access-key"
   export HETZNER_SECRET_KEY="your-secret-key"
   export HETZNER_BUCKET="vault-backups"
   ```

### Python Storage Configuration

```python
from wrm_pipeline.wrm_pipeline.vault.backup import (
    HetznerStorageConfig,
    HetznerObjectStorageBackend,
)

config = HetznerStorageConfig(
    endpoint="https://s3.hetzner.com",
    access_key="your-access-key",
    secret_key="your-secret-key",
    bucket="vault-backups",
    region="us-east-1",
    storage_class="STANDARD",
)

storage_backend = HetznerObjectStorageBackend(config)
manager = SnapshotManager(vault_client, storage_backend=storage_backend)
```

### Storage Commands

```bash
# List remote backups
rclone listremotes
rclone ls hetzner:vault-backups

# Sync backups
rclone sync /var/backups/vault hetzner:vault-backups --verbose

# Download specific backup
rclone copy hetzner:vault-backups/full/vault-backup-full-20240110.snap /tmp/
```

## Troubleshooting

### Common Issues

#### 1. Backup Fails with "Vault is Sealed"

**Symptom**: Backup script exits with error about sealed Vault

**Solution**:
```bash
# Check seal status
vault status

# Unseal if needed (requires unseal keys)
vault unseal <unseal-key-1>
vault unseal <unseal-key-2>
vault unseal <unseal-key-3>
```

#### 2. GPG Encryption Fails

**Symptom**: GPG key not found or decryption fails

**Solution**:
```bash
# List available keys
gpg --list-keys

# Import key if missing
gpg --import /path/to/private-key.gpg

# Configure encryption key in script
export ENCRYPTION_KEY="key-id"
export ENCRYPTION_RECIPIENT="email@example.com"
```

#### 3. Hetzner Upload Fails

**Symptom**: Upload to object storage fails with authentication error

**Solution**:
```bash
# Test credentials
rclone config show hetzner

# Verify bucket exists
rclone lsd hetzner:

# Check permissions
aws s3 ls s3://vault-backups/ \
  --endpoint-url=https://s3.hetzner.com \
  --access-key=your-access-key \
  --secret-key=your-secret-key
```

#### 4. Restore Fails with Incompatible Snapshot

**Symptom**: Restore fails with "incompatible snapshot" error

**Solution**:
```bash
# Verify Vault version matches snapshot source
vault version

# Check if snapshot is from same cluster
# Raft snapshots are cluster-specific

# May need to restore to same version of Vault
# Check release notes for migration requirements
```

#### 5. Large Backup Timeout

**Symptom**: Backup times out for large datasets

**Solution**:
```bash
# Increase timeout in environment
export VAULT_TIMEOUT=120

# Use incremental backups more frequently
# Split backup into smaller chunks
```

### Log Locations

- **Backup log**: `/var/backups/vault/backup.log`
- **Restore log**: `/var/backups/vault/restore.log`
- **Vault logs**: `/var/log/vault/`

### Verification Commands

```bash
# Verify backup file integrity
sha256sum /var/backups/vault/*.snap*

# Verify GPG signature
gpg --verify /var/backups/vault/*.snap.gpg

# List all backups
ls -la /var/backups/vault/vault-backup-*.snap*

# Check backup metadata
cat /var/backups/vault/backup-metadata.json | jq
```

### Getting Help

If issues persist:

1. Check logs for detailed error messages
2. Verify all prerequisites are met
3. Test with `--dry-run` flag first
4. Contact system administrator

## Best Practices

1. **Test restores regularly**:
   - Schedule quarterly disaster recovery tests
   - Document restore procedures
   - Verify team familiarity with process

2. **Secure backup storage**:
   - Encrypt all backups
   - Store encryption keys separately
   - Limit access to backup storage
   - Use separate credentials for backup operations

3. **Monitor backup health**:
   - Set up alerts for failed backups
   - Monitor backup size trends
   - Track success/failure rates
   - Review retention policy effectiveness

4. **Document recovery procedures**:
   - Maintain runbooks for common scenarios
   - Include contact information
   - Document decision trees for different failure modes

## Related Documentation

- [Vault Server Configuration](vault-server-config.md)
- [Vault Unseal Management](vault-unseal-management.md)
- [Emergency Recovery Procedures](vault-emergency-recovery.md)
- [Vault Policies](vault-policies.md)
