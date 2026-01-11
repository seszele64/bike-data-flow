# Vault Troubleshooting Guide

**Feature**: 001-hashicorp-vault-integration  
**Last Updated**: 2026-01-11

This guide covers common issues, error messages, and recovery procedures for HashiCorp Vault.

---

## Table of Contents

1. [Connection Issues](#connection-issues)
2. [Authentication Failures](#authentication-failures)
3. [Unseal Problems](#unseal-problems)
4. [Secret Access Errors](#secret-access-errors)
5. [Performance Issues](#performance-issues)
6. [Recovery Procedures](#recovery-procedures)
7. [TLS Certificate Issues](#tls-certificate-issues)
8. [Log Analysis](#log-analysis)

---

## Connection Issues

### "connection refused" or "i/o timeout"

**Symptoms:**
- `VaultConnectionError: Error making API request`
- `Could not connect to any given endpoint`
- Requests time out immediately

**Causes:**
- Vault server is not running
- Wrong `VAULT_ADDR` (typo, wrong port, wrong protocol)
- Firewall blocking port 8200
- Network connectivity issues

**Solutions:**

1. **Check if Vault is running:**
   ```bash
   sudo systemctl status vault
   sudo systemctl status vault-unseal-monitor  # If using auto-unseal
   ```

2. **Verify the address:**
   ```bash
   # Check environment variable
   echo $VAULT_ADDR
   
   # Test connectivity (from application server)
   curl -s -o /dev/null -w "%{http_code}" $VAULT_ADDR/v1/sys/health
   ```

3. **Check firewall rules:**
   ```bash
   # Check if port is listening
   sudo ss -tlnp | grep 8200
   
   # Check UFW rules
   sudo ufw status
   
   # Check iptables
   sudo iptables -L -n
   ```

4. **Check DNS resolution:**
   ```bash
   # Test DNS for vault hostname
   nslookup vault.example.com
   dig vault.example.com
   ```

### "no such host"

**Symptoms:**
- DNS resolution fails
- `getaddrinfo` errors in logs

**Solutions:**
1. Verify DNS records point to correct IP
2. Add entry to `/etc/hosts` for testing:
   ```
   192.168.1.100 vault.internal.bike-data-flow.com
   ```
3. Check NTP synchronization (time drift can cause issues)

---

## Authentication Failures

### "invalid role_id or secret_id"

**Symptoms:**
- `VaultAuthenticationError: Invalid role_id or secret_id`
- Authentication attempts fail immediately

**Solutions:**

1. **Verify credentials:**
   ```bash
   # On Vault server
   export VAULT_TOKEN=your-root-token
   vault read auth/approle/role/dagster/role-id
   vault write -f auth/approle/role/dagster/secret-id
   ```

2. **Check role configuration:**
   ```bash
   vault read auth/approle/role/dagster
   
   # Verify policies are attached
   vault read auth/approle/role/dagster | grep Policies
   ```

3. **Regenerate secret-id (if needed):**
   ```bash
   # Secret IDs can only be retrieved once
   vault write -f auth/approle/role/dagster/secret-id
   ```

### "permission denied" or "access denied"

**Symptoms:**
- `VaultPermissionError: Permission denied`
- Can connect but cannot read/write secrets

**Solutions:**

1. **Check policies attached to role:**
   ```bash
   vault read auth/approle/role/dagster
   
   # List all policies
   vault policy list
   
   # Read specific policy
   vault policy read dagster-secrets
   ```

2. **Verify policy syntax:**
   ```hcl
   # Check policy file for errors
   vault policy read dagster-secrets
   
   # Compare with expected permissions
   path "secret/data/bike-data-flow/*" {
     capabilities = ["read", "list"]
   }
   ```

3. **Test policy in isolation:**
   ```bash
   # Create a token with the role's policies
   vault token create -role=dagster -ttl=1h
   
   # Use the token to read
   VAULT_TOKEN=new-token vault read secret/data/bike-data-flow/production/database
   ```

### "entity not found" or "alias not found"

**Symptoms:**
- AppRole authentication works but entity mapping fails
- Vault Enterprise only issue

**Solutions:**
1. Ensure entity merge is not removing the entity
2. Check alias consistency
3. Verify entity associations

---

## Unseal Problems

### "vault is sealed"

**Symptoms:**
- `VaultSealedError: Vault is sealed`
- All API requests fail with seal status
- `vault status` shows `Sealed: true`

**Solutions:**

1. **Manual unseal (if using Shamir seal):**
   ```bash
   export VAULT_ADDR=https://vault.example.com:8200
   vault operator unseal <UNSEAL_KEY_1>
   vault operator unseal <UNSEAL_KEY_2>
   vault operator unseal <UNSEAL_KEY_3>
   ```

2. **Check seal status:**
   ```bash
   vault status
   
   # Expected output:
   # Key                     Value
   # ---                     -----
   # Seal Type               shamir
   # Sealed                  true  <-- Should be false after unseal
   # Total Shares            5
   # Threshold               3
   # ...

3. **Auto-unseal setup (recommended for production):**
   - Configure seal with cloud KMS (AWS KMS, Azure Key Vault, GCP KMS)
   - See [docs/vault-unseal-management.md](vault-unseal-management.md)

4. **Recovery seal (emergency):**
   - Only use if primary seal is unavailable
   - Requires recovery keys from initialization

### "unseal key not found" or "unable to decode key"

**Symptoms:**
- Unseal fails with key errors
- Keys not in expected format

**Solutions:**
1. Ensure keys are entered correctly (no extra spaces)
2. Check if using correct key shares
3. Verify key material hasn't been corrupted

---

## Secret Access Errors

### "secret not found"

**Symptoms:**
- `VaultSecretNotFoundError: Secret not found at path`
- Path exists but version doesn't

**Solutions:**

1. **Verify secret path:**
   ```bash
   # List secrets at path
   vault kv list secret/bike-data-flow/production/
   
   # Get specific secret
   vault kv get secret/bike-data-flow/production/database
   ```

2. **Check mount point:**
   ```bash
   # List enabled secrets engines
   vault secrets list
   
   # Verify mount is kv-v2
   vault secrets enable -path=secret kv-v2
   ```

3. **Check for version issues:**
   ```bash
   # Read specific version
   vault kv get -version=1 secret/bike-data-flow/production/database
   
   # Check metadata
   vault kv metadata get secret/bike-data-flow/production/database
   ```

### "invalid JSON" or "data format error"

**Symptoms:**
- Secret data parsing fails
- Data read differently than written

**Solutions:**
1. Ensure secrets are written as JSON-compatible data
2. Check for special characters in values
3. Verify encoding is UTF-8

---

## Performance Issues

### High latency on secret reads

**Symptoms:**
- read_file operations take > 500ms
- Pipeline performance degrades

**Solutions:**

1. **Enable caching:**
   ```python
   from wrm_pipeline.vault import VaultSecretsResource
   
   resources = {
       "vault": VaultSecretsResource.configured({
           "vault_addr": EnvVar("VAULT_ADDR"),
           "cache_ttl": 300,  # 5 minutes cache
           ...
       })
   }
   ```

2. **Run benchmark to identify issues:**
   ```bash
   python scripts/vault/benchmark-latency.py \
       --vault-addr $VAULT_ADDR \
       --role-id $VAULT_ROLE_ID \
       --secret-id $VAULT_SECRET_ID \
       --read-iterations 100 \
       --json
   ```

3. **Check Vault performance:**
   ```bash
   # Vault metrics
   curl $VAULT_ADDR/v1/sys/metrics
   
   # Check storage backend performance
   vault status | grep -i "storage\|cluster"
   ```

### Connection pool exhaustion

**Symptoms:**
- "too many open files"
- Connection timeouts
- Socket errors

**Solutions:**
1. Increase file descriptor limit:
   ```bash
   # Edit /etc/security/limits.conf
   vault soft nofile 65536
   hard nofile 65536
   ```
2. Check connection pooling configuration
3. Monitor active connections

---

## Recovery Procedures

### Emergency: Root Token Recovery

**Only if you have unseal keys!**

1. **Decrypt root token (if using Shamir with PGP):**
   ```bash
   # Use the PGP private key to decrypt
   echo "encrypted-root-token" | base64 -d | \
       gpg --decrypt --batch --yes
   ```

2. **Use recovery key (Vault Enterprise):**
   ```bash
   VAULT_TOKEN=recovery-token vault auth enable userpass
   ```

### Emergency: Re-initialize Vault (Destructive!)

**⚠️ WARNING: This deletes ALL data! Only use as last resort!**

1. **Stop Vault:**
   ```bash
   sudo systemctl stop vault
   ```

2. **Clear storage:**
   ```bash
   # For Raft storage
   sudo rm -rf /var/lib/vault/data/*
   
   # For file-based storage
   sudo rm -rf /vault/data/*
   ```

3. **Re-initialize:**
   ```bash
   sudo systemctl start vault
   vault operator init -key-shares=5 -key-threshold=3
   ```

### Emergency: Restore from Backup

1. **List available backups:**
   ```bash
   python scripts/vault/view-backups.py --list
   ```

2. **Restore backup:**
   ```bash
   # Download and restore
   python scripts/vault/restore-vault.py \
       --backup-id vault-backup-full-20240101_120000-abc12345
   ```

3. **Verify restoration:**
   ```bash
   vault read secret/data/bike-data-flow/production/database
   ```

---

## TLS Certificate Issues

### "certificate verify failed"

**Symptoms:**
- TLS handshake errors
- SSL certificate validation failures

**Solutions:**

1. **Verify certificate:**
   ```bash
   # Check certificate validity
   openssl x509 -in /etc/letsencrypt/live/vault.example.com/cert.pem -text -noout
   
   # Check expiration
   openssl x509 -enddate -noout -in /etc/letsencrypt/live/vault.example.com/cert.pem
   
   # Verify chain
   openssl verify -CAfile /etc/letsencrypt/live/vault.example.com/chain.pem \
       /etc/letsencrypt/live/vault.example.com/cert.pem
   ```

2. **Set CA certificate path:**
   ```bash
   export VAULT_CACERT=/etc/letsencrypt/live/vault.example.com/chain.pem
   ```

3. **Disable verification (NOT recommended for production):**
   ```python
   config = VaultConnectionConfig(
       vault_addr=VAULT_ADDR,
       verify=False  # ⚠️ Security risk!
   )
   ```

### "certificate expired" or "not yet valid"

**Solutions:**

1. **Renew certificate:**
   ```bash
   sudo certbot renew --dry-run
   sudo certbot renew
   ```

2. **Restart Vault after renewal:**
   ```bash
   sudo systemctl restart vault
   ```

3. **Check system clock:**
   ```bash
   timedatectl
   sudo ntpdate pool.ntp.org
   ```

---

## Log Analysis

### Enable Debug Logging

```bash
# Set log level in vault.hcl
log_level = "debug"

# Or via environment
export VAULT_LOG_LEVEL=debug
```

### View Vault Logs

```bash
# Journald logs
sudo journalctl -u vault -f --no-pager

# Or directly
sudo tail -f /var/log/vault/vault.log
```

### Common Log Patterns

| Pattern | Meaning | Action |
|---------|---------|--------|
| `authentication failed` | Bad credentials | Verify role_id/secret_id |
| `permission denied` | Policy issue | Check policies |
| `invalid request` | Malformed request | Check request format |
| `connection refused` | Server down | Check Vault status |
| `seal threshold reached` | Unseal needed | Unseal Vault |

### Audit Log Analysis

```bash
# View audit logs
python scripts/vault/view-audit-logs.py /var/log/vault/audit.log --format table

# Filter for errors
python scripts/vault/view-audit-logs.py /var/log/vault/audit.log \
    --success false --limit 50
```

---

## Diagnostic Commands

### Quick Health Check

```bash
#!/bin/bash
# Quick health check script

echo "=== Vault Health Check ==="

# Check service status
echo -n "Service Status: "
sudo systemctl is-active vault

# Check seal status
echo -n "Seal Status: "
vault status -format=json | jq -r '.sealed'

# Check API accessibility
echo -n "API Response: "
curl -s -o /dev/null -w "%{http_code}" $VAULT_ADDR/v1/sys/health

# Check token validity
echo -n "Token Valid: "
vault token lookup > /dev/null 2>&1 && echo "Yes" || echo "No"

echo "=== Complete ==="
```

### Generate Support Bundle

```bash
#!/bin/bash
# Generate support bundle for debugging

BUNDLE="vault-support-$(date +%Y%m%d-%H%M%S).tar.gz"

# Collect information
tar czf $BUNDLE \
    /var/log/vault/*.log \
    /etc/vault.d/*.hcl \
    /etc/letsencrypt/live/vault.example.com//fullchain.pem \
    <(vault status) \
    <(vault secrets list) \
    <(vault policy list)

echo "Support bundle created: $BUNDLE"
```

---

## Additional Resources

- [Vault Documentation](https://developer.hashicorp.com/vault/docs)
- [Vault Community Forum](https://discuss.hashicorp.com/c/vault)
- [Vault Troubleshooting FAQ](https://developer.hashicorp.com/vault/docs/troubleshooting)
- [Vault Audit Logging](vault-audit-logging.md)
- [Vault Backup & Recovery](vault-backup-recovery.md)
- [Vault Unseal Management](vault-unseal-management.md)
