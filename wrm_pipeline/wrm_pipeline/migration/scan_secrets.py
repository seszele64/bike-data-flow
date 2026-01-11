"""Secret scanner script for detecting hardcoded secrets in the codebase.

This module scans the codebase for patterns that indicate hardcoded secrets,
such as API keys, passwords, connection strings, and other sensitive data.
It outputs a list of locations where secrets are found (NOT the secrets themselves).

Supported file extensions: .py, .env, .yaml, .yml, .json

Usage:
    python -m wrm_pipeline.wrm_pipeline.migration.scan_secrets [--path PATH] [--output OUTPUT]

Example:
    python -m wrm_pipeline.wrm_pipeline.migration.scan_secrets --path /path/to/project --output results.json
"""

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SecretFinding:
    """Represents a potential secret finding in the codebase."""
    file_path: str
    line_number: int
    line_content: str
    pattern_name: str
    pattern_category: str
    severity: str = "high"


@dataclass
class ScanResult:
    """Represents the result of a secret scan."""
    findings: list[SecretFinding] = field(default_factory=list)
    files_scanned: int = 0
    secrets_found: int = 0
    
    def to_dict(self) -> dict:
        """Convert scan result to dictionary for JSON serialization."""
        return {
            "summary": {
                "files_scanned": self.files_scanned,
                "secrets_found": self.secrets_found,
            },
            "findings": [
                {
                    "file_path": f.file_path,
                    "line_number": f.line_number,
                    "pattern_name": f.pattern_name,
                    "pattern_category": f.pattern_category,
                    "severity": f.severity,
                    "line_content_preview": f.line_content[:100] + "..." if len(f.line_content) > 100 else f.line_content,
                }
                for f in self.findings
            ]
        }


# Common secret patterns with regex
SECRET_PATTERNS = [
    # API Keys and Tokens
    (
        "aws_access_key",
        r"(?:AWS_ACCESS_KEY|aws_access_key_id|aws_secret_access_key)[\s]*[=:][\s]*['\"][A-Za-z0-9]{20,}['\"]",
        "credentials",
        "high",
    ),
    (
        "aws_secret_key",
        r"(?:AWS_SECRET_KEY|aws_secret_access_key)[\s]*[=:][\s]*['\"][A-Za-z0-9/+]{40,}['\"]",
        "credentials",
        "high",
    ),
    (
        "api_key_generic",
        r"(?:API_KEY|api_key|API_SECRET|api_secret)[\s]*[=:][\s]*['\"][A-Za-z0-9_\-]{20,}['\"]",
        "credentials",
        "high",
    ),
    (
        "token_generic",
        r"(?:Bearer\s+)?token[\s]*[=:][\s]*['\"][A-Za-z0-9_\-\.]{20,}['\"]",
        "credentials",
        "high",
    ),
    (
        "bearer_token",
        r"Bearer\s+[A-Za-z0-9_\-\.]+",
        "credentials",
        "high",
    ),
    (
        "github_token",
        r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}",
        "credentials",
        "high",
    ),
    (
        "slack_token",
        r"xox[baprs]-([0-9a-zA-Z]{10,48})",
        "credentials",
        "high",
    ),
    (
        "google_api_key",
        r"AIza[0-9A-Za-z\-_]{35}",
        "credentials",
        "high",
    ),
    (
        "private_key",
        r"-----BEGIN\s+(?:RSA\s+)?PRIVATE KEY-----",
        "credentials",
        "high",
    ),
    
    # Passwords
    (
        "password_assignment",
        r"(?:password|passwd|pwd|secret)[\s]*[=:][\s]*['\"][^'\"]{8,}['\"]",
        "credentials",
        "high",
    ),
    (
        "password_in_comment",
        r"#.*password[:\s=]+\S+",
        "credentials",
        "medium",
    ),
    
    # Database Connection Strings
    (
        "postgres_connection",
        r"(?:postgres|postgresql)://[^\s\"']+",
        "database",
        "high",
    ),
    (
        "mysql_connection",
        r"mysql://[^\s\"']+",
        "database",
        "high",
    ),
    (
        "mongodb_connection",
        r"mongodb(?:\+srv)?://[^\s\"']+",
        "database",
        "high",
    ),
    (
        "redis_connection",
        r"redis://[^\s\"']+",
        "database",
        "high",
    ),
    (
        "connection_string",
        r"(?:Driver|SERVER|HOST|DATABASE|UID|PWD)[^=\n]*=[^;\n]+(?:;[^\n]+)*",
        "database",
        "high",
    ),
    
    # Environment Variable Names (potential secrets)
    (
        "env_secret_var",
        r"(?:SECRET|PASSWORD|API_KEY|TOKEN|CREDENTIAL|AUTH)[\s]*[=:][\s]*\${[^}]+}",
        "configuration",
        "medium",
    ),
    (
        "env_var_assignment",
        r"VAULT_[A-Z_]+|SECRET_[A-Z_]+|PASSWORD_[A-Z_]+|API_[A-Z_]+|TOKEN_[A-Z_]+",
        "configuration",
        "low",
    ),
    
    # JWT Tokens
    (
        "jwt_token",
        r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+",
        "credentials",
        "high",
    ),
    
    # Other Sensitive Patterns
    (
        "credit_card",
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})\b",
        "pii",
        "high",
    ),
    (
        "social_security",
        r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b",
        "pii",
        "high",
    ),
    (
        "private_ip",
        r"(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})",
        "configuration",
        "low",
    ),
]


class SecretScanner:
    """Scanner for detecting hardcoded secrets in the codebase."""
    
    # File extensions to scan
    SCAN_EXTENSIONS = {".py", ".env", ".env.local", ".env.production", ".yaml", ".yml", ".json", ".txt", ".cfg", ".conf"}
    
    # Directories and files to ignore
    IGNORE_PATTERNS = {
        ".git",
        ".gitignore",
        ".env",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".eggs",
        "*.egg-info",
        "dist",
        "build",
        "*.pyc",
        "*.pyo",
    }
    
    def __init__(self, base_path: Optional[str] = None):
        """Initialize the secret scanner.
        
        Args:
            base_path: Base directory to scan. Defaults to current working directory.
        """
        self.base_path = Path(base_path) if base_path else Path.cwd()
        self.compiled_patterns = self._compile_patterns()
    
    def _compile_patterns(self) -> list[tuple[re.Pattern, str, str, str]]:
        """Compile regex patterns for efficiency."""
        compiled = []
        for pattern_name, pattern, category, severity in SECRET_PATTERNS:
            try:
                compiled.append((re.compile(pattern, re.IGNORECASE), pattern_name, category, severity))
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{pattern_name}': {e}")
        return compiled
    
    def _should_ignore_path(self, path: Path) -> bool:
        """Check if a path should be ignored.
        
        Args:
            path: Path to check.
            
        Returns:
            True if the path should be ignored.
        """
        path_str = str(path)
        for pattern in self.IGNORE_PATTERNS:
            if pattern.startswith("*"):
                # Extension pattern
                if path.suffix == pattern[1:]:
                    return True
            elif pattern in path_str:
                return True
            # Check if path is within an ignored directory
            for part in path.parts:
                if part == pattern or part.startswith(pattern):
                    return True
        return False
    
    def _is_scannable_file(self, file_path: Path) -> bool:
        """Check if a file should be scanned.
        
        Args:
            file_path: Path to the file.
            
        Returns:
            True if the file should be scanned.
        """
        return file_path.suffix in self.SCAN_EXTENSIONS
    
    def scan_file(self, file_path: Path) -> list[SecretFinding]:
        """Scan a single file for secrets.
        
        Args:
            file_path: Path to the file to scan.
            
        Returns:
            List of secret findings in the file.
        """
        findings = []
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except (IOError, OSError) as e:
            logger.debug(f"Could not read file {file_path}: {e}")
            return findings
        
        for line_num, line in enumerate(lines, start=1):
            line_content = line.rstrip('\n\r')
            
            for pattern, pattern_name, category, severity in self.compiled_patterns:
                if pattern.search(line_content):
                    # Check if this is likely a false positive (e.g., variable name, not actual value)
                    if self._is_false_positive(line_content, pattern_name):
                        continue
                    
                    findings.append(SecretFinding(
                        file_path=str(file_path),
                        line_number=line_num,
                        line_content=line_content,
                        pattern_name=pattern_name,
                        pattern_category=category,
                        severity=severity,
                    ))
        
        return findings
    
    def _is_false_positive(self, line_content: str, pattern_name: str) -> bool:
        """Check if a match is likely a false positive.
        
        Args:
            line_content: The line content that matched.
            pattern_name: The name of the pattern that matched.
            
        Returns:
            True if the match is likely a false positive.
        """
        # Skip lines that are just variable declarations without actual values
        false_positive_patterns = [
            r"(?:export\s+)?(?:API_KEY|PASSWORD|SECRET|TOKEN)[\s]*=",
            r"#.*(?:TODO|FIXME|XXX).*(?:password|secret|key)",
            r"placeholder.*(?:password|secret|key)",
            r"<.*>(?:password|secret|key)",
        ]
        
        for fp_pattern in false_positive_patterns:
            if re.search(fp_pattern, line_content, re.IGNORECASE):
                return True
        
        return False
    
    def scan_directory(self, directory: Optional[Path] = None) -> ScanResult:
        """Scan a directory for secrets.
        
        Args:
            directory: Directory to scan. Defaults to base path.
            
        Returns:
            ScanResult containing all findings.
        """
        directory = directory or self.base_path
        result = ScanResult()
        
        logger.info(f"Scanning directory: {directory}")
        
        for root, dirs, files in os.walk(directory):
            # Modify dirs in-place to skip ignored directories
            dirs[:] = [d for d in dirs if not self._should_ignore_path(Path(root) / d)]
            
            for file in files:
                file_path = Path(root) / file
                
                if self._should_ignore_path(file_path):
                    continue
                
                if not self._is_scannable_file(file_path):
                    continue
                
                result.files_scanned += 1
                findings = self.scan_file(file_path)
                result.findings.extend(findings)
        
        result.secrets_found = len(result.findings)
        return result
    
    def generate_report(self, result: ScanResult, output_path: Optional[str] = None) -> str:
        """Generate a report of the scan results.
        
        Args:
            result: ScanResult to report on.
            output_path: Path to write report to (optional).
            
        Returns:
            Report as a string.
        """
        report_lines = [
            "=" * 60,
            "SECRET SCAN REPORT",
            "=" * 60,
            f"Base Path: {self.base_path}",
            f"Files Scanned: {result.files_scanned}",
            f"Secrets Found: {result.secrets_found}",
            "-" * 60,
        ]
        
        if result.findings:
            # Group findings by severity
            by_severity = {"high": [], "medium": [], "low": []}
            for finding in result.findings:
                by_severity[finding.severity].append(finding)
            
            for severity in ["high", "medium", "low"]:
                findings = by_severity[severity]
                if findings:
                    report_lines.append(f"\n{severity.upper()} SEVERITY FINDINGS ({len(findings)}):")
                    report_lines.append("-" * 40)
                    
                    for finding in findings:
                        report_lines.append(f"  File: {finding.file_path}")
                        report_lines.append(f"  Line: {finding.line_number}")
                        report_lines.append(f"  Pattern: {finding.pattern_name}")
                        report_lines.append(f"  Category: {finding.pattern_category}")
                        report_lines.append(f"  Preview: {finding.line_content[:80]}...")
                        report_lines.append("")
        else:
            report_lines.append("\nNo secrets found!")
        
        report_lines.append("=" * 60)
        report_lines.append("END OF REPORT")
        report_lines.append("=" * 60)
        
        report = "\n".join(report_lines)
        
        if output_path:
            with open(output_path, 'w') as f:
                f.write(report)
            logger.info(f"Report written to: {output_path}")
        
        return report


def main():
    """Main entry point for the secret scanner."""
    parser = argparse.ArgumentParser(
        description="Scan codebase for hardcoded secrets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Scan current directory
    python -m wrm_pipeline.wrm_pipeline.migration.scan_secrets
    
    # Scan specific directory and output JSON
    python -m wrm_pipeline.wrm_pipeline.migration.scan_secrets --path /path/to/project --output results.json
    
    # Scan and output both text and JSON reports
    python -m wrm_pipeline.wrm_pipeline.migration.scan_secrets --path /path/to/project --output report.txt --json results.json
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
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")
    
    # Run scan
    scanner = SecretScanner(args.path)
    result = scanner.scan_directory()
    
    # Generate and display report
    report = scanner.generate_report(result, args.output)
    print(report)
    
    # Generate JSON report if requested
    if args.json:
        with open(args.json, 'w') as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"\nJSON report written to: {args.json}")
    
    # Return exit code based on findings
    if result.secrets_found > 0:
        print(f"\nWARNING: Found {result.secrets_found} potential secrets!")
        return 1
    else:
        print("\nSUCCESS: No secrets found!")
        return 0


if __name__ == "__main__":
    exit(main())
