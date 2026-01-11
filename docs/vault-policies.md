# Vault Access Control Policies

**Document Version:** 1.0  
**Last Updated:** 2026-01-10  
**Module:** `wrm_pipeline.wrm_pipeline.vault.policies`

---

## Overview

This document describes the access control policies implemented for the bike-data-flow HashiCorp Vault deployment. The policies follow the **principle of least privilege**, ensuring that each entity (user, application, service) has only the minimum permissions required to perform their designated tasks.

## Policy Structure

Vault policies use HashiCorp Configuration Language (HCL) to define access rules. Each policy consists of path-based rules that specify which capabilities are allowed on which paths.

### Basic Syntax

```hcl
path "secret/data/bike-data-flow/<path>" {
  capabilities = ["read", "list", "create", "update", "delete", "sudo"]
}
```

### Available Capabilities

| Capability | Description |
|------------|-------------|
| `read` | Read secret values |
| `list` | List secret paths |
| `create` | Create new secrets |
| `update` | Update existing secrets |
| `delete` | Delete secrets |
| `patch` | Partially update secrets |
| `sudo` | Access admin-only paths (requires root token or sudo capability) |

### Path Patterns

Vault supports several path pattern types:

| Pattern | Description | Example |
|---------|-------------|---------|
| Exact path | Matches exact path | `secret/data/bike-data-flow/db` |
| Single wildcard `*` | Matches any characters in one segment | `secret/data/bike-data-flow/*` |
| Multi-segment `**` | Matches multiple path segments | `secret/data/bike-data-flow/**` |
| Template matching | Matches multiple options | `{app,database,api}/*` |

---

## Pre-Defined Policies

### 1. Dagster Secrets Policy (`dagster-secrets`)

**Purpose:** Provides read-only access to secrets required by Dagster pipelines for execution.

**Use Case:** Assign to Dagster pipeline runners, sensor executors, and orchestration services.

**Access Level:**
- Read access to `app/`, `database/`, `api/`, and `storage/` secret categories
- List access to all categories
- No write, delete, or modify capabilities

**Example HCL Path Rules:**
```hcl
path "secret/data/bike-data-flow/database/*" {
  capabilities = ["read", "list"]
}
```

**Security Considerations:**
- Pipelines cannot modify secrets (prevents accidental or malicious changes)
- Pipelines cannot delete secrets (prevents data loss)
- Access is scoped to specific categories, not entire namespace

---

### 2. Read-Only Secrets Policy (`readonly-secrets`)

**Purpose:** Provides read-only access for monitoring, auditing, and readonly users.

**Use Case:** Assign to monitoring systems, auditors, and developers who need to view configuration without modification.

**Access Level:**
- Read access to all secrets in the bike-data-flow namespace
- List access to all paths
- No write, delete, or admin capabilities

**Example HCL Path Rules:**
```hcl
path "secret/data/bike-data-flow/*" {
  capabilities = ["read", "list"]
}
```

**Security Considerations:**
- Cannot create new secrets (prevents unauthorized secret creation)
- Cannot update existing secrets (prevents configuration drift)
- Cannot delete secrets (prevents data loss)

---

### 3. Admin Secrets Policy (`admin-secrets`)

**Purpose:** Provides full administrative access for operations team members.

**Use Case:** Assign to DevOps engineers, platform administrators, and security operations.

**Access Level:**
- Full CRUD access to all secrets
- Sudo capability for admin-only paths
- Policy management capabilities

**Example HCL Path Rules:**
```hcl
path "secret/data/bike-data-flow/*" {
  capabilities = ["create", "read", "update", "delete", "list", "patch", "sudo"]
}
```

**Security Considerations:**
- ⚠️ **WARNING:** This is a highly privileged policy
- Only assign to trusted, vetted personnel
- Consider using short-lived tokens with this policy
- Enable audit logging for all admin actions

---

## Creating Custom Policies

### Using the PolicyManager Class

```python
from wrm_pipeline.wrm_pipeline.vault.policies import PolicyManager
from wrm_pipeline.wrm_pipeline.vault.models import AccessPolicy, PolicyRule

# Initialize policy manager
manager = PolicyManager(policies_dir="config/vault/policies")

# Create a custom policy
custom_policy = AccessPolicy(
    name="custom-app-policy",
    description="Custom policy for my application",
    rules=[
        PolicyRule(
            path="secret/data/bike-data-flow/myapp/*",
            capabilities=["read", "list"],
            description="Read access to myapp secrets"
        ),
    ]
)

# Validate the policy
manager.validate_policy(custom_policy)

# Generate HCL
hcl_output = manager.to_hcl(custom_policy)
print(hcl_output)

# Save to file
manager.save_policy_file(custom_policy)
```

### Policy Best Practices

1. **Follow Least Privilege**
   - Grant only the capabilities required
   - Use specific path patterns (avoid `**` wildcards when possible)
   - Regularly review and prune unused policies

2. **Use Path Prefixes**
   - Organize secrets by category: `app/`, `database/`, `api/`, `storage/`
   - Use environment prefixes: `development/`, `staging/`, `production/`

3. **Separate Read and write_to_file Access**
   - Create separate policies for read-only and read-write access
   - Assign read-only policies to applications
   - Reserve write access for deployment pipelines

4. **Document Policies**
   - Include purpose and use case in policy descriptions
   - Document which entities should have the policy
   - Note any security considerations

---

## Policy Deployment Procedures

### Deploying a New Policy

1. **Create the policy file** in `config/vault/policies/`
   ```bash
   vim config/vault/policies/my-new-policy.hcl
   ```

2. **Validate the policy syntax**
   ```bash
   vault policy fmt config/vault/policies/my-new-policy.hcl
   ```

3. **Apply the policy to Vault**
   ```bash
   vault policy apply my-new-policy config/vault/policies/my-new-policy.hcl
   ```

4. **Associate policy with entity** (via Vault ACL or AppRole)

5. **Test access** with the entity's token

### Updating an Existing Policy

1. **Export current policy** (for backup)
   ```bash
   vault read sys/policy/my-policy -format=json > backup-my-policy.json
   ```

2. **Modify the policy file** in `config/vault/policies/`

3. **Apply the updated policy**
   ```bash
   vault policy apply my-policy config/vault/policies/my-policy.hcl
   ```

4. **Verify the update**
   ```bash
   vault read sys/policy/my-policy
   ```

5. **Test access** to ensure changes work as expected

### Removing a Policy

1. **Check policy usage** (ensure no entities depend on it)
   ```bash
   vault list sys/acl
   ```

2. **Remove policy associations** from all entities

3. **Delete the policy from Vault**
   ```bash
   vault delete sys/policy/my-policy
   ```

4. **Remove the policy file** from `config/vault/policies/`

---

## Policy Testing Procedures

### Test Read Access

```bash
# Read a secret with the entity's token
VAULT_TOKEN=$(vault write auth/approle/login role_id=xxx secret_id=xxx -field=token)
vault kv get -field=password secret/data/bike-data-flow/database/postgres
```

### Test List Access

```bash
# List secrets at a path
vault kv list secret/data/bike-data-flow/database/
```

### Test Write Access

```bash
# Attempt to write a secret (should fail for read-only policies)
vault kv put secret/data/bike-data-flow/test key=value
# Expected: Permission denied
```

### Test Deny Scenarios

```bash
# Attempt to access paths not covered by policy
vault kv get secret/data/bike-data-flow/admin-only
# Expected: Permission denied
```

---

## Using PolicyManager in Code

### Generating Built-in Policies

```python
from wrm_pipeline.wrm_pipeline.vault.policies import PolicyManager

manager = PolicyManager()

# Generate read-only policy
readonly_policy = manager.generate_readonly_policy(
    base_path="secret/data/bike-data-flow",
    name="readonly-secrets",
)

# Generate Dagster policy
dagster_policy = manager.generate_dagster_policy(
    base_path="secret/data/bike-data-flow",
    name="dagster-secrets",
)

# Generate admin policy
admin_policy = manager.generate_admin_policy(
    base_path="secret/data/bike-data-flow",
    name="admin-secrets",
)
```

### Syncing Policies to Vault

```python
from wrm_pipeline.wrm_pipeline.vault.policies import PolicyManager
from wrm_pipeline.wrm_pipeline.vault.client import VaultClient, VaultConnectionConfig

# Create connection
config = VaultConnectionConfig(
    vault_addr="https://vault.example.com:8200",
    auth_method="approle",
    role_id="your-role-id",
    secret_id="your-secret-id",
)

# Create client
client = VaultClient(config)

# Initialize policy manager
manager = PolicyManager(policies_dir="config/vault/policies")

# Sync all built-in policies
for policy_name in ["dagster-secrets", "readonly-secrets", "admin-secrets"]:
    policy = manager.load_policy_from_package(policy_name)
    manager.sync_policy(client, policy)
    print(f"Synced policy: {policy_name}")
```

---

## Audit and Monitoring

All policy-related operations should be logged and monitored:

1. **Enable audit devices** for Vault (see `docs/vault-audit-logging.md`)
2. **Monitor policy changes** in audit logs
3. **Alert on admin policy usage**
4. **Review policy assignments quarterly**

---

## Troubleshooting

### Policy Not Taking Effect

1. **Verify policy upload**
   ```bash
   vault read sys/policy/<policy-name>
   ```

2. **Check token policies**
   ```bash
   vault token lookup <token>
   ```

3. **Verify entity association**
   ```bash
   vault read auth/approle/role/<role-name>
   ```

4. **Check path matching**
   - Ensure paths match exactly (including `secret/data/` prefix)
   - Verify wildcard usage matches intended scope

### Permission Denied Errors

1. **Verify required capabilities**
   ```bash
   vault read sys/policy/<policy-name>
   ```

2. **Check policy order** (in case of conflicting rules)
   - Vault evaluates more specific paths first
   - Order within policy doesn't matter

---

## References

- [Vault Policy Documentation](https://developer.hashicorp.com/vault/docs/concepts/policies)
- [Vault ACL Policy Syntax](https://developer.hashicorp.com/vault/docs/concepts/policies#acl-policy-syntax)
- [Vault Secrets Engine](https://developer.hashicorp.com/vault/docs/secrets)
- [bike-data-flow Vault Configuration](./vault-server-config.md)
- [Vault Rotation Configuration](./vault-rotation-config.md)
