"""Pydantic models for HashiCorp Vault integration.

This module defines all data models used by the Vault client, including
secrets, configuration, policies, and audit logging.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class VaultHealthStatus(str, Enum):
    """Vault health status values."""

    INITIALIZED = "initialized"
    SEALED = "sealed"
    UNSEALED = "unsealed"
    STANDBY = "standby"
    MAINTENANCE = "maintenance"
    DISABLED = "disabled"


class RotationType(str, Enum):
    """Types of secret rotation."""

    MANUAL = "manual"
    AUTOMATIC = "automatic"
    SCHEDULED = "scheduled"
    DYNAMIC = "dynamic"


class AuditOperation(str, Enum):
    """Types of audit operations."""

    READ = "read"
    WRITE = "write"
    UPDATE = "update"
    DELETE = "delete"
    LIST = "list"
    DENY = "deny"


class Secret(BaseModel):
    """Represents a secret stored in Vault.

    This model corresponds to the KV v2 secrets engine response format.
    """

    path: str = Field(
        ...,
        description="Full path to the secret in Vault KV v2",
        examples=["secret/data/bike-data-flow/production/database"],
    )
    data: dict[str, Any] = Field(
        ...,
        description="Secret data key-value pairs",
        examples=[{"username": "db_user", "host": "db.example.com"}],
    )
    version: int = Field(
        default=1,
        description="Current version number",
        ge=1,
    )
    created_time: datetime = Field(
        ...,
        description="Creation timestamp",
    )
    custom_metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Custom metadata",
    )

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        """Validate Vault path format."""
        if not v or not v.strip():
            raise ValueError("Path cannot be empty")
        # Vault paths should not contain spaces or special chars except /_-.
        if " " in v:
            raise ValueError("Path cannot contain spaces")
        return v.strip()

    class Config:
        json_schema_extra = {
            "example": {
                "path": "secret/data/bike-data-flow/production/database",
                "data": {
                    "username": "db_user",
                    "host": "db.example.com",
                },
                "version": 3,
                "created_time": "2026-01-10T10:00:00Z",
            }
        }


class VaultSecret(BaseModel):
    """Simplified secret model for external use."""

    path: str = Field(
        ...,
        description="Secret path",
        examples=["bike-data-flow/production/database"],
    )
    data: dict[str, Any] = Field(
        ...,
        description="Secret data",
    )
    version: Optional[int] = Field(
        default=None,
        description="Secret version if available",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "path": "bike-data-flow/production/database",
                "data": {"username": "db_user", "password": "secret123"},
                "version": 2,
            }
        }


class SecretMetadata(BaseModel):
    """Metadata for a secret."""

    secret_path: str = Field(..., description="Path to the secret")
    created_time: datetime = Field(..., description="Creation time")
    deletion_time: Optional[datetime] = Field(
        default=None,
        description="Deletion time if deleted",
    )
    destroyed: bool = Field(default=False, description="Whether secret is destroyed")
    version: int = Field(default=1, description="Current version")

    class Config:
        json_schema_extra = {
            "example": {
                "secret_path": "secret/data/bike-data-flow/production/database",
                "created_time": "2026-01-10T10:00:00Z",
                "destroyed": False,
                "version": 3,
            }
        }


class PolicyRule(BaseModel):
    """Single policy rule defining access to a path."""

    path: str = Field(
        ...,
        description="Secret path pattern (supports wildcards)",
        examples=["secret/data/bike-data-flow/production/*"],
    )
    capabilities: list[str] = Field(
        ...,
        description=(
            "Allowed capabilities: create, read, update, delete, list, patch, sudo"
        ),
        examples=[["read", "list"]],
    )
    description: Optional[str] = Field(
        default=None,
        description="Human-readable description",
    )

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: list[str]) -> list[str]:
        """Validate capability values."""
        valid = {"create", "read", "update", "delete", "list", "patch", "sudo"}
        for cap in v:
            if cap not in valid:
                raise ValueError(f"Invalid capability: {cap}. Must be one of {valid}")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "path": "secret/data/bike-data-flow/production/*",
                "capabilities": ["read"],
                "description": "Read access to all production secrets",
            }
        }


class AccessPolicy(BaseModel):
    """Defines access control policy for Vault secrets."""

    name: str = Field(
        ...,
        description="Policy name (e.g., 'dagster-secrets')",
        examples=["dagster-secrets"],
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    rules: list[PolicyRule] = Field(
        default_factory=list,
        description="Policy rules",
    )
    description: Optional[str] = Field(
        default=None,
        description="Policy description",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "dagster-secrets",
                "rules": [
                    {
                        "path": "secret/data/bike-data-flow/production/*",
                        "capabilities": ["read"],
                        "description": "Read access to production secrets",
                    }
                ],
                "description": "Dagster pipeline access to secrets",
            }
        }


class ListenerConfig(BaseModel):
    """Network listener configuration."""

    type: str = Field(default="tcp", description="Listener type")
    address: str = Field(
        default="0.0.0.0:8200",
        description="Bind address",
        examples=["0.0.0.0:8200"],
    )
    cluster_address: str = Field(
        default="0.0.0.0:8201",
        description="Cluster communication address",
        examples=["0.0.0.0:8201"],
    )

    class Config:
        json_schema_extra = {
            "example": {
                "type": "tcp",
                "address": "0.0.0.0:8200",
                "cluster_address": "0.0.0.0:8201",
            }
        }


class TLSConfig(BaseModel):
    """TLS certificate configuration."""

    enabled: bool = Field(default=True, description="Enable TLS")
    cert_file: Optional[str] = Field(
        default=None,
        description="Path to TLS certificate",
        examples=["/etc/vault.d/tls/vault.crt"],
    )
    key_file: Optional[str] = Field(
        default=None,
        description="Path to TLS private key",
        examples=["/etc/vault.d/tls/vault.key"],
    )
    min_version: str = Field(
        default="tls12",
        description="Minimum TLS version",
        examples=["tls12"],
    )
    cipher_suites: Optional[str] = Field(
        default=None,
        description="Custom cipher suites (optional)",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "enabled": True,
                "cert_file": "/etc/vault.d/tls/vault.crt",
                "key_file": "/etc/vault.d/tls/vault.key",
                "min_version": "tls12",
            }
        }


class SealConfig(BaseModel):
    """Seal configuration for key management."""

    type: str = Field(
        default="shamir",
        description="Seal type: shamir or awskms, etc.",
        examples=["shamir"],
    )
    disabled: bool = Field(default=False, description="Disable seal (not recommended)")

    class Config:
        json_schema_extra = {
            "example": {
                "type": "shamir",
                "disabled": False,
            }
        }


class TelemetryConfig(BaseModel):
    """Telemetry and monitoring configuration."""

    statsite_address: Optional[str] = Field(
        default=None,
        description="StatsD/Statsite address",
        examples=["10.0.0.1:8125"],
    )
    disable_hostname: bool = Field(
        default=False,
        description="Disable hostname prefix",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "statsite_address": "10.0.0.1:8125",
                "disable_hostname": False,
            }
        }


class VaultConfig(BaseModel):
    """Vault server configuration."""

    storage_backend: str = Field(
        default="raft",
        description="Storage backend type",
        examples=["raft"],
    )
    listener: ListenerConfig = Field(..., description="Listener configuration")
    tls: TLSConfig = Field(..., description="TLS configuration")
    seal: Optional[SealConfig] = Field(
        default=None,
        description="Seal configuration for auto-unseal",
    )
    telemetry: Optional[TelemetryConfig] = Field(
        default=None,
        description="Telemetry settings",
    )
    cluster_name: Optional[str] = Field(
        default=None,
        description="Cluster name",
        examples=["bike-data-flow-vault"],
    )
    disable_mlock: bool = Field(
        default=True,
        description="Disable mlock (required for containers)",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "storage_backend": "raft",
                "listener": {
                    "type": "tcp",
                    "address": "0.0.0.0:8200",
                    "cluster_address": "0.0.0.0:8201",
                },
                "tls": {
                    "enabled": True,
                    "cert_file": "/etc/vault.d/tls/vault.crt",
                    "key_file": "/etc/vault.d/tls/vault.key",
                    "min_version": "tls12",
                },
                "cluster_name": "bike-data-flow-vault",
                "disable_mlock": True,
            }
        }


class VaultConnectionConfig(BaseModel):
    """Configuration for Vault client connections."""

    vault_addr: str = Field(
        ...,
        description="Vault server address (https://...)",
        examples=["https://vault.example.com:8200"],
    )
    auth_method: str = Field(
        default="approle",
        description="Authentication method",
        examples=["approle"],
    )
    role_id: Optional[str] = Field(
        default=None,
        description="AppRole role ID (if approle)",
    )
    secret_id: Optional[str] = Field(
        default=None,
        description="AppRole secret ID (if approle)",
    )
    token: Optional[str] = Field(
        default=None,
        description="Vault token (if token auth)",
    )
    namespace: Optional[str] = Field(
        default=None,
        description="Enterprise namespace",
    )
    timeout: int = Field(
        default=30,
        description="Request timeout in seconds",
        ge=1,
        le=300,
    )
    retries: int = Field(
        default=3,
        description="Number of retry attempts",
        ge=0,
        le=10,
    )
    cache_ttl: int = Field(
        default=300,
        description="Secret cache TTL in seconds",
        ge=0,
        le=3600,
    )
    verify: bool = Field(
        default=True,
        description="Verify TLS certificates",
    )

    @field_validator("vault_addr")
    @classmethod
    def validate_vault_addr(cls, v: str) -> str:
        """Validate Vault address format."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("vault_addr must start with http:// or https://")
        return v.rstrip("/")

    @field_validator("auth_method")
    @classmethod
    def validate_auth_method(cls, v: str) -> str:
        """Validate authentication method."""
        valid = {"approle", "token", "kubernetes", "userpass", "ldap"}
        if v not in valid:
            raise ValueError(f"Invalid auth_method: {v}. Must be one of {valid}")
        return v

    def validate(self) -> bool:
        """Validate configuration has required auth credentials."""
        if self.auth_method == "approle":
            return bool(self.role_id)
        elif self.auth_method == "token":
            return bool(self.token)
        elif self.auth_method == "kubernetes":
            return True  # Uses service account
        else:
            return False

    class Config:
        json_schema_extra = {
            "example": {
                "vault_addr": "https://vault.example.com:8200",
                "auth_method": "approle",
                "role_id": "my-role-id",
                "secret_id": "my-secret-id",
                "timeout": 30,
                "retries": 3,
                "cache_ttl": 300,
            }
        }


class RotationStatus(str, Enum):
    """Status of a rotation operation."""

    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ROLLED_BACK = "rolled_back"


class RotationHistory(BaseModel):
    """Record of a secret rotation operation."""

    id: str = Field(
        ...,
        description="Unique rotation history ID",
        examples=["rot-abc123"],
    )
    secret_path: str = Field(
        ...,
        description="Path to the secret that was rotated",
        examples=["secret/data/bike-data-flow/production/database"],
    )
    rotation_type: RotationType = Field(..., description="Type of rotation performed")
    status: RotationStatus = Field(..., description="Rotation status")
    timestamp: datetime = Field(..., description="When the rotation occurred")
    performed_by: Optional[str] = Field(
        default=None,
        description="User or system that performed the rotation",
        examples=["system-scheduler", "admin@bike-data-flow"],
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if rotation failed",
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        description="Duration of rotation in seconds",
    )
    previous_version: Optional[int] = Field(
        default=None,
        description="Previous secret version",
    )
    new_version: Optional[int] = Field(
        default=None,
        description="New secret version",
    )
    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Additional rotation metadata",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "id": "rot-abc123",
                "secret_path": "secret/data/bike-data-flow/production/database",
                "rotation_type": "automatic",
                "status": "success",
                "timestamp": "2026-01-10T12:00:00Z",
                "performed_by": "system-scheduler",
                "duration_seconds": 2.5,
                "previous_version": 5,
                "new_version": 6,
            }
        }


class SecretRotationPolicy(BaseModel):
    """Configuration for secret rotation behavior."""

    secret_path: str = Field(
        ...,
        description="Path to secret being rotated",
        examples=["secret/data/bike-data-flow/production/api-key"],
    )
    rotation_type: RotationType = Field(
        ...,
        description="Type of rotation (manual, automatic, scheduled)",
    )
    rotation_period_days: Optional[int] = Field(
        default=None,
        description="Days between rotations",
        ge=1,
    )
    rotation_script_path: Optional[str] = Field(
        default=None,
        description="Path to custom rotation script",
        examples=["/opt/vault/scripts/rotate-db-credentials.sh"],
    )
    last_rotated: Optional[datetime] = Field(
        default=None,
        description="Last rotation timestamp",
    )
    next_rotation: Optional[datetime] = Field(
        default=None,
        description="Next scheduled rotation",
    )
    is_active: bool = Field(
        default=True,
        description="Whether rotation is active for this secret",
    )
    cron_schedule: Optional[str] = Field(
        default=None,
        description="Cron expression for scheduled rotation",
        examples=["0 2 * * 0"],  # Weekly on Sunday at 2am
    )
    notify_on_failure: bool = Field(
        default=True,
        description="Send notifications on rotation failure",
    )
    notify_emails: list[str] = Field(
        default_factory=list,
        description="Email addresses to notify",
        examples=["admin@bike-data-flow"],
    )
    max_retries: int = Field(
        default=3,
        description="Maximum rotation retry attempts",
        ge=0,
        le=10,
    )
    rollback_on_failure: bool = Field(
        default=True,
        description="Rollback to previous version on failure",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "secret_path": "secret/data/bike-data-flow/production/api-key",
                "rotation_type": "scheduled",
                "rotation_period_days": 90,
                "rotation_script_path": "/opt/vault/scripts/rotate-api-key.sh",
                "last_rotated": "2025-10-12T10:00:00Z",
                "next_rotation": "2026-01-10T10:00:00Z",
                "is_active": True,
                "cron_schedule": "0 2 * * 0",
                "notify_on_failure": True,
                "notify_emails": ["admin@bike-data-flow"],
                "max_retries": 3,
                "rollback_on_failure": True,
            }
        }

    @field_validator("notify_emails")
    @classmethod
    def validate_emails(cls, v: list[str]) -> list[str]:
        """Validate email addresses."""
        import re
        email_pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        for email in v:
            if not re.match(email_pattern, email):
                raise ValueError(f"Invalid email address: {email}")
        return v


class AuditLog(BaseModel):
    """Audit log entry for Vault operations."""

    timestamp: datetime = Field(..., description="Operation timestamp")
    accessor: str = Field(
        ...,
        description="Entity that performed the operation",
        examples=["token-abcd1234"],
    )
    operation: AuditOperation = Field(..., description="Type of operation")
    path: str = Field(
        ...,
        description="Secret path accessed",
        examples=["secret/data/bike-data-flow/production/database"],
    )
    success: bool = Field(..., description="Whether operation succeeded")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    client_ip: Optional[str] = Field(
        default=None,
        description="Client IP address",
        examples=["10.0.0.5"],
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Vault request ID",
        examples=["req-xyz789"],
    )

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2026-01-10T10:30:00Z",
                "accessor": "token-abcd1234",
                "operation": "read",
                "path": "secret/data/bike-data-flow/production/database",
                "success": True,
                "client_ip": "10.0.0.5",
                "request_id": "req-xyz789",
            }
        }


class VaultHealth(BaseModel):
    """Vault server health status."""

    status: VaultHealthStatus = Field(..., description="Current status")
    version: str = Field(..., description="Vault version", examples=["1.15.0"])
    cluster_id: Optional[str] = Field(
        default=None,
        description="Cluster identifier",
        examples=["vault-cluster-1234"],
    )
    cluster_name: Optional[str] = Field(
        default=None,
        description="Cluster name",
        examples=["bike-data-flow-vault"],
    )
    replication_mode: Optional[str] = Field(
        default=None,
        description="Replication mode",
    )
    server_time_utc: datetime = Field(..., description="Server time")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "unsealed",
                "version": "1.15.0",
                "cluster_id": "vault-cluster-1234",
                "cluster_name": "bike-data-flow-vault",
                "server_time_utc": "2026-01-10T10:30:00Z",
            }
        }
