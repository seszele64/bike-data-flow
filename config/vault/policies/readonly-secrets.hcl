# Read-Only Access to All Secrets
# Policy: readonly-secrets
#
# Purpose: Provides read-only access for monitoring, auditing, and
# readonly users who need to view secret values without modification.
# This policy follows the principle of least privilege - no write,
# delete, or admin capabilities are granted.

# Read access to all bike-data-flow secrets
path "secret/data/bike-data-flow/*" {
  capabilities = ["read", "list"]
  description = "Read and list access to all secrets"
}

# List access at the root level
path "secret/data/bike-data-flow" {
  capabilities = ["list"]
  description = "List available secret paths"
}

# Read access to any metadata
path "secret/metadata/bike-data-flow/*" {
  capabilities = ["read", "list"]
  description = "Read metadata for all secrets"
}

# Capabilities explicitly denied for readonly:
# - create: Cannot create new secrets
# - update: Cannot modify existing secrets
# - delete: Cannot delete secrets
# - patch: Cannot partially update secrets
# - sudo: Cannot access admin-only paths
