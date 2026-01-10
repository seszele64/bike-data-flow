# Full Admin Access to Secrets
# Policy: admin-secrets
#
# Purpose: Provides full administrative access to all secrets for
# operations team members. This policy grants all capabilities
# including sudo for admin-only operations and policy management.
# WARNING: This is a highly privileged policy - assign only to trusted admins.

# Full access to all bike-data-flow secrets
path "secret/data/bike-data-flow/*" {
  capabilities = ["create", "read", "update", "delete", "list", "patch", "sudo"]
  description = "Full access to all secrets with sudo for admin operations"
}

# Full access at root level
path "secret/data/bike-data-flow" {
  capabilities = ["create", "read", "update", "delete", "list"]
  description = "Full access at secret root level"
}

# Access to secret metadata
path "secret/metadata/bike-data-flow/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
  description = "Full access to secret metadata"
}

# Policy management capabilities
path "sys/policy/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
  description = "Manage Vault policies"
}

# ACL management
path "sys/acl/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
  description = "Manage ACL policies"
}

# Token management (for creating child tokens)
path "auth/token/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
  description = "Manage authentication tokens"
}

# Audit log access
path "sys/audit/*" {
  capabilities = ["read", "list"]
  description = "Read audit logs (read-only for security)"
}

# Health and status access
path "sys/health" {
  capabilities = ["read"]
  description = "Read Vault health status"
}

# Capabilities included:
# - create: Create new secrets
# - read: Read secret values
# - update: Update existing secrets
# - delete: Delete secrets
# - list: List secret paths
# - patch: Partially update secrets
# - sudo: Access admin-only paths and operations
