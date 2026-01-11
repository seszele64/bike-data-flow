# Vault Audit Device Configuration
# This file configures audit devices for HashiCorp Vault
# Location: /etc/vault.d/audit-devices.hcl
# Include this file in the main Vault configuration with: include "/etc/vault.d/audit-devices.hcl"

# =============================================================================
# File Audit Device
# Logs all audit events to a local file
# =============================================================================
audit {
  device "file" {
    path                  = "/var/log/vault/audit.log"
    log_file              = "/var/log/vault/audit.log"
    log_raw               = true
    hmac_key              = ""  # Optional: Set a shared HMAC key for integrity verification
    format                = "json"
    rotation_interval     = "24h"
    rotation_max_files    = 10
    rotation_file_size    = 10240  # 10MB in KB
  }
}

# =============================================================================
# Syslog Audit Device (Optional)
# Logs audit events to the system log for integration with SIEM tools
# Comment out if not needed
# =============================================================================
# audit {
#   device "syslog" {
#     tag                  = "vault"
#     facility             = "AUTH"
#     format               = "json"
#     oddysey_compatible   = false
#   }
# }

# =============================================================================
# Socket Audit Device (Optional)
# For forwarding logs to remote logging services
# Uncomment and configure if needed
# =============================================================================
# audit {
#   device "socket" {
#     address              = "tcp://127.0.0.1:9090"
#     socket_type          = "tcp"
#     format               = "json"
#     write_timeout        = 2
#     connection_timeout   = 2
#   }
# }

# =============================================================================
# Audit Device Options
# =============================================================================

# Default audit device settings
# These apply to all audit devices unless overridden

# Enable auditing of non-HTTP operations (e.g., internal operations)
audit_enable_local = true

# Experimental: Enable audit for wrapped responses
audit_audit_hidden = false

# =============================================================================
# Log Retention Configuration
# =============================================================================
# Note: Vault does not manage log retention internally
# Use external tools like:
#   - logrotate for file-based logs
#   - systemd journal settings for syslog
#   - SIEM tool policies for remote logging
#
# Example logrotate config for /etc/logrotate.d/vault:
#
# /var/log/vault/audit.log {
#     daily
#     rotate 90
#     compress
#     delaycompress
#     missingok
#     notifempty
#     create 0640 root vault
#     sharedscripts
#     postrotate
#         systemctl reload vault > /dev/null 2>&1 || true
#     endscript
# }

# =============================================================================
# Compliance Requirements
# =============================================================================
# For PCI-DSS, SOC 2, HIPAA compliance:
# - Retention: Minimum 90 days, 1 year recommended
# - Access: Restrict access to audit logs to authorized personnel only
# - Integrity: Use HMAC keys to verify log integrity
# - Backup: Include audit logs in backup strategy
# - Monitoring: Integrate with SIEM for real-time alerting
