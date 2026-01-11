"""Environment variable detector for identifying secrets in .env files.

This module parses .env files and environment variables to identify
secret-like variables, categorizes them by sensitivity level, and
generates a report of environment variables to migrate to Vault.

Supported formats: .env, .env.local, .env.production, .env.development

Usage:
    python -m wrm_pipeline.wrm_pipeline.migration.detect_env [--path PATH] [--output OUTPUT]

Example:
    python -m wrm_pipeline.wrm_pipeline.migration.detect_env --path /path/to/project --output env_report.json
"""

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class SensitivityLevel(Enum):
    """Sensitivity levels for environment variables."""
    CRITICAL = "critical"  # Passwords, API keys, tokens
    HIGH = "high"          # Database credentials, encryption keys
    MEDIUM = "medium"      # Configuration values that could be sensitive
    LOW = "low"            # Non-sensitive configuration
    UNKNOWN = "unknown"


@dataclass
class EnvVarFinding:
    """Represents an environment variable finding."""
    name: str
    value: str
    source_file: str
    sensitivity: SensitivityLevel
    category: str
    recommended_vault_path: str
    has_value: bool = True


@dataclass
class EnvDetectionResult:
    """Result of environment variable detection."""
    variables: list[EnvVarFinding] = field(default_factory=list)
    files_processed: int = 0
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "summary": {
                "files_processed": self.files_processed,
                "total_variables": len(self.variables),
                "by_sensitivity": {
                    s.value: len([v for v in self.variables if v.sensitivity == s])
                    for s in SensitivityLevel
                }
            },
            "variables": [
                {
                    "name": v.name,
                    "source_file": v.source_file,
                    "sensitivity": v.sensitivity.value,
                    "category": v.category,
                    "recommended_vault_path": v.recommended_vault_path,
                    "has_value": v.has_value,
                }
                for v in self.variables
            ]
        }


# Patterns to identify secret-like variable names
SECRET_PATTERNS = {
    SensitivityLevel.CRITICAL: [
        r"^SECRET$",
        r"^SECRET_",
        r"^PRIVATE_",
        r"^PRIVATE_KEY",
        r"^API_SECRET",
        r"^ACCESS_SECRET",
        r"^JWT_SECRET",
        r"^SESSION_SECRET",
        r"^ENCRYPTION_KEY",
        r"^ENCRYPTION_IV",
    ],
    SensitivityLevel.HIGH: [
        r"^PASSWORD$",
        r"^PASSWORD_",
        r"^PASSWD$",
        r"^PWD$",
        r"^DB_PASSWORD",
        r"^POSTGRES_PASSWORD",
        r"^MYSQL_PASSWORD",
        r"^DATABASE_PASSWORD",
        r"^ROOT_PASSWORD",
        r"^API_KEY$",
        r"^API_KEY_",
        r"^API_TOKEN",
        r"^AUTH_TOKEN",
        r"^BEARER_TOKEN",
        r"^ACCESS_TOKEN",
        r"^REFRESH_TOKEN",
        r"^VAULT_",
        r"^CREDENTIAL",
        r"^CREDENTIALS",
    ],
    SensitivityLevel.MEDIUM: [
        r"^TOKEN$",
        r"^TOKEN_",
        r"^KEY$",
        r"^KEY_",
        r"^CERT$",
        r"^CERT_",
        r"^CERTIFICATE",
        r"^SALT$",
        r"^HMAC_",
        r"^SIGNATURE",
        r"^SECURE_",
        r"^ADMIN_",
        r"^OWNER_",
    ],
    SensitivityLevel.LOW: [
        r"^DEBUG",
        r"^LOG_LEVEL",
        r"^ENVIRONMENT",
        r"^ENV$",
        r"^MODE$",
        r"^STAGE$",
        r"^VERSION",
        r"^TIMEOUT",
        r"^RETRIES",
        r"^PORT$",
        r"^HOST$",
        r"^ENDPOINT",
        r"^URL$",
        r"^URI$",
    ],
}

# Category mappings
CATEGORY_MAPPING = {
    "database": ["DB_", "POSTGRES_", "MYSQL_", "SQL_", "DATABASE_"],
    "storage": ["S3_", "AWS_", "MINIO_", "STORAGE_"],
    "api": ["API_", "TOKEN", "KEY", "AUTH"],
    "vault": ["VAULT_"],
    "logging": ["LOG_", "LOGGING_"],
    "config": ["CONFIG_", "CFG_"],
    "general": [],
}


class EnvDetector:
    """Detector for environment variables containing secrets."""
    
    # Environment file patterns to scan
    ENV_FILE_PATTERNS = [
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        ".env.staging",
        ".env.test",
        ".env.testing",
    ]
    
    def __init__(self, base_path: Optional[str] = None):
        """Initialize the environment variable detector.
        
        Args:
            base_path: Base directory to scan. Defaults to current working directory.
        """
        self.base_path = Path(base_path) if base_path else Path.cwd()
    
    def _determine_sensitivity(self, var_name: str) -> SensitivityLevel:
        """Determine the sensitivity level of an environment variable.
        
        Args:
            var_name: Name of the environment variable.
            
        Returns:
            SensitivityLevel enum value.
        """
        upper_name = var_name.upper()
        
        for level, patterns in SECRET_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, upper_name):
                    return level
        
        return SensitivityLevel.UNKNOWN
    
    def _determine_category(self, var_name: str) -> str:
        """Determine the category of an environment variable.
        
        Args:
            var_name: Name of the environment variable.
            
        Returns:
            Category string.
        """
        upper_name = var_name.upper()
        
        for category, prefixes in CATEGORY_MAPPING.items():
            for prefix in prefixes:
                if upper_name.startswith(prefix):
                    return category
        
        return "general"
    
    def _get_vault_path(self, var_name: str, category: str) -> str:
        """Get the recommended Vault path for an environment variable.
        
        Args:
            var_name: Name of the environment variable.
            category: Category of the variable.
            
        Returns:
            Recommended Vault path.
        """
        # Map category to Vault path structure
        path_mapping = {
            "database": "database",
            "storage": "storage",
            "api": "api",
            "vault": "vault",
            "logging": "logging",
            "config": "config",
            "general": "app",
        }
        
        vault_category = path_mapping.get(category, "app")
        
        # Convert var name to snake_case for Vault key
        key = var_name.lower()
        
        return f"bike-data-flow/{vault_category}/{key}"
    
    def _parse_env_file(self, file_path: Path) -> dict[str, str]:
        """Parse an environment file.
        
        Args:
            file_path: Path to the .env file.
            
        Returns:
            Dictionary of variable names to values.
        """
        variables = {}
        
        if not file_path.exists():
            return variables
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse KEY=VALUE format
                    if '=' in line:
                        parts = line.split('=', 1)
                        key = parts[0].strip()
                        value = parts[1].strip()
                        
                        # Remove quotes if present
                        if (value.startswith('"') and value.endswith('"')) or \
                           (value.startswith("'") and value.endswith("'")):
                            value = value[1:-1]
                        
                        if key:
                            variables[key] = value
        except (IOError, OSError) as e:
            print(f"Warning: Could not read {file_path}: {e}")
        
        return variables
    
    def scan_env_files(self, directory: Optional[Path] = None) -> EnvDetectionResult:
        """Scan for environment files and detect secrets.
        
        Args:
            directory: Directory to scan. Defaults to base path.
            
        Returns:
            EnvDetectionResult with all findings.
        """
        directory = directory or self.base_path
        result = EnvDetectionResult()
        
        # Find all .env files
        env_files = []
        for pattern in self.ENV_FILE_PATTERNS:
            env_files.extend(directory.glob(f"**/{pattern}"))
        
        # Also check the base path itself
        for pattern in self.ENV_FILE_PATTERNS:
            base_file = directory / pattern
            if base_file.exists() and base_file not in env_files:
                env_files.append(base_file)
        
        # Deduplicate
        env_files = list(set(env_files))
        
        for env_file in sorted(env_files):
            variables = self._parse_env_file(env_file)
            
            for var_name, var_value in variables.items():
                sensitivity = self._determine_sensitivity(var_name)
                category = self._determine_category(var_name)
                vault_path = self._get_vault_path(var_name, category)
                
                finding = EnvVarFinding(
                    name=var_name,
                    value=var_value,
                    source_file=str(env_file),
                    sensitivity=sensitivity,
                    category=category,
                    recommended_vault_path=vault_path,
                    has_value=bool(var_value),
                )
                
                result.variables.append(finding)
            
            if variables:
                result.files_processed += 1
        
        return result
    
    def detect_current_env(self) -> EnvDetectionResult:
        """Detect secrets from current process environment.
        
        Returns:
            EnvDetectionResult with all findings.
        """
        result = EnvDetectionResult()
        
        # Common secret environment variable names
        secret_var_patterns = [
            "SECRET", "PASSWORD", "API_KEY", "TOKEN", "CREDENTIAL",
            "VAULT_ADDR", "VAULT_TOKEN", "VAULT_ROLE_ID", "VAULT_SECRET_ID",
            "AWS_ACCESS_KEY", "AWS_SECRET_KEY", "DATABASE_URL",
        ]
        
        for var_name, var_value in os.environ.items():
            for pattern in secret_var_patterns:
                if pattern in var_name.upper():
                    sensitivity = self._determine_sensitivity(var_name)
                    category = self._determine_category(var_name)
                    vault_path = self._get_vault_path(var_name, category)
                    
                    finding = EnvVarFinding(
                        name=var_name,
                        value=var_value,
                        source_file="process_environment",
                        sensitivity=sensitivity,
                        category=category,
                        recommended_vault_path=vault_path,
                        has_value=bool(var_value),
                    )
                    
                    result.variables.append(finding)
                    break
        
        result.files_processed = 1
        return result
    
    def generate_report(self, result: EnvDetectionResult, output_path: Optional[str] = None) -> str:
        """Generate a report of the environment variable detection.
        
        Args:
            result: EnvDetectionResult to report on.
            output_path: Path to write report to (optional).
            
        Returns:
            Report as a string.
        """
        report_lines = [
            "=" * 60,
            "ENVIRONMENT VARIABLE DETECTION REPORT",
            "=" * 60,
            f"Files Processed: {result.files_processed}",
            f"Total Variables Found: {len(result.variables)}",
            "-" * 60,
        ]
        
        # Group by sensitivity
        by_sensitivity = {}
        for finding in result.variables:
            by_sensitivity.setdefault(finding.sensitivity, []).append(finding)
        
        for level in [SensitivityLevel.CRITICAL, SensitivityLevel.HIGH, 
                      SensitivityLevel.MEDIUM, SensitivityLevel.LOW, 
                      SensitivityLevel.UNKNOWN]:
            if level in by_sensitivity:
                findings = by_sensitivity[level]
                report_lines.append(f"\n{level.value.upper()} SENSITIVITY ({len(findings)} variables):")
                report_lines.append("-" * 40)
                
                for finding in sorted(findings, key=lambda x: x.name):
                    value_preview = finding.value[:20] + "..." if len(finding.value) > 20 else finding.value
                    report_lines.append(f"  Name: {finding.name}")
                    report_lines.append(f"  Source: {finding.source_file}")
                    report_lines.append(f"  Category: {finding.category}")
                    report_lines.append(f"  Vault Path: {finding.recommended_vault_path}")
                    report_lines.append(f"  Has Value: {finding.has_value}")
                    report_lines.append("")
        
        report_lines.append("=" * 60)
        report_lines.append("END OF REPORT")
        report_lines.append("=" * 60)
        
        report = "\n".join(report_lines)
        
        if output_path:
            with open(output_path, 'w') as f:
                f.write(report)
            print(f"Report written to: {output_path}")
        
        return report


def main():
    """Main entry point for the environment variable detector."""
    parser = argparse.ArgumentParser(
        description="Detect secrets in environment variables and .env files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Scan .env files in current directory
    python -m wrm_pipeline.wrm_pipeline.migration.detect_env
    
    # Scan specific directory
    python -m wrm_pipeline.wrm_pipeline.migration.detect_env --path /path/to/project
    
    # Output JSON report
    python -m wrm_pipeline.wrm_pipeline.migration.detect_env --output env_report.json --json env_data.json
    
    # Include current process environment
    python -m wrm_pipeline.wrm_pipeline.migration.detect_env --include-process
        """
    )
    
    parser.add_argument(
        "--path", "-p",
        default=None,
        help="Base directory to scan (default: current working directory)"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file path for text report"
    )
    parser.add_argument(
        "--json",
        default=None,
        help="Output file path for JSON report"
    )
    parser.add_argument(
        "--include-process", "-i",
        action="store_true",
        help="Include current process environment variables"
    )
    
    args = parser.parse_args()
    
    detector = EnvDetector(args.path)
    
    # Scan .env files
    result = detector.scan_env_files()
    
    # Include process environment if requested
    if args.include_process:
        process_result = detector.detect_current_env()
        # Merge findings (avoiding duplicates)
        existing_names = {v.name for v in result.variables}
        for finding in process_result.variables:
            if finding.name not in existing_names:
                result.variables.append(finding)
                existing_names.add(finding.name)
    
    # Generate and display report
    report = detector.generate_report(result, args.output)
    print(report)
    
    # Generate JSON report if requested
    if args.json:
        with open(args.json, 'w') as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"\nJSON report written to: {args.json}")
    
    return 0


if __name__ == "__main__":
    exit(main())
