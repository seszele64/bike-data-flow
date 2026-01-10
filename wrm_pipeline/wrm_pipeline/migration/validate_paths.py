"""Path structure validator for Vault secret paths.

This module provides functionality to validate Vault path structure,
ensure consistent naming conventions, check for duplicates, and verify
write permissions.

Usage:
    python -m wrm_pipeline.wrm_pipeline.migration.validate_paths --paths paths.json

Example:
    python -m wrm_pipeline.wrm_pipeline.migration.validate_paths \\
        --paths paths.json \\
        --vault-addr https://vault.example.com:8200
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from wrm_pipeline.wrm_pipeline.vault.client import VaultClient
from wrm_pipeline.wrm_pipeline.vault.exceptions import (
    VaultAuthenticationError,
    VaultConnectionError,
)
from wrm_pipeline.wrm_pipeline.vault.models import VaultConnectionConfig

logger = logging.getLogger(__name__)


class PathValidationStatus(Enum):
    """Validation status for paths."""
    VALID = "valid"
    INVALID = "invalid"
    WARNING = "warning"
    DUPLICATE = "duplicate"
    EXISTS = "exists"


@dataclass
class PathValidationResult:
    """Result of validating a path."""
    path: str
    status: PathValidationStatus
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class ValidationReport:
    """Overall validation report."""
    results: list[PathValidationResult] = field(default_factory=list)
    valid_count: int = 0
    invalid_count: int = 0
    warning_count: int = 0
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "summary": {
                "total": len(self.results),
                "valid": self.valid_count,
                "invalid": self.invalid_count,
                "warnings": self.warning_count,
            },
            "results": [
                {
                    "path": r.path,
                    "status": r.status.value,
                    "message": r.message,
                    "details": r.details,
                }
                for r in self.results
            ]
        }


class PathValidator:
    """Validator for Vault secret path structure."""
    
    # Naming conventions
    VALID_NAME_PATTERN = re.compile(r'^[a-z0-9][a-z0-9_-]*$')
    RESERVED_PATHS = {"secret", "sys", "cubbyhole"}
    MAX_PATH_LENGTH = 512
    MAX_SEGMENTS = 64
    
    # Recommended path structure
    RECOMMENDED_STRUCTURE = "application/environment/category/secret-name"
    # Example: bike-data-flow/production/database/postgres-password
    
    def __init__(
        self,
        vault_addr: Optional[str] = None,
        auth_method: str = "approle",
        role_id: Optional[str] = None,
        secret_id: Optional[str] = None,
        token: Optional[str] = None,
        namespace: Optional[str] = None,
        verify: bool = True,
        path_prefix: str = "bike-data-flow",
    ):
        """Initialize the path validator.
        
        Args:
            vault_addr: Vault server address.
            auth_method: Authentication method.
            role_id: AppRole role ID.
            secret_id: AppRole secret ID.
            token: Vault token.
            namespace: Enterprise namespace.
            verify: Whether to verify TLS.
            path_prefix: Default path prefix.
        """
        self.path_prefix = path_prefix
        self.vault_client: Optional[VaultClient] = None
        
        if vault_addr:
            config = VaultConnectionConfig(
                vault_addr=vault_addr,
                auth_method=auth_method,
                role_id=role_id,
                secret_id=secret_id,
                token=token,
                namespace=namespace,
                verify=verify,
            )
            self.vault_client = VaultClient(config)
    
    def _normalize_path(self, path: str, add_prefix: bool = True) -> str:
        """Normalize a secret path.
        
        Args:
            path: Path to normalize.
            add_prefix: Whether to add path prefix.
            
        Returns:
            Normalized path.
        """
        path = path.strip("/")
        
        # Remove existing prefixes
        for prefix in ["secret/data/", "secret/", f"{self.path_prefix}/"]:
            if path.startswith(prefix):
                path = path[len(prefix):]
        
        # Add prefix if requested
        if add_prefix:
            path = f"{self.path_prefix}/{path}"
        
        return path
    
    def _validate_segment(self, segment: str) -> tuple[bool, str]:
        """Validate a single path segment.
        
        Args:
            segment: Path segment to validate.
            
        Returns:
            Tuple of (is_valid, error_message).
        """
        if not segment:
            return False, "Empty path segment"
        
        if len(segment) > 256:
            return False, f"Segment '{segment}' exceeds maximum length of 256 characters"
        
        if not self.VALID_NAME_PATTERN.match(segment):
            return False, f"Segment '{segment}' contains invalid characters. Use only lowercase letters, numbers, underscores, and hyphens"
        
        if segment.startswith("-") or segment.startswith("_"):
            return False, f"Segment '{segment}' cannot start with underscore or hyphen"
        
        return True, ""
    
    def validate_path_format(self, path: str) -> PathValidationResult:
        """Validate the format of a secret path.
        
        Args:
            path: Secret path to validate.
            
        Returns:
            PathValidationResult.
        """
        normalized_path = self._normalize_path(path)
        
        # Check for reserved paths
        first_segment = normalized_path.split("/")[0] if "/" in normalized_path else normalized_path
        if first_segment in self.RESERVED_PATHS:
            return PathValidationResult(
                path=normalized_path,
                status=PathValidationStatus.INVALID,
                message=f"Path uses reserved prefix '{first_segment}'",
                details={"reserved_path": first_segment}
            )
        
        # Check path length
        if len(normalized_path) > self.MAX_PATH_LENGTH:
            return PathValidationResult(
                path=normalized_path,
                status=PathValidationStatus.INVALID,
                message=f"Path exceeds maximum length of {self.MAX_PATH_LENGTH} characters",
                details={"path_length": len(normalized_path)}
            )
        
        # Check segment count
        segments = normalized_path.split("/")
        if len(segments) > self.MAX_SEGMENTS:
            return PathValidationResult(
                path=normalized_path,
                status=PathValidationStatus.INVALID,
                message=f"Path has too many segments (max {self.MAX_SEGMENTS})",
                details={"segment_count": len(segments)}
            )
        
        # Validate each segment
        for segment in segments:
            valid, error = self._validate_segment(segment)
            if not valid:
                return PathValidationResult(
                    path=normalized_path,
                    status=PathValidationStatus.INVALID,
                    message=error,
                    details={"invalid_segment": segment}
                )
        
        # Check for consistent naming
        if any(seg.isupper() for seg in segments):
            return PathValidationResult(
                path=normalized_path,
                status=PathValidationStatus.WARNING,
                message="Path contains uppercase letters - consider using lowercase for consistency",
                details={"warning": "uppercase_letters"}
            )
        
        return PathValidationResult(
            path=normalized_path,
            status=PathValidationStatus.VALID,
            message="Path format is valid",
            details={"normalized_path": normalized_path}
        )
    
    def validate_path_structure(
        self,
        path: str,
        expected_prefix: Optional[str] = None,
        expected_category: Optional[str] = None,
    ) -> PathValidationResult:
        """Validate path follows recommended structure.
        
        Args:
            path: Secret path to validate.
            expected_prefix: Expected path prefix.
            expected_category: Expected category segment.
            
        Returns:
            PathValidationResult.
        """
        result = self.validate_path_format(path)
        
        if result.status != PathValidationStatus.VALID:
            return result
        
        normalized_path = result.details.get("normalized_path", path)
        segments = normalized_path.split("/")
        
        # Check prefix
        if expected_prefix and segments[0] != expected_prefix:
            return PathValidationResult(
                path=normalized_path,
                status=PathValidationStatus.WARNING,
                message=f"Path prefix '{segments[0]}' does not match expected '{expected_prefix}'",
                details={"actual_prefix": segments[0], "expected_prefix": expected_prefix}
            )
        
        # Check for minimum structure
        if len(segments) < 3:
            return PathValidationResult(
                path=normalized_path,
                status=PathValidationStatus.WARNING,
                message="Path is too short. Recommended structure: prefix/category/secret-name",
                details={"segment_count": len(segments)}
            )
        
        # Validate category if specified
        if expected_category and len(segments) > 1:
            if segments[1] != expected_category:
                return PathValidationResult(
                    path=normalized_path,
                    status=PathValidationStatus.WARNING,
                    message=f"Category '{segments[1]}' does not match expected '{expected_category}'",
                    details={"actual_category": segments[1], "expected_category": expected_category}
                )
        
        return result
    
    def check_duplicate_paths(self, paths: list[str]) -> ValidationReport:
        """Check for duplicate paths.
        
        Args:
            paths: List of paths to check.
            
        Returns:
            ValidationReport with all results.
        """
        report = ValidationReport()
        normalized_paths: dict[str, list[str]] = {}
        
        for path in paths:
            normalized = self._normalize_path(path)
            normalized_paths.setdefault(normalized, []).append(path)
        
        for normalized, originals in normalized_paths.items():
            if len(originals) > 1:
                result = PathValidationResult(
                    path=normalized,
                    status=PathValidationStatus.DUPLICATE,
                    message=f"Found {len(originals)} variations of this path",
                    details={"variations": originals, "count": len(originals)}
                )
            else:
                result = self.validate_path_format(originals[0])
            
            report.results.append(result)
        
        report.valid_count = sum(1 for r in report.results if r.status == PathValidationStatus.VALID)
        report.invalid_count = sum(1 for r in report.results if r.status == PathValidationStatus.INVALID)
        report.warning_count = sum(1 for r in report.results if r.status in [
            PathValidationStatus.WARNING, PathValidationStatus.DUPLICATE
        ])
        
        return report
    
    def check_existing_secrets(self, paths: list[str]) -> ValidationReport:
        """Check if paths already exist in Vault.
        
        Args:
            paths: List of paths to check.
            
        Returns:
            ValidationReport.
        """
        report = ValidationReport()
        
        if not self.vault_client:
            for path in paths:
                result = PathValidationResult(
                    path=path,
                    status=PathValidationStatus.WARNING,
                    message="Cannot check existence - no Vault connection",
                    details={"connected": False}
                )
                report.results.append(result)
            return report
        
        try:
            self.vault_client._ensure_authenticated()
        except VaultAuthenticationError:
            for path in paths:
                result = PathValidationResult(
                    path=path,
                    status=PathValidationStatus.INVALID,
                    message="Cannot authenticate to Vault",
                    details={"error": "authentication_failed"}
                )
                report.results.append(result)
            return report
        
        for path in paths:
            normalized = self._normalize_path(path)
            
            try:
                self.vault_client.get_secret(normalized)
                result = PathValidationResult(
                    path=normalized,
                    status=PathValidationStatus.EXISTS,
                    message="Secret already exists at this path",
                    details={"exists": True}
                )
            except Exception:
                result = PathValidationResult(
                    path=normalized,
                    status=PathValidationStatus.VALID,
                    message="Path is available (secret does not exist)",
                    details={"exists": False}
                )
            
            report.results.append(result)
        
        report.valid_count = len(paths)
        return report
    
    def check_write_permissions(self, paths: list[str]) -> ValidationReport:
        """Check write permissions for paths.
        
        Args:
            paths: List of paths to check.
            
        Returns:
            ValidationReport.
        """
        report = ValidationReport()
        
        if not self.vault_client:
            for path in paths:
                result = PathValidationResult(
                    path=path,
                    status=PathValidationStatus.WARNING,
                    message="Cannot check permissions - no Vault connection",
                    details={"connected": False}
                )
                report.results.append(result)
            return report
        
        try:
            self.vault_client._ensure_authenticated()
        except VaultAuthenticationError:
            for path in paths:
                result = PathValidationResult(
                    path=path,
                    status=PathValidationStatus.INVALID,
                    message="Cannot authenticate to Vault",
                    details={"error": "authentication_failed"}
                )
                report.results.append(result)
            return report
        
        for path in paths:
            normalized = self._normalize_path(path)
            
            # Try to write a test secret
            test_data = {"_test_write": True}
            try:
                self.vault_client.write_secret(normalized, test_data)
                # Clean up test write
                self.vault_client.delete_secret(normalized)
                
                result = PathValidationResult(
                    path=normalized,
                    status=PathValidationStatus.VALID,
                    message="Write permission confirmed",
                    details={"can_write": True}
                )
            except Exception as e:
                result = PathValidationResult(
                    path=normalized,
                    status=PathValidationStatus.INVALID,
                    message=f"Write permission denied: {e}",
                    details={"can_write": False, "error": str(e)}
                )
            
            report.results.append(result)
        
        report.valid_count = sum(1 for r in report.results if r.status == PathValidationStatus.VALID)
        report.invalid_count = sum(1 for r in report.results if r.status == PathValidationStatus.INVALID)
        
        return report
    
    def validate_all(
        self,
        paths: list[str],
        check_duplicates: bool = True,
        check_existence: bool = True,
        check_permissions: bool = True,
    ) -> ValidationReport:
        """Run all validations on paths.
        
        Args:
            paths: List of paths to validate.
            check_duplicates: Check for duplicates.
            check_existence: Check if paths exist.
            check_permissions: Check write permissions.
            
        Returns:
            Combined ValidationReport.
        """
        # First, validate format and check duplicates
        report = self.check_duplicate_paths(paths)
        
        # Get valid paths for further checks
        valid_paths = [
            r.path for r in report.results
            if r.status in [PathValidationStatus.VALID, PathValidationStatus.EXISTS]
        ]
        
        if check_existence and self.vault_client:
            existence_report = self.check_existing_secrets(valid_paths)
            # Merge results
            path_to_result = {r.path: r for r in existence_report.results}
            for result in report.results:
                if result.path in path_to_result:
                    result.details.update(path_to_result[result.path].details)
        
        if check_permissions and self.vault_client:
            permission_report = self.check_write_permissions(valid_paths)
            # Merge results
            path_to_result = {r.path: r for r in permission_report.results}
            for result in report.results:
                if result.path in path_to_result:
                    result.details.update(path_to_result[result.path].details)
        
        return report
    
    def generate_path_template(self, application: str, environment: str) -> dict:
        """Generate a standard path template for an application.
        
        Args:
            application: Application name.
            environment: Environment (production, staging, development).
            
        Returns:
            Dictionary with path templates.
        """
        prefix = f"{self.path_prefix}/{application}/{environment}"
        
        return {
            "database": {
                "description": "Database credentials",
                "paths": {
                    "postgres": f"{prefix}/database/postgres",
                    "mysql": f"{prefix}/database/mysql",
                }
            },
            "api": {
                "description": "API keys and tokens",
                "paths": {
                    "external": f"{prefix}/api/external",
                    "internal": f"{prefix}/api/internal",
                }
            },
            "storage": {
                "description": "Storage credentials",
                "paths": {
                    "s3": f"{prefix}/storage/s3",
                    "minio": f"{prefix}/storage/minio",
                }
            },
            "app": {
                "description": "Application secrets",
                "paths": {
                    "secrets": f"{prefix}/app/secrets",
                    "config": f"{prefix}/app/config",
                }
            },
        }


def main():
    """Main entry point for the path validator."""
    parser = argparse.ArgumentParser(
        description="Validate Vault secret path structure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Validate paths from command line
    python -m wrm_pipeline.wrm_pipeline.migration.validate_paths \\
        --paths bike-data-flow/production/database/password \\
        --paths bike-data-flow/staging/api/key
    
    # Validate paths from JSON file
    python -m wrm_pipeline.wrm_pipeline.migration.validate_paths --input paths.json
    
    # Full validation with Vault connection
    python -m wrm_pipeline.wrm_pipeline.migration.validate_paths \\
        --input paths.json \\
        --vault-addr https://vault.example.com:8200 \\
        --auth-method approle \\
        --role-id my-role \\
        --secret-id my-secret \\
        --check-permissions
        """
    )
    
    parser.add_argument(
        "--paths",
        action="append",
        default=[],
        help="Secret paths to validate (can be specified multiple times)"
    )
    parser.add_argument(
        "--input", "-i",
        default=None,
        help="JSON file containing paths to validate"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file for JSON report"
    )
    parser.add_argument(
        "--vault-addr",
        default=None,
        help="Vault server address"
    )
    parser.add_argument(
        "--auth-method",
        default="approle",
        choices=["approle", "token", "kubernetes"],
        help="Authentication method"
    )
    parser.add_argument(
        "--role-id",
        default=None,
        help="AppRole role ID"
    )
    parser.add_argument(
        "--secret-id",
        default=None,
        help="AppRole secret ID"
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Vault token"
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="Enterprise namespace"
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable TLS verification"
    )
    parser.add_argument(
        "--prefix",
        default="bike-data-flow",
        help="Default path prefix"
    )
    parser.add_argument(
        "--check-duplicates",
        action="store_true",
        help="Check for duplicate paths"
    )
    parser.add_argument(
        "--check-existence",
        action="store_true",
        help="Check if paths exist in Vault"
    )
    parser.add_argument(
        "--check-permissions",
        action="store_true",
        help="Check write permissions"
    )
    parser.add_argument(
        "--template",
        action="store_true",
        help="Generate path template and exit"
    )
    parser.add_argument(
        "--application",
        default="bike-data-flow",
        help="Application name for template generation"
    )
    parser.add_argument(
        "--environment",
        default="production",
        help="Environment for template generation"
    )
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    # Load paths from input file
    paths = list(args.paths)
    if args.input:
        with open(args.input, 'r') as f:
            data = json.load(f)
            if isinstance(data, list):
                paths.extend(data)
            elif isinstance(data, dict) and "paths" in data:
                paths.extend(data["paths"])
    
    if not paths:
        if args.template:
            # Generate template
            validator = PathValidator(path_prefix=args.prefix)
            template = validator.generate_path_template(args.application, args.environment)
            print(json.dumps(template, indent=2))
            return 0
        else:
            parser.print_help()
            print("\nError: No paths provided. Use --paths or --input")
            sys.exit(1)
    
    # Initialize validator
    validator = PathValidator(
        vault_addr=args.vault_addr,
        auth_method=args.auth_method,
        role_id=args.role_id,
        secret_id=args.secret_id,
        token=args.token,
        namespace=args.namespace,
        verify=not args.no_verify,
        path_prefix=args.prefix,
    )
    
    # Run validation
    all_checks = not (args.check_duplicates or args.check_existence or args.check_permissions)
    
    report = validator.validate_all(
        paths=paths,
        check_duplicates=args.check_duplicates or all_checks,
        check_existence=args.check_existence or (args.vault_addr and all_checks),
        check_permissions=args.check_permissions or (args.vault_addr and all_checks),
    )
    
    # Print results
    print("\n" + "=" * 60)
    print("PATH VALIDATION REPORT")
    print("=" * 60)
    print(f"Total paths: {len(paths)}")
    print(f"Valid: {report.valid_count}")
    print(f"Invalid: {report.invalid_count}")
    print(f"Warnings: {report.warning_count}")
    print("-" * 60)
    
    for result in report.results:
        status_symbol = {
            PathValidationStatus.VALID: "✓",
            PathValidationStatus.INVALID: "✗",
            PathValidationStatus.WARNING: "⚠",
            PathValidationStatus.DUPLICATE: "⚠",
            PathValidationStatus.EXISTS: "ℹ",
        }.get(result.status, "?")
        
        print(f"{status_symbol} {result.path}")
        print(f"   Status: {result.status.value}")
        print(f"   Message: {result.message}")
        if result.details:
            print(f"   Details: {result.details}")
        print()
    
    # Save report
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"Report saved to: {args.output}")
    
    # Exit code based on results
    if report.invalid_count > 0:
        sys.exit(1)
    return 0


if __name__ == "__main__":
    exit(main())
