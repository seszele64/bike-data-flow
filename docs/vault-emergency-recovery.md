# Vault Emergency Recovery Procedures

This document provides step-by-step emergency recovery procedures for HashiCorp Vault in the bike-data-flow project. It covers disaster recovery scenarios, data loss prevention, and escalation procedures.

## Table of Contents

1. [Emergency Contacts](#emergency-contacts)
2. [Emergency Scenarios](#emergency-scenarios)
3. [Immediate Response](#immediate-response)
4. [Recovery Procedures](#recovery-procedures)
5. [Unseal Key Recovery](#unseal-key-recovery)
6. [Disaster Recovery Runbook](#disaster-recovery-runbook)
7. [Data Loss Prevention](#data-loss-prevention)
8. [Post-Incident Procedures](#post-incident-procedures)

---

## Emergency Contacts

### Primary Team

| Role | Contact | Responsibility |
|------|---------|----------------|
| Vault Administrator | admin@bike-data-flow | Primary recovery lead |
| DevOps Lead | devops@bike-data-flow | Infrastructure recovery |
| Security Lead | security@bike-data-flow | Access control & audit |
| On-Call Engineer | See PagerDuty | 24/7 incident response |

### External Contacts

| Service | Contact | Purpose |
|---------|---------|---------|
| Hetzner Support | Via Robot Panel | Object storage issues |
| HashiCorp Support | Enterprise support portal | Vault enterprise issues |

### Escalation Path

```
1. On-Call Engineer (PagerDuty)
   ↓ (if no response in 15 min)
2. DevOps Lead
   ↓ (if no response in 30 min)
3. Security Lead
   ↓ (if no response in 1 hour)
4. CTO / Management
```

---

## Emergency Scenarios

### Scenario 1: Vault Server Unavailable

**Severity**: P1 - Critical

**Indicators**:
- Vault health check fails (`curl https://vault.bike-data-flow.com:8200/v1/sys/health`)
- Applications unable to retrieve secrets
- Health check script reports failure

**Immediate Actions**:
1. Check server status (SSH to vault.bike-data-flow.com)
2. Verify Vault service status: `systemctl status vault`
3. Check disk space: `df -h`
4. Check logs: `journalctl -u vault -n 100`

**Recovery Steps**:
```bash
# If service is stopped but server is running
sudo systemctl start vault
sudo systemctl status vault

# If service fails to start, check logs
journalctl -u vault -f

# If disk is full, clear space
du -sh /var/log/vault/*
df -h /var/lib/vault
```

### Scenario 2: Vault is Sealed

**Severity**: P1 - Critical

**Indicators**:
- Health check returns `sealed: true`
- `vault status` shows `Sealed: true`

**Immediate Actions**:
1. Gather unseal keys (see [Unseal Key Recovery](#unseal-key-recovery))
2. Unseal the vault:

```bash
export VAULT_ADDR="https://vault.bike-data-flow.com:8200"
vault operator unseal <unseal-key-1>
vault operator unseal <unseal-key-2>
vault operator unseal <unseal-key-3>

# Verify
vault status
```

**If Unseal Keys Are Lost**:
See [Unseal Key Recovery](#unseal-key-recovery)

### Scenario 3: Complete Server Failure

**Severity**: P0 - Critical

**Indicators**:
- Server unreachable via SSH
- Hardware failure confirmed
- Data center outage

**Immediate Actions**:
1. Notify infrastructure team immediately
2. Begin restore procedure on standby server
3. Contact Hetzner support if hardware issue

**Recovery Steps**:
```bash
# On new/replacement server
# 1. Install Vault (use init-vault.sh)
./scripts/vault/init-vault.sh --install-deps

# 2. Restore from latest snapshot
./scripts/vault/restore-vault.sh --backup /var/backups/vault/latest.snap --force

# 3. Verify restore
vault secrets list
vault policy list

# 4. Update DNS/load balancer if server IP changed
```

### Scenario 4: Accidental Secret Deletion

**Severity**: P1 - High

**Indicators**:
- Application reports missing secrets
- `vault kv get` returns "not found"
- Audit logs show delete operations

**Immediate Actions**:
1. Identify when deletion occurred (check audit logs)
2. Check if versioned secrets exist:

```bash
# List secret versions
vault kv list -versions=secret/data/bike-data-flow/production/database
```

**Recovery Steps**:
```bash
# Restore specific version (if versioning enabled)
vault kv get -version=5 secret/data/bike-data-flow/production/database

# Or restore from backup
./scripts/vault/restore-vault.sh --point-in-time "2024-01-10T15:00:00Z"
```

### Scenario 5: Suspected Security Breach

**Severity**: P0 - Critical

**Indicators**:
- Unauthorized access detected
- Suspicious API calls in logs
- Unexpected policy changes
- Unknown tokens active

**Immediate Actions**:
1. **DO NOT** restore from backup (may restore attacker access)
2. Revoke all tokens immediately:

```bash
# List all tokens
vault list auth/token/accessors

# Revoke all tokens (except root and emergency tokens)
# WARNING: This will disconnect all users/applications

# Check for unauthorized policies
vault policy list
vault policy read suspicious-policy
```

3. Rotate all secrets:
   - Database credentials
   - API keys
   - TLS certificates
   - Encryption keys

4. Engage security team immediately

**Full Recovery**:
1. Isolate affected systems
2. Preserve forensic evidence
3. Identify attack vector
4. Clean and rebuild affected systems
5. Rotate ALL credentials
6. Restore from clean backup (after investigation)

---

## Immediate Response

### Initial Assessment Checklist

- [ ] Confirm the incident (verify with multiple sources)
- [ ] Assess severity level (P0/P1/P2/P3)
- [ ] Identify affected systems
- [ ] Check recent changes (deployments, config changes)
- [ ] Review recent audit logs
- [ ] Notify appropriate team members

### Incident Documentation

Document everything:

```markdown
# Incident Report

## Summary
[Description of incident]

## Timeline
| Time | Action | Person |
|------|--------|--------|
| HH:MM | Detected issue | On-call |
| HH:MM | Started investigation | Admin |
| HH:MM | Identified root cause | Admin |
| HH:MM | Started recovery | Admin |
| HH:MM | Recovery complete | Admin |

## Root Cause
[Detailed explanation]

## Impact
- Systems affected: [list]
- Data affected: [description]
- Users affected: [number]

## Resolution
[Steps taken]

## Follow-up Actions
- [ ] Review and update procedures
- [ ] Implement preventive measures
- [ ] Schedule post-mortem
```

---

## Recovery Procedures

### Full Vault Restore

Use when the entire Vault instance needs to be restored.

```bash
# 1. Ensure backup exists
ls -la /var/backups/vault/vault-backup-*.snap*

# 2. Stop Vault service
sudo systemctl stop vault

# 3. Backup current data (if any)
sudo cp -r /var/lib/vault/data /var/lib/vault/data.backup.$(date +%Y%m%d)

# 4. Clear Raft data directory (if using Raft storage)
sudo rm -rf /var/lib/vault/data/*

# 5. Start Vault
sudo systemctl start vault

# 6. Wait for initialization
sleep 5

# 7. Check status
vault status

# 8. Restore from snapshot
./scripts/vault/restore-vault.sh --backup /var/backups/vault/latest.snap --force

# 9. Verify restore
vault secrets list
vault policy list

# 10. Restart services
sudo systemctl restart vault
```

### Partial Restore (Specific Secrets)

Use when only specific secrets need to be recovered.

```python
# Python script for selective restore
from wrm_pipeline.wrm_pipeline.vault.backup import create_snapshot_manager
from wrm_pipeline.wrm_pipeline.vault.client import VaultClient

# Connect to Vault
client = VaultClient(config)

# Download backup
manager = create_snapshot_manager(...)
backup = manager.list_snapshots(limit=1)[0]
manager.download_snapshot(backup.id)

# Parse and selectively restore
import json
with open(f"/var/backups/vault/{backup.id}.json") as f:
    backup_data = json.load(f)

# Restore specific secret
for path, data in backup_data["secrets"].items():
    if path.startswith("bike-data-flow/production/"):
        client.write_secret(path, data)
        print(f"Restored: {path}")
```

### Point-in-Time Recovery

Recover to a specific timestamp.

```bash
# List available backups with timestamps
./scripts/vault/restore-vault.sh --list-backups

# Restore to specific time
./scripts/vault/restore-vault.sh --point-in-time "2024-01-10T15:00:00Z"

# Verify point-in-time recovery
vault kv get secret/bike-data-flow/production/database
```

---

## Unseal Key Recovery

### Locations of Unseal Keys

Unseal keys should be stored in multiple secure locations:

| Location | Access | Last Updated |
|----------|--------|--------------|
| HashiCorp Cloud (if using) | Admin, Security | 2024-01-01 |
| Hardware Security Module | HSM Admin | 2024-01-01 |
| Paper Backup (safe) | Admin, Security | 2024-01-01 |
| 1Password (emergency vault) | On-call engineers | 2024-01-01 |

### Recovering Unseal Keys

If unseal keys are lost, follow this procedure:

#### Option 1: Recovery Keys ( Shamir )

If you have a Shamir recovery key:

```bash
vault operator unseal -recovery-shamir
# Enter recovery key portions when prompted
```

#### Option 2: Regenerate Unseal Keys (Requires Root Token)

**WARNING**: This will invalidate all existing unseal keys and require immediate re-initialization.

```bash
# Must have root token
export VAULT_TOKEN="root-token"

# Generate new unseal keys
vault operator generate-root -init

# This will provide new unseal keys
# WARNING: Old keys will no longer work
```

#### Option 3: Restore from Backup (If keys are truly lost)

1. Deploy new Vault server
2. Restore from backup (which includes encrypted unseal keys)
3. Use the unseal keys from the backup

### Emergency Unseal Procedure

1. **Access key storage**:
   ```bash
   # From 1Password (requires VPN)
   op read "op://Vault/Emergency/unseal-key-1"
   ```

2. **Unseal the vault**:
   ```bash
   export VAULT_ADDR="https://vault.bike-data-flow.com:8200"
   vault operator unseal <key-1>
   vault operator unseal <key-2>
   vault operator unseal <key-3>
   ```

3. **Verify status**:
   ```bash
   vault status
   # Should show: Sealed: false
   ```

---

## Disaster Recovery Runbook

### Preparation (Before Disaster)

1. **Maintain up-to-date backups**:
   - Daily incremental backups
   - Weekly full snapshots
   - Offsite storage in Hetzner Object Storage

2. **Document recovery procedures**:
   - Keep this document current
   - Schedule quarterly DR tests
   - Train team on recovery procedures

3. **Prepare recovery environment**:
   - Standby server (if available)
   - Pre-configured Vault installation
   - Access to backup storage

### During Disaster

1. **Assess the situation** (5 minutes)
   - Confirm the incident
   - Determine scope
   - Identify required actions

2. **Communicate** (10 minutes)
   - Notify on-call engineer
   - Update status page
   - Alert affected teams

3. **Execute recovery** (varies)
   - Follow appropriate procedure
   - Document all actions
   - Verify at each step

4. **Validate** (15-30 minutes)
   - Confirm secrets accessible
   - Test application integration
   - Verify policies applied

### After Disaster

1. **Post-incident review** (within 48 hours)
   - Root cause analysis
   - Document lessons learned
   - Update procedures

2. **Improvements** (within 1 week)
   - Implement preventive measures
   - Update monitoring
   - Enhance documentation

---

## Data Loss Prevention

### Backup Strategy

| Data Type | Backup Frequency | Retention | Storage |
|-----------|-----------------|-----------|---------|
| Raft snapshots | Weekly | 30 days | Local + Hetzner |
| Export files | Daily | 7 days | Local |
| Configuration | On-change | Forever | Git + Hetzner |
| Policies | On-change | Forever | Git + Hetzner |

### Monitoring for Data Loss

```bash
# Check for recent deletions
./scripts/vault/health-check.sh --audit

# Monitor secret access patterns
vault audit list

# Set up alerts for mass deletions
# (Configure in monitoring system)
```

### Prevention Measures

1. **Enable versioning** on all secret paths:
   ```hcl
   # In Vault configuration
   secret {
     version = 2
   }
   ```

2. **Restrict delete permissions**:
   ```hcl
   # Policy to prevent deletion
   path "secret/data/*" {
     capabilities = ["create", "read", "update", "list"]
     # No "delete" capability
   }
   ```

3. **Require MFA for destructive operations**:
   ```hcl
   path "sys/storage/raft/force-unlock" {
     capabilities = ["sudo"]
   }
   ```

4. **Audit all access**:
   ```bash
   # Enable file audit device
   vault audit enable file file_path=/var/log/vault/audit.log
   ```

---

## Post-Incident Procedures

### Immediate Post-Incident (0-24 hours)

1. **Verify system stability**:
   ```bash
   # Run comprehensive health check
   ./scripts/vault/health-check.sh --full
   
   # Check all secrets accessible
   vault secrets list
   vault policy list
   ```

2. **Document incident timeline**:
   - When was the issue detected?
   - What actions were taken?
   - When was recovery completed?

3. **Notify stakeholders**:
   - Send incident summary
   - Include impact assessment
   - Provide recovery status

### Short-Term (1-7 days)

1. **Root cause analysis**:
   - What caused the incident?
   - What could have prevented it?
   - How was it detected?

2. **Update documentation**:
   - Revise this runbook
   - Update procedures
   - Add new monitoring

3. **Implement fixes**:
   - Apply patches
   - Update configurations
   - Enhance monitoring

### Long-Term (1-4 weeks)

1. **Quarterly DR test**:
   - Schedule test restore
   - Validate procedures
   - Measure recovery time

2. **Process improvements**:
   - Automate recovery steps
   - Improve monitoring
   - Reduce recovery time

3. **Team training**:
   - Review procedures
   - Practice recovery
   - Cross-train team members

---

## Quick Reference Card

### Emergency Commands

```bash
# Check Vault status
vault status

# Unseal Vault
vault operator unseal <key>

# Health check
./scripts/vault/health-check.sh

# Restore from backup
./scripts/vault/restore-vault.sh --force

# List secrets
vault secrets list

# Get specific secret
vault kv get secret/path/to/secret
```

### Key Locations

| Item | Location |
|------|----------|
| Backup directory | `/var/backups/vault` |
| Vault data | `/var/lib/vault/data` |
| Vault logs | `/var/log/vault/` |
| Vault config | `/etc/vault.d/vault.hcl` |
| Unseal keys | See 1Password: `op://Vault/Emergency/` |

### URLs

| Service | URL |
|---------|-----|
| Vault UI | https://vault.bike-data-flow.com:8200/ui |
| Health check | https://vault.bike-data-flow.com:8200/v1/sys/health |
| Metrics | https://vault.bike-data-flow.com:8200/v1/sys/metrics |

---

## Related Documentation

- [Vault Backup and Recovery](vault-backup-recovery.md)
- [Vault Server Configuration](vault-server-config.md)
- [Vault Unseal Management](vault-unseal-management.md)
- [Vault Policies](vault-policies.md)
- [Emergency Contact List](emergency-contacts.md)

---

**Document Version**: 1.0  
**Last Updated**: 2024-01-10  
**Next Review**: 2024-04-10  
**Owner**: DevOps Team
