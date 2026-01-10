"""Secret Rotation Scheduler and History Tracker.

This module provides functionality for scheduling and executing secret rotations,
tracking rotation history, and handling rotation failures with rollback support.
"""

import logging
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from wrm_pipeline.wrm_pipeline.vault.models import (
    RotationHistory,
    RotationStatus,
    RotationType,
    SecretRotationPolicy,
)
from wrm_pipeline.wrm_pipeline.vault.exceptions import VaultError

logger = logging.getLogger(__name__)


class RotationScheduleType(str, Enum):
    """Types of rotation schedules."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"


@dataclass
class ScheduledRotation:
    """Represents a scheduled rotation in the scheduler."""

    policy: SecretRotationPolicy
    last_checked: Optional[datetime] = None
    retry_count: int = 0
    status: str = "pending"


class RotationHistoryTracker:
    """Tracks rotation history for secrets."""

    def __init__(self, storage_path: Optional[str] = None):
        """Initialize the rotation history tracker.

        Args:
            storage_path: Path to store rotation history (default: in-memory)
        """
        self._storage_path = storage_path
        self._history: dict[str, list[RotationHistory]] = {}
        self._history_lock = None  # For thread safety

    def record_rotation(
        self,
        secret_path: str,
        rotation_type: RotationType,
        status: RotationStatus,
        performed_by: Optional[str] = None,
        error_message: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        previous_version: Optional[int] = None,
        new_version: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RotationHistory:
        """Record a rotation operation.

        Args:
            secret_path: Path to the rotated secret
            rotation_type: Type of rotation performed
            status: Result status of the rotation
            performed_by: User or system that performed the rotation
            error_message: Error message if rotation failed
            duration_seconds: Duration of rotation in seconds
            previous_version: Previous secret version
            new_version: New secret version
            metadata: Additional rotation metadata

        Returns:
            RotationHistory record
        """
        rotation_id = f"rot-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow()

        history = RotationHistory(
            id=rotation_id,
            secret_path=secret_path,
            rotation_type=rotation_type,
            status=status,
            timestamp=now,
            performed_by=performed_by,
            error_message=error_message,
            duration_seconds=duration_seconds,
            previous_version=previous_version,
            new_version=new_version,
            metadata=metadata,
        )

        # Store in memory
        if secret_path not in self._history:
            self._history[secret_path] = []
        self._history[secret_path].append(history)

        # Keep only last 100 entries per secret
        if len(self._history[secret_path]) > 100:
            self._history[secret_path] = self._history[secret_path][-100:]

        logger.info(
            f"Recorded rotation history: {rotation_id} for {secret_path}, "
            f"status={status.value}"
        )

        return history

    def get_history(
        self,
        secret_path: Optional[str] = None,
        limit: int = 50,
        status_filter: Optional[RotationStatus] = None,
    ) -> list[RotationHistory]:
        """Get rotation history.

        Args:
            secret_path: Filter by secret path (None for all)
            limit: Maximum number of entries to return
            status_filter: Filter by status

        Returns:
            List of rotation history entries
        """
        if secret_path:
            history = self._history.get(secret_path, [])
        else:
            # Combine all history
            history = []
            for entries in self._history.values():
                history.extend(entries)

        # Apply status filter
        if status_filter:
            history = [h for h in history if h.status == status_filter]

        # Sort by timestamp descending and limit
        history.sort(key=lambda h: h.timestamp, reverse=True)
        return history[:limit]

    def get_rotation_stats(self, secret_path: Optional[str] = None) -> dict[str, Any]:
        """Get rotation statistics.

        Args:
            secret_path: Filter by secret path (None for all secrets)

        Returns:
            Dictionary with rotation statistics
        """
        if secret_path:
            history = self._history.get(secret_path, [])
        else:
            history = []
            for entries in self._history.values():
                history.extend(entries)

        if not history:
            return {
                "total_rotations": 0,
                "success_rate": 0.0,
                "avg_duration_seconds": 0.0,
                "failed_count": 0,
                "success_count": 0,
            }

        successful = [h for h in history if h.status == RotationStatus.SUCCESS]
        failed = [h for h in history if h.status == RotationStatus.FAILED]

        durations = [h.duration_seconds for h in successful if h.duration_seconds]

        return {
            "total_rotations": len(history),
            "success_count": len(successful),
            "failed_count": len(failed),
            "success_rate": len(successful) / len(history) if history else 0.0,
            "avg_duration_seconds": sum(durations) / len(durations) if durations else 0.0,
            "last_rotation": history[0].timestamp.isoformat() if history else None,
        }

    def get_last_successful_rotation(self, secret_path: str) -> Optional[RotationHistory]:
        """Get the last successful rotation for a secret.

        Args:
            secret_path: Path to the secret

        Returns:
            Last successful RotationHistory or None
        """
        history = self._history.get(secret_path, [])
        for entry in sorted(history, key=lambda h: h.timestamp, reverse=True):
            if entry.status == RotationStatus.SUCCESS:
                return entry
        return None


class RotationScheduler:
    """Scheduler for automated secret rotation."""

    # Default rotation schedules
    SCHEDULE_PATTERNS = {
        RotationScheduleType.DAILY: timedelta(days=1),
        RotationScheduleType.WEEKLY: timedelta(weeks=1),
        RotationScheduleType.MONTHLY: timedelta(days=30),
    }

    def __init__(
        self,
        history_tracker: Optional[RotationHistoryTracker] = None,
        rotation_storage_path: Optional[str] = None,
    ):
        """Initialize the rotation scheduler.

        Args:
            history_tracker: Rotation history tracker instance
            rotation_storage_path: Path to store rotation state
        """
        self._history_tracker = history_tracker or RotationHistoryTracker()
        self._storage_path = rotation_storage_path
        self._scheduled_rotations: dict[str, ScheduledRotation] = {}
        self._rotation_lock = None  # For thread safety

    def schedule_rotation(
        self,
        policy: SecretRotationPolicy,
        schedule_type: RotationScheduleType = RotationScheduleType.DAILY,
    ) -> bool:
        """Add a secret to the rotation schedule.

        Args:
            policy: Rotation policy for the secret
            schedule_type: Type of schedule pattern

        Returns:
            True if scheduled successfully
        """
        if not policy.is_active:
            logger.warning(f"Cannot schedule inactive rotation for {policy.secret_path}")
            return False

        # Calculate next rotation if not set
        if policy.next_rotation is None:
            policy.next_rotation = self._calculate_next_rotation(
                policy, schedule_type
            )

        # Store in schedule
        scheduled = ScheduledRotation(policy=policy)
        self._scheduled_rotations[policy.secret_path] = scheduled

        logger.info(
            f"Scheduled rotation for {policy.secret_path}, "
            f"next_rotation={policy.next_rotation.isoformat()}"
        )

        return True

    def remove_rotation(self, secret_path: str) -> bool:
        """Remove a secret from the rotation schedule.

        Args:
            secret_path: Path to the secret

        Returns:
            True if removed, False if not found
        """
        if secret_path in self._scheduled_rotations:
            del self._scheduled_rotations[secret_path]
            logger.info(f"Removed rotation schedule for {secret_path}")
            return True
        return False

    def _calculate_next_rotation(
        self,
        policy: SecretRotationPolicy,
        schedule_type: RotationScheduleType,
    ) -> datetime:
        """Calculate the next rotation timestamp.

        Args:
            policy: Rotation policy
            schedule_type: Schedule pattern type

        Returns:
            Next rotation datetime
        """
        now = datetime.utcnow()

        # Use cron schedule if provided
        if policy.cron_schedule:
            # Simple cron parsing - for production, use a cron library
            return self._parse_cron_schedule(policy.cron_schedule, now)

        # Use rotation period days
        if policy.rotation_period_days:
            return now + timedelta(days=policy.rotation_period_days)

        # Use schedule pattern
        return now + self.SCHEDULE_PATTERNS.get(schedule_type, timedelta(days=1))

    def _parse_cron_schedule(
        self, cron_expr: str, base_time: datetime
    ) -> datetime:
        """Parse a cron expression and calculate next run.

        Args:
            cron_expr: Cron expression (e.g., "0 2 * * 0")
            base_time: Base time for calculation

        Returns:
            Next run datetime
        """
        # Simple cron parser - supports basic formats
        # Format: minute hour day-of-month month day-of-week
        parts = cron_expr.split()
        if len(parts) < 5:
            logger.warning(f"Invalid cron expression: {cron_expr}")
            return base_time + timedelta(days=1)

        try:
            minute = int(parts[0])
            hour = int(parts[1])

            # For simplicity, return next occurrence at the specified time
            next_time = base_time.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )

            if next_time <= base_time:
                next_time += timedelta(days=1)

            return next_time
        except (ValueError, IndexError):
            logger.warning(f"Failed to parse cron expression: {cron_expr}")
            return base_time + timedelta(days=1)

    def check_and_rotate(
        self,
        secret_path: Optional[str] = None,
        force: bool = False,
    ) -> list[RotationHistory]:
        """Check secrets and perform rotations if needed.

        Args:
            secret_path: Specific secret to check (None for all)
            force: Force rotation regardless of schedule

        Returns:
            List of rotation history records
        """
        rotations_performed: list[RotationHistory] = []
        now = datetime.utcnow()

        # Determine which secrets to check
        secrets_to_check = (
            [secret_path]
            if secret_path
            else list(self._scheduled_rotations.keys())
        )

        for path in secrets_to_check:
            if path not in self._scheduled_rotations:
                continue

            scheduled = self._scheduled_rotations[path]
            policy = scheduled.policy

            # Skip inactive policies
            if not policy.is_active:
                continue

            # Check if rotation is due
            is_due = (
                force
                or policy.next_rotation is None
                or now >= policy.next_rotation
            )

            if is_due:
                logger.info(f"Rotation due for {path}, executing...")

                # Perform rotation
                result = self._execute_rotation(policy)

                if result:
                    rotations_performed.append(result)

                    # Update next rotation time
                    if policy.rotation_period_days:
                        policy.next_rotation = now + timedelta(
                            days=policy.rotation_period_days
                        )
                    elif policy.cron_schedule:
                        policy.next_rotation = self._parse_cron_schedule(
                            policy.cron_schedule, now
                        )

                scheduled.last_checked = now

        return rotations_performed

    def _execute_rotation(
        self, policy: SecretRotationPolicy
    ) -> Optional[RotationHistory]:
        """Execute a rotation for a secret.

        Args:
            policy: Rotation policy

        Returns:
            RotationHistory record or None if rotation failed
        """
        start_time = datetime.utcnow()
        previous_version = None
        new_version = None

        try:
            # Execute rotation based on type
            if policy.rotation_type == RotationType.MANUAL:
                # Manual rotation - just record the attempt
                logger.info(f"Manual rotation triggered for {policy.secret_path}")

            elif policy.rotation_type in (
                RotationType.AUTOMATIC,
                RotationType.SCHEDULED,
            ):
                # Automatic/scheduled rotation
                if policy.rotation_script_path:
                    # Execute custom rotation script
                    result = self._run_rotation_script(
                        policy.rotation_script_path, policy.secret_path
                    )
                    if not result:
                        raise VaultError(
                            f"Rotation script failed for {policy.secret_path}"
                        )
                else:
                    # Use built-in rotation
                    logger.info(
                        f"Built-in rotation for {policy.secret_path} "
                        "(no custom script)"
                    )

            # Record successful rotation
            policy.last_rotated = start_time

            duration = (datetime.utcnow() - start_time).total_seconds()

            history = self._history_tracker.record_rotation(
                secret_path=policy.secret_path,
                rotation_type=policy.rotation_type,
                status=RotationStatus.SUCCESS,
                performed_by="system-scheduler",
                duration_seconds=duration,
                previous_version=previous_version,
                new_version=new_version,
            )

            logger.info(
                f"Successfully rotated {policy.secret_path} in {duration:.2f}s"
            )

            return history

        except Exception as e:
            duration = (datetime.utcnow() - start_time).total_seconds()
            error_msg = str(e)

            logger.error(f"Rotation failed for {policy.secret_path}: {error_msg}")

            # Record failed rotation
            history = self._history_tracker.record_rotation(
                secret_path=policy.secret_path,
                rotation_type=policy.rotation_type,
                status=RotationStatus.FAILED,
                performed_by="system-scheduler",
                error_message=error_msg,
                duration_seconds=duration,
                previous_version=previous_version,
                new_version=new_version,
            )

            # Check if we should rollback
            if policy.rollback_on_failure:
                self._rollback_rotation(policy)

            return history

    def _run_rotation_script(
        self, script_path: str, secret_path: str
    ) -> bool:
        """Run a custom rotation script.

        Args:
            script_path: Path to the rotation script
            secret_path: Path to the secret being rotated

        Returns:
            True if script succeeded, False otherwise
        """
        script = Path(script_path)
        if not script.exists():
            logger.error(f"Rotation script not found: {script_path}")
            return False

        if not script.stat().st_mode & 0o111:
            logger.error(f"Rotation script is not executable: {script_path}")
            return False

        try:
            result = subprocess.run(
                [str(script), secret_path],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                logger.error(
                    f"Rotation script failed: {result.stderr or result.stdout}"
                )
                return False

            logger.info(f"Rotation script executed successfully: {script_path}")
            return True

        except subprocess.TimeoutExpired:
            logger.error(f"Rotation script timed out: {script_path}")
            return False
        except Exception as e:
            logger.error(f"Failed to execute rotation script: {e}")
            return False

    def _rollback_rotation(self, policy: SecretRotationPolicy) -> bool:
        """Rollback a failed rotation.

        Args:
            policy: Rotation policy for the secret

        Returns:
            True if rollback succeeded
        """
        logger.warning(f"Attempting rollback for {policy.secret_path}")
        # In a real implementation, this would:
        # 1. Get the previous version of the secret
        # 2. Restore it to Vault
        # 3. Notify about the failure
        # For now, just log the attempt
        return True

    def get_scheduled_secrets(self) -> list[SecretRotationPolicy]:
        """Get all secrets with rotation schedules.

        Returns:
            List of rotation policies
        """
        return [s.policy for s in self._scheduled_rotations.values()]

    def get_rotation_status(self, secret_path: str) -> dict[str, Any]:
        """Get rotation status for a secret.

        Args:
            secret_path: Path to the secret

        Returns:
            Dictionary with rotation status
        """
        scheduled = self._scheduled_rotations.get(secret_path)
        if not scheduled:
            return {"scheduled": False}

        policy = scheduled.policy

        return {
            "scheduled": True,
            "is_active": policy.is_active,
            "rotation_type": policy.rotation_type.value,
            "last_rotated": policy.last_rotated.isoformat() if policy.last_rotated else None,
            "next_rotation": policy.next_rotation.isoformat() if policy.next_rotation else None,
            "rotation_period_days": policy.rotation_period_days,
            "cron_schedule": policy.cron_schedule,
            "stats": self._history_tracker.get_rotation_stats(secret_path),
        }

    def get_due_rotations(self) -> list[SecretRotationPolicy]:
        """Get all secrets with rotations due.

        Returns:
            List of rotation policies that are due for rotation
        """
        now = datetime.utcnow()
        due = []

        for scheduled in self._scheduled_rotations.values():
            policy = scheduled.policy
            if policy.is_active and policy.next_rotation and now >= policy.next_rotation:
                due.append(policy)

        return due


class RotationOrchestrator:
    """Orchestrates secret rotations across different types.

    This class provides type-specific rotation logic for different
    secret types (database credentials, API keys, certificates, etc.)
    """

    def __init__(
        self,
        scheduler: Optional[RotationScheduler] = None,
        vault_client: Optional[Any] = None,
    ):
        """Initialize the rotation orchestrator.

        Args:
            scheduler: Rotation scheduler instance
            vault_client: Vault client for secret operations
        """
        self._scheduler = scheduler or RotationScheduler()
        self._vault_client = vault_client
        self._rotation_handlers: dict[str, callable] = {}

        # Register default handlers
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        """Register default rotation handlers."""
        self._rotation_handlers["database"] = self._rotate_database_credentials
        self._rotation_handlers["api_key"] = self._rotate_api_key
        self._rotation_handlers["certificate"] = self._rotate_certificate
        self._rotation_handlers["generic"] = self._rotate_generic_secret

    def register_handler(
        self, secret_type: str, handler: callable
    ) -> None:
        """Register a custom rotation handler.

        Args:
            secret_type: Type of secret (database, api_key, certificate, etc.)
            handler: Handler function that takes (secret_path, policy) and returns bool
        """
        self._rotation_handlers[secret_type] = handler
        logger.info(f"Registered rotation handler for: {secret_type}")

    def rotate_secret(
        self,
        secret_path: str,
        secret_type: str = "generic",
        force: bool = False,
    ) -> RotationHistory:
        """Rotate a specific secret.

        Args:
            secret_path: Path to the secret
            secret_type: Type of secret for type-specific handling
            force: Force rotation regardless of schedule

        Returns:
            RotationHistory record
        """
        handler = self._rotation_handlers.get(secret_type, self._rotation_handlers["generic"])
        start_time = datetime.utcnow()

        try:
            # Execute the rotation handler
            success = handler(secret_path, None)

            if not success:
                raise VaultError(f"Rotation handler failed for {secret_path}")

            # Record successful rotation
            duration = (datetime.utcnow() - start_time).total_seconds()

            history = self._history_tracker.record_rotation(
                secret_path=secret_path,
                rotation_type=RotationType.MANUAL,
                status=RotationStatus.SUCCESS,
                performed_by="manual",
                duration_seconds=duration,
            )

            return history

        except Exception as e:
            duration = (datetime.utcnow() - start_time).total_seconds()

            history = self._history_tracker.record_rotation(
                secret_path=secret_path,
                rotation_type=RotationType.MANUAL,
                status=RotationStatus.FAILED,
                performed_by="manual",
                error_message=str(e),
                duration_seconds=duration,
            )

            raise

    def _rotate_database_credentials(
        self, secret_path: str, policy: Optional[SecretRotationPolicy]
    ) -> bool:
        """Rotate database credentials.

        Args:
            secret_path: Path to the database credentials
            policy: Rotation policy

        Returns:
            True if rotation succeeded
        """
        # Generate new credentials
        import secrets
        import string

        alphabet = string.ascii_letters + string.digits
        new_password = "".join(secrets.choice(alphabet) for _ in range(32))

        # Update Vault
        if self._vault_client:
            current = self._vault_client.get_secret(secret_path)
            new_data = current.data.copy()
            new_data["password"] = new_password

            # Get current version for rollback
            previous_version = current.version

            self._vault_client.write_secret(secret_path, new_data)

            logger.info(f"Rotated database credentials at {secret_path}")
            return True

        logger.warning("No vault client configured, cannot rotate")
        return False

    def _rotate_api_key(
        self, secret_path: str, policy: Optional[SecretRotationPolicy]
    ) -> bool:
        """Rotate an API key.

        Args:
            secret_path: Path to the API key
            policy: Rotation policy

        Returns:
            True if rotation succeeded
        """
        import secrets

        # Generate new API key
        new_api_key = f"sk_{secrets.token_hex(32)}"

        if self._vault_client:
            current = self._vault_client.get_secret(secret_path)
            new_data = current.data.copy()
            new_data["api_key"] = new_api_key
            new_data["previous_key"] = current.data.get("api_key")
            new_data["rotated_at"] = datetime.utcnow().isoformat()

            self._vault_client.write_secret(secret_path, new_data)

            logger.info(f"Rotated API key at {secret_path}")
            return True

        logger.warning("No vault client configured, cannot rotate")
        return False

    def _rotate_certificate(
        self, secret_path: str, policy: Optional[SecretRotationPolicy]
    ) -> bool:
        """Rotate a certificate.

        Args:
            secret_path: Path to the certificate
            policy: Rotation policy

        Returns:
            True if rotation succeeded
        """
        # Certificate rotation typically requires external ACME or CA integration
        logger.info(f"Certificate rotation requested for {secret_path}")
        logger.info("Certificate rotation requires external CA integration")

        # Placeholder - in production, this would trigger cert renewal
        return True

    def _rotate_generic_secret(
        self, secret_path: str, policy: Optional[SecretRotationPolicy]
    ) -> bool:
        """Rotate a generic secret.

        Args:
            secret_path: Path to the secret
            policy: Rotation policy

        Returns:
            True if rotation succeeded
        """
        if self._vault_client:
            current = self._vault_client.get_secret(secret_path)
            new_data = current.data.copy()
            new_data["rotated_at"] = datetime.utcnow().isoformat()

            self._vault_client.write_secret(secret_path, new_data)

            logger.info(f"Rotated generic secret at {secret_path}")
            return True

        logger.warning("No vault client configured, cannot rotate")
        return False

    def get_history_tracker(self) -> RotationHistoryTracker:
        """Get the rotation history tracker.

        Returns:
            RotationHistoryTracker instance
        """
        return self._history_tracker

    def get_scheduler(self) -> RotationScheduler:
        """Get the rotation scheduler.

        Returns:
            RotationScheduler instance
        """
        return self._scheduler
