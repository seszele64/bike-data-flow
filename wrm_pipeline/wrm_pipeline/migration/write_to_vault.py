"""Vault secret writer for migrating secrets to HashiCorp Vault.

This module provides functionality to write secrets to Vault's KV store,
supporting batch writes, validation, and various secret formats.

Usage:
    python -m wrm_pipeline.wrm_pipeline.migration.write_to_vault --secrets secrets.json --vault-addr VAULT_ADDR

Example:
    python -m wrm_pipeline.wrm_pipeline.migration.write_to_vault \\
        --secrets secrets.json \\
        --vault-addr https://vault.example.com:8200 \\
        --auth-method approle \\
        --role-id role-id \\
        --secret-id secret-id
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from wrm_pipeline.wrm_pipeline.vault.client import VaultClient
from wrm_pipeline.wrm_pipeline.vault.exceptions import (
    VaultAuthenticationError,
    VaultConnectionError,
    VaultError,
)
from wrm_pipeline.wrm_pipeline.vault.models import VaultConnectionConfig

logger = logging.getLogger(__name__)


@dataclass
class WriteResult:
    """Result of writing secrets to Vault."""
    successful: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class SecretToWrite:
    """Secret to be written to Vault."""
    path: str
    data: dict[str, Any]
    format: str = "json"  # json, plain, key-value
    validate: bool = True


class VaultSecretWriter:
    """Writer for secrets to HashiCorp Vault."""
    
    # Default Vault path structure
    DEFAULT_PATH_PREFIX = "bike-data-flow"
    
    # Path structure categories
    CATEGORIES = ["app", "database", "api", "storage", "logging", "vault"]
    
    def __init__(
        self,
        vault_addr: str,
        auth_method: str = "approle",
        role_id: Optional[str] = None,
        secret_id: Optional[str] = None,
        token: Optional[str] = None,
        namespace: Optional[str] = None,
        verify: bool = True,
        path_prefix: Optional[str] = None,
    ):
        """Initialize the Vault secret writer.
        
        Args:
            vault_addr: Vault server address.
            auth_method: Authentication method (approle, token, kubernetes).
            role_id: AppRole role ID (if using approle auth).
            secret_id: AppRole secret ID (if using approle auth).
            token: Vault token (if using token auth).
            namespace: Enterprise namespace.
            verify: Whether to verify TLS certificates.
            path_prefix: Prefix for secret paths.
        """
        self.path_prefix = path_prefix or self.DEFAULT_PATH_PREFIX
        self.path_prefix = f"{self.path_prefix}/{self.path_prefix}" if not self.path_prefix.endswith(self.path_prefix.split('/')[-1]) else self.path_prefix
        
        config = VaultConnectionConfig(
            vault_addr=vault_addr,
            auth_method=auth_method,
            role_id=role_id,
            secret_id=secret_id,
            token=token,
            namespace=namespace,
            verify=verify,
        )
        
        self.client = VaultClient(config)
    
    def __enter__(self) -> "VaultSecretWriter":
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()
    
    def close(self) -> None:
        """Close the Vault client."""
        self.client.close()
    
    def _normalize_path(self, path: str) -> str:
        """Normalize a secret path.
        
        Args:
            path: Secret path to normalize.
            
        Returns:
            Normalized path with proper structure.
        """
        # Remove leading/trailing slashes
        path = path.strip("/")
        
        # Remove path prefix if already included
        prefix_stripped = self.path_prefix.strip("/")
        if path.startswith(prefix_stripped):
            path = path[len(prefix_stripped):].strip("/")
        
        # Ensure path doesn't start with secret/data
        if path.startswith("secret/data/"):
            path = path.replace("secret/data/", "", 1)
        elif path.startswith("secret/"):
            path = path.replace("secret/", "", 1)
        
        return path
    
    def _validate_secret(self, path: str, data: dict[str, Any]) -> list[str]:
        """Validate secret data before writing.
        
        Args:
            path: Secret path.
            data: Secret data to validate.
            
        Returns:
            List of validation errors (empty if valid).
        """
        errors = []
        
        # Check for empty data
        if not data:
            errors.append(f"Secret at path '{path}' has no data")
            return errors
        
        # Check for empty values
        for key, value in data.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"Secret at path '{path}' has empty value for key '{key}'")
        
        # Check for reserved keys
        reserved_keys = ["_", "__"]
        for key in data.keys():
            if key.startswith("_") or key.startswith("-"):
                errors.append(f"Secret at path '{path}' has reserved key '{key}'")
        
        # Validate path structure
        if "/" not in path:
            errors.append(f"Secret path '{path}' should have at least one category (e.g., 'app/secret')")
        
        return errors
    
    def _format_secret_data(self, secret: SecretToWrite) -> dict[str, Any]:
        """Format secret data based on format type.
        
        Args:
            secret: Secret to format.
            
        Returns:
            Formatted secret data.
        """
        if secret.format == "plain":
            # Convert plain text to key-value format
            return {"value": secret.data.get("value", "")}
        
        elif secret.format == "json":
            # JSON format - assume data is already in proper format
            return secret.data
        
        elif secret.format == "key-value":
            # Key-value pairs - ensure all values are strings
            return {str(k): str(v) for k, v in secret.data.items()}
        
        else:
            # Default to JSON format
            return secret.data
    
    def write_secret(
        self,
        path: str,
        data: dict[str, Any],
        format: str = "json",
        validate: bool = True,
    ) -> tuple[bool, Optional[str]]:
        """Write a single secret to Vault.
        
        Args:
            path: Secret path (e.g., "app/database/password").
            data: Secret data to write.
            format: Secret format (json, plain, key-value).
            validate: Whether to validate before writing.
            
        Returns:
            Tuple of (success, error_message).
        """
        normalized_path = self._normalize_path(path)
        
        if validate:
            errors = self._validate_secret(normalized_path, data)
            if errors:
                return False, "; ".join(errors)
        
        formatted_data = self._format_secret_data(
            SecretToWrite(path=normalized_path, data=data, format=format, validate=False)
        )
        
        try:
            self.client.write_secret(normalized_path, formatted_data)
            logger.info(f"Successfully wrote secret to: {normalized_path}")
            return True, None
        except VaultConnectionError as e:
            logger.error(f"Connection error writing to {normalized_path}: {e}")
            return False, f"Connection error: {e}"
        except VaultAuthenticationError as e:
            logger.error(f"Authentication error writing to {normalized_path}: {e}")
            return False, f"Authentication error: {e}"
        except VaultError as e:
            logger.error(f"Vault error writing to {normalized_path}: {e}")
            return False, f"Vault error: {e}"
        except Exception as e:
            logger.error(f"Unexpected error writing to {normalized_path}: {e}")
            return False, f"Unexpected error: {e}"
    
    def write_batch(
        self,
        secrets: list[SecretToWrite],
        dry_run: bool = False,
    ) -> WriteResult:
        """Write multiple secrets to Vault in batch.
        
        Args:
            secrets: List of secrets to write.
            dry_run: If True, simulate writes without actually writing.
            
        Returns:
            WriteResult with summary of operations.
        """
        result = WriteResult()
        
        for secret in secrets:
            normalized_path = self._normalize_path(secret.path)
            
            if dry_run:
                result.skipped.append(normalized_path)
                logger.info(f"[DRY RUN] Would write to: {normalized_path}")
                continue
            
            success, error = self.write_secret(
                path=secret.path,
                data=secret.data,
                format=secret.format,
                validate=secret.validate,
            )
            
            if success:
                result.successful.append(normalized_path)
            else:
                result.failed.append({
                    "path": normalized_path,
                    "error": error,
                })
        
        return result
    
    def write_from_dict(
        self,
        secrets_dict: dict[str, dict[str, Any]],
        category: str = "app",
        dry_run: bool = False,
    ) -> WriteResult:
        """Write secrets from a dictionary.
        
        Args:
            secrets_dict: Dictionary mapping secret names to data.
            category: Category for the secret path.
            dry_run: If True, simulate writes.
            
        Returns:
            WriteResult with summary of operations.
        """
        secrets = []
        
        for name, data in secrets_dict.items():
            path = f"{category}/{name}"
            secrets.append(SecretToWrite(path=path, data=data))
        
        return self.write_batch(secrets, dry_run=dry_run)
    
    def write_from_json_file(
        self,
        file_path: str,
        root_key: Optional[str] = None,
        dry_run: bool = False,
    ) -> WriteResult:
        """Write secrets from a JSON file.
        
        Args:
            file_path: Path to JSON file.
            root_key: Optional root key in the JSON to use as secrets data.
            dry_run: If True, simulate writes.
            
        Returns:
            WriteResult with summary of operations.
        """
        path = Path(file_path)
        
        if not path.exists():
            logger.error(f"JSON file not found: {file_path}")
            return WriteResult()
        
        try:
            with open(path, 'r') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {file_path}: {e}")
            return WriteResult()
        
        # If root_key specified, extract that portion
        if root_key:
            if root_key in data:
                data = data[root_key]
            else:
                logger.error(f"Root key '{root_key}' not found in {file_path}")
                return WriteResult()
        
        # If data is a list, process each item
        if isinstance(data, list):
            secrets = []
            for item in data:
                if isinstance(item, dict) and "path" in item and "data" in item:
                    secrets.append(SecretToWrite(
                        path=item["path"],
                        data=item["data"],
                        format=item.get("format", "json"),
                        validate=item.get("validate", True),
                    ))
            return self.write_batch(secrets, dry_run=dry_run)
        
        # If data is a dict, assume it's secrets by category
        if isinstance(data, dict):
            secrets = []
            for category, secrets_dict in data.items():
                if isinstance(secrets_dict, dict):
                    for name, secret_data in secrets_dict.items():
                        path = f"{category}/{name}"
                        secrets.append(SecretToWrite(path=path, data=secret_data))
            return self.write_batch(secrets, dry_run=dry_run)
        
        logger.error(f"Unexpected JSON structure in {file_path}")
        return WriteResult()
    
    def verify_secret(self, path: str) -> tuple[bool, Optional[dict]]:
        """Verify that a secret was written correctly.
        
        Args:
            path: Secret path to verify.
            
        Returns:
            Tuple of (verified, secret_data).
        """
        normalized_path = self._normalize_path(path)
        
        try:
            secret = self.client.get_secret(normalized_path)
            return True, secret.data
        except Exception as e:
            logger.error(f"Failed to verify secret at {normalized_path}: {e}")
            return False, None


def main():
    """Main entry point for the Vault secret writer."""
    parser = argparse.ArgumentParser(
        description="Write secrets to HashiCorp Vault",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Write single secret
    python -m wrm_pipeline.wrm_pipeline.migration.write_to_vault \\
        --vault-addr https://vault.example.com:8200 \\
        --path app/database/password \\
        --data '{"password": "secret123"}'
    
    # Batch write from JSON file
    python -m wrm_pipeline.wrm_pipeline.migration.write_to_vault \\
        --vault-addr https://vault.example.com:8200 \\
        --secrets secrets.json \\
        --auth-method approle \\
        --role-id my-role \\
        --secret-id my-secret
    
    # Dry run to preview changes
    python -m wrm_pipeline.wrm_pipeline.migration.write_to_vault \\
        --vault-addr https://vault.example.com:8200 \\
        --secrets secrets.json \\
        --dry-run
        """
    )
    
    parser.add_argument(
        "--vault-addr",
        required=True,
        help="Vault server address (e.g., https://vault.example.com:8200)"
    )
    parser.add_argument(
        "--auth-method",
        default="approle",
        choices=["approle", "token", "kubernetes"],
        help="Authentication method (default: approle)"
    )
    parser.add_argument(
        "--role-id",
        default=None,
        help="AppRole role ID (required for approle auth)"
    )
    parser.add_argument(
        "--secret-id",
        default=None,
        help="AppRole secret ID (required for approle auth)"
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Vault token (required for token auth)"
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="Enterprise namespace"
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable TLS certificate verification"
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Secret path for single secret write"
    )
    parser.add_argument(
        "--data",
        default=None,
        help="JSON data for single secret write"
    )
    parser.add_argument(
        "--secrets",
        default=None,
        help="JSON file containing secrets to write"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate writes without actually writing"
    )
    parser.add_argument(
        "--path-prefix",
        default="bike-data-flow",
        help="Prefix for secret paths (default: bike-data-flow)"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify secrets after writing"
    )
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s"
    )
    
    # Check authentication
    if args.auth_method == "approle":
        if not args.role_id or not args.secret_id:
            print("Error: --role-id and --secret-id are required for approle auth")
            sys.exit(1)
    elif args.auth_method == "token":
        if not args.token:
            print("Error: --token is required for token auth")
            sys.exit(1)
    
    writer = VaultSecretWriter(
        vault_addr=args.vault_addr,
        auth_method=args.auth_method,
        role_id=args.role_id,
        secret_id=args.secret_id,
        token=args.token,
        namespace=args.namespace,
        verify=not args.no_verify,
        path_prefix=args.path_prefix,
    )
    
    try:
        with writer:
            if args.path and args.data:
                # Write single secret
                try:
                    data = json.loads(args.data)
                except json.JSONDecodeError as e:
                    print(f"Error: Invalid JSON data: {e}")
                    sys.exit(1)
                
                if args.dry_run:
                    print(f"[DRY RUN] Would write to path: {args.path}")
                    print(f"[DRY RUN] Data: {data}")
                else:
                    success, error = writer.write_secret(args.path, data)
                    if success:
                        print(f"Successfully wrote secret to: {args.path}")
                        if args.verify:
                            verified, secret_data = writer.verify_secret(args.path)
                            if verified:
                                print(f"Verified secret at {args.path}: {secret_data}")
                    else:
                        print(f"Error writing secret: {error}")
                        sys.exit(1)
            
            elif args.secrets:
                # Write batch from JSON file
                print(f"Writing secrets from: {args.secrets}")
                
                if args.dry_run:
                    print("[DRY RUN MODE - No changes will be made]")
                
                result = writer.write_from_json_file(args.secrets, dry_run=args.dry_run)
                
                print(f"\nResults:")
                print(f"  Successful: {len(result.successful)}")
                print(f"  Failed: {len(result.failed)}")
                print(f"  Skipped (dry run): {len(result.skipped)}")
                
                if result.failed:
                    print("\nFailed secrets:")
                    for failure in result.failed:
                        print(f"  Path: {failure['path']}")
                        print(f"  Error: {failure['error']}")
                
                if result.successful and args.verify:
                    print("\nVerifying written secrets...")
                    for path in result.successful:
                        verified, data = writer.verify_secret(path)
                        if verified:
                            print(f"  Verified: {path}")
                        else:
                            print(f"  Verification failed: {path}")
                
                if result.failed:
                    sys.exit(1)
            
            else:
                parser.print_help()
                print("\nError: Either --path/--data or --secrets must be provided")
                sys.exit(1)
    
    except VaultConnectionError as e:
        print(f"Connection error: {e}")
        sys.exit(1)
    except VaultAuthenticationError as e:
        print(f"Authentication error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(130)
    except Exception as e:
        print(f"Unexpected error: {e}")
        logger.exception("Full traceback:")
        sys.exit(1)
    
    print("\nDone!")
    return 0


if __name__ == "__main__":
    exit(main())
