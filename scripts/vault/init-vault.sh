#!/bin/bash
# Vault Initialization Script
# Initializes a new Vault cluster or resumes from existing unseal keys
#
# Usage:
#   ./init-vault.sh [--existing-keys] [--key-shares N] [--key-threshold M]

set -euo pipefail

# Configuration
VAULT_ADDR="${VAULT_ADDR:-https://127.0.0.1:8200}"
KEY_SHARES="${KEY_SHARES:-5}"
KEY_THRESHOLD="${KEY_THRESHOLD:-3}"
SECRETS_DIR="${SECRETS_DIR:-/var/lib/vault/secrets}"
LOG_FILE="${SECRETS_DIR}/init-$(date +%Y%m%d-%H%M%S).log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Logging function
log() {
    local level="$1"
    shift
    local message="$*"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${timestamp} [${level}] ${message}" | tee -a "$LOG_FILE"
}

info() { log "INFO" "$*"; }
warn() { log "${YELLOW}WARN${NC}" "$*"; }
error() { log "${RED}ERROR${NC}" "$*"; }
success() { log "${GREEN}SUCCESS${NC}" "$*"; }

# Check prerequisites
check_prerequisites() {
    info "Checking prerequisites..."
    
    if ! command -v vault &> /dev/null; then
        error "Vault CLI not found. Please install Vault first."
        exit 1
    fi
    
    mkdir -p "$SECRETS_DIR"
    chmod 700 "$SECRETS_DIR"
    
    success "Prerequisites check passed"
}

# Check if Vault is already initialized
check_initialized() {
    info "Checking if Vault is already initialized..."
    
    local status
    status=$(curl -sf "${VAULT_ADDR}/v1/sys/health" | jq -r '.initialized')
    
    if [ "$status" = "true" ]; then
        warn "Vault is already initialized!"
        read -p "Do you want to continue anyway? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            info "Aborting initialization."
            exit 0
        fi
    fi
    
    success "Vault is not initialized"
}

# Initialize Vault
initialize_vault() {
    info "Initializing Vault with ${KEY_SHARES} key shares and threshold ${KEY_THRESHOLD}..."
    
    local output
    output=$(vault operator init \
        -address="$VAULT_ADDR" \
        -key-shares="$KEY_SHARES" \
        -key-threshold="$KEY_THRESHOLD" \
        -format=json 2>&1)
    
    if [ $? -ne 0 ]; then
        error "Failed to initialize Vault: $output"
        exit 1
    fi
    
    # Parse JSON output
    local unseal_keys_b64
    local root_token
    unseal_keys_b64=$(echo "$output" | jq -r '.unseal_keys_b64[]')
    root_token=$(echo "$output" | jq -r '.root_token')
    
    echo ""
    echo "========================================"
    echo "  VAULT INITIALIZATION COMPLETE"
    echo "========================================"
    echo ""
    echo "ROOT TOKEN: ${root_token}"
    echo ""
    echo "UNSEAL KEYS:"
    
    # Store secrets securely
    local key_index=1
    local timestamp=$(date +%Y%m%d-%H%M%S)
    
    echo "$unseal_keys_b64" | while read -r key; do
        local key_file="${SECRETS_DIR}/unseal-key-${key_index}-${timestamp}.txt"
        echo "$key" > "$key_file"
        chmod 600 "$key_file"
        echo "  Key ${key_index}: ${key} -> ${key_file}"
        ((key_index++))
    done
    
    # Store root token securely
    local token_file="${SECRETS_DIR}/root-token-${timestamp}.txt"
    echo "$root_token" > "$token_file"
    chmod 600 "$token_file"
    
    echo ""
    echo "========================================"
    warn "IMPORTANT: Securely store all unseal keys and root token!"
    
    export VAULT_TOKEN="$root_token"
    success "Vault initialization complete"
    return 0
}

# Unseal Vault with existing keys
unseal_with_existing_keys() {
    info "Unsealing Vault with existing keys..."
    
    local keys=()
    local key_count=0
    
    echo ""
    echo "Enter unseal keys (press Enter after each key, Ctrl+D when done):"
    
    while read -r -p "Key $((key_count + 1)): " key; do
        if [ -n "$key" ]; then
            keys+=("$key")
            ((key_count++))
        fi
    done
    
    if [ ${#keys[@]} -lt $KEY_THRESHOLD ]; then
        error "Not enough unseal keys provided"
        exit 1
    fi
    
    for key in "${keys[@]}"; do
        local result
        result=$(vault operator unseal -address="$VAULT_ADDR" "$key" 2>&1)
        
        if echo "$result" | grep -q "Sealed: false"; then
            success "Vault unsealed successfully"
            return 0
        fi
    done
    
    error "Failed to unseal Vault"
    exit 1
}

# Check seal status
check_seal_status() {
    info "Checking Vault seal status..."
    
    local seal_status
    seal_status=$(curl -sf "${VAULT_ADDR}/v1/sys/health" | jq -r '.sealed')
    
    if [ "$seal_status" = "false" ]; then
        success "Vault is unsealed and ready"
        return 0
    else
        warn "Vault is sealed. Unsealing required."
        return 1
    fi
}

# Print usage
usage() {
    echo "Vault Initialization Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --existing-keys   Use existing unseal keys"
    echo "  --key-shares N    Number of unseal keys (default: 5)"
    echo "  --key-threshold M Minimum keys to unseal (default: 3)"
    echo "  --check           Only check seal status"
    echo "  -h, --help        Show this help"
}

# Main function
main() {
    local mode="init"
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --existing-keys)
                mode="unseal-existing"
                shift
                ;;
            --key-shares)
                KEY_SHARES="$2"
                shift 2
                ;;
            --key-threshold)
                KEY_THRESHOLD="$2"
                shift 2
                ;;
            --check)
                check_seal_status
                exit $?
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
    
    echo "========================================"
    echo "  Vault Initialization Script"
    echo "========================================"
    echo ""
    info "VAULT_ADDR: ${VAULT_ADDR}"
    echo ""
    
    case $mode in
        init)
            check_prerequisites
            check_initialized
            initialize_vault
            ;;
        unseal-existing)
            check_prerequisites
            unseal_with_existing_keys
            ;;
    esac
}

main "$@"
