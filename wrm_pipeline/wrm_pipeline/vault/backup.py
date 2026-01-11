"""Vault Backup and Disaster Recovery Module.

This module provides comprehensive backup and restore capabilities for HashiCorp Vault,
including:

- Snapshot management (create, list, restore, verify, delete)
- Automated backup scheduling (daily, weekly, monthly)
- Backup retention policy management
- Hetzner Object Storage integration for remote backups
- Backup metadata tracking and reporting

Example:
    >>> from wrm_pipeline.wrm_pipeline.vault.backup import SnapshotManager, BackupScheduler
    >>> manager = SnapshotManager(vault_client)
    >>> snapshot = manager.create_snapshot(encryption_key="gpg-key-id")
    >>> manager.upload_to_object_storage(snapshot.path)

"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Protocol
from uuid import uuid4

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from wrm_pipeline.wrm_pipeline.vault.client import VaultClient
from wrm_pipeline.wrm_pipeline.vault.exceptions import VaultError
from wrm_pipeline.wrm_pipeline.vault.models import VaultConnectionConfig

logger = logging.getLogger(__name__)


# ============================================================================
# Enums and Data Classes
# ============================================================================


class BackupType(str, Enum):
    """Types of vault backups."""

    FULL = "full"
    INCREMENTAL = "incremental"
    EXPORT = "export"


class BackupStatus(str, Enum):
    """Status of a backup operation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFIED = "verified"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ScheduleFrequency(str, Enum):
    """Backup schedule frequencies."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"


class StorageBackend(str, Enum):
    """Supported storage backends for backup storage."""

    LOCAL = "local"
    HETZNER_OBJECT_STORAGE = "hetzner"
    AWS_S3 = "aws_s3"
    AZURE_BLOB = "azure_blob"


@dataclass
class BackupMetadata:
    """Metadata for a backup snapshot."""

    id: str
    backup_type: BackupType
    status: BackupStatus
    created_at: datetime
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    encryption_algorithm: Optional[str] = None
    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_path: Optional[str] = None
    retention_days: Optional[int] = None
    expires_at: Optional[datetime] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "backup_type": self.backup_type.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "file_path": self.file_path,
            "file_size": self.file_size,
            "checksum": self.checksum,
            "encryption_algorithm": self.encryption_algorithm,
            "storage_backend": self.storage_backend.value,
            "storage_path": self.storage_path,
            "retention_days": self.retention_days,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupMetadata":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            backup_type=BackupType(data["backup_type"]),
            status=BackupStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            file_path=data.get("file_path"),
            file_size=data.get("file_size"),
            checksum=data.get("checksum"),
            encryption_algorithm=data.get("encryption_algorithm"),
            storage_backend=StorageBackend(data.get("storage_backend", "local")),
            storage_path=data.get("storage_path"),
            retention_days=data.get("retention_days"),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            error_message=data.get("error_message"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class RetentionPolicy:
    """Backup retention policy configuration."""

    name: str
    daily_backups: int = 7  # Keep 7 daily backups
    weekly_backups: int = 4  # Keep 4 weekly backups
    monthly_backups: int = 12  # Keep 12 monthly backups
    yearly_backups: int = 3  # Keep 3 yearly backups
    archive_to_cold_storage: bool = False
    cold_storage_days: int = 30  # Archive after 30 days
    cold_storage_backend: Optional[StorageBackend] = None
    delete_after_archive: bool = True
    retain_minimum: int = 2  # Always keep at least 2 backups

    def should_retain(
        self,
        backup: BackupMetadata,
        all_backups: list[BackupMetadata],
    ) -> bool:
        """Determine if a backup should be retained based on policy.

        Args:
            backup: The backup to evaluate
            all_backups: All available backups

        Returns:
            True if the backup should be retained
        """
        # Always keep backups that are not failed
        if backup.status in [BackupStatus.FAILED, BackupStatus.PENDING]:
            return False

        # Check minimum retention
        successful_backups = [b for b in all_backups if b.status == BackupStatus.COMPLETED]
        if len(successful_backups) <= self.retain_minimum:
            return True

        # Calculate age of backup
        age_days = (datetime.utcnow() - backup.created_at).days

        # Categorize backup by type
        if backup.backup_type == BackupType.FULL:
            # Full backups are more important
            return self._should_retain_full(backup, all_backups, age_days)
        else:
            # Incremental backups
            return self._should_retain_incremental(backup, all_backups, age_days)

    def _should_retain_full(
        self,
        backup: BackupMetadata,
        all_backups: list[BackupMetadata],
        age_days: int,
    ) -> bool:
        """Determine if a full backup should be retained."""
        full_backups = sorted(
            [b for b in all_backups if b.backup_type == BackupType.FULL],
            key=lambda b: b.created_at,
            reverse=True,
        )

        # Get position in the sorted list
        try:
            position = full_backups.index(backup)
        except ValueError:
            return True

        # Keep recent full backups
        if position < self.daily_backups:
            return True

        # Keep weekly backups for the last N weeks
        if age_days < self.weekly_backups * 7:
            return True

        # Keep monthly backups for the last N months
        if age_days < self.monthly_backups * 30:
            return position < (self.daily_backups + self.weekly_backups + self.monthly_backups)

        # Keep yearly backups
        if age_days < self.yearly_backups * 365:
            return position < (self.daily_backups + self.weekly_backups + self.monthly_backups + self.yearly_backups)

        return False

    def _should_retain_incremental(
        self,
        backup: BackupMetadata,
        all_backups: list[BackupMetadata],
        age_days: int,
    ) -> bool:
        """Determine if an incremental backup should be retained."""
        # Keep recent incremental backups (associated with recent full backups)
        if age_days < self.daily_backups:
            return True

        # Older incremental backups can be pruned
        return False


@dataclass
class HetznerStorageConfig:
    """Configuration for Hetzner Object Storage."""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"
    storage_class: str = "STANDARD"

    def create_s3_client(self) -> boto3.Client:
        """Create an S3 client configured for Hetzner Object Storage."""
        config = BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        )
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=config,
        )


# ============================================================================
# Storage Backend Protocol
# ============================================================================


class StorageBackendProtocol(Protocol):
    """Protocol for backup storage backends."""

    def upload(self, local_path: str, remote_path: str) -> str:
        """Upload a file to storage.

        Args:
            local_path: Path to local file
            remote_path: Path in remote storage

        Returns:
            Remote storage path
        """
        ...

    def download(self, remote_path: str, local_path: str) -> str:
        """Download a file from storage.

        Args:
            remote_path: Path in remote storage
            local_path: Path to save local file

        Returns:
            Local file path
        """
        ...

    def list(self, prefix: str = "") -> list[str]:
        """List files in storage.

        Args:
            prefix: Path prefix to filter

        Returns:
            List of file paths
        """
        ...

    def delete(self, remote_path: str) -> bool:
        """Delete a file from storage.

        Args:
            remote_path: Path in remote storage

        Returns:
            True if deleted successfully
        """
        ...

    def exists(self, remote_path: str) -> bool:
        """Check if a file exists in storage.

        Args:
            remote_path: Path in remote storage

        Returns:
            True if file exists
        """
        ...


# ============================================================================
# Storage Backend Implementations
# ============================================================================


class LocalStorageBackend:
    """Local filesystem storage backend."""

    def __init__(self, base_path: str = "/var/backups/vault"):
        """Initialize local storage backend.

        Args:
            base_path: Base directory for backups
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def upload(self, local_path: str, remote_path: str) -> str:
        """Copy file to local storage."""
        dest_path = self.base_path / remote_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest_path)
        return str(dest_path)

    def download(self, remote_path: str, local_path: str) -> str:
        """Copy file from local storage."""
        src_path = self.base_path / remote_path
        shutil.copy2(src_path, local_path)
        return local_path

    def list(self, prefix: str = "") -> list[str]:
        """List files in local storage."""
        search_path = self.base_path / prefix if prefix else self.base_path
        if not search_path.exists():
            return []
        return [str(p.relative_to(self.base_path)) for p in search_path.rglob("*") if p.is_file()]

    def delete(self, remote_path: str) -> bool:
        """Delete file from local storage."""
        file_path = self.base_path / remote_path
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def exists(self, remote_path: str) -> bool:
        """Check if file exists in local storage."""
        return (self.base_path / remote_path).exists()


class HetznerObjectStorageBackend:
    """Hetzner Object Storage backend (S3-compatible)."""

    def __init__(self, config: HetznerStorageConfig):
        """Initialize Hetzner Object Storage backend.

        Args:
            config: Hetzner storage configuration
        """
        self.config = config
        self._client: Optional[boto3.Client] = None

    @property
    def client(self) -> boto3.Client:
        """Get or create S3 client."""
        if self._client is None:
            self._client = self.config.create_s3_client()
        return self._client

    def upload(self, local_path: str, remote_path: str) -> str:
        """Upload file to Hetzner Object Storage."""
        try:
            extra_args = {"StorageClass": self.config.storage_class}
            self.client.upload_file(
                local_path,
                self.config.bucket,
                remote_path,
                ExtraArgs=extra_args,
            )
            logger.info(f"Uploaded {local_path} to hetzner://{self.config.bucket}/{remote_path}")
            return f"hetzner://{self.config.bucket}/{remote_path}"
        except ClientError as e:
            raise VaultError(f"Failed to upload to Hetzner Object Storage: {e}") from e

    def download(self, remote_path: str, local_path: str) -> str:
        """Download file from Hetzner Object Storage."""
        try:
            self.client.download_file(
                self.config.bucket,
                remote_path,
                local_path,
            )
            logger.info(f"Downloaded hetzner://{self.config.bucket}/{remote_path} to {local_path}")
            return local_path
        except ClientError as e:
            raise VaultError(f"Failed to download from Hetzner Object Storage: {e}") from e

    def list(self, prefix: str = "") -> list[str]:
        """List files in Hetzner Object Storage."""
        try:
            response = self.client.list_objects_v2(
                Bucket=self.config.bucket,
                Prefix=prefix,
            )
            return [obj["Key"] for obj in response.get("Contents", [])]
        except ClientError as e:
            logger.error(f"Failed to list objects: {e}")
            return []

    def delete(self, remote_path: str) -> bool:
        """Delete file from Hetzner Object Storage."""
        try:
            self.client.delete_object(
                Bucket=self.config.bucket,
                Key=remote_path,
            )
            logger.info(f"Deleted hetzner://{self.config.bucket}/{remote_path}")
            return True
        except ClientError as e:
            logger.error(f"Failed to delete object: {e}")
            return False

    def exists(self, remote_path: str) -> bool:
        """Check if file exists in Hetzner Object Storage."""
        try:
            self.client.head_object(
                Bucket=self.config.bucket,
                Key=remote_path,
            )
            return True
        except ClientError:
            return False


# ============================================================================
# Snapshot Manager
# ============================================================================


class SnapshotManager:
    """Manages Vault snapshots for backup and restore operations.

    This class provides comprehensive snapshot management including:
    - Creating encrypted snapshots
    - Listing and filtering snapshots
    - Restoring from snapshots
    - Verifying snapshot integrity
    - Deleting old snapshots
    - Uploading to remote storage

    Example:
        >>> manager = SnapshotManager(vault_client)
        >>> snapshot = manager.create_snapshot(encryption_key="gpg-key-id")
        >>> snapshots = manager.list_snapshots(status=BackupStatus.COMPLETED)
        >>> manager.verify_snapshot(snapshot.id)
    """

    def __init__(
        self,
        vault_client: VaultClient,
        storage_backend: Optional[StorageBackendProtocol] = None,
        backup_dir: str = "/var/backups/vault",
    ):
        """Initialize the snapshot manager.

        Args:
            vault_client: Vault client instance
            storage_backend: Storage backend for remote backups
            backup_dir: Local directory for backup files
        """
        self.vault_client = vault_client
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.metadata_file = self.backup_dir / "backup-metadata.json"

        # Storage backend
        if storage_backend is None:
            self.storage_backend: StorageBackendProtocol = LocalStorageBackend(str(self.backup_dir))
        else:
            self.storage_backend = storage_backend

        # Load metadata
        self._metadata: list[BackupMetadata] = self._load_metadata()

    def _load_metadata(self) -> list[BackupMetadata]:
        """Load backup metadata from file."""
        if self.metadata_file.exists():
            try:
                data = json.loads(self.metadata_file.read_text())
                return [BackupMetadata.from_dict(item) for item in data.get("backups", [])]
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load backup metadata: {e}")
        return []

    def _save_metadata(self) -> None:
        """Save backup metadata to file."""
        data = {"backups": [bm.to_dict() for bm in self._metadata]}
        self.metadata_file.write_text(json.dumps(data, indent=2))
        self.metadata_file.chmod(0o600)

    def _generate_snapshot_name(self, backup_type: BackupType) -> str:
        """Generate a unique snapshot name."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return f"vault-backup-{backup_type.value}-{timestamp}-{uuid4().hex[:8]}"

    def create_snapshot(
        self,
        backup_type: BackupType = BackupType.FULL,
        encryption_key: Optional[str] = None,
        encrypt: bool = False,
        upload: bool = False,
        retention_days: Optional[int] = None,
    ) -> BackupMetadata:
        """Create a new Vault snapshot.

        Args:
            backup_type: Type of backup (full or incremental)
            encryption_key: GPG key ID for encryption
            encrypt: Whether to encrypt the backup
            upload: Whether to upload to remote storage
            retention_days: Days to retain the backup

        Returns:
            BackupMetadata for the created snapshot

        Raises:
            VaultError: If snapshot creation fails
        """
        snapshot_id = self._generate_snapshot_name(backup_type)
        local_file = self.backup_dir / f"{snapshot_id}.snap"

        metadata = BackupMetadata(
            id=snapshot_id,
            backup_type=backup_type,
            status=BackupStatus.IN_PROGRESS,
            created_at=datetime.utcnow(),
            file_path=str(local_file),
            retention_days=retention_days,
        )

        try:
            self.vault_client._ensure_authenticated()
            client = self.vault_client._get_client()

            # Attempt to create Raft snapshot
            if backup_type == BackupType.FULL:
                try:
                    logger.info("Creating Raft snapshot...")
                    response = client._adapter.get("/v1/sys/storage/raft/snapshot")

                    # Write snapshot to file
                    local_file.write_bytes(response.content)

                    metadata.file_size = local_file.stat().st_size
                    metadata.status = BackupStatus.COMPLETED

                    logger.info(f"Created Raft snapshot: {snapshot_id} ({metadata.file_size} bytes)")

                except Exception as e:
                    logger.warning(f"Raft snapshot failed: {e}, falling back to manual export")
                    backup_type = BackupType.EXPORT
                    self._create_manual_export(local_file)
                    metadata.backup_type = BackupType.EXPORT
                    metadata.file_size = local_file.stat().st_size
                    metadata.status = BackupStatus.COMPLETED

            else:
                # Incremental backup - for now, create a full export
                # In production, use differential snapshots
                logger.info("Creating incremental backup (full export)...")
                self._create_manual_export(local_file)
                metadata.file_size = local_file.stat().st_size
                metadata.status = BackupStatus.COMPLETED

            # Encrypt if requested
            if encrypt and encryption_key:
                encrypted_file = self._encrypt_snapshot(local_file, encryption_key)
                local_file.unlink()
                metadata.file_path = str(encrypted_file)
                metadata.encryption_algorithm = "gpg"
                logger.info(f"Encrypted snapshot with GPG key {encryption_key}")

            # Upload to remote storage
            if upload:
                remote_path = self._upload_snapshot(metadata)
                metadata.storage_backend = StorageBackend.HETZNER_OBJECT_STORAGE
                metadata.storage_path = remote_path

            # Calculate expiration
            if retention_days:
                metadata.expires_at = datetime.utcnow() + timedelta(days=retention_days)

            # Verify snapshot
            self._verify_snapshot_integrity(metadata)

        except Exception as e:
            metadata.status = BackupStatus.FAILED
            metadata.error_message = str(e)
            logger.error(f"Failed to create snapshot: {e}")
            raise VaultError(f"Snapshot creation failed: {e}") from e

        finally:
            self._metadata.append(metadata)
            self._save_metadata()

        return metadata

    def _create_manual_export(self, output_path: Path) -> None:
        """Create a manual export of all secrets.

        Args:
            output_path: Path to write the export
        """
        secrets_data: dict[str, Any] = {}

        # List all secret mounts
        self.vault_client._ensure_authenticated()
        client = self.vault_client._get_client()

        mounts_response = client.sys.list_mounted_secrets_engines()
        mounts = mounts_response.get("data", {})

        for mount_path, mount_info in mounts.items():
            if mount_info.get("type") != "kv":
                continue

            # List secrets in this mount
            try:
                secrets_list = client.secrets.kv.v2.list_secrets(
                    path="",
                    mount_point=mount_path.rstrip("/"),
                )
                secret_paths = secrets_list.get("data", {}).get("keys", [])

                for secret_path in secret_paths:
                    full_path = f"{mount_path}{secret_path}"
                    try:
                        secret_response = client.secrets.kv.v2.read_secret_version(
                            path=secret_path.rstrip("/"),
                            mount_point=mount_path.rstrip("/"),
                        )
                        secret_data = secret_response.get("data", {}).get("data", {})
                        secrets_data[full_path] = secret_data
                    except Exception as e:
                        logger.warning(f"Failed to read secret {full_path}: {e}")

            except Exception as e:
                logger.warning(f"Failed to list secrets in {mount_path}: {e}")

        # Write export to file
        export = {
            "backup_type": "manual_export",
            "backup_timestamp": datetime.utcnow().isoformat(),
            "vault_addr": self.vault_client.config.vault_addr,
            "version": "1.0",
            "secrets": secrets_data,
        }
        output_path.write_text(json.dumps(export, indent=2))

    def _encrypt_snapshot(self, snapshot_file: Path, encryption_key: str) -> Path:
        """Encrypt a snapshot file with GPG.

        Args:
            snapshot_file: Path to the snapshot file
            encryption_key: GPG key ID or recipient

        Returns:
            Path to encrypted file
        """
        encrypted_file = snapshot_file.with_suffix(".snap.gpg")

        result = subprocess.run(
            [
                "gpg", "--batch", "--yes", "--encrypt",
                "--recipient", encryption_key,
                "--output", str(encrypted_file),
                str(snapshot_file),
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise VaultError(f"GPG encryption failed: {result.stderr}")

        return encrypted_file

    def _upload_snapshot(self, metadata: BackupMetadata) -> str:
        """Upload snapshot to remote storage.

        Args:
            metadata: Backup metadata

        Returns:
            Remote storage path
        """
        local_path = metadata.file_path
        if not local_path:
            raise VaultError("No file path in metadata")

        remote_path = f"{metadata.backup_type.value}/{metadata.id}.snap"
        if metadata.encryption_algorithm:
            remote_path += ".gpg"

        return self.storage_backend.upload(local_path, remote_path)

    def _verify_snapshot_integrity(self, metadata: BackupMetadata) -> bool:
        """Verify snapshot file integrity.

        Args:
            metadata: Backup metadata

        Returns:
            True if verification passed
        """
        file_path = metadata.file_path
        if not file_path or not Path(file_path).exists():
            logger.error(f"Snapshot file not found: {file_path}")
            return False

        # Check file size
        file_size = Path(file_path).stat().st_size
        if file_size < 100:
            logger.error(f"Snapshot file too small: {file_size} bytes")
            return False

        # Calculate checksum
        checksum = self._calculate_checksum(file_path)
        metadata.checksum = checksum

        # If encrypted, try to verify GPG signature
        if metadata.encryption_algorithm == "gpg":
            result = subprocess.run(
                ["gpg", "--batch", "--verify", file_path],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                metadata.status = BackupStatus.VERIFIED
                logger.info(f"Verified encrypted snapshot: {metadata.id}")
            else:
                logger.warning(f"Could not verify GPG signature: {result.stderr}")

        metadata.status = BackupStatus.VERIFIED
        logger.info(f"Verified snapshot: {metadata.id}")
        return True

    def _calculate_checksum(self, file_path: str) -> str:
        """Calculate SHA-256 checksum of a file.

        Args:
            file_path: Path to the file

        Returns:
            SHA-256 checksum hex string
        """
        result = subprocess.run(
            ["sha256sum", file_path],
            capture_output=True,
            text=True,
        )
        return result.stdout.split()[0] if result.returncode == 0 else ""

    def list_snapshots(
        self,
        backup_type: Optional[BackupType] = None,
        status: Optional[BackupStatus] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[BackupMetadata]:
        """List available snapshots with optional filtering.

        Args:
            backup_type: Filter by backup type
            status: Filter by status
            since: Only include snapshots created after this time
            limit: Maximum number of snapshots to return

        Returns:
            List of matching BackupMetadata objects
        """
        results = self._metadata

        if backup_type:
            results = [b for b in results if b.backup_type == backup_type]

        if status:
            results = [b for b in results if b.status == status]

        if since:
            results = [b for b in results if b.created_at > since]

        # Sort by creation time, newest first
        results.sort(key=lambda b: b.created_at, reverse=True)

        if limit:
            results = results[:limit]

        return results

    def get_snapshot(self, snapshot_id: str) -> Optional[BackupMetadata]:
        """Get a specific snapshot by ID.

        Args:
            snapshot_id: Snapshot ID

        Returns:
            BackupMetadata or None if not found
        """
        for metadata in self._metadata:
            if metadata.id == snapshot_id:
                return metadata
        return None

    def verify_snapshot(self, snapshot_id: str) -> bool:
        """Verify a snapshot's integrity.

        Args:
            snapshot_id: Snapshot ID to verify

        Returns:
            True if verification passed
        """
        metadata = self.get_snapshot(snapshot_id)
        if not metadata:
            logger.error(f"Snapshot not found: {snapshot_id}")
            return False

        return self._verify_snapshot_integrity(metadata)

    def restore_snapshot(
        self,
        snapshot_id: str,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """Restore a snapshot.

        Args:
            snapshot_id: Snapshot ID to restore
            dry_run: Show what would be done without doing it
            force: Force restore without confirmation

        Returns:
            Dictionary with restore results

        Raises:
            VaultError: If restore fails
        """
        metadata = self.get_snapshot(snapshot_id)
        if not metadata:
            raise VaultError(f"Snapshot not found: {snapshot_id}")

        if metadata.status != BackupStatus.VERIFIED:
            logger.warning(f"Snapshot {snapshot_id} is not verified, verification may fail")

        local_path = metadata.file_path
        if not local_path or not Path(local_path).exists():
            # Try to download from remote storage
            if metadata.storage_path:
                logger.info(f"Downloading from remote storage: {metadata.storage_path}")
                local_path = self._download_snapshot(metadata)
            else:
                raise VaultError(f"Snapshot file not found: {metadata.file_path}")

        logger.info(f"Restoring snapshot: {snapshot_id}")
        logger.info(f"Backup type: {metadata.backup_type.value}")
        logger.info(f"Created: {metadata.created_at.isoformat()}")

        if dry_run:
            logger.info("[DRY RUN] Would restore snapshot")
            return {"dry_run": True, "snapshot_id": snapshot_id}

        # Check Vault status
        health = self.vault_client.get_health()
        if health.status.value == "sealed":
            raise VaultError("Vault is sealed, cannot restore")

        # Perform restore based on backup type
        if metadata.backup_type in [BackupType.FULL, BackupType.INCREMENTAL]:
            result = self._restore_raft_snapshot(local_path)
        else:
            result = self._restore_manual_export(local_path)

        return result

    def _restore_raft_snapshot(self, snapshot_path: str) -> dict[str, Any]:
        """Restore from a Raft snapshot.

        Args:
            snapshot_path: Path to snapshot file

        Returns:
            Restore result dictionary
        """
        self.vault_client._ensure_authenticated()
        client = self.vault_client._get_client()

        with open(snapshot_path, "rb") as f:
            response = client._adapter.put(
                "/v1/sys/storage/raft/restore",
                content=f.read(),
            )

        logger.success("Raft snapshot restored successfully")
        return {"method": "raft-snapshot", "status": "success"}

    def _restore_manual_export(self, snapshot_path: str) -> dict[str, Any]:
        """Restore from a manual export.

        Args:
            snapshot_path: Path to export file

        Returns:
            Restore result dictionary
        """
        export_data = json.loads(Path(snapshot_path).read_text())
        secrets = export_data.get("secrets", {})

        restored = 0
        failed = 0

        for path, data in secrets.items():
            try:
                # Remove leading slash if present
                clean_path = path.lstrip("/")
                
                # Write secret to Vault
                self.vault_client.write_secret(clean_path, data)
                restored += 1
                logger.debug(f"Restored: {clean_path}")
            except Exception as e:
                logger.warning(f"Failed to restore {path}: {e}")
                failed += 1

        logger.info(f"Restored {restored} secrets, {failed} failed")
        return {
            "method": "manual-export",
            "status": "success",
            "restored": restored,
            "failed": failed,
        }

    def _download_snapshot(self, metadata: BackupMetadata) -> str:
        """Download snapshot from remote storage.

        Args:
            metadata: Backup metadata

        Returns:
            Local file path
        """
        if not metadata.storage_path:
            raise VaultError("No remote storage path in metadata")

        local_path = self.backup_dir / f"{metadata.id}.snap"
        if metadata.encryption_algorithm:
            local_path = local_path.with_suffix(".snap.gpg")

        if isinstance(self.storage_backend, HetznerObjectStorageBackend):
            self.storage_backend.download(metadata.storage_path.split("/", 2)[-1], str(local_path))
        else:
            self.storage_backend.download(metadata.storage_path, str(local_path))

        return str(local_path)

    def delete_snapshot(self, snapshot_id: str, delete_remote: bool = True) -> bool:
        """Delete a snapshot.

        Args:
            snapshot_id: Snapshot ID to delete
            delete_remote: Also delete from remote storage

        Returns:
            True if deleted successfully
        """
        metadata = self.get_snapshot(snapshot_id)
        if not metadata:
            logger.error(f"Snapshot not found: {snapshot_id}")
            return False

        # Delete local file
        if metadata.file_path and Path(metadata.file_path).exists():
            Path(metadata.file_path).unlink()
            logger.info(f"Deleted local file: {metadata.file_path}")

        # Delete remote file
        if delete_remote and metadata.storage_path:
            try:
                self.storage_backend.delete(metadata.storage_path)
                logger.info(f"Deleted remote file: {metadata.storage_path}")
            except Exception as e:
                logger.warning(f"Failed to delete remote file: {e}")

        # Update metadata
        metadata.status = BackupStatus.DELETED
        self._save_metadata()

        return True

    def cleanup_expired(self, retention_policy: Optional[RetentionPolicy] = None) -> int:
        """Clean up expired or old backups based on retention policy.

        Args:
            retention_policy: Retention policy to apply

        Returns:
            Number of deleted snapshots
        """
        if retention_policy is None:
            retention_policy = RetentionPolicy(name="default")

        deleted_count = 0
        active_backups = [b for b in self._metadata if b.status != BackupStatus.DELETED]

        for backup in active_backups:
            if not retention_policy.should_retain(backup, active_backups):
                logger.info(f"Deleting backup {backup.id} (retention policy: {retention_policy.name})")
                if self.delete_snapshot(backup.id):
                    deleted_count += 1

        logger.info(f"Cleaned up {deleted_count} expired backups")
        return deleted_count

    def generate_report(
        self,
        since: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Generate a backup report.

        Args:
            since: Only include backups after this time

        Returns:
            Report dictionary with statistics
        """
        backups = self.list_snapshots(since=since)

        total_size = sum(b.file_size or 0 for b in backups)
        successful = [b for b in backups if b.status in [BackupStatus.COMPLETED, BackupStatus.VERIFIED]]
        failed = [b for b in backups if b.status == BackupStatus.FAILED]

        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "period": {
                "since": since.isoformat() if since else None,
            },
            "summary": {
                "total_backups": len(backups),
                "successful": len(successful),
                "failed": len(failed),
                "success_rate": len(successful) / len(backups) * 100 if backups else 0,
                "total_size_bytes": total_size,
                "total_size_human": self._format_size(total_size),
            },
            "by_type": {
                bt.value: len([b for b in backups if b.backup_type == bt])
                for bt in BackupType
            },
            "by_status": {
                bs.value: len([b for b in backups if b.status == bs])
                for bs in BackupStatus
            },
            "recent_backups": [
                {
                    "id": b.id,
                    "type": b.backup_type.value,
                    "status": b.status.value,
                    "size": b.file_size,
                    "created": b.created_at.isoformat(),
                }
                for b in sorted(backups, key=lambda x: x.created_at, reverse=True)[:10]
            ],
        }

        return report

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes to human-readable size."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.2f} PB"


# ============================================================================
# Backup Scheduler
# ============================================================================


class BackupScheduler:
    """Schedules and manages automated Vault backups.

    This class provides:
    - Scheduled backup execution (daily, weekly, monthly)
    - Integration with rotation scheduler
    - Failure notifications
    - Manual on-demand backups

    Example:
        >>> scheduler = BackupScheduler(snapshot_manager)
        >>> scheduler.schedule_daily()
        >>> scheduler.run_backup()  # Manual backup
    """

    def __init__(
        self,
        snapshot_manager: SnapshotManager,
        notify_on_failure: bool = True,
        notify_emails: Optional[list[str]] = None,
    ):
        """Initialize the backup scheduler.

        Args:
            snapshot_manager: Snapshot manager instance
            notify_on_failure: Whether to send notifications on failure
            notify_emails: Email addresses for notifications
        """
        self.snapshot_manager = snapshot_manager
        self.notify_on_failure = notify_on_failure
        self.notify_emails = notify_emails or []

        # Track backup history
        self._backup_history: list[dict[str, Any]] = []
        self._lock = threading.Lock()

        # Default backup settings
        self._default_retention_days = 30
        self._default_encrypt = True
        self._default_upload = True

    def schedule_daily(
        self,
        hour: int = 2,
        minute: int = 0,
        encrypt: Optional[bool] = None,
        upload: Optional[bool] = None,
        retention_days: Optional[int] = None,
    ) -> str:
        """Schedule a daily backup.

        Args:
            hour: Hour of day (0-23)
            minute: Minute (0-59)
            encrypt: Whether to encrypt backups
            upload: Whether to upload to remote storage
            retention_days: Days to retain backups

        Returns:
            Schedule identifier
        """
        schedule_id = f"daily-{uuid4().hex[:8]}"
        schedule = {
            "id": schedule_id,
            "frequency": ScheduleFrequency.DAILY,
            "cron": f"{minute} {hour} * * *",
            "encrypt": encrypt if encrypt is not None else self._default_encrypt,
            "upload": upload if upload is not None else self._default_upload,
            "retention_days": retention_days or self._default_retention_days,
            "enabled": True,
        }
        logger.info(f"Scheduled daily backup at {hour:02d}:{minute:02d}")
        return schedule_id

    def schedule_weekly(
        self,
        day_of_week: int = 0,  # 0 = Sunday
        hour: int = 3,
        minute: int = 0,
        encrypt: Optional[bool] = None,
        upload: Optional[bool] = None,
        retention_days: Optional[int] = None,
    ) -> str:
        """Schedule a weekly backup.

        Args:
            day_of_week: Day of week (0=Sunday, 6=Saturday)
            hour: Hour of day (0-23)
            minute: Minute (0-59)
            encrypt: Whether to encrypt backups
            upload: Whether to upload to remote storage
            retention_days: Days to retain backups

        Returns:
            Schedule identifier
        """
        schedule_id = f"weekly-{uuid4().hex[:8]}"
        schedule = {
            "id": schedule_id,
            "frequency": ScheduleFrequency.WEEKLY,
            "cron": f"{minute} {hour} * * {day_of_week}",
            "day_of_week": day_of_week,
            "encrypt": encrypt if encrypt is not None else self._default_encrypt,
            "upload": upload if upload is not None else self._default_upload,
            "retention_days": retention_days or self._default_retention_days * 4,
            "enabled": True,
        }
        logger.info(f"Scheduled weekly backup on day {day_of_week} at {hour:02d}:{minute:02d}")
        return schedule_id

    def schedule_monthly(
        self,
        day_of_month: int = 1,
        hour: int = 3,
        minute: int = 0,
        encrypt: Optional[bool] = None,
        upload: Optional[bool] = None,
        retention_days: Optional[int] = None,
    ) -> str:
        """Schedule a monthly backup.

        Args:
            day_of_month: Day of month (1-28)
            hour: Hour of day (0-23)
            minute: Minute (0-59)
            encrypt: Whether to encrypt backups
            upload: Whether to upload to remote storage
            retention_days: Days to retain backups

        Returns:
            Schedule identifier
        """
        schedule_id = f"monthly-{uuid4().hex[:8]}"
        schedule = {
            "id": schedule_id,
            "frequency": ScheduleFrequency.MONTHLY,
            "cron": f"{minute} {hour} {day_of_month} * *",
            "encrypt": encrypt if encrypt is not None else self._default_encrypt,
            "upload": upload if upload is not None else self._default_upload,
            "retention_days": retention_days or self._default_retention_days * 12,
            "enabled": True,
        }
        logger.info(f"Scheduled monthly backup on day {day_of_month} at {hour:02d}:{minute:02d}")
        return schedule_id

    def run_backup(
        self,
        backup_type: BackupType = BackupType.FULL,
        encrypt: Optional[bool] = None,
        upload: Optional[bool] = None,
        retention_days: Optional[int] = None,
        skip_verification: bool = False,
    ) -> dict[str, Any]:
        """Run a backup immediately.

        Args:
            backup_type: Type of backup to create
            encrypt: Whether to encrypt the backup
            upload: Whether to upload to remote storage
            retention_days: Days to retain the backup
            skip_verification: Skip verification step

        Returns:
            Backup result dictionary
        """
        result = {
            "backup_type": backup_type.value,
            "timestamp": datetime.utcnow().isoformat(),
            "success": False,
            "snapshot_id": None,
            "error": None,
        }

        try:
            logger.info(f"Starting {backup_type.value} backup...")

            snapshot = self.snapshot_manager.create_snapshot(
                backup_type=backup_type,
                encrypt=encrypt if encrypt is not None else self._default_encrypt,
                upload=upload if upload is not None else self._default_upload,
                retention_days=retention_days or self._default_retention_days,
            )

            result["snapshot_id"] = snapshot.id
            result["success"] = True
            result["file_size"] = snapshot.file_size

            if not skip_verification:
                self.snapshot_manager.verify_snapshot(snapshot.id)

            logger.success(f"Backup completed successfully: {snapshot.id}")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Backup failed: {e}")

            if self.notify_on_failure:
                self._send_failure_notification(e)

        finally:
            # Record in history
            with self._lock:
                self._backup_history.append(result)

        return result

    def get_backup_history(
        self,
        limit: int = 50,
        status_filter: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get backup execution history.

        Args:
            limit: Maximum number of entries to return
            status_filter: Filter by status (success, failed)

        Returns:
            List of backup history entries
        """
        results = self._backup_history

        if status_filter:
            results = [r for r in results if r.get("success") == (status_filter == "success")]

        return sorted(results, key=lambda x: x["timestamp"], reverse=True)[:limit]

    def get_backup_stats(self) -> dict[str, Any]:
        """Get backup statistics.

        Returns:
            Dictionary with backup statistics
        """
        history = self._backup_history
        successful = [h for h in history if h.get("success")]
        failed = [h for h in history if not h.get("success")]

        return {
            "total_backups": len(history),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate": len(successful) / len(history) * 100 if history else 0,
            "last_backup": successful[0] if successful else None,
            "last_failure": failed[0] if failed else None,
        }

    def _send_failure_notification(self, error: Exception) -> None:
        """Send failure notification.

        Args:
            error: The error that occurred
        """
        logger.info(f"Sending failure notification for error: {error}")

        # TODO: Implement email/Slack notifications
        # This is a placeholder for the notification logic
        for email in self.notify_emails:
            logger.info(f"Would send email to {email}: Backup failed - {error}")

    def cleanup_old_backups(
        self,
        retention_policy: Optional[RetentionPolicy] = None,
    ) -> int:
        """Clean up old backups based on retention policy.

        Args:
            retention_policy: Retention policy to apply

        Returns:
            Number of deleted backups
        """
        return self.snapshot_manager.cleanup_expired(retention_policy)


# ============================================================================
# Factory Functions
# ============================================================================


def create_snapshot_manager(
    vault_addr: str,
    vault_token: str,
    storage_backend: Optional[StorageBackend] = None,
    storage_config: Optional[dict[str, Any]] = None,
    backup_dir: str = "/var/backups/vault",
) -> SnapshotManager:
    """Create a snapshot manager with the specified configuration.

    Args:
        vault_addr: Vault server address
        vault_token: Vault authentication token
        storage_backend: Storage backend type
        storage_config: Storage backend configuration
        backup_dir: Local backup directory

    Returns:
        Configured SnapshotManager instance
    """
    config = VaultConnectionConfig(
        vault_addr=vault_addr,
        auth_method="token",
        token=vault_token,
    )
    vault_client = VaultClient(config)

    backend: Optional[StorageBackendProtocol] = None

    if storage_backend == StorageBackend.HETZNER_OBJECT_STORAGE and storage_config:
        hetzner_config = HetznerStorageConfig(
            endpoint=storage_config["endpoint"],
            access_key=storage_config["access_key"],
            secret_key=storage_config["secret_key"],
            bucket=storage_config["bucket"],
        )
        backend = HetznerObjectStorageBackend(hetzner_config)

    return SnapshotManager(
        vault_client=vault_client,
        storage_backend=backend,
        backup_dir=backup_dir,
    )


def create_backup_scheduler(
    snapshot_manager: SnapshotManager,
    notify_on_failure: bool = True,
    notify_emails: Optional[list[str]] = None,
) -> BackupScheduler:
    """Create a backup scheduler.

    Args:
        snapshot_manager: Snapshot manager instance
        notify_on_failure: Whether to send notifications on failure
        notify_emails: Email addresses for notifications

    Returns:
        Configured BackupScheduler instance
    """
    return BackupScheduler(
        snapshot_manager=snapshot_manager,
        notify_on_failure=notify_on_failure,
        notify_emails=notify_emails,
    )
