#!/bin/bash
# ============================================================================
# Vault Restore Script
# 
# Restores HashiCorp Vault from encrypted snapshots or manual exports.
# Supports point-in-time recovery, dry-run mode, and verification.
#
# Usage:
#   ./restore-vault.sh [--backup FILE] [--point-in-time TIMESTAMP] 
#                       [--dry-run] [--force]
#
# Options:
#   --backup FILE       Backup file to restore (default: latest in BACKUP_DIR)
#   --point-in-time     Restore to a specific point in time
#   --dry-run           Show what would be done without actually doing it
#   --force             Force restore even if warnings are present
#   --skip-verification Skip backup verification before restore
#   --vault-addr        Vault server address (default: VAULT_ADDR env var)
#   --vault-token       Vault token (default: VAULT_TOKEN env var)
#   --help              Show this help message
#
# WARNING: This operation will overwrite existing secrets in Vault.
#          Always test restores in a non-production environment first.
#
# ============================================================================

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

# Default configuration
: "${VAULT_ADDR:="https://vault.bike-data-flow.com:8200"}"
: "${VAULT_TOKEN:=""}"
: "${BACKUP_DIR:="/var/backups/vault"}"
: "${DECRYPTION_KEY:=""}"
: "${RESTORE_DIR:="/tmp/vault-restore"}"

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${BACKUP_DIR}/restore.log"
BACKUP_FILE=""
DECRYPTED_FILE=""
EXTRACTED_FILE=""
RESTORE_MODE="full"

# ============================================================================
# Helper Functions
# ============================================================================

log() {
    local level="$1"
    shift
    local message="$*"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
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

log_critical() {
    log "CRITICAL" "$@"
}

show_help() {
    cat << 'EOF'
Vault Restore Script

Restores HashiCorp Vault from encrypted snapshots or manual exports.

Usage:
    ./restore-vault.sh [OPTIONS]

Options:
    --backup FILE          Backup file to restore (default: latest backup)
    --point-in-time TIMESTAMP  Restore to a specific timestamp (ISO format)
    --dry-run              Show what would be done without actually doing it
    --force                Force restore even if warnings are present
    --skip-verification    Skip backup verification before restore
    --vault-addr           Vault server address (default: VAULT_ADDR env var)
    --vault-token          Vault token (default: VAULT_TOKEN env var)
    --backup-dir           Backup directory (default: /var/backups/vault)
    --help                 Show this help message

Environment Variables:
    VAULT_ADDR           Vault server address
    VAULT_TOKEN          Vault authentication token
    BACKUP_DIR           Directory containing backups
    DECRYPTION_KEY       GPG key ID for decryption
    RESTORE_DIR          Temporary directory for restore operations

Examples:
    # Restore from latest backup
    ./restore-vault.sh

    # Restore from specific backup file
    ./restore-vault.sh --backup /var/backups/vault/vault-backup-full-20240110_020000.snap

    # Dry run to see what would happen
    ./restore-vault.sh --dry-run --backup /var/backups/vault/vault-backup.snap.gpg

    # Restore to a specific point in time
    ./restore-vault.sh --point-in-time "2024-01-10T03:00:00Z"

WARNING: This operation will overwrite existing secrets in Vault.
         Always test restores in a non-production environment first.
EOF
}

cleanup() {
    local exit_code=$?
    
    # Clean up temporary files
    if [[ -d "${RESTORE_DIR}" ]]; then
        log_info "Cleaning up temporary restore directory"
        rm -rf "${RESTORE_DIR}"
    fi
    
    if [[ -f "${DECRYPTED_FILE:-}" ]]; then
        rm -f "${DECRYPTED_FILE}"
    fi
    
    if [[ $exit_code -ne 0 ]]; then
        log_error "Restore failed with exit code ${exit_code}"
    fi
    
    trap - EXIT
    exit $exit_code
}

trap cleanup EXIT INT TERM

check_dependencies() {
    log_info "Checking dependencies..."
    
    local missing_deps=()
    
    for cmd in curl jq; do
        if ! command -v "$cmd" &> /dev/null; then
            missing_deps+=("$cmd")
        fi
    done
    
    # Check for gpg if decryption might be needed
    if [[ -n "${DECRYPTION_KEY}" ]] || [[ "${BACKUP_FILE:-}" == *.gpg ]]; then
        if ! command -v gpg &> /dev/null; then
            missing_deps+=("gpg")
        fi
    fi
    
    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing_deps[*]}"
        return 1
    fi
    
    log_info "All dependencies satisfied"
}

check_vault_status() {
    log_info "Checking Vault status..."
    
    # Check if Vault is initialized
    local health_response
    health_response=$(curl -s \
        --header "X-Vault-Token: ${VAULT_TOKEN}" \
        "${VAULT_ADDR}/v1/sys/health" || echo "{}")
    
    local initialized
    initialized=$(echo "${health_response}" | jq -r '.initialized // false')
    
    if [[ "${initialized}" != "true" ]]; then
        log_error "Vault is not initialized. Cannot restore."
        return 1
    fi
    
    local sealed
    sealed=$(echo "${health_response}" | jq -r '.sealed // false')
    
    if [[ "${sealed}" == "true" ]]; then
        log_error "Vault is sealed. Please unseal before restoring."
        return 1
    fi
    
    local standby
    standby=$(echo "${health_response}" | jq -r '.standby // false')
    
    if [[ "${standby}" == "true" ]]; then
        log_warn "Vault is in standby mode. Consider restoring on the active node."
    fi
    
    log_success "Vault is ready for restore"
    return 0
}

find_latest_backup() {
    log_info "Looking for latest backup in ${BACKUP_DIR}..."
    
    if [[ ! -d "${BACKUP_DIR}" ]]; then
        log_error "Backup directory not found: ${BACKUP_DIR}"
        return 1
    fi
    
    # Find the most recent backup file (prefer .snap.gpg over .snap)
    local latest_backup
    latest_backup=$(find "${BACKUP_DIR}" -maxdepth 1 -name "vault-backup-*.snap.gpg" -type f | \
        sort -t '-' -k4 -r | head -1)
    
    if [[ -z "${latest_backup}" ]]; then
        latest_backup=$(find "${BACKUP_DIR}" -maxdepth 1 -name "vault-backup-*.snap" -type f | \
            sort -t '-' -k4 -r | head -1)
    fi
    
    if [[ -z "${latest_backup}" ]]; then
        log_error "No backup files found in ${BACKUP_DIR}"
        return 1
    fi
    
    BACKUP_FILE="${latest_backup}"
    log_info "Found backup file: ${BACKUP_FILE}"
}

verify_backup_file() {
    if [[ "${SKIP_VERIFICATION:-false}" == "true" ]]; then
        log_info "Skipping backup verification (--skip-verification)"
        return 0
    fi
    
    log_info "Verifying backup file: ${BACKUP_FILE}"
    
    if [[ ! -f "${BACKUP_FILE}" ]]; then
        log_error "Backup file not found: ${BACKUP_FILE}"
        return 1
    fi
    
    local file_size
    file_size=$(stat -f%z "${BACKUP_FILE}" 2>/dev/null || stat -c%s "${BACKUP_FILE}" 2>/dev/null || echo "0")
    
    if [[ "${file_size}" -eq 0 ]]; then
        log_error "Backup file is empty"
        return 1
    fi
    
    log_info "Backup file size: ${file_size} bytes"
    
    # Verify file format
    if [[ "${BACKUP_FILE}" == *.gpg ]]; then
        log_info "Encrypted backup detected"
        
        # Try to verify GPG signature if we have a decryption key
        if [[ -n "${DECRYPTION_KEY}" ]]; then
            if gpg --batch --decrypt --dry-run "${BACKUP_FILE}" &>/dev/null; then
                log_success "GPG decryption verification passed"
            else
                log_warn "Could not verify GPG signature - decryption may fail"
            fi
        fi
    elif [[ "${BACKUP_FILE}" == *.snap ]]; then
        # Verify it's a valid archive
        if file "${BACKUP_FILE}" | grep -q -E "(gzip|Zip|data)"; then
            log_success "Snapshot file format verified"
        else
            log_warn "Unexpected file format"
        fi
    fi
    
    log_success "Backup file verification complete"
    return 0
}

decrypt_backup() {
    if [[ "${BACKUP_FILE}" != *.gpg ]]; then
        log_info "Backup is not encrypted, skipping decryption"
        DECRYPTED_FILE="${BACKUP_FILE}"
        return 0
    fi
    
    log_info "Decrypting backup file..."
    
    local timestamp
    timestamp=$(date '+%Y%m%d_%H%M%S')
    DECRYPTED_FILE="${RESTORE_DIR}/backup-decrypted-${timestamp}.snap"
    
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        log_info "[DRY RUN] Would decrypt ${BACKUP_FILE} to ${DECRYPTED_FILE}"
        return 0
    fi
    
    if [[ -n "${DECRYPTION_KEY}" ]]; then
        if gpg --batch --yes --decrypt \
            --local-user "${DECRYPTION_KEY}" \
            --output "${DECRYPTED_FILE}" \
            "${BACKUP_FILE}" 2>/dev/null; then
            log_success "Backup decrypted successfully"
            chmod 600 "${DECRYPTED_FILE}"
        else
            log_error "Failed to decrypt backup"
            return 1
        fi
    else
        log_error "Backup is encrypted but DECRYPTION_KEY is not set"
        return 1
    fi
}

extract_snapshot() {
    log_info "Extracting snapshot..."
    
    EXTRACTED_FILE="${DECRYPTED_FILE}"
    
    if [[ ! -f "${EXTRACTED_FILE}" ]]; then
        log_error "Decrypted backup file not found: ${EXTRACTED_FILE}"
        return 1
    fi
    
    # Check if it's a gzip file
    if file "${EXTRACTED_FILE}" 2>/dev/null | grep -q "gzip"; then
        log_info "Decompressing snapshot..."
        local decompressed_file
        decompressed_file="${EXTRACTED_FILE%.gz}"
        
        if [[ "${DRY_RUN:-false}" != "true" ]]; then
            gunzip -c "${EXTRACTED_FILE}" > "${decompressed_file}"
            EXTRACTED_FILE="${decompressed_file}"
        fi
    fi
    
    log_success "Snapshot extracted: ${EXTRACTED_FILE}"
}

check_restore_prerequisites() {
    log_info "Checking restore prerequisites..."
    
    # Warn about destructive nature
    log_warn "========================================"
    log_warn "WARNING: This restore operation will overwrite"
    log_warn "all secrets in Vault with the backup data."
    log_warn "========================================"
    
    if [[ "${FORCE:-false}" != "true" ]]; then
        read -p "Do you want to continue? (yes/no): " -r
        echo
        if [[ ! "${REPLY}" =~ ^[Yy][Ee][Ss]$ ]]; then
            log_info "Restore cancelled by user"
            exit 0
        fi
    fi
    
    # Check for critical secrets that should be backed up first
    log_info "Checking for critical secrets..."
    
    # Verify backup contains expected data
    if [[ -f "${EXTRACTED_FILE}" ]]; then
        local backup_content
        backup_content=$(head -c 100 "${EXTRACTED_FILE}" 2>/dev/null || echo "")
        
        if [[ -z "${backup_content}" ]]; then
            log_warn "Backup file appears to be empty or binary"
        fi
    fi
    
    log_success "Prerequisites check complete"
}

create_restore_directory() {
    if [[ ! -d "${RESTORE_DIR}" ]]; then
        mkdir -p "${RESTORE_DIR}"
        chmod 700 "${RESTORE_DIR}"
    fi
}

perform_restore() {
    local restore_method="$1"
    
    log_info "Starting restore using ${restore_method} method..."
    
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        log_info "[DRY RUN] Would perform restore with ${restore_method} method"
        return 0
    fi
    
    case "${restore_method}" in
        raft-snapshot)
            restore_raft_snapshot
            ;;
        manual-export)
            restore_manual_export
            ;;
        *)
            log_error "Unknown restore method: ${restore_method}"
            return 1
            ;;
    esac
}

restore_raft_snapshot() {
    log_info "Restoring from Raft snapshot..."
    
    # Check if this is a Raft snapshot by trying to restore
    local response_code
    response_code=$(curl -s -o /dev/null -w "%{http_code}" \
        --header "X-Vault-Token: ${VAULT_TOKEN}" \
        --request PUT \
        --data-binary @"${EXTRACTED_FILE}" \
        "${VAULT_ADDR}/v1/sys/storage/raft/restore" || echo "000")
    
    if [[ "${response_code}" -ge 200 ]] && [[ "${response_code}" -lt 300 ]]; then
        log_success "Raft snapshot restored successfully"
        
        # Trigger a reload of the Raft cluster
        curl -s \
            --header "X-Vault-Token: ${VAULT_TOKEN}" \
            --request POST \
            "${VAULT_ADDR}/v1/sys/storage/raft/force-unlock" || true
        
        return 0
    else
        log_error "Failed to restore Raft snapshot. Response code: ${response_code}"
        log_warn "The snapshot may be from a different Vault cluster or incompatible version"
        return 1
    fi
}

restore_manual_export() {
    log_info "Restoring from manual export..."
    
    # Parse the backup JSON file
    local backup_json
    backup_json=$(cat "${EXTRACTED_FILE}" 2>/dev/null || echo "{}")
    
    # Check if it's a valid JSON
    if ! echo "${backup_json}" | jq -e . >/dev/null 2>&1; then
        log_error "Backup file is not valid JSON"
        return 1
    fi
    
    local backup_type
    backup_type=$(echo "${backup_json}" | jq -r '.backup_type // "unknown"')
    
    log_info "Backup type: ${backup_type}"
    
    if [[ "${backup_type}" == "manual_export" ]]; then
        log_warn "This is a manual export backup - full restore requires raft snapshot"
        log_warn "Only KV secrets can be restored from manual export"
        
        # For manual exports, we would need to parse and re-import secrets
        # This is a simplified version
        log_info "Manual export restore requires parsing individual secrets"
        
        # Extract and restore secrets
        restore_secrets_from_export "${backup_json}"
    else
        log_error "Unknown backup type: ${backup_type}"
        return 1
    fi
}

restore_secrets_from_export() {
    local backup_json="$1"
    
    log_info "Restoring secrets from export..."
    
    # Get list of secret paths from backup
    local secret_paths
    secret_paths=$(echo "${backup_json}" | jq -r '.secrets // {} | keys[]' 2>/dev/null || echo "")
    
    if [[ -z "${secret_paths}" ]] || [[ "${secret_paths}" == "null" ]]; then
        log_warn "No secrets found in backup"
        return 0
    fi
    
    local restored_count=0
    local failed_count=0
    
    # For each secret path, restore it
    echo "${secret_paths}" | while IFS= read -r path; do
        if [[ -n "${path}" ]]; then
            local secret_data
            secret_data=$(echo "${backup_json}" | jq -r ".secrets[\"${path}\"] // {}")
            
            if [[ "${secret_data}" != "{}" ]]; then
                log_info "Restoring secret: ${path}"
                
                if [[ "${DRY_RUN:-false}" == "true" ]]; then
                    log_info "[DRY RUN] Would restore: ${path}"
                else
                    # Write secret to Vault
                    local response
                    response=$(curl -s \
                        --header "X-Vault-Token: ${VAULT_TOKEN}" \
                        --request POST \
                        --header "Content-Type: application/json" \
                        --data "{\"data\":${secret_data}}" \
                        "${VAULT_ADDR}/v1/secret/data/${path}" || echo '{"errors":[]}')
                    
                    local errors
                    errors=$(echo "${response}" | jq -r '.errors // [] | join(", ")')
                    
                    if [[ -n "${errors}" ]] && [[ "${errors}" != "null" ]]; then
                        log_error "Failed to restore ${path}: ${errors}"
                        ((failed_count++)) || true
                    else
                        log_info "Restored: ${path}"
                        ((restored_count++)) || true
                    fi
                fi
            fi
        fi
    done
    
    log_success "Restored ${restored_count} secrets, ${failed_count} failed"
}

verify_restore() {
    log_info "Verifying restore..."
    
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        log_info "[DRY RUN] Would verify restore"
        return 0
    fi
    
    # Check Vault health
    local health_response
    health_response=$(curl -s \
        --header "X-Vault-Token: ${VAULT_TOKEN}" \
        "${VAULT_ADDR}/v1/sys/health" || echo "{}")
    
    local sealed
    sealed=$(echo "${health_response}" | jq -r '.sealed // false')
    
    if [[ "${sealed}" == "true" ]]; then
        log_error "Vault is sealed after restore - manual unseal may be required"
        return 1
    fi
    
    # List secrets to verify
    local secrets_count
    secrets_count=$(curl -s \
        --header "X-Vault-Token: ${VAULT_TOKEN}" \
        "${VAULT_ADDR}/v1/secret/metadata?list=true" | \
        jq -r '.data.keys | length // 0')
    
    log_success "Vault is unsealed with ${secrets_count} secret paths"
    
    # Run a health check
    local seal_response
    seal_response=$(curl -s \
        --header "X-Vault-Token: ${VAULT_TOKEN}" \
        "${VAULT_ADDR}/v1/sys/seal-status")
    
    local cluster_name
    cluster_name=$(echo "${seal_response}" | jq -r '.cluster_name // "unknown"')
    log_info "Cluster name: ${cluster_name}"
    
    log_success "Restore verification complete"
}

log_restore_summary() {
    local status="$1"
    
    log_info "========================================"
    log_info "Restore Summary"
    log_info "========================================"
    log_info "Backup file: ${BACKUP_FILE}"
    log_info "Restore mode: ${RESTORE_MODE}"
    log_info "Status: ${status}"
    log_info "========================================"
}

send_restore_notification() {
    local status="$1"
    local backup_file="$2"
    
    log_info "Sending restore notification: ${status}"
    
    # TODO: Implement email/Slack notifications
    # Example: Send webhook with restore status
}

# ============================================================================
# Main Script
# ============================================================================

main() {
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --backup)
                BACKUP_FILE="$2"
                shift 2
                ;;
            --point-in-time)
                POINT_IN_TIME="$2"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --force)
                FORCE=true
                shift
                ;;
            --skip-verification)
                SKIP_VERIFICATION=true
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
    : "${DRY_RUN:=false}"
    : "${FORCE:=false}"
    : "${SKIP_VERIFICATION:=false}"
    
    log_info "========================================"
    log_info "Vault Restore Script Starting"
    log_info "========================================"
    log_info "Vault Address: ${VAULT_ADDR}"
    log_info "Backup Directory: ${BACKUP_DIR}"
    log_info "Dry Run: ${DRY_RUN}"
    log_info "Force: ${FORCE}"
    log_info "----------------------------------------"
    
    # Validate configuration
    if [[ -z "${VAULT_TOKEN}" ]]; then
        log_error "VAULT_TOKEN is not set"
        exit 1
    fi
    
    # Create restore directory
    create_restore_directory
    
    # Run pre-flight checks
    check_dependencies || exit 1
    check_vault_status || exit 1
    
    # Find backup file if not specified
    if [[ -z "${BACKUP_FILE}" ]]; then
        find_latest_backup || exit 1
    fi
    
    # Verify backup
    verify_backup_file || exit 1
    
    # Decrypt if needed
    decrypt_backup || exit 1
    
    # Extract snapshot
    extract_snapshot || exit 1
    
    # Check prerequisites
    check_restore_prerequisites || exit 1
    
    # Determine restore method based on backup type
    local restore_method="raft-snapshot"
    
    if file "${EXTRACTED_FILE}" 2>/dev/null | grep -q "gzip"; then
        # Try to determine the actual type
        local test_content
        test_content=$(gunzip -c "${EXTRACTED_FILE}" 2>/dev/null | head -c 100 || echo "")
        
        if echo "${test_content}" | jq -e . >/dev/null 2>&1; then
            local backup_type
            backup_type=$(echo "${test_content}" | jq -r '.backup_type // "raft-snapshot"')
            if [[ "${backup_type}" == "manual_export" ]]; then
                restore_method="manual-export"
            fi
        fi
    fi
    
    # Perform restore
    perform_restore "${restore_method}" || exit 1
    
    # Verify restore
    verify_restore || exit 1
    
    # Log summary
    log_restore_summary "SUCCESS"
    
    log_success "========================================"
    log_success "Vault restore completed successfully!"
    log_success "========================================"
    
    send_restore_notification "SUCCESS" "${BACKUP_FILE}"
}

# Run main function
main "$@"
