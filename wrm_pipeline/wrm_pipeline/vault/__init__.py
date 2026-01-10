"""Vault module for HashiCorp Vault integration.

This module provides utilities for securely managing secrets using HashiCorp Vault,
including a VaultClient wrapper, Pydantic models, a Dagster resource, and exceptions.
"""

from wrm_pipeline.wrm_pipeline.vault.client import VaultClient, get_cached_client
from wrm_pipeline.wrm_pipeline.vault.exceptions import (
    VaultAuthenticationError,
    VaultConnectionError,
    VaultSecretNotFoundError,
    VaultError,
)
from wrm_pipeline.wrm_pipeline.vault.models import (
    VaultSecret,
    VaultConnectionConfig,
    SecretMetadata,
    AccessPolicy,
    PolicyRule,
    VaultConfig,
    SecretRotationPolicy,
    AuditLog,
    VaultHealth,
    VaultHealthStatus,
    RotationType,
    AuditOperation,
)
from wrm_pipeline.wrm_pipeline.vault.resource import (
    VaultSecretsResource,
    VaultSecretsResourceConfig,
    vault_secrets_resource,
)

__all__ = [
    "VaultClient",
    "get_cached_client",
    "VaultAuthenticationError",
    "VaultConnectionError",
    "VaultSecretNotFoundError",
    "VaultError",
    "VaultSecret",
    "VaultConnectionConfig",
    "SecretMetadata",
    "AccessPolicy",
    "PolicyRule",
    "VaultConfig",
    "SecretRotationPolicy",
    "AuditLog",
    "VaultHealth",
    "VaultHealthStatus",
    "RotationType",
    "AuditOperation",
    "VaultSecretsResource",
    "VaultSecretsResourceConfig",
    "vault_secrets_resource",
]
