# Vault Unseal Key Management Documentation

This document describes best practices for managing Vault unseal keys using Shamir's Secret Sharing.

## Overview

Vault uses **Shamir's Secret Sharing** to split the master key into multiple unseal keys.

## Default Configuration

| Parameter | Default Value | Description |
|-----------|---------------|-------------|
| Key Shares | 5 | Number of unseal keys generated |
| Key Threshold | 3 | Minimum keys required to unseal |

### Recommended Configurations

### Development/Testing (Single Person)

```bash
vault operator init -key-shares=1 -key-threshold=1
```

### Small Team (2-3 People)

```bash
vault operator init -key-shares=3 -key-threshold=2
```

### Production (5+ People)

```bash
vault operator init -key-shares=5 -key-threshold=3
```

## Key Storage Recommendations

| Location | Security Level | Example |
|----------|---------------|---------|
| Physical safe | High | Office safe, bank deposit box |
| Hardware security module | Very High | YubiKey, HSM |
| Encrypted USB drive | Medium-High | Stored in secure location |
| Password manager | Medium | 1Password, Bitwarden |
| Paper backup | Medium | Stored in fireproof safe |

### Digital Storage Best Practices

```bash
# Never store keys in:
# - Plain text files
# - Environment variables (in code)
# - Version control
# - Email or chat
# - Unencrypted storage
```

## Emergency Unseal Procedures

### Scenario 1: Routine Unsealing

```bash
# Each key holder unseals with their key
vault operator unseal <KEY_1>
vault operator unseal <KEY_2>
vault operator unseal <KEY_3>
```

### Scenario 2: All Keys Lost

**⚠️ CRITICAL**: If all unseal keys are lost, Vault CANNOT be recovered!

### Scenario 3: Partial Disaster Recovery

```bash
# 1. Restore from snapshot if available
vault operator snapshot restore backup.snap

# 2. If Vault is sealed, obtain keys
# 3. Unseal with available keys

# 4. If threshold not met, regenerate keys (DANGEROUS!)
vault operator rekey -key-shares=5 -key-threshold=3
```

## Key Rotation Procedures

### When to Rotate Keys

- After security incident
- After key holder leaves organization
- Periodic rotation (quarterly/annually)

### Rotation Steps

```bash
# 1. Ensure Vault is unsealed and you have root token
export VAULT_TOKEN="your-root-token"

# 2. Generate new unseal keys
vault operator rekey -key-shares=5 -key-threshold=3

# 3. Enter current unseal keys when prompted
# 4. Save new unseal keys securely
```

## Shamir's Secret Sharing Technical Details

### How It Works

1. **Master Key Generation**: Random value generated
2. **Key Splitting**: Master key split into N shares using polynomial
3. **Distribution**: Shares distributed to key holders
4. **Reconstruction**: Any K shares can reconstruct master key

### Security Properties

| Property | Description |
|----------|-------------|
| Perfect Secrecy | K-1 shares reveal nothing about the key |
| No Single Point of Failure | Losing up to N-K keys is recoverable |

## Key Ceremony Checklist

### Before Ceremony

- [ ] Schedule with all key holders
- [ ] Prepare secure location (camera, witness)
- [ ] Generate keys offline if possible
- [ ] Prepare secure storage for each key
- [ ] Document receipt of each key

### During Ceremony

- [ ] Verify attendee identities
- [ ] Record ceremony (video/audio)
- [ ] Generate unseal keys
- [ ] Distribute keys to holders
- [ ] Have each holder acknowledge receipt

### After Ceremony

- [ ] Securely store ceremony recording
- [ ] Update key holder documentation
- [ ] Test recovery procedures

## Automated Unseal (Cloud KMS)

For production, consider auto-unseal with cloud KMS:

### AWS KMS

```hcl
seal "awskms" {
  kms_key_id = "your-kms-key-id"
  region     = "us-east-1"
}
```

### Azure Key Vault

```hcl
seal "azurekeyvault" {
  tenant_id      = "your-tenant-id"
  vault_name     = "your-vault"
  key_name       = "your-key"
}
```

### GCP Cloud KMS

```hcl
seal "gcpckms" {
  project  = "your-project"
  location = "global"
  key_ring = "your-key-ring"
  key_name = "your-key"
}
```

## Monitoring and Alerts

### Key Usage Monitoring

```bash
# Monitor seal status
curl https://vault.example.com/v1/sys/health | jq '.sealed'
```

### Alert Thresholds

| Alert | Condition |
|-------|-----------|
| Warning | Vault sealed for > 1 hour |
| Critical | Multiple failed unseal attempts |
| Emergency | Key holder unavailable (no backup) |

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| "Master key not found" | All keys not provided | Gather all required keys |
| "Invalid key" | Wrong key used | Verify key authenticity |
| "Threshold not met" | Not enough keys | Obtain more keys |

## References

- [Vault Seal/Unseal](https://developer.hashicorp.com/vault/docs/concepts/seal)
- [Shamir's Secret Sharing](https://en.wikipedia.org/wiki/Shamir%27s_Secret_Sharing)
- [Vault Operator Rekey](https://developer.hashicorp.com/vault/docs/commands/operator/rekey)
