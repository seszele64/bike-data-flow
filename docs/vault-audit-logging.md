# Vault Audit Logging Documentation

This document describes the audit logging configuration, usage, and best practices for HashiCorp Vault in the bike-data-flow project.

## Overview

Vault audit logging provides a comprehensive record of all access and operations performed on secrets. This is essential for:
- **Security monitoring**: Track who accessed what secrets and when
- **Compliance**: Meet requirements for PCI-DSS, SOC 2, HIPAA, and other standards
- **Troubleshooting**: Debug access issues and policy problems
- **Forensics**: Investigate security incidents

## Audit Device Configuration

### File-Based Audit Device

The primary audit device writes logs to a local file in JSON format.

**Configuration File**: [`config/vault/audit-devices.hcl`](../config/vault/audit-devices.hcl)

**Key Settings**:
- `path`: Location of the audit log file (`/var/log/vault/audit.log`)
- `log_raw`: Records the full request/response cycle
- `format`: JSON format for structured logging
- `rotation_interval`: Automatic log rotation every 24 hours
- `rotation_max_files`: Keep up to 10 rotated log files

**Enabling the Audit Device**:

```bash
# Enable file audit device
vault audit enable file file_path=/var/log/vault/audit.log

# Or with options
vault audit enable -path=vault-audit file \
    file_path=/var/log/vault/audit.log \
    log_raw=true \
    hmac_key="your-shared-key"
```

**Include in Vault Configuration**:

Add to `/etc/vault.d/vault.hcl`:
```hcl
include "/etc/vault.d/audit-devices.hcl"
```

### Syslog Audit Device (Optional)

For integration with SIEM tools, enable the syslog device:

```bash
vault audit enable syslog tag=vault facility=AUTH
```

### Socket Audit Device (Optional)

For forwarding to remote log collectors:

```bash
vault audit enable socket \
    address=tcp://localhost:9090 \
    socket_type=tcp \
    format=json
```

## Log Format and Structure

### Audit Log Entry Structure

Each audit log entry is a JSON object with the following structure:

```json
{
  "time": "2026-01-10T10:30:00.123456789Z",
  "type": "request",
  "request": {
    "id": "req-a1b2c3d4e5f6",
    "operation": "read",
    "client_token": "s.hmac...",
    "client_token_accessor": "acc.1234",
    "path": "secret/data/bike-data-flow/production/database",
    "data": null,
    "remote_address": "10.0.0.5",
    "namespace": {"id": "default"},
    "mount_point": "secret"
  },
  "auth": {
    "token_type": "service",
    "policies": ["default", "dagster-secrets"],
    "token_policies": ["default", "dagster-secrets"],
    "entity_id": "entity-uuid"
  },
  "response": {
    "data": {"...secret data..."},
    "lease_id": "",
    "renewable": false
  }
}
```

### Response on Failure

Failed operations include an `error` field:

```json
{
  "time": "2026-01-10T10:31:00.123456789Z",
  "type": "response",
  "error": "permission denied",
  "request": {
    "path": "secret/data/admin/sensitive",
    "operation": "read",
    "client_token_accessor": "acc.1234"
  }
}
```

## Using the Python Audit Module

### Parsing Audit Logs

```python
from wrm_pipeline.vault.audit import AuditLogParser, AuditLogCollection

# Parse from a single file
parser = AuditLogParser()
logs = parser.parse_from_file("/var/log/vault/audit.log")

# Parse from a directory (all .log files)
logs = parser.parse_from_directory("/var/log/vault/")
```

### Filtering Logs

```python
from wrm_pipeline.vault.audit import AuditOperation

# Filter by operation type
read_logs = logs.filter_by_operation(AuditOperation.READ)

# Filter by multiple operations
access_logs = logs.filter_by_operation(
    AuditOperation.READ,
    AuditOperation.LIST
)

# Filter by time range
from datetime import datetime, timedelta
start = datetime.utcnow() - timedelta(days=7)
end = datetime.utcnow()
recent_logs = logs.filter_by_time(start, end)

# Filter by path pattern (regex)
prod_logs = logs.filter_by_path(r"secret/data/.*production.*")

# Filter by actor
actor_logs = logs.filter_by_actor("token-accessor-id")

# Filter by success/failure
failed_logs = logs.filter_by_success(False)

# Text search
search_logs = logs.search_logs("database")

# Chain filters
filtered = logs \
    .filter_by_time(start, end) \
    .filter_by_operation(AuditOperation.READ) \
    .filter_by_path(r"^secret/data/bike-data-flow.*")
```

### Analyzing Access Patterns

```python
# Get access pattern statistics
patterns = logs.get_access_patterns()

print(f"Total operations: {patterns['total_operations']}")
print(f"Success rate: {patterns['success_rate']:.2%}")
print(f"Unique actors: {len(patterns['by_actor'])}")
print(f"Top paths: {patterns['top_paths'][:5]}")
print(f"Top actors: {patterns['top_actors'][:5]}")
```

### Detecting Anomalies

```python
# Detect unusual activity
anomalies = logs.detect_anomalies(
    threshold=100,           # Operations threshold
    time_window_minutes=60   # Time window
)

for anomaly in anomalies:
    print(f"Type: {anomaly['type']}")
    print(f"Severity: {anomaly['severity']}")
    print(f"Details: {anomaly}")
```

### Generating Audit Reports

```python
from wrm_pipeline.vault.audit import AuditLogRetention

retention = AuditLogRetention()
retention.configure_retention(
    retention_days=90,
    archive_directory="/var/log/vault/archive",
    archive_before_delete=True
)

# Generate a report
report = retention.generate_audit_report(
    log_directory="/var/log/vault",
    start_date=datetime(2026, 1, 1),
    end_date=datetime(2026, 1, 31)
)

print(json.dumps(report, indent=2))
```

## Using the Shell Script

### Basic Usage

```bash
# View audit logs
./scripts/vault/view-audit-logs.sh -f /var/log/vault/audit.log

# Filter by operation
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ -o read

# Filter by actor
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ -a token-abc123

# Table format
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ --format table --limit 50

# JSON output
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ --format json > logs.json
```

### Advanced Options

```bash
# Search for specific text
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ --search "database"

# Filter by path pattern
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ -p "secret/data/production/*"

# Show only failed operations
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ -s false

# Real-time tail
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ --tail

# Show statistics
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ --stats

# Detect anomalies
./scripts/vault/view-audit-logs.sh -f /var/log/vault/ --anomalies
```

## Log Retention Policies

### Configuration

The default retention period is **90 days** (compliant with most regulations).

**Configure in Python**:
```python
retention = AuditLogRetention(retention_days=90)
retention.configure_retention(
    retention_days=365,  # 1 year for stricter compliance
    archive_directory="/var/backups/vault-audit-logs",
    archive_before_delete=True
)
```

**System Logrotate Configuration**:

Create `/etc/logrotate.d/vault-audit`:
```
/var/log/vault/audit.log {
    daily
    rotate 90
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root vault
    sharedscripts
    postrotate
        systemctl reload vault > /dev/null 2>&1 || true
    endscript
}
```

### Cleanup Old Logs

```python
# Dry run first
result = retention.cleanup_old_logs("/var/log/vault", dry_run=True)
print(f"Would delete: {len(result['deleted_files'])} files")

# Actually cleanup
result = retention.cleanup_old_logs("/var/log/vault", dry_run=False)
print(f"Deleted: {len(result['deleted_files'])} files")
print(f"Freed: {result['freed_bytes']} bytes")
```

## Compliance Requirements

### PCI-DSS

- **Requirement 10**: Audit trails
- **Retention**: Minimum 1 year
- **Recommendations**:
  - Enable file audit device with `log_raw=true`
  - Configure log rotation with 90-day retention minimum
  - Store copies in secure, immutable storage
  - Integrate with SIEM for real-time monitoring

### SOC 2

- **CC6.1**: Logical access controls
- **Recommendations**:
  - Log all authentication attempts (success and failure)
  - Monitor for excessive failed operations
  - Alert on unusual access patterns

### HIPAA

- **164.312(b)**: Audit controls
- **Retention**: Minimum 6 years
- **Recommendations**:
  - Enable all audit devices
  - Protect audit logs from tampering
  - Regular access reviews using audit logs

## Integration with SIEM Tools

### Splunk

1. Install Splunk HTTP Event Collector (HEC) app
2. Configure Vault to use socket audit device:

```bash
vault audit enable socket \
    address=https://splunk-server:8088/services/collector \
    socket_type=tcp \
    format=json \
    header.Authorization="Splunk token"
```

3. Configure inputs on Splunk side

### ELK Stack (Elasticsearch)

1. Configure Logstash or Filebeat to monitor `/var/log/vault/audit.log`
2. Use JSON parsing in Logstash:

```ruby
filter {
  json {
    source => "message"
    target => "vault_audit"
  }
}
```

### Datadog

1. Enable syslog audit device
2. Configure Datadog agent to collect syslog
3. Use Datadog's Vault integration dashboards

## Troubleshooting

### Audit Device Not Logging

**Symptom**: Operations complete but no entries in audit log

**Solutions**:
```bash
# Check audit device status
vault audit list

# Verify file permissions
ls -la /var/log/vault/audit.log

# Check Vault logs
journalctl -u vault -n 100

# Re-enable if needed
vault audit enable file file_path=/var/log/vault/audit.log
```

### Permission Denied on Audit Device

**Symptom**: Vault fails to start with audit device error

**Solutions**:
```bash
# Fix file permissions
sudo chown vault:vault /var/log/vault/
sudo chmod 640 /var/log/vault/audit.log

# Create parent directory if missing
sudo mkdir -p /var/log/vault
sudo chown vault:vault /var/log/vault
```

### Log Files Too Large

**Solutions**:
1. Enable log rotation in configuration
2. Configure logrotate as shown above
3. Use compression for archived logs
4. Ship logs to external storage

### Missing Audit Entries

**Possible Causes**:
1. Audit device disabled - re-enable with `vault audit enable`
2. Log file full - check disk space
3. Mount point unmounted - verify `/var/log/vault` is mounted

## Best Practices

1. **Enable Multiple Audit Devices**: Use file + syslog for redundancy
2. **Secure Audit Logs**: Restrict access to vault:vault, chmod 640
3. **Centralized Logging**: Ship logs to SIEM for alerting
4. **Regular Reviews**: Use the audit module for weekly access reviews
5. **Alerting**: Set up alerts for:
   - Multiple failed operations from single actor
   - Access to sensitive paths
   - Operations during off-hours
6. **Backup Audit Logs**: Include in backup strategy
7. **Immutable Storage**: Store copies in WORM storage for compliance
8. **Log Integrity**: Use HMAC keys to verify log integrity

## File Locations

| File | Purpose |
|------|---------|
| `config/vault/audit-devices.hcl` | Audit device configuration |
| `wrm_pipeline/wrm_pipeline/vault/audit.py` | Python audit module |
| `scripts/vault/view-audit-logs.sh` | Shell script for log viewing |
| `/var/log/vault/audit.log` | Primary audit log location |
| `/etc/logrotate.d/vault-audit` | Log rotation configuration |
