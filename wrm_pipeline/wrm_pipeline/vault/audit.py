"""Audit logging module for HashiCorp Vault.

This module provides functionality for parsing, filtering, analyzing, and
managing Vault audit logs for security monitoring and compliance.

Example:
    >>> from wrm_pipeline.vault.audit import AuditLogParser, AuditLogFilter
    >>> parser = AuditLogParser()
    >>> logs = parser.parse_from_file("/var/log/vault/audit.log")
    >>> filtered = logs.filter_by_operation(AuditOperation.READ)
    >>> print(f"Found {len(filtered)} read operations")
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional

if TYPE_CHECKING:
    from collections.abc import Iterable


logger = logging.getLogger(__name__)


class AuditOperation(str, Enum):
    """Types of audit operations with human-readable descriptions.

    Maps to Vault audit event types and capability operations.
    """

    READ = "read"
    WRITE = "write"
    UPDATE = "update"
    DELETE = "delete"
    LIST = "list"
    DENY = "deny"
    POLICY_READ = "policy_read"
    POLICY_WRITE = "policy_write"
    AUTH = "auth"
    TOKEN = "token"
    REVOKE = "revoke"
    RENEW = "renew"

    @property
    def description(self) -> str:
        """Get human-readable description of the operation."""
        descriptions = {
            "read": "Read secret value",
            "write": "Create or overwrite secret",
            "update": "Update existing secret",
            "delete": "Delete secret",
            "list": "List secrets or keys",
            "deny": "Access denied due to policy",
            "policy_read": "Read policy configuration",
            "policy_write": "Create or modify policy",
            "auth": "Authentication operation",
            "token": "Token operation",
            "revoke": "Revoke token or secret",
            "renew": "Renew token or secret",
        }
        return descriptions.get(self.value, "Unknown operation")


class AuditLog:
    """Represents a single audit log entry from Vault.

    Attributes:
        id: Unique identifier for the audit log entry
        timestamp: When the operation occurred
        operation: Type of operation performed
        path: Secret path that was accessed
        actor: Entity that performed the operation (token accessor or entity ID)
        success: Whether the operation succeeded
        error_message: Error message if the operation failed
        metadata: Additional context about the operation
        client_ip: IP address of the client
        request_id: Vault request ID for tracing
    """

    id: str
    timestamp: datetime
    operation: AuditOperation
    path: str
    actor: str
    success: bool
    error_message: Optional[str]
    metadata: dict[str, Any]
    client_ip: Optional[str]
    request_id: Optional[str]

    def __init__(
        self,
        id: str,
        timestamp: datetime,
        operation: AuditOperation,
        path: str,
        actor: str,
        success: bool,
        error_message: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        client_ip: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        self.id = id
        self.timestamp = timestamp
        self.operation = operation
        self.path = path
        self.actor = actor
        self.success = success
        self.error_message = error_message
        self.metadata = metadata or {}
        self.client_ip = client_ip
        self.request_id = request_id

    @classmethod
    def from_vault_log(cls, log_entry: dict[str, Any]) -> "AuditLog":
        """Create an AuditLog from a Vault audit log entry.

        Args:
            log_entry: Raw JSON log entry from Vault audit device

        Returns:
            Parsed AuditLog instance

        Example:
            >>> entry = json.loads(line)
            >>> audit_log = AuditLog.from_vault_log(entry)
        """
        # Extract timestamp
        time_str = log_entry.get("time", "")
        try:
            timestamp = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            timestamp = datetime.utcnow()

        # Extract request info
        request = log_entry.get("request", {})
        auth = request.get("auth", {})
        client_ip = request.get("remote_address")
        request_id = request.get("id")

        # Extract operation from request path and capability
        operation_str = cls._extract_operation(request)
        operation = cls._parse_operation(operation_str)

        # Extract path (handle different formats)
        path = request.get("path", "") or log_entry.get("path", "")

        # Extract actor (token accessor or entity ID)
        accessor = auth.get("accessor", "")
        entity_id = auth.get("entity_id", "")
        actor = accessor or entity_id or "unknown"

        # Extract success/failure
        response = log_entry.get("response", {})
        success = response.get("data", {}).get("success", True)
        if "error" in log_entry:
            success = False

        # Extract error message
        error_message = log_entry.get("error", None)
        if not error_message:
            error_response = response.get("errors", [])
            if error_response:
                error_message = error_response[0] if isinstance(error_response, list) else error_response

        # Generate unique ID
        log_id = f"{timestamp.strftime('%Y%m%d%H%M%S')}-{request_id or hash(path)}"

        # Extract metadata
        metadata = {
            "mount_point": request.get("mount_point", ""),
            "namespace": request.get("namespace", ""),
            "client_cert": auth.get("client_certificate", ""),
            "token_issue_time": auth.get("token_issue_time", ""),
            "token_policies": auth.get("policies", []),
        }

        return cls(
            id=log_id,
            timestamp=timestamp,
            operation=operation,
            path=path,
            actor=actor,
            success=success,
            error_message=error_message,
            metadata=metadata,
            client_ip=client_ip,
            request_id=request_id,
        )

    @staticmethod
    def _extract_operation(request: dict[str, Any]) -> str:
        """Extract operation type from Vault request."""
        operation = request.get("operation", "").lower()
        if operation:
            return operation

        # Fall back to capability-based detection
        capabilities = request.get("capabilities", [])
        if not isinstance(capabilities, list):
            capabilities = [capabilities]

        if "deny" in capabilities:
            return "deny"
        elif "sudo" in capabilities:
            return "write"
        elif "update" in capabilities:
            return "update"
        elif "create" in capabilities:
            return "write"
        elif "read" in capabilities:
            return "read"
        elif "delete" in capabilities:
            return "delete"
        elif "list" in capabilities:
            return "list"

        # Check path patterns
        path = request.get("path", "").lower()
        if "policy" in path:
            if "read" in capabilities or "list" in capabilities:
                return "policy_read"
            else:
                return "policy_write"
        elif "auth" in path:
            return "auth"
        elif "token" in path:
            return "token"

        return "read"  # Default

    @staticmethod
    def _parse_operation(operation_str: str) -> AuditOperation:
        """Parse operation string to AuditOperation enum."""
        mapping = {
            "read": AuditOperation.READ,
            "write": AuditOperation.WRITE,
            "update": AuditOperation.UPDATE,
            "delete": AuditOperation.DELETE,
            "list": AuditOperation.LIST,
            "deny": AuditOperation.DENY,
            "policy_read": AuditOperation.POLICY_READ,
            "policy_write": AuditOperation.POLICY_WRITE,
            "auth": AuditOperation.AUTH,
            "token": AuditOperation.TOKEN,
            "revoke": AuditOperation.REVOKE,
            "renew": AuditOperation.RENEW,
        }
        return mapping.get(operation_str.lower(), AuditOperation.READ)

    def to_dict(self) -> dict[str, Any]:
        """Convert audit log to dictionary for serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "operation": self.operation.value,
            "path": self.path,
            "actor": self.actor,
            "success": self.success,
            "error_message": self.error_message,
            "metadata": self.metadata,
            "client_ip": self.client_ip,
            "request_id": self.request_id,
        }

    def to_json(self) -> str:
        """Convert audit log to JSON string."""
        return json.dumps(self.to_dict())

    def __repr__(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        return (
            f"AuditLog(id={self.id}, {self.operation.value} {status} on {self.path} "
            f"by {self.actor} at {self.timestamp.isoformat()})"
        )


class AuditLogCollection:
    """A collection of audit logs with filtering and aggregation capabilities.

    Provides a fluent API for filtering and analyzing audit logs.
    """

    def __init__(self, logs: Optional[list[AuditLog]] = None):
        self._logs: list[AuditLog] = logs or []

    def __len__(self) -> int:
        return len(self._logs)

    def __iter__(self) -> Iterator[AuditLog]:
        return iter(self._logs)

    def __getitem__(self, index: int) -> AuditLog:
        return self._logs[index]

    def filter_by_time(
        self, start: Optional[datetime] = None, end: Optional[datetime] = None
    ) -> "AuditLogCollection":
        """Filter logs by time range.

        Args:
            start: Start of time range (inclusive)
            end: End of time range (inclusive)

        Returns:
            Filtered collection
        """
        filtered = self._logs
        if start:
            filtered = [log for log in filtered if log.timestamp >= start]
        if end:
            filtered = [log for log in filtered if log.timestamp <= end]
        return AuditLogCollection(filtered)

    def filter_by_operation(
        self, *operations: AuditOperation | str
    ) -> "AuditLogCollection":
        """Filter logs by operation type.

        Args:
            operations: Operations to include

        Returns:
            Filtered collection
        """
        op_values = set()
        for op in operations:
            if isinstance(op, str):
                try:
                    op_values.add(AuditOperation(op))
                except ValueError:
                    op_values.add(op)
            else:
                op_values.add(op)

        filtered = [log for log in self._logs if log.operation in op_values]
        return AuditLogCollection(filtered)

    def filter_by_path(self, pattern: str | re.Pattern) -> "AuditLogCollection":
        """Filter logs by secret path pattern.

        Args:
            pattern: Regex pattern or literal path prefix

        Returns:
            Filtered collection
        """
        if isinstance(pattern, str):
            pattern = re.compile(f"^{re.escape(pattern)}.*$")

        filtered = [log for log in self._logs if pattern.search(log.path)]
        return AuditLogCollection(filtered)

    def filter_by_actor(self, actor: str | list[str]) -> "AuditLogCollection":
        """Filter logs by actor (token accessor or entity ID).

        Args:
            actor: Actor ID or list of actor IDs

        Returns:
            Filtered collection
        """
        if isinstance(actor, str):
            actors = {actor}
        else:
            actors = set(actor)

        filtered = [log for log in self._logs if log.actor in actors]
        return AuditLogCollection(filtered)

    def filter_by_success(self, success: bool) -> "AuditLogCollection":
        """Filter logs by success/failure status.

        Args:
            success: True for successful operations, False for failures

        Returns:
            Filtered collection
        """
        filtered = [log for log in self._logs if log.success == success]
        return AuditLogCollection(filtered)

    def filter_by_client_ip(self, ip: str | list[str]) -> "AuditLogCollection":
        """Filter logs by client IP address.

        Args:
            ip: IP address or list of addresses

        Returns:
            Filtered collection
        """
        if isinstance(ip, str):
            ips = {ip}
        else:
            ips = set(ip)

        filtered = [log for log in self._logs if log.client_ip in ips]
        return AuditLogCollection(filtered)

    def search_logs(self, query: str | re.Pattern) -> "AuditLogCollection":
        """Search logs by text content.

        Args:
            query: Text search string or regex pattern

        Returns:
            Filtered collection
        """
        if isinstance(query, str):
            query = re.compile(re.escape(query), re.IGNORECASE)

        def matches(log: AuditLog) -> bool:
            searchable = " ".join(
                [
                    log.path,
                    log.actor,
                    log.error_message or "",
                    str(log.metadata),
                ]
            )
            return bool(query.search(searchable))

        filtered = [log for log in self._logs if matches(log)]
        return AuditLogCollection(filtered)

    def aggregate_by_operation(self) -> dict[AuditOperation, list[AuditLog]]:
        """Group logs by operation type.

        Returns:
            Dictionary mapping operation to list of logs
        """
        result: dict[AuditOperation, list[AuditLog]] = {}
        for log in self._logs:
            if log.operation not in result:
                result[log.operation] = []
            result[log.operation].append(log)
        return result

    def aggregate_by_time(self, interval: str = "hour") -> dict[str, list[AuditLog]]:
        """Group logs by time window.

        Args:
            interval: Time interval - 'hour', 'day', 'week', 'month'

        Returns:
            Dictionary mapping time window to list of logs
        """
        result: dict[str, list[AuditLog]] = {}

        if interval == "hour":
            key_func = lambda dt: dt.strftime("%Y-%m-%d %H:00")
        elif interval == "day":
            key_func = lambda dt: dt.strftime("%Y-%m-%d")
        elif interval == "week":
            key_func = lambda dt: dt.strftime("%Y-W%U")
        elif interval == "month":
            key_func = lambda dt: dt.strftime("%Y-%m")
        else:
            key_func = lambda dt: dt.strftime("%Y-%m-%d %H:00")

        for log in self._logs:
            key = key_func(log.timestamp)
            if key not in result:
                result[key] = []
            result[key].append(log)

        return result

    def get_access_patterns(self) -> dict[str, Any]:
        """Analyze access patterns from the logs.

        Returns:
            Dictionary with access pattern statistics
        """
        patterns: dict[str, Any] = {
            "total_operations": len(self._logs),
            "by_operation": {},
            "by_path": {},
            "by_actor": {},
            "success_rate": 0.0,
            "top_paths": [],
            "top_actors": [],
            "time_distribution": {},
        }

        success_count = 0

        for log in self._logs:
            # Count by operation
            op = log.operation.value
            patterns["by_operation"][op] = patterns["by_operation"].get(op, 0) + 1

            # Count by path
            patterns["by_path"][log.path] = patterns["by_path"].get(log.path, 0) + 1

            # Count by actor
            patterns["by_actor"][log.actor] = patterns["by_actor"].get(log.actor, 0) + 1

            # Success count
            if log.success:
                success_count += 1

        # Calculate success rate
        if len(self._logs) > 0:
            patterns["success_rate"] = success_count / len(self._logs)

        # Top paths
        sorted_paths = sorted(
            patterns["by_path"].items(), key=lambda x: x[1], reverse=True
        )
        patterns["top_paths"] = sorted_paths[:10]

        # Top actors
        sorted_actors = sorted(
            patterns["by_actor"].items(), key=lambda x: x[1], reverse=True
        )
        patterns["top_actors"] = sorted_actors[:10]

        # Time distribution
        patterns["time_distribution"] = self.aggregate_by_time("hour")

        return patterns

    def detect_anomalies(
        self,
        threshold: int = 100,
        time_window_minutes: int = 60,
    ) -> list[dict[str, Any]]:
        """Detect unusual activity patterns.

        Args:
            threshold: Number of operations to consider anomalous
            time_window_minutes: Time window to analyze

        Returns:
            List of anomaly reports
        """
        anomalies: list[dict[str, Any]] = []

        if not self._logs:
            return anomalies

        # Get time range
        timestamps = [log.timestamp for log in self._logs]
        window_end = max(timestamps)
        window_start = window_end - timedelta(minutes=time_window_minutes)

        # Check for excessive operations by single actor
        actor_counts: dict[str, list[AuditLog]] = {}
        for log in self._logs:
            if log.timestamp >= window_start:
                if log.actor not in actor_counts:
                    actor_counts[log.actor] = []
                actor_counts[log.actor].append(log)

        for actor, logs in actor_counts.items():
            if len(logs) > threshold:
                anomalies.append(
                    {
                        "type": "excessive_operations",
                        "actor": actor,
                        "count": len(logs),
                        "threshold": threshold,
                        "time_window_minutes": time_window_minutes,
                        "operations": [log.operation.value for log in logs],
                        "severity": "high" if len(logs) > threshold * 2 else "medium",
                    }
                )

        # Check for excessive failures
        failed_logs = self.filter_by_success(False).filter_by_time(
            window_start, window_end
        )
        if len(failed_logs) > threshold * 0.5:  # 50% of threshold for failures
            actors_with_failures: dict[str, int] = {}
            for log in failed_logs:
                actors_with_failures[log.actor] = actors_with_failures.get(log.actor, 0) + 1

            for actor, count in actors_with_failures.items():
                if count > threshold * 0.2:  # 20% of threshold
                    anomalies.append(
                        {
                            "type": "excessive_failures",
                            "actor": actor,
                            "count": count,
                            "threshold": threshold * 0.2,
                            "severity": "high" if count > threshold * 0.5 else "medium",
                        }
                    )

        # Check for access to sensitive paths
        sensitive_patterns = [
            r"^secret/data/.*/production.*",
            r"^secret/data/.*/admin.*",
            r"^sys/.*",
        ]
        sensitive_logs = self
        for pattern in sensitive_patterns:
            sensitive_logs = sensitive_logs.filter_by_path(pattern)

        for log in sensitive_logs._logs:
            if log.timestamp >= window_start:
                anomalies.append(
                    {
                        "type": "sensitive_path_access",
                        "path": log.path,
                        "actor": log.actor,
                        "operation": log.operation.value,
                        "timestamp": log.timestamp.isoformat(),
                        "severity": "high",
                    }
                )

        return anomalies

    def sort_by_timestamp(self, ascending: bool = True) -> "AuditLogCollection":
        """Sort logs by timestamp.

        Args:
            ascending: Sort order (True for oldest first)

        Returns:
            Sorted collection
        """
        sorted_logs = sorted(
            self._logs, key=lambda log: log.timestamp, reverse=not ascending
        )
        return AuditLogCollection(sorted_logs)

    def to_list(self) -> list[AuditLog]:
        """Get logs as a list."""
        return self._logs

    def to_json(self) -> str:
        """Export logs as JSON."""
        return json.dumps([log.to_dict() for log in self._logs], indent=2)

    def statistics(self) -> dict[str, Any]:
        """Get summary statistics for the log collection."""
        stats: dict[str, Any] = {
            "total_logs": len(self._logs),
            "unique_actors": len(set(log.actor for log in self._logs)),
            "unique_paths": len(set(log.path for log in self._logs)),
            "operations": self.aggregate_by_operation(),
            "success_count": sum(1 for log in self._logs if log.success),
            "failure_count": sum(1 for log in self._logs if not log.success),
            "time_range": {
                "earliest": None,
                "latest": None,
            },
        }

        if self._logs:
            timestamps = [log.timestamp for log in self._logs]
            stats["time_range"]["earliest"] = min(timestamps).isoformat()
            stats["time_range"]["latest"] = max(timestamps).isoformat()

        return stats


class AuditLogParser:
    """Parser for Vault audit log files.

    Supports reading from plain text, gzip compressed files,
    and parsing Vault's JSON audit log format.
    """

    def __init__(self):
        self._logs: list[AuditLog] = []

    def parse_vault_logs(self, log_data: str | list[dict[str, Any]]) -> AuditLogCollection:
        """Parse Vault audit log entries.

        Args:
            log_data: JSON string or list of log entries

        Returns:
            Collection of parsed audit logs
        """
        if isinstance(log_data, str):
            lines = log_data.strip().split("\n")
            entries = []
            for line in lines:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse log line: {line[:100]}...")
            return AuditLogCollection(
                [AuditLog.from_vault_log(entry) for entry in entries]
            )
        else:
            return AuditLogCollection(
                [AuditLog.from_vault_log(entry) for entry in log_data]
            )

    def parse_from_file(
        self, file_path: str, compressed: bool = False
    ) -> AuditLogCollection:
        """Read and parse audit logs from a file.

        Args:
            file_path: Path to the audit log file
            compressed: Whether the file is gzip compressed

        Returns:
            Collection of parsed audit logs
        """
        path = Path(file_path)

        if not path.exists():
            logger.error(f"Audit log file not found: {file_path}")
            return AuditLogCollection([])

        try:
            if compressed or file_path.endswith(".gz"):
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    content = f.read()
            else:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()

            return self.parse_vault_logs(content)

        except Exception as e:
            logger.error(f"Error reading audit log file: {e}")
            return AuditLogCollection([])

    def parse_from_directory(
        self, directory: str, pattern: str = "*.log*"
    ) -> AuditLogCollection:
        """Parse audit logs from all files in a directory.

        Args:
            directory: Path to directory containing log files
            pattern: Glob pattern for file matching

        Returns:
            Collection of all parsed audit logs
        """
        dir_path = Path(directory)
        all_logs: list[AuditLog] = []

        if not dir_path.exists():
            logger.error(f"Directory not found: {directory}")
            return AuditLogCollection([])

        for file_path in dir_path.glob(pattern):
            collection = self.parse_from_file(str(file_path))
            all_logs.extend(collection.to_list())

        return AuditLogCollection(all_logs)


class AuditLogRetention:
    """Manages audit log retention and cleanup policies.

    Supports compliance requirements for log retention periods.
    """

    def __init__(self, retention_days: int = 90):
        """Initialize retention manager.

        Args:
            retention_days: Number of days to retain logs
        """
        self.retention_days = retention_days
        self.archive_directory: Optional[str] = None
        self._retention_configured = False

    def configure_retention(
        self,
        retention_days: int = 90,
        archive_directory: Optional[str] = None,
        archive_before_delete: bool = True,
    ) -> None:
        """Configure log retention policy.

        Args:
            retention_days: Days to retain logs
            archive_directory: Directory for archived logs
            archive_before_delete: Whether to archive logs before deletion
        """
        self.retention_days = retention_days
        self.archive_directory = archive_directory
        self.archive_before_delete = archive_before_delete
        self._retention_configured = True

        logger.info(
            f"Audit log retention configured: {retention_days} days, "
            f"archive: {archive_directory}"
        )

    def cleanup_old_logs(
        self,
        log_directory: str,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Clean up logs older than retention period.

        Args:
            log_directory: Directory containing log files
            dry_run: If True, only report what would be deleted

        Returns:
            Summary of cleanup operation
        """
        result: dict[str, Any] = {
            "retention_days": self.retention_days,
            "dry_run": dry_run,
            "deleted_files": [],
            "archived_files": [],
            "freed_bytes": 0,
        }

        if not self._retention_configured:
            logger.warning("Retention policy not configured. Using default 90 days.")
            self.retention_days = 90

        cutoff_date = datetime.utcnow() - timedelta(days=self.retention_days)
        log_dir = Path(log_directory)

        if not log_dir.exists():
            logger.error(f"Log directory not found: {log_directory}")
            return result

        for file_path in log_dir.glob("*audit*.log*"):
            try:
                # Get file modification time
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

                if mtime < cutoff_date:
                    file_size = file_path.stat().st_size

                    if dry_run:
                        result["deleted_files"].append(
                            {
                                "path": str(file_path),
                                "mtime": mtime.isoformat(),
                                "size_bytes": file_size,
                            }
                        )
                    else:
                        # Archive if configured
                        if self.archive_before_delete and self.archive_directory:
                            archive_path = Path(self.archive_directory) / file_path.name
                            archive_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(file_path), str(archive_path))
                            result["archived_files"].append(str(archive_path))
                        else:
                            file_path.unlink()

                        result["deleted_files"].append(str(file_path))
                        result["freed_bytes"] += file_size

            except Exception as e:
                logger.error(f"Error processing {file_path}: {e}")

        return result

    def get_log_stats(self, log_directory: str) -> dict[str, Any]:
        """Get statistics about audit logs in a directory.

        Args:
            log_directory: Directory containing log files

        Returns:
            Statistics about audit logs
        """
        stats: dict[str, Any] = {
            "total_files": 0,
            "total_size_bytes": 0,
            "oldest_log": None,
            "newest_log": None,
            "by_day": {},
        }

        log_dir = Path(log_directory)
        if not log_dir.exists():
            return stats

        for file_path in log_dir.glob("*audit*.log*"):
            if file_path.is_file():
                stats["total_files"] += 1
                file_size = file_path.stat().st_size
                stats["total_size_bytes"] += file_size

                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                day_key = mtime.strftime("%Y-%m-%d")

                if day_key not in stats["by_day"]:
                    stats["by_day"][day_key] = {
                        "count": 0,
                        "size_bytes": 0,
                    }
                stats["by_day"][day_key]["count"] += 1
                stats["by_day"][day_key]["size_bytes"] += file_size

                if stats["oldest_log"] is None or mtime < stats["oldest_log"]:
                    stats["oldest_log"] = mtime.isoformat()
                if stats["newest_log"] is None or mtime > stats["newest_log"]:
                    stats["newest_log"] = mtime.isoformat()

        return stats

    def generate_audit_report(
        self,
        log_directory: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Generate a periodic audit report.

        Args:
            log_directory: Directory containing log files
            start_date: Start of report period
            end_date: End of report period

        Returns:
            Audit report dictionary
        """
        parser = AuditLogParser()
        collection = parser.parse_from_directory(log_directory)

        # Filter by date range if specified
        if start_date or end_date:
            collection = collection.filter_by_time(start_date, end_date)

        patterns = collection.get_access_patterns()
        anomalies = collection.detect_anomalies()

        report: dict[str, Any] = {
            "report_period": {
                "start": start_date.isoformat() if start_date else "all",
                "end": end_date.isoformat() if end_date else "all",
            },
            "summary": {
                "total_operations": patterns["total_operations"],
                "unique_actors": len(patterns["by_actor"]),
                "unique_paths": len(patterns["by_path"]),
                "success_rate": patterns["success_rate"],
            },
            "operations_by_type": patterns["by_operation"],
            "top_accessed_paths": patterns["top_paths"],
            "top_actors": patterns["top_actors"],
            "anomalies_detected": len(anomalies),
            "anomaly_details": anomalies,
            "time_distribution": patterns["time_distribution"],
            "generated_at": datetime.utcnow().isoformat(),
        }

        return report


def view_audit_logs(
    log_path: str,
    filters: Optional[dict[str, Any]] = None,
    format_output: str = "text",
) -> str:
    """View and filter audit logs.

    Args:
        log_path: Path to audit log file or directory
        filters: Dictionary of filter criteria
        format_output: Output format - 'text', 'json', or 'table'

    Returns:
        Formatted log output
    """
    parser = AuditLogParser()

    if Path(log_path).is_dir():
        collection = parser.parse_from_directory(log_path)
    else:
        collection = parser.parse_from_file(log_path)

    # Apply filters
    if filters:
        if "start_time" in filters:
            collection = collection.filter_by_time(start=filters["start_time"])
        if "end_time" in filters:
            collection = collection.filter_by_time(end=filters["end_time"])
        if "operation" in filters:
            ops = filters["operation"]
            if isinstance(ops, str):
                ops = [ops]
            collection = collection.filter_by_operation(*ops)
        if "path_pattern" in filters:
            collection = collection.filter_by_path(filters["path_pattern"])
        if "actor" in filters:
            collection = collection.filter_by_actor(filters["actor"])
        if "success" in filters:
            collection = collection.filter_by_success(filters["success"])
        if "search" in filters:
            collection = collection.search_logs(filters["search"])

    # Sort by timestamp
    collection = collection.sort_by_timestamp(ascending=False)

    if format_output == "json":
        return collection.to_json()

    # Text or table format
    lines = []
    for log in collection.to_list():
        status = "✓" if log.success else "✗"
        lines.append(
            f"{log.timestamp.isoformat()} | {status:1} | "
            f"{log.operation.value:12} | {log.actor[:16]:16} | {log.path}"
        )

    if format_output == "table":
        header = "Timestamp                    | S | Operation    | Actor             | Path"
        separator = "-" * 80
        return "\n".join([header, separator] + lines)

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="View Vault audit logs")
    parser.add_argument("path", help="Path to audit log file or directory")
    parser.add_argument("--operation", "-o", help="Filter by operation type")
    parser.add_argument("--path-pattern", "-p", help="Filter by path pattern")
    parser.add_argument("--actor", "-a", help="Filter by actor")
    parser.add_argument("--success", "-s", choices=["true", "false"], help="Filter by success")
    parser.add_argument("--format", "-f", choices=["text", "json", "table"], default="text")
    parser.add_argument("--limit", "-l", type=int, default=100, help="Limit output lines")

    args = parser.parse_args()

    filters = {}
    if args.operation:
        filters["operation"] = args.operation
    if args.path_pattern:
        filters["path_pattern"] = args.path_pattern
    if args.actor:
        filters["actor"] = args.actor
    if args.success:
        filters["success"] = args.success == "true"

    output = view_audit_logs(args.path, filters, args.format)
    lines = output.split("\n")[:args.limit]
    print("\n".join(lines))
