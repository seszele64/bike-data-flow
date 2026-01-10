"""Policy management for HashiCorp Vault.

This module provides the PolicyManager class for creating, managing, and syncing
Vault access control policies in HCL format.

Features:
- Create policies from AccessPolicy models
- Generate HCL-formatted policy strings
- Sync policies to Vault server
- Validate policy syntax
- Support for path wildcards and fine-grained access control
"""

import logging
import re
from pathlib import Path
from typing import Optional

from wrm_pipeline.wrm_pipeline.vault.models import AccessPolicy, PolicyRule

logger = logging.getLogger(__name__)


class PolicyValidationError(Exception):
    """Raised when policy validation fails."""

    pass


class PolicyManager:
    """Manages Vault access control policies.

    This class provides methods for:
    - Creating policies from AccessPolicy models
    - Generating HCL-formatted policy strings
    - Syncing policies to Vault
    - Listing and managing existing policies

    Example:
        >>> manager = PolicyManager()
        >>> policy = AccessPolicy(
        ...     name="dagster-secrets",
        ...     rules=[PolicyRule(path="secret/data/bike-data-flow/*", capabilities=["read"])]
        ... )
        >>> hcl = manager.to_hcl(policy)
        >>> manager.create_policy(client, policy)  # Requires authenticated Vault client
    """

    # Valid capability values per Vault documentation
    VALID_CAPABILITIES = frozenset({"create", "read", "update", "delete", "list", "patch", "sudo"})

    # Reserved policy names that cannot be used
    RESERVED_NAMES = frozenset({"root"})

    def __init__(self, policies_dir: Optional[str] = None):
        """Initialize the policy manager.

        Args:
            policies_dir: Directory for storing policy files (optional)
        """
        self.policies_dir = Path(policies_dir) if policies_dir else None

    def create_policy(self, policy: AccessPolicy) -> AccessPolicy:
        """Validate and create a policy.

        Args:
            policy: AccessPolicy model to create

        Returns:
            The validated policy

        Raises:
            PolicyValidationError: If policy validation fails
        """
        self.validate_policy(policy)
        logger.info(f"Created policy: {policy.name}")
        return policy

    def validate_policy(self, policy: AccessPolicy) -> bool:
        """Validate a policy for correctness.

        Args:
            policy: AccessPolicy model to validate

        Returns:
            True if valid

        Raises:
            PolicyValidationError: If validation fails
        """
        # Check policy name
        if not policy.name:
            raise PolicyValidationError("Policy name is required")

        if not re.match(r"^[a-zA-Z0-9_-]+$", policy.name):
            raise PolicyValidationError(
                "Policy name must contain only alphanumeric characters, hyphens, and underscores"
            )

        if policy.name in self.RESERVED_NAMES:
            raise PolicyValidationError(f"Policy name '{policy.name}' is reserved")

        # Check for duplicate rules
        seen_paths: dict[str, int] = {}
        for i, rule in enumerate(policy.rules):
            if rule.path in seen_paths:
                raise PolicyValidationError(
                    f"Duplicate path rule at index {i} and {seen_paths[rule.path]}"
                )
            seen_paths[rule.path] = i

        # Validate each rule
        for rule in policy.rules:
            self._validate_policy_rule(rule)

        logger.info(f"Validated policy: {policy.name}")
        return True

    def _validate_policy_rule(self, rule: PolicyRule) -> None:
        """Validate a single policy rule.

        Args:
            rule: PolicyRule to validate

        Raises:
            PolicyValidationError: If validation fails
        """
        # Validate path
        if not rule.path:
            raise PolicyValidationError("Path is required in policy rule")

        # Check for path traversal attempts
        if ".." in rule.path:
            raise PolicyValidationError(f"Invalid path with path traversal: {rule.path}")

        # Validate capabilities
        for cap in rule.capabilities:
            if cap not in self.VALID_CAPABILITIES:
                raise PolicyValidationError(
                    f"Invalid capability '{cap}'. Must be one of {sorted(self.VALID_CAPABILITIES)}"
                )

    def to_hcl(self, policy: AccessPolicy) -> str:
        """Generate HCL-formatted policy string.

        Args:
            policy: AccessPolicy model to convert

        Returns:
            HCL-formatted policy string

        Example:
            >>> policy = AccessPolicy(
            ...     name="test-policy",
            ...     rules=[PolicyRule(path="secret/data/app/*", capabilities=["read", "list"])]
            ... )
            >>> print(manager.to_hcl(policy))
            path "secret/data/app/*" {
              capabilities = ["read", "list"]
            }
        """
        lines: list[str] = []

        # Add header comment if description exists
        if policy.description:
            lines.append(f"# {policy.description}")
            lines.append(f"# Policy: {policy.name}")
            lines.append("")

        # Add policy name as comment for clarity
        lines.append(f"# Access policy: {policy.name}")
        lines.append("")

        # Generate path rules
        for rule in policy.rules:
            hcl_rule = self._rule_to_hcl(rule)
            lines.append(hcl_rule)

        return "\n".join(lines)

    def _rule_to_hcl(self, rule: PolicyRule) -> str:
        """Convert a PolicyRule to HCL format.

        Args:
            rule: PolicyRule to convert

        Returns:
            HCL-formatted rule string
        """
        # Format capabilities as HCL array
        caps_str = ", ".join(f'"{cap}"' for cap in rule.capabilities)

        # Build the path block
        lines = [f'path "{rule.path}" {{']
        lines.append(f"  capabilities = [{caps_str}]")

        # Add description if present
        if rule.description:
            lines.append(f"  # {rule.description}")

        lines.append("}")

        return "\n".join(lines)

    def parse_hcl(self, hcl_content: str) -> AccessPolicy:
        """Parse HCL content into an AccessPolicy model.

        Args:
            hcl_content: HCL policy content

        Returns:
            AccessPolicy model

        Raises:
            PolicyValidationError: If parsing fails
        """
        # Simple HCL parser for Vault policies
        # Vault policies use a simple format that we can parse with regex
        lines = hcl_content.strip().split("\n")

        # Extract policy name from comment if available
        policy_name = None
        description = None

        for line in lines:
            if line.startswith("# Policy:"):
                policy_name = line.replace("# Policy:", "").strip()
            elif line.startswith("#"):
                description = line.replace("#", "").strip()

        if not policy_name:
            raise PolicyValidationError("Could not determine policy name from HCL content")

        # Parse path rules
        rules: list[PolicyRule] = []
        current_path = None
        current_caps: list[str] = []
        current_desc = None

        for line in lines:
            line = line.strip()

            # Skip comments (except the policy name we already extracted)
            if line.startswith("#") or not line:
                continue

            # Check for path definition
            path_match = re.match(r'path\s+"([^"]+)"\s*\{', line)
            if path_match:
                # Save previous rule if exists
                if current_path:
                    rules.append(PolicyRule(
                        path=current_path,
                        capabilities=current_caps,
                        description=current_desc,
                    ))

                current_path = path_match.group(1)
                current_caps = []
                current_desc = None
                continue

            # Check for capabilities
            caps_match = re.match(r'capabilities\s*=\s*\[(.*)\]', line)
            if caps_match and current_path:
                caps_str = caps_match.group(1)
                # Parse capability strings
                current_caps = re.findall(r'"([^"]+)"', caps_str)
                continue

            # Check for closing brace
            if line == "}":
                if current_path:
                    rules.append(PolicyRule(
                        path=current_path,
                        capabilities=current_caps,
                        description=current_desc,
                    ))
                    current_path = None
                continue

        return AccessPolicy(
            name=policy_name,
            rules=rules,
            description=description,
        )

    def sync_policy(self, client, policy: AccessPolicy) -> None:
        """Sync a policy to Vault.

        Args:
            client: Authenticated Vault client
            policy: AccessPolicy model to sync

        Raises:
            PolicyValidationError: If policy validation fails
            VaultError: If Vault operation fails
        """
        self.validate_policy(policy)
        hcl = self.to_hcl(policy)

        try:
            client.sys.create_or_update_policy(
                name=policy.name,
                policy=hcl,
            )
            logger.info(f"Synced policy to Vault: {policy.name}")
        except Exception as e:
            logger.error(f"Failed to sync policy '{policy.name}' to Vault: {e}")
            raise

    def list_policies(self, client) -> list[str]:
        """List all policies in Vault.

        Args:
            client: Authenticated Vault client

        Returns:
            List of policy names
        """
        try:
            response = client.sys.list_policies()
            return response.get("policies", [])
        except Exception as e:
            logger.error(f"Failed to list policies: {e}")
            return []

    def get_policy(self, client, name: str) -> Optional[str]:
        """Get a policy's HCL content from Vault.

        Args:
            client: Authenticated Vault client
            name: Policy name

        Returns:
            HCL policy content or None if not found
        """
        try:
            response = client.sys.read_policy(name=name)
            return response.get("rules", "")
        except Exception:
            return None

    def delete_policy(self, client, name: str) -> bool:
        """Delete a policy from Vault.

        Args:
            client: Authenticated Vault client
            name: Policy name

        Returns:
            True if deleted, False if not found
        """
        try:
            client.sys.delete_policy(name=name)
            logger.info(f"Deleted policy from Vault: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete policy '{name}' from Vault: {e}")
            return False

    def policy_exists(self, client, name: str) -> bool:
        """Check if a policy exists in Vault.

        Args:
            client: Authenticated Vault client
            name: Policy name

        Returns:
            True if policy exists
        """
        try:
            client.sys.read_policy(name=name)
            return True
        except Exception:
            return False

    def save_policy_file(self, policy: AccessPolicy, path: Optional[str] = None) -> Path:
        """Save a policy to a file.

        Args:
            policy: AccessPolicy model to save
            path: Optional custom path (uses policies_dir if not provided)

        Returns:
            Path to saved file
        """
        if path is None:
            if self.policies_dir is None:
                raise ValueError("policies_dir must be set to save policy files")
            path = self.policies_dir / f"{policy.name}.hcl"

        hcl = self.to_hcl(policy)
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(hcl)

        logger.info(f"Saved policy file: {file_path}")
        return file_path

    def load_policy_file(self, path: str) -> AccessPolicy:
        """Load a policy from a file.

        Args:
            path: Path to HCL policy file

        Returns:
            AccessPolicy model
        """
        hcl_content = Path(path).read_text()
        return self.parse_hcl(hcl_content)

    def load_policy_from_package(self, name: str) -> AccessPolicy:
        """Load a built-in policy from the package.

        Args:
            name: Policy name (e.g., "dagster-secrets", "readonly-secrets", "admin-secrets")

        Returns:
            AccessPolicy model

        Raises:
            FileNotFoundError: If policy file not found
        """
        import importlib.resources

        policy_path = f"policies/{name}.hcl"

        try:
            # Try to read from package resources
            if importlib.resources.files("wrm_pipeline.wrm_pipeline.vault").joinpath(policy_path).is_file():
                hcl_content = (
                    importlib.resources.files("wrm_pipeline.wrm_pipeline.vault")
                    .joinpath(policy_path)
                    .read_text()
                )
                return self.parse_hcl(hcl_content)
        except (ModuleNotFoundError, FileNotFoundError, TypeError):
            pass

        # Fallback: check local policies directory
        local_path = Path(f"config/vault/policies/{name}.hcl")
        if local_path.exists():
            return self.load_policy_file(str(local_path))

        raise FileNotFoundError(f"Policy '{name}' not found")

    def generate_readonly_policy(
        self,
        base_path: str = "secret/data/bike-data-flow",
        name: str = "readonly-secrets",
        description: Optional[str] = None,
    ) -> AccessPolicy:
        """Generate a read-only access policy.

        Args:
            base_path: Base path for secrets
            name: Policy name
            description: Optional description

        Returns:
            AccessPolicy model with read-only access
        """
        return AccessPolicy(
            name=name,
            description=description or "Read-only access to all secrets",
            rules=[
                PolicyRule(
                    path=f"{base_path}/*",
                    capabilities=["read", "list"],
                    description="Read and list all secrets",
                ),
                PolicyRule(
                    path=f"{base_path}",
                    capabilities=["list"],
                    description="List secrets at root",
                ),
            ],
        )

    def generate_dagster_policy(
        self,
        base_path: str = "secret/data/bike-data-flow",
        name: str = "dagster-secrets",
        description: Optional[str] = None,
    ) -> AccessPolicy:
        """Generate a Dagster pipeline access policy.

        Args:
            base_path: Base path for secrets
            name: Policy name
            description: Optional description

        Returns:
            AccessPolicy model for Dagster pipelines
        """
        return AccessPolicy(
            name=name,
            description=description or "Dagster pipeline access to secrets",
            rules=[
                PolicyRule(
                    path=f"{base_path}/{{app,database,api,storage}}/*",
                    capabilities=["read", "list"],
                    description="Read access to app, database, api, and storage secrets",
                ),
                PolicyRule(
                    path=f"{base_path}/{{app,database,api,storage}}",
                    capabilities=["list"],
                    description="List secret categories",
                ),
                PolicyRule(
                    path=f"{base_path}/{{app,database,api,storage}}/*",
                    capabilities=["read"],
                    description="Read secrets in all categories",
                ),
            ],
        )

    def generate_admin_policy(
        self,
        base_path: str = "secret/data/bike-data-flow",
        name: str = "admin-secrets",
        description: Optional[str] = None,
    ) -> AccessPolicy:
        """Generate an admin access policy with full permissions.

        Args:
            base_path: Base path for secrets
            name: Policy name
            description: Optional description

        Returns:
            AccessPolicy model with full admin access
        """
        return AccessPolicy(
            name=name,
            description=description or "Full admin access to all secrets",
            rules=[
                PolicyRule(
                    path=f"{base_path}/*",
                    capabilities=["create", "read", "update", "delete", "list", "sudo"],
                    description="Full access to all secrets",
                ),
                PolicyRule(
                    path=f"{base_path}",
                    capabilities=["create", "read", "update", "delete", "list"],
                    description="Full access at root level",
                ),
            ],
        )
