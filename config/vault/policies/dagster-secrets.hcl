# Dagster Pipeline Access to Secrets
# Policy: dagster-secrets
#
# Purpose: Provides read-only access to secrets required by Dagster pipelines.
# This policy follows the principle of least privilege - pipelines can only
# read the secrets they need for execution.

# Application secrets - read access for configuration
path "secret/data/bike-data-flow/app/*" {
  capabilities = ["read", "list"]
  description = "Read access to application configuration secrets"
}

# Application secrets root - list only
path "secret/data/bike-data-flow/app" {
  capabilities = ["list"]
  description = "List application secret keys"
}

# Database credentials - read access
path "secret/data/bike-data-flow/database/*" {
  capabilities = ["read", "list"]
  description = "Read access to database credentials"
}

# Database secrets root - list only
path "secret/data/bike-data-flow/database" {
  capabilities = ["list"]
  description = "List database secret keys"
}

# API keys and tokens - read access
path "secret/data/bike-data-flow/api/*" {
  capabilities = ["read", "list"]
  description = "Read access to API keys and tokens"
}

# API secrets root - list only
path "secret/data/bike-data-flow/api" {
  capabilities = ["list"]
  description = "List API secret keys"
}

# Storage credentials - read access
path "secret/data/bike-data-flow/storage/*" {
  capabilities = ["read", "list"]
  description = "Read access to storage credentials"
}

# Storage secrets root - list only
path "secret/data/bike-data-flow/storage" {
  capabilities = ["list"]
  description = "List storage secret keys"
}
