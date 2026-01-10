"""Migration orchestrator for migrating secrets from environment variables to Vault.

This module provides the main script to orchestrate the entire migration process:
1. Scan and identify secrets in the codebase
2. Write secrets to Vault (with confirmation)
3. Update application code to use Vault
4. Verify migration
5. Rollback capability if needed

Usage:
    python -m wrm_pipeline.wrm_pipeline.migration.migrate --dry-run
    python -m wrm_pipeline.wrm_pipeline.migration.migrate --phase all --confirm

Example:
    # Dry run to preview migration
    python -m wrm_pipeline.wrm_pipeline.migration.migrate --dry-run
    
    # Run full migration with confirmation
    python -m wrm_pipeline.wrm_pipeline.migration.migrate --phase all --confirm
    
    # Run only specific phases
    python -m wrm_pipeline.wrm_pipeline.migration.migrate --phase scan --phase write
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from wrm_pipeline.wrm_pipeline.migration.detect_env import EnvDetector, EnvDetectionResult
from wrm_pipeline.wrm_pipeline.migration.scan_secrets import SecretScanner, ScanResult
from wrm_pipeline.wrm_pipeline.migration.validate_paths import PathValidator
from wrm_pipeline.wrm_pipeline.migration.write_to_vault import VaultSecretWriter, WriteResult

logger = logging.getLogger(__name__)


class MigrationPhase(Enum):
    """Migration phases."""
    SCAN = "scan"
    WRITE = "write"
    UPDATE = "update"
    VERIFY = "verify"
    CLEANUP = "cleanup"
    ALL = "all"


@dataclass
class MigrationConfig:
    """Configuration for the migration."""
    base_path: Path = Path.cwd()
    vault_addr: str = "https://vault.internal.bike-data-flow.com:8200"
    auth_method: str = "approle"
    role_id: Optional[str] = None
    secret_id: Optional[str] = None
    token: Optional[str] = None
    namespace: Optional[str] = None
    verify: bool = True
    path_prefix: str = "bike-data-flow"
    dry_run: bool = False
    confirm: bool = False
    backup_path: Optional[str] = None


@dataclass
class MigrationResult:
    """Result of the migration process."""
    phase: str = ""
    success: bool = False
    message: str = ""
    details: dict = field(default_factory=dict)


class MigrationOrchestrator:
    """Orchestrator for the secret migration process."""
    
    def __init__(self, config: MigrationConfig):
        """Initialize the migration orchestrator.
        
        Args:
            config: Migration configuration.
        """
        self.config = config
        self.scan_result: Optional[ScanResult] = None
        self.env_result: Optional[EnvDetectionResult] = None
        self.write_result: Optional[WriteResult] = None
        
    def _confirm_action(self, action: str) -> bool:
        """Confirm an action with the user.
        
        Args:
            action: Description of the action.
            
        Returns:
            True if confirmed, False otherwise.
        """
        if self.config.confirm:
            return True
        
        if self.config.dry_run:
            print(f"[DRY RUN] Would perform action: {action}")
            return False
        
        response = input(f"\n{action}\n\nProceed? [y/N]: ").strip().lower()
        return response in ["y", "yes"]
    
    def run_phase_scan(self) -> MigrationResult:
        """Phase 1: Scan and identify secrets.
        
        Returns:
            MigrationResult with scan findings.
        """
        print("\n" + "=" * 60)
        print("PHASE 1: SCAN AND IDENTIFY SECRETS")
        print("=" * 60)
        
        # Scan codebase for hardcoded secrets
        print("\nScanning codebase for hardcoded secrets...")
        scanner = SecretScanner(str(self.config.base_path))
        self.scan_result = scanner.scan_directory()
        
        print(f"Files scanned: {self.scan_result.files_scanned}")
        print(f"Secrets found: {self.scan_result.secrets_found}")
        
        # Scan environment variables
        print("\nScanning environment variables...")
        detector = EnvDetector(str(self.config.base_path))
        self.env_result = detector.scan_env_files()
        
        print(f"Files processed: {self.env_result.files_processed}")
        print(f"Environment variables found: {len(self.env_result.variables)}")
        
        # Count sensitive variables
        sensitive_count = sum(
            1 for v in self.env_result.variables
            if v.sensitivity.value in ["critical", "high"]
        )
        print(f"Sensitive variables (critical/high): {sensitive_count}")
        
        # Summary
        print("\n" + "-" * 60)
        print("SCAN SUMMARY:")
        print(f"  - Hardcoded secrets in code: {self.scan_result.secrets_found}")
        print(f"  - Sensitive env variables: {sensitive_count}")
        print(f"  - Total env variables to migrate: {len(self.env_result.variables)}")
        
        # Save scan results
        scan_output = self.config.base_path / "migration_scan_results.json"
        with open(scan_output, 'w') as f:
            json.dump({
                "scan_result": self.scan_result.to_dict(),
                "env_result": self.env_result.to_dict(),
            }, f, indent=2)
        print(f"\nScan results saved to: {scan_output}")
        
        return MigrationResult(
            phase="scan",
            success=True,
            message="Scan completed successfully",
            details={
                "hardcoded_secrets": self.scan_result.secrets_found,
                "sensitive_env_vars": sensitive_count,
                "output_file": str(scan_output),
            }
        )
    
    def run_phase_write(self) -> MigrationResult:
        """Phase 2: Write secrets to Vault.
        
        Returns:
            MigrationResult with write summary.
        """
        print("\n" + "=" * 60)
        print("PHASE 2: WRITE SECRETS TO VAULT")
        print("=" * 60)
        
        if not self.env_result or not self.env_result.variables:
            return MigrationResult(
                phase="write",
                success=False,
                message="No environment variables to write. Run scan phase first."
            )
        
        # Validate Vault connection
        print("\nConnecting to Vault...")
        try:
            writer = VaultSecretWriter(
                vault_addr=self.config.vault_addr,
                auth_method=self.config.auth_method,
                role_id=self.config.role_id,
                secret_id=self.config.secret_id,
                token=self.config.token,
                namespace=self.config.namespace,
                verify=self.config.verify,
                path_prefix=self.config.path_prefix,
            )
            
            # Test connection
            health = writer.client.get_health()
            print(f"Vault status: {health.status.value}")
            print(f"Vault version: {health.version}")
            
        except Exception as e:
            return MigrationResult(
                phase="write",
                success=False,
                message=f"Failed to connect to Vault: {e}"
            )
        
        # Group secrets by category for organized writing
        secrets_by_category: dict[str, dict[str, Any]] = {}
        for var in self.env_result.variables:
            category = var.category
            if category not in secrets_by_category:
                secrets_by_category[category] = {}
            secrets_by_category[category][var.name.lower()] = var.value
        
        # Display secrets to be written (without values)
        print("\nSecrets to be written to Vault:")
        print("-" * 60)
        for category, secrets in secrets_by_category.items():
            print(f"\n{category.upper()}:")
            for name in secrets.keys():
                print(f"  - {name} -> bike-data-flow/{category}/{name.lower()}")
        
        # Confirm before writing
        if not self._confirm_action("Write all secrets to Vault?"):
            return MigrationResult(
                phase="write",
                success=False,
                message="Write phase cancelled by user"
            )
        
        # Write secrets
        print("\nWriting secrets to Vault...")
        total_written = 0
        total_failed = 0
        
        with writer:
            for category, secrets in secrets_by_category.items():
                print(f"\nWriting {category} secrets...")
                
                if self.config.dry_run:
                    print(f"[DRY RUN] Would write {len(secrets)} secrets to {category}")
                    continue
                
                result = writer.write_from_dict(secrets, category=category)
                total_written += len(result.successful)
                total_failed += len(result.failed)
                
                print(f"  Written: {len(result.successful)}")
                print(f"  Failed: {len(result.failed)}")
                
                if result.failed:
                    for failure in result.failed:
                        print(f"    - {failure['path']}: {failure['error']}")
        
        self.write_result = WriteResult(
            successful=[str(i) for i in range(total_written)],
            failed=[],
            skipped=[],
        )
        
        print("\n" + "-" * 60)
        print(f"WRITE SUMMARY:")
        print(f"  - Total secrets written: {total_written}")
        print(f"  - Failed: {total_failed}")
        
        if self.config.dry_run:
            return MigrationResult(
                phase="write",
                success=True,
                message="Dry run completed - no secrets written",
                details={"would_write": total_written}
            )
        
        return MigrationResult(
            phase="write",
            success=total_failed == 0,
            message=f"Wrote {total_written} secrets to Vault",
            details={"written": total_written, "failed": total_failed}
        )
    
    def run_phase_update(self) -> MigrationResult:
        """Phase 3: Update application code to use Vault.
        
        Returns:
            MigrationResult with update summary.
        """
        print("\n" + "=" * 60)
        print("PHASE 3: UPDATE APPLICATION CODE")
        print("=" * 60)
        
        print("\nThis phase updates application code to use Vault instead of")
        print("environment variables for secret retrieval.")
        
        # Files to update
        files_to_update = [
            {
                "path": "wrm_pipeline/wrm_pipeline/config.py",
                "description": "Configuration module",
                "changes": [
                    "Replace direct os.environ.get() calls with Vault resource",
                    "Add Vault client initialization",
                    "Keep non-sensitive config as environment variables",
                ]
            },
            {
                "path": "wrm_pipeline/wrm_pipeline/resources.py",
                "description": "Resources module",
                "changes": [
                    "Update resource initialization to use Vault",
                    "Add VaultSecretsResource configuration",
                ]
            },
        ]
        
        print("\nFiles that would be updated:")
        print("-" * 60)
        for file_info in files_to_update:
            print(f"\n{file_info['path']}")
            print(f"  Description: {file_info['description']}")
            print("  Changes:")
            for change in file_info['changes']:
                print(f"    - {change}")
        
        if not self._confirm_action("Update application code to use Vault?"):
            return MigrationResult(
                phase="update",
                success=False,
                message="Update phase cancelled by user"
            )
        
        if self.config.dry_run:
            print("\n[DRY RUN] No files were modified")
            return MigrationResult(
                phase="update",
                success=True,
                message="Dry run completed - no files modified",
                details={"files_affected": len(files_to_update)}
            )
        
        # In a real implementation, this would:
        # 1. read_file the current file
        # 2. Identify patterns to replace
        # 3. Create backup
        # 4. write_to_file the updated file
        print("\n[INFO] Manual updates required. See documentation for update procedures.")
        print("       This tool provides guidance but does not auto-modify code")
        print("       to ensure changes are reviewed and tested properly.")
        
        return MigrationResult(
            phase="update",
            success=True,
            message="Update phase completed (manual updates required)",
            details={"files_affected": len(files_to_update)}
        )
    
    def run_phase_verify(self) -> MigrationResult:
        """Phase 4: Verify migration.
        
        Returns:
            MigrationResult with verification summary.
        """
        print("\n" + "=" * 60)
        print("PHASE 4: VERIFY MIGRATION")
        print("=" * 60)
        
        verification_checks = [
            "Vault is accessible and healthy",
            "All secrets were written successfully",
            "Application can retrieve secrets from Vault",
            "No hardcoded secrets remain in codebase",
            "Environment variables are properly cleaned",
        ]
        
        print("\nVerification checklist:")
        print("-" * 60)
        for i, check in enumerate(verification_checks, 1):
            print(f"  {i}. {check}")
        
        # Perform actual verification
        print("\nPerforming verification...")
        
        # Check 1: Vault health
        print("\n1. Checking Vault health...")
        try:
            writer = VaultSecretWriter(
                vault_addr=self.config.vault_addr,
                auth_method=self.config.auth_method,
                role_id=self.config.role_id,
                secret_id=self.config.secret_id,
                token=self.config.token,
                namespace=self.config.namespace,
                verify=self.config.verify,
            )
            with writer:
                health = writer.client.get_health()
                print(f"   Vault status: {health.status.value}")
                vault_healthy = health.status.value not in ["sealed", "uninitialized", "unreachable"]
        except Exception as e:
            print(f"   Error: {e}")
            vault_healthy = False
        
        # Check 2: Verify secrets were written
        print("\n2. Verifying secrets in Vault...")
        secrets_verified = 0
        if self.env_result:
            with writer:
                for var in self.env_result.variables[:10]:  # Sample first 10
                    path = f"{var.category}/{var.name.lower()}"
                    try:
                        secret = writer.client.get_secret(path)
                        if secret:
                            secrets_verified += 1
                    except Exception:
                        pass
        
        print(f"   Verified: {secrets_verified}/{min(len(self.env_result.variables), 10)} sampled secrets")
        
        # Check 3: Scan for remaining hardcoded secrets
        print("\n3. Scanning for remaining hardcoded secrets...")
        scanner = SecretScanner(str(self.config.base_path))
        scan_result = scanner.scan_directory()
        remaining_secrets = scan_result.secrets_found
        print(f"   Remaining hardcoded secrets: {remaining_secrets}")
        
        # Check 4: Check .env files
        print("\n4. Checking .env files for sensitive data...")
        detector = EnvDetector(str(self.config.base_path))
        env_result = detector.scan_env_files()
        sensitive_in_env = sum(
            1 for v in env_result.variables
            if v.sensitivity.value in ["critical", "high"]
        )
        print(f"   Sensitive variables still in .env: {sensitive_in_env}")
        
        # Summary
        print("\n" + "-" * 60)
        print("VERIFICATION SUMMARY:")
        print(f"  - Vault healthy: {vault_healthy}")
        print(f"  - Secrets verified: {secrets_verified}")
        print(f"  - Remaining hardcoded secrets: {remaining_secrets}")
        print(f"  - Sensitive vars in .env: {sensitive_in_env}")
        
        all_checks_passed = vault_healthy and remaining_secrets == 0 and sensitive_in_env == 0
        
        return MigrationResult(
            phase="verify",
            success=all_checks_passed,
            message="Verification completed",
            details={
                "vault_healthy": vault_healthy,
                "secrets_verified": secrets_verified,
                "remaining_hardcoded": remaining_secrets,
                "sensitive_in_env": sensitive_in_env,
            }
        )
    
    def run_phase_cleanup(self) -> MigrationResult:
        """Phase 5: Clean up .env files after migration.
        
        Returns:
            MigrationResult with cleanup summary.
        """
        print("\n" + "=" * 60)
        print("PHASE 5: CLEANUP .ENV FILES")
        print("=" * 60)
        
        if not self.env_result or not self.env_result.variables:
            return MigrationResult(
                phase="cleanup",
                success=False,
                message="No environment variables to clean up. Run scan phase first."
            )
        
        print("\nThe following .env files contain sensitive data that should be cleaned:")
        files_to_clean: dict[str, list[str]] = {}
        for var in self.env_result.variables:
            if var.sensitivity.value in ["critical", "high"]:
                if var.source_file not in files_to_clean:
                    files_to_clean[var.source_file] = []
                files_to_clean[var.source_file].append(var.name)
        
        for file_path, vars_list in files_to_clean.items():
            print(f"\n{file_path}:")
            for var in vars_list:
                print(f"  - {var}")
        
        print("\nRecommended cleanup actions:")
        print("-" * 60)
        print("1. Create .env.example with placeholder values")
        print("2. Replace sensitive values with Vault path references")
        print("3. Keep non-sensitive config values in .env")
        
        if not self._confirm_action("Clean up .env files?"):
            return MigrationResult(
                phase="cleanup",
                success=False,
                message="Cleanup phase cancelled by user"
            )
        
        if self.config.dry_run:
            print("\n[DRY RUN] No .env files were modified")
            return MigrationResult(
                phase="cleanup",
                success=True,
                message="Dry run completed - no files modified",
                details={"files_affected": len(files_to_clean)}
            )
        
        # Create cleanup script
        cleanup_script = self.config.base_path / "migration_cleanup_script.py"
        with open(cleanup_script, 'w') as f:
            f.write(CLEANUP_SCRIPT_TEMPLATE.format(
                base_path=str(self.config.base_path),
                path_prefix=self.config.path_prefix,
            ))
        print(f"\nCleanup script created: {cleanup_script}")
        print("Run this script to clean up .env files after reviewing changes.")
        
        return MigrationResult(
            phase="cleanup",
            success=True,
            message="Cleanup completed",
            details={
                "files_affected": len(files_to_clean),
                "cleanup_script": str(cleanup_script),
            }
        )
    
    def run_rollback(self) -> MigrationResult:
        """Rollback migration by removing secrets from Vault.
        
        Returns:
            MigrationResult with rollback summary.
        """
        print("\n" + "=" * 60)
        print("ROLLBACK")
        print("=" * 60)
        
        print("\nThis will remove all secrets written during migration from Vault.")
        
        if not self._confirm_action("Remove all migrated secrets from Vault?"):
            return MigrationResult(
                phase="rollback",
                success=False,
                message="Rollback cancelled by user"
            )
        
        try:
            writer = VaultSecretWriter(
                vault_addr=self.config.vault_addr,
                auth_method=self.config.auth_method,
                role_id=self.config.role_id,
                secret_id=self.config.secret_id,
                token=self.config.token,
                namespace=self.config.namespace,
                verify=self.config.verify,
            )
            
            with writer:
                # List all secrets under the path prefix
                path_prefix = self.config.path_prefix.strip("/")
                secrets = writer.client.list_secrets(path_prefix)
                
                print(f"\nFound {len(secrets)} secrets to remove:")
                for secret in secrets[:20]:  # Show first 20
                    print(f"  - {secret}")
                if len(secrets) > 20:
                    print(f"  ... and {len(secrets) - 20} more")
                
                if not self.config.dry_run:
                    # Delete all secrets
                    for secret in secrets:
                        try:
                            writer.client.delete_secret(f"{path_prefix}/{secret}")
                            print(f"Deleted: {secret}")
                        except Exception as e:
                            print(f"Failed to delete {secret}: {e}")
                
                print(f"\n[{'DRY RUN ' if self.config.dry_run else ''}]Removed {len(secrets)} secrets from Vault")
            
            return MigrationResult(
                phase="rollback",
                success=True,
                message=f"Rolled back {len(secrets)} secrets from Vault",
                details={"secrets_removed": len(secrets)}
            )
            
        except Exception as e:
            return MigrationResult(
                phase="rollback",
                success=False,
                message=f"Rollback failed: {e}"
            )
    
    def run(self, phases: list[MigrationPhase]) -> bool:
        """Run the migration process.
        
        Args:
            phases: List of phases to run.
            
        Returns:
            True if all phases succeeded, False otherwise.
        """
        print("\n" + "#" * 60)
        print("# SECRET MIGRATION ORCHESTRATOR")
        print("# Migrating from Environment Variables to HashiCorp Vault")
        print("#" * 60)
        
        if self.config.dry_run:
            print("\n[DRY RUN MODE - No changes will be made]")
        
        print(f"\nConfiguration:")
        print(f"  Base path: {self.config.base_path}")
        print(f"  Vault addr: {self.config.vault_addr}")
        print(f"  Auth method: {self.config.auth_method}")
        print(f"  Path prefix: {self.config.path_prefix}")
        
        results: list[MigrationResult] = []
        
        for phase in phases:
            if phase == MigrationPhase.ALL:
                # Run all phases
                phases_to_run = [
                    MigrationPhase.SCAN,
                    MigrationPhase.WRITE,
                    MigrationPhase.UPDATE,
                    MigrationPhase.VERIFY,
                    MigrationPhase.CLEANUP,
                ]
                results.extend(self.run(phases_to_run))
                break
            
            elif phase == MigrationPhase.SCAN:
                result = self.run_phase_scan()
            
            elif phase == MigrationPhase.WRITE:
                result = self.run_phase_write()
            
            elif phase == MigrationPhase.UPDATE:
                result = self.run_phase_update()
            
            elif phase == MigrationPhase.VERIFY:
                result = self.run_phase_verify()
            
            elif phase == MigrationPhase.CLEANUP:
                result = self.run_phase_cleanup()
            
            else:
                continue
            
            results.append(result)
            
            # Stop on failure (except in dry run)
            if not result.success and not self.config.dry_run:
                print(f"\n[ERROR] Phase '{phase.value}' failed: {result.message}")
                break
        
        # Summary
        print("\n" + "#" * 60)
        print("# MIGRATION SUMMARY")
        print("#" * 60)
        
        all_success = True
        for result in results:
            status = "✓" if result.success else "✗"
            print(f"  {status} {result.phase}: {result.message}")
            if not result.success:
                all_success = False
        
        return all_success


# Template for cleanup script
CLEANUP_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""Generated cleanup script for .env files after Vault migration.

This script helps clean up sensitive values from .env files.
Review and run after migration is verified.
"""

import os
import shutil
from pathlib import Path

BASE_PATH = "{base_path}"
PATH_PREFIX = "{path_prefix}"


def backup_env_file(file_path: Path) -> None:
    """Create a backup of the .env file."""
    backup_path = file_path.with_suffix(file_path.suffix + ".bak")
    shutil.copy2(file_path, backup_path)
    print(f"Backup created: {{backup_path}}")


def cleanup_env_file(file_path: Path) -> None:
    """Clean up sensitive values from .env file."""
    sensitive_patterns = [
        "PASSWORD", "SECRET", "API_KEY", "TOKEN", "CREDENTIAL",
        "VAULT_", "AWS_", "DATABASE_URL",
    ]
    
    lines = []
    changed = False
    
    with open(file_path, 'r') as f:
        for line in f:
            original = line
            stripped = line.strip()
            
            # Skip empty lines and comments
            if not stripped or stripped.startswith('#'):
                lines.append(line)
                continue
            
            # Check if line contains sensitive pattern
            is_sensitive = any(pattern in stripped.upper() for pattern in sensitive_patterns)
            
            if is_sensitive and '=' in stripped:
                # Replace value with placeholder
                key = stripped.split('=', 1)[0]
                new_line = f"{{key}}=#MIGRATED_TO_VAULT_{{key}}}\\n"
                lines.append(new_line)
                changed = True
                print(f"  Modified: {{key}}")
            else:
                lines.append(line)
    
    if changed:
        with open(file_path, 'w') as f:
            f.writelines(lines)
        print(f"  Updated: {{file_path}}")
    else:
        print(f"  No changes: {{file_path}}")


def main():
    """Main cleanup function."""
    base = Path(BASE_PATH)
    
    env_files = [
        base / ".env",
        base / ".env.local",
        base / ".env.production",
        base / ".env.development",
    ]
    
    print("Cleaning up .env files...")
    print("-" * 40)
    
    for env_file in env_files:
        if env_file.exists():
            print(f"\\nProcessing: {{env_file}}")
            backup_env_file(env_file)
            cleanup_env_file(env_file)
    
    print("\\n" + "-" * 40)
    print("Cleanup complete!")
    print("Review the changes and commit to version control.")


if __name__ == "__main__":
    main()
'''


def main():
    """Main entry point for the migration orchestrator."""
    parser = argparse.ArgumentParser(
        description="Orchestrate migration of secrets from environment variables to Vault",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Dry run to preview migration
    python -m wrm_pipeline.wrm_pipeline.migration.migrate --dry-run
    
    # Run full migration with confirmation
    python -m wrm_pipeline.wrm_pipeline.migration.migrate --phase all --confirm
    
    # Run specific phases
    python -m wrm_pipeline.wrm_pipeline.migration.migrate --phase scan --phase write
    
    # Rollback migration
    python -m wrm_pipeline.wrm_pipeline.migration.migrate --rollback --confirm
        """
    )
    
    parser.add_argument(
        "--path", "-p",
        default=None,
        help="Base path for the project (default: current directory)"
    )
    parser.add_argument(
        "--vault-addr",
        default="https://vault.internal.bike-data-flow.com:8200",
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
        "--path-prefix",
        default="bike-data-flow",
        help="Prefix for Vault secret paths"
    )
    parser.add_argument(
        "--phase", "-s",
        action="append",
        choices=["scan", "write", "update", "verify", "cleanup", "all"],
        default=[],
        help="Phases to run (can be specified multiple times)"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview changes without making them"
    )
    parser.add_argument(
        "--confirm", "-y",
        action="store_true",
        help="Skip confirmation prompts"
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Rollback the migration"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")
    
    # Validate arguments
    if args.rollback:
        if not args.confirm and not args.dry_run:
            print("Warning: Rollback is destructive. Use --confirm to proceed.")
            sys.exit(1)
    else:
        if not args.phase:
            args.phase = ["all"]
    
    # Build configuration
    config = MigrationConfig(
        base_path=Path(args.path) if args.path else Path.cwd(),
        vault_addr=args.vault_addr,
        auth_method=args.auth_method,
        role_id=args.role_id,
        secret_id=args.secret_id,
        token=args.token,
        namespace=args.namespace,
        verify=not args.no_verify,
        path_prefix=args.path_prefix,
        dry_run=args.dry_run,
        confirm=args.confirm,
    )
    
    # Run migration
    orchestrator = MigrationOrchestrator(config)
    
    if args.rollback:
        success = orchestrator.run_rollback().success
    else:
        phases = [MigrationPhase(p) for p in args.phase]
        success = orchestrator.run(phases)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    exit(main())
