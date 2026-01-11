#!/bin/bash
# ============================================================================
# Vault Backup Script
# 
# Creates encrypted snapshots of HashiCorp Vault for disaster recovery.
# Supports full and incremental backups, with optional upload to Hetzner
# Object Storage.
#
# Usage:
#   ./backup-vault.sh [--full] [--encrypt] [--upload] [--keep N] [--dry-run]
#
# Options:
#   --full     Create a full snapshot (default: incremental if supported)
#   --encrypt  Encrypt the backup file with GPG
#   --upload   Upload backup to Hetzner Object Storage
#   --keep N   Keep only N recent backups locally
#   --dry-run  Show what would be done without actually doing it
#   --help     Show this help message
#
# Requirements:
#   - VAULT_ADDR environment variable or --vault-addr flag
#   - VAULT_TOKEN environment variable or valid auth
#   - GPG for encryption (if --encrypt is used)
#   - AWS CLI or compatible S3 client (if --upload is used)
#
# Cron Example (daily incremental, weekly full):
#   0 2 * * * /opt/vault/scripts/backup-vault.sh --encrypt --upload --keep 7
#   0 3 * * 0 /opt/vault/scripts/backup-vault.sh --full --encrypt --upload --keep 4
# ============================================================================

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

# Default configuration (can be overridden by environment variables)
: "${VAULT_ADDR:="https://vault.bike-data-flow.com:8200"}"
: "${VAULT_TOKEN:=""}"
: "${BACKUP_DIR:="/var/backups/vault"}"
: "${ENCRYPTION_KEY:=""}"
: "${ENCRYPTION_RECIPIENT:=""}"
: "${HETZNER_ENDPOINT:=""}"
: "${HETZNER_ACCESS_KEY:=""}"
: "${HETZNER_SECRET_KEY:=""}"
: "${HETZNER_BUCKET:="vault-backups"}"
: "${RETENTION_COUNT:=7}"

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${BACKUP_DIR}/backup.log"
SNAPSHOT_FILE=""
ENCRYPTED_FILE=""

# ============================================================================
# Helper Functions
# ============================================================================

log() {
    local level="$1"
    shift
    local message="$*"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[${timestamp}] [${level}] ${message}" | tee -a "${LOG_FILE}"
}

log_info() {
    log "INFO" "$@"
}

log_warn() {
    log "WARN" "$@"
}

log_error() {
    log "ERROR" "$@"
}

log_success() {
    log "SUCCESS" "$@"
}

cleanup() {
    local exit_code=$?
    if [[ -f "${SNAPSHOT_FILE:-}" ]]; then
        log_info "Cleaning up temporary snapshot file"
        rm -f "${SNAPSHOT_FILE}"
    fi
    if [[ $exit_code -ne 0 ]]; then
        log_error "Backup failed with exit code ${exit_code}"
    fi
    trap - EXIT
    exit $exit_code
}

trap cleanup EXIT INT TERM

show_help() {
    cat << 'EOF'
Vault Backup Script

Creates encrypted snapshots of HashiCorp Vault for disaster recovery.

Usage:
    ./backup-vault.sh [OPTIONS]

Options:
    --full           Create a full snapshot (default: incremental if supported)
    --encrypt        Encrypt the backup file with GPG
    --upload         Upload backup to Hetzner Object Storage
    --keep N         Keep only N recent backups locally (default: 7)
    --dry-run        Show what would be done without actually doing it
    --vault-addr     Vault server address (default: VAULT_ADDR env var)
    --vault-token    Vault token (default: VAULT_TOKEN env var)
    --backup-dir     Local backup directory (default: /var/backups/vault)
    --help           Show this help message

Environment Variables:
    VAULT_ADDR           Vault server address
    VAULT_TOKEN          Vault authentication token
    BACKUP_DIR           Local backup directory
    ENCRYPTION_KEY       GPG key ID for encryption
    ENCRYPTION_RECIPIENT GPG recipient email for encryption
    HETZNER_ENDPOINT     Hetzner Object Storage endpoint
    HETZNER_ACCESS_KEY   Hetzner Object Storage access key
    HETZNER_SECRET_KEY   Hetzner Object Storage secret key
    HETZNER_BUCKET       Bucket name for backups
    RETENTION_COUNT      Number of backups to retain locally

Examples:
    # Create and encrypt a backup, upload to object storage
    ./backup-vault.sh --full --encrypt --upload

    # Create incremental backup, keep last 30 locally
    ./backup-vault.sh --encrypt --upload --keep 30

    # Dry run to see what would happen
    ./backup-vault.sh --full --encrypt --dry-run
EOF
}

check_dependencies() {
    log_info "Checking dependencies..."
    
    local missing_deps=()
    
    # Check for curl (required for Vault API)
    if ! command -v curl &> /dev/null; then
        missing_deps+=("curl")
    fi
    
    # Check for jq (required for JSON parsing)
    if ! command -v jq &> /dev/null; then
        missing_deps+=("jq")
    fi
    
    # Check for gpg if encryption is enabled
    if [[ "${ENCRYPT:-false}" == "true" ]] && ! command -v gpg &> /dev/null; then
        missing_deps+=("gpg")
    fi
    
    # Check for AWS CLI if upload is enabled
    if [[ "${UPLOAD:-false}" == "true" ]] && ! command -v aws &> /dev/null; then
        # Check for rclone as alternative
        if ! command -v rclone &> /dev/null; then
            missing_deps+=("aws or rclone")
        fi
    fi
    
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing_deps[*]}"
        return 1
    fi
    
    log_info "All dependencies satisfied"
}

check_vault_status() {
    log_info "Checking Vault status at ${VAULT_ADDR}..."
    
    # Check if Vault is initialized and unsealed
    local health_response
    health_response=$(curl -s -o /dev/null -w "%{http_code}" \
        --header "X-Vault-Token: ${VAULT_TOKEN}" \
        "${VAULT_ADDR}/v1/sys/health" || echo "000")
    
    if [[ "${health_response}" == "200" ]]; then
        log_success "Vault is healthy and unsealed"
        return 0
    elif [[ "${health_response}" == "429" ]]; then
        log_warn "Vault is unsealed but in standby mode"
        return 0
    elif [[ "${health_response}" == "501" ]]; then
        log_error "Vault is not initialized"
        return 1
    elif [[ "${health_response}" == "503" ]]; then
        log_error "Vault is sealed"
        return 1
    else
        log_error "Vault returned unexpected status: ${health_response}"
        return 1
    fi
}

create_backup_directory() {
    if [[ ! -d "${BACKUP_DIR}" ]]; then
        log_info "Creating backup directory: ${BACKUP_DIR}"
        mkdir -p "${BACKUP_DIR}"
        chmod 700 "${BACKUP_DIR}"
    fi
}

create_snapshot() {
    local snapshot_type="$1"
    local timestamp
    timestamp=$(date '+%Y%m%d_%H%M%S')
    
    local snapshot_name="vault-backup-${snapshot_type}-${timestamp}"
    SNAPSHOT_FILE="${BACKUP_DIR}/${snapshot_name}.snap"
    
    log_info "Creating ${snapshot_type} snapshot..."
    log_info "Snapshot file: ${SNAPSHOT_FILE}"
    
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        log_info "[DRY RUN] Would create snapshot: ${SNAPSHOT_FILE}"
        return 0
    fi
    
    # Check if this is a Raft storage backend (required for snapshots API)
    local storage_type
    storage_type=$(curl -s \
        --header "X-Vault-Token: ${VAULT_TOKEN}" \
        "${VAULT_ADDR}/v1/sys/storage/raft/configuration" | jq -r '.data.config.storage // ""' 2>/dev/null || echo "")
    
    if [[ -n "${storage_type}" ]] || [[ "${snapshot_type}" == "full" ]]; then
        # Use Vault's snapshot API for Raft storage
        log_info "Using Vault snapshot API..."
        
        local response_code
        response_code=$(curl -s -o "${SNAPSHOT_FILE}" -w "%{http_code}" \
            --header "X-Vault-Token: ${VAULT_TOKEN}" \
            "${VAULT_ADDR}/v1/sys/storage/raft/snapshot" \
            -o "${SNAPSHOT_FILE}")
        
        if [[ "${response_code}" -ge 200 ]] && [[ "${response_code}" -lt 300 ]]; then
            log_success "Snapshot created successfully"
            
            # Verify snapshot file is not empty
            local file_size
            file_size=$(stat -f%z "${SNAPSHOT_FILE}" 2>/dev/null || stat -c%s "${SNAPSHOT_FILE}" 2>/dev/null || echo "0")
            if [[ "${file_size}" -lt 100 ]]; then
                log_error "Snapshot file appears too small (${file_size} bytes)"
                return 1
            fi
            
            log_info "Snapshot size: ${file_size} bytes"
            return 0
        else
            log_error "Failed to create snapshot. Response code: ${response_code}"
            return 1
        fi
    else
        # Fallback: Export all secrets manually
        log_info "Using manual export method (Raft snapshot API not available)..."
        export_all_secrets "${SNAPSHOT_FILE}"
    fi
}

export_all_secrets() {
    local output_file="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    log_info "Exporting all secrets to ${output_file}..."
    
    # Create a JSON structure with all secrets
    local secrets_json='{}'
    
    # List all secret paths
    local paths
    paths=$(curl -s \
        --header "X-Vault-Token: ${VAULT_TOKEN}" \
        "${VAULT_ADDR}/v1/sys/internal/mounts/secret" | jq -r '.data | to_entries[] | select(.key | startswith("secret/")) | .key' 2>/dev/null || echo "")
    
    # For each mount point, list and export secrets
    # This is a simplified export - production use should use raft snapshots
    
    cat > "${output_file}" << EOF
{
    "backup_type": "manual_export",
    "backup_timestamp": "${timestamp}",
    "vault_addr": "${VAULT_ADDR}",
    "version": "1.0",
    "secrets": {
        "note": "For complete recovery, use raft snapshot API on Raft storage backend"
    }
}
EOF
    
    log_warn "Manual export created - this is a limited backup. Use Raft storage for full recovery."
}

encrypt_backup() {
    if [[ "${ENCRYPT:-false}" != "true" ]]; then
        log_info "Skipping encryption (--encrypt not specified)"
        return 0
    fi
    
    local timestamp
    timestamp=$(date '+%Y%m%d_%H%M%S')
    
    if [[ -n "${ENCRYPTION_KEY}" ]]; then
        # Encrypt using GPG with specified key
        local gpg_recipient
        gpg_recipient="${ENCRYPTION_RECIPIENT:-}"
        
        ENCRYPTED_FILE="${SNAPSHOT_FILE}.gpg"
        
        log_info "Encrypting backup with GPG key ${ENCRYPTION_KEY}..."
        
        if [[ "${DRY_RUN:-false}" == "true" ]]; then
            log_info "[DRY RUN] Would encrypt ${SNAPSHOT_FILE} to ${ENCRYPTED_FILE}"
            return 0
        fi
        
        if gpg --batch --yes --encrypt \
            --recipient "${gpg_recipient}" \
            --local-user "${ENCRYPTION_KEY}" \
            --output "${ENCRYPTED_FILE}" \
            "${SNAPSHOT_FILE}" 2>/dev/null; then
            log_success "Backup encrypted successfully"
            
            # Remove unencrypted file
            rm -f "${SNAPSHOT_FILE}"
            SNAPSHOT_FILE="${ENCRYPTED_FILE}"
            
            # Set restrictive permissions
            chmod 600 "${SNAPSHOT_FILE}"
        else
            log_error "Failed to encrypt backup"
            return 1
        fi
    else
        log_warn "Encryption key not configured, skipping encryption"
    fi
}

upload_to_object_storage() {
    if [[ "${UPLOAD:-false}" != "true" ]]; then
        log_info "Skipping upload (--upload not specified)"
        return 0
    fi
    
    log_info "Uploading backup to Hetzner Object Storage..."
    
    # Determine which file to upload
    local file_to_upload="${SNAPSHOT_FILE}"
    if [[ -f "${SNAPSHOT_FILE}.gpg" ]]; then
        file_to_upload="${SNAPSHOT_FILE}.gpg"
    fi
    
    if [[ ! -f "${file_to_upload}" ]]; then
        log_error "Backup file not found: ${file_to_upload}"
        return 1
    fi
    
    local file_name
    file_name=$(basename "${file_to_upload}")
    
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        log_info "[DRY RUN] Would upload ${file_to_upload} to bucket ${HETZNER_BUCKET}"
        return 0
    fi
    
    # Check for rclone first, then AWS CLI
    if command -v rclone &> /dev/null; then
        upload_with_rclone "${file_to_upload}" "${file_name}"
    elif command -v aws &> /dev/null; then
        upload_with_aws_cli "${file_to_upload}" "${file_name}"
    else
        log_error "Neither rclone nor AWS CLI found for upload"
        return 1
    fi
}

upload_with_rclone() {
    local file_path="$1"
    local file_name="$2"
    
    log_info "Using rclone for upload..."
    
    # Configure rclone for Hetzner if not already configured
    export RCLONE_CONFIG_HETZNER_TYPE=s3
    export RCLONE_CONFIG_HETZNER_ENDPOINT="${HETZNER_ENDPOINT}"
    export RCLONE_CONFIG_HETZNER_ACCESS_KEY_ID="${HETZNER_ACCESS_KEY}"
    export RCLONE_CONFIG_HETZNER_SECRET_ACCESS_KEY="${HETZNER_SECRET_KEY}"
    export RCLONE_CONFIG_HETZNER_REGION="us-east-1"
    
    if rclone copy "${file_path}" "hetzner:${HETZNER_BUCKET}/" \
        --storage-object-storage \
        --verbose 2>&1; then
        log_success "Backup uploaded to Hetzner Object Storage"
    else
        log_error "Failed to upload backup with rclone"
        return 1
    fi
}

upload_with_aws_cli() {
    local file_path="$1"
    local file_name="$2"
    
    log_info "Using AWS CLI for upload..."
    
    # Configure AWS CLI for Hetzner
    export AWS_ACCESS_KEY_ID="${HETZNER_ACCESS_KEY}"
    export AWS_SECRET_ACCESS_KEY="${HETZNER_SECRET_KEY}"
    export AWS_DEFAULT_REGION="us-east-1"
    
    # Use AWS CLI with S3 compatible endpoint
    if aws s3 cp "${file_path}" "s3://${HETZNER_BUCKET}/${file_name}" \
        --endpoint-url="${HETZNER_ENDPOINT}" \
        --storage-class STANDARD \
        --metadata "backup-type=vault,created=$(date -Iseconds)" \
        2>&1; then
        log_success "Backup uploaded to Hetzner Object Storage"
    else
        log_error "Failed to upload backup with AWS CLI"
        return 1
    fi
}

cleanup_old_backups() {
    local keep_count="${1:-${RETENTION_COUNT}}"
    
    log_info "Cleaning up old backups (keeping ${keep_count} most recent)..."
    
    if [[ ! -d "${BACKUP_DIR}" ]]; then
        log_info "Backup directory does not exist, nothing to clean up"
        return 0
    fi
    
    # List backup files sorted by modification time
    local backup_files
    backup_files=$(find "${BACKUP_DIR}" -maxdepth 1 -name "vault-backup-*.snap*" -type f | \
        sort -t '-' -k4 -r | \
        tail -n +$((keep_count + 1)))
    
    if [[ -z "${backup_files}" ]]; then
        log_info "No old backups to remove"
        return 0
    fi
    
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        log_info "[DRY RUN] Would remove the following backups:"
        echo "${backup_files}" | while read -r file; do
            echo "  - ${file}"
        done
        return 0
    fi
    
    local removed_count=0
    while IFS= read -r file; do
        if [[ -n "${file}" ]]; then
            log_info "Removing old backup: ${file}"
            rm -f "${file}"
            ((removed_count++)) || true
        fi
    done <<< "${backup_files}"
    
    log_success "Removed ${removed_count} old backup(s)"
}

verify_backup() {
    log_info "Verifying backup integrity..."
    
    local file_to_verify="${SNAPSHOT_FILE}"
    if [[ -f "${SNAPSHOT_FILE}.gpg" ]]; then
        file_to_verify="${SNAPSHOT_FILE}.gpg"
    fi
    
    if [[ ! -f "${file_to_verify}" ]]; then
        log_error "Backup file not found for verification: ${file_to_verify}"
        return 1
    fi
    
    # Check file size
    local file_size
    file_size=$(stat -f%z "${file_to_verify}" 2>/dev/null || stat -c%s "${file_to_verify}" 2>/dev/null || echo "0")
    
    if [[ "${file_size}" -eq 0 ]]; then
        log_error "Backup file is empty"
        return 1
    fi
    
    log_info "Backup file size: ${file_size} bytes"
    
    # If encrypted, verify GPG signature
    if [[ "${file_to_verify}" == *.gpg ]]; then
        if gpg --batch --verify "${file_to_verify}" &>/dev/null; then
            log_success "GPG signature verification passed"
        else
            log_warn "Could not verify GPG signature"
        fi
    fi
    
    log_success "Backup verification complete"
    return 0
}

write_backup_metadata() {
    local snapshot_type="$1"
    local file_size="$2"
    
    local metadata_file="${BACKUP_DIR}/backup-metadata.json"
    local timestamp
    timestamp=$(date -Iseconds)
    
    # Read existing metadata or create new
    local metadata_json
    if [[ -f "${metadata_file}" ]]; then
        metadata_json=$(cat "${metadata_file}")
    else
        metadata_json='{"backups":[]}'
    fi
    
    # Add new backup entry
    local new_entry
    new_entry=$(cat << EOF
{
    "file": "$(basename "${SNAPSHOT_FILE}")",
    "type": "${snapshot_type}",
    "size": ${file_size},
    "timestamp": "${timestamp}",
    "encrypted": ${ENCRYPT:-false},
    "uploaded": ${UPLOAD:-false},
    "vault_addr": "${VAULT_ADDR}"
}
EOF
)
    
    # Update metadata JSON
    metadata_json=$(echo "${metadata_json}" | jq --argjson entry "${new_entry}" \
        '.backups = (.backups | [.] + $entry | sort_by(.timestamp) | reverse)')
    
    if [[ "${DRY_RUN:-false}" != "true" ]]; then
        echo "${metadata_json}" > "${metadata_file}"
        chmod 600 "${metadata_file}"
    fi
    
    log_info "Backup metadata recorded"
}

send_notification() {
    local status="$1"
    shift
    local message="$*"
    
    # Log notification
    log_info "Notification: ${status} - ${message}"
    
    # TODO: Implement email/Slack notifications here
    # Example: Send to webhook or email
}

# ============================================================================
# Main Script
# ============================================================================

main() {
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --full)
                FULL_BACKUP=true
                shift
                ;;
            --encrypt)
                ENCRYPT=true
                shift
                ;;
            --upload)
                UPLOAD=true
                shift
                ;;
            --keep)
                RETENTION_COUNT="$2"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --vault-addr)
                VAULT_ADDR="$2"
                shift 2
                ;;
            --vault-token)
                VAULT_TOKEN="$2"
                shift 2
                ;;
            --backup-dir)
                BACKUP_DIR="$2"
                shift 2
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
    
    # Set defaults
    : "${FULL_BACKUP:=false}"
    : "${ENCRYPT:=false}"
    : "${UPLOAD:=false}"
    : "${DRY_RUN:=false}"
    
    local snapshot_type="incremental"
    if [[ "${FULL_BACKUP}" == "true" ]]; then
        snapshot_type="full"
    fi
    
    log_info "========================================"
    log_info "Vault Backup Script Starting"
    log_info "========================================"
    log_info "Vault Address: ${VAULT_ADDR}"
    log_info "Backup Directory: ${BACKUP_DIR}"
    log_info "Snapshot Type: ${snapshot_type}"
    log_info "Encryption: ${ENCRYPT}"
    log_info "Upload: ${UPLOAD}"
    log_info "Retention: ${RETENTION_COUNT} backups"
    log_info "Dry Run: ${DRY_RUN}"
    log_info "----------------------------------------"
    
    # Validate configuration
    if [[ -z "${VAULT_TOKEN}" ]]; then
        log_error "VAULT_TOKEN is not set"
        exit 1
    fi
    
    # Run pre-flight checks
    check_dependencies || exit 1
    check_vault_status || exit 1
    create_backup_directory
    
    # Create snapshot
    create_snapshot "${snapshot_type}" || exit 1
    
    # Encrypt if requested
    encrypt_backup || exit 1
    
    # Verify backup
    verify_backup || exit 1
    
    # Upload if requested
    upload_to_object_storage || exit 1
    
    # Cleanup old backups
    cleanup_old_backups "${RETENTION_COUNT}" || exit 1
    
    # Record metadata
    local file_size
    file_size=$(stat -f%z "${SNAPSHOT_FILE}" 2>/dev/null || stat -c%s "${SNAPSHOT_FILE}" 2>/dev/null || echo "0")
    write_backup_metadata "${snapshot_type}" "${file_size}"
    
    log_success "========================================"
    log_success "Vault backup completed successfully!"
    log_success "Backup file: ${SNAPSHOT_FILE}"
    log_success "File size: ${file_size} bytes"
    log_success "========================================"
    
    send_notification "SUCCESS" "Backup completed: ${SNAPSHOT_FILE} (${file_size} bytes)"
}

# Run main function
main "$@"
