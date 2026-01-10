#!/bin/bash
# Vault Health Check Script
# Verifies Vault server health and returns exit codes for monitoring
#
# Usage:
#   ./health-check.sh [--verbose] [--check-tls]
#
# Exit Codes:
#   0 - Healthy
#   1 - Warning (minor issues)
#   2 - Critical (Vault not responding)
#   3 - Unsealed or needs attention
#   4 - TLS/certificate issues

set -euo pipefail

# Configuration
VAULT_ADDR="${VAULT_ADDR:-https://127.0.0.1:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-}"
CERT_PATH="${CERT_PATH:-/etc/vault.d/tls/vault.crt}"
TIMEOUT="${TIMEOUT:-10}"
VERBOSE="${VERBOSE:-false}"

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

# Counters
PASSED=0
WARNINGS=0
CRITICAL=0

# Output functions
pass() {
    ((PASSED++))
    if [ "$VERBOSE" = "true" ]; then
        echo -e "[${GREEN}PASS${NC}] $*"
    fi
}

warn() {
    ((WARNINGS++))
    echo -e "[${YELLOW}WARN${NC}] $*"
}

crit() {
    ((CRITICAL++))
    echo -e "[${RED}FAIL${NC}] $*"
}

info() {
    if [ "$VERBOSE" = "true" ]; then
        echo -e "[INFO] $*"
    fi
}

# Check 1: Process Status
check_process() {
    info "Checking Vault process..."
    
    local pid
    pid=$(pgrep -x vault 2>/dev/null || echo "")
    
    if [ -n "$pid" ]; then
        pass "Vault process is running (PID: $pid)"
        return 0
    else
        crit "Vault process is not running"
        return 2
    fi
}

# Check 2: API Responsiveness
check_api() {
    info "Checking Vault API..."
    
    local response
    local http_code
    
    response=$(curl -sf \
        --connect-timeout "$TIMEOUT" \
        --max-time "$TIMEOUT" \
        -w "\n%{http_code}" \
        "${VAULT_ADDR}/v1/sys/health" 2>/dev/null) || true
    
    http_code=$(echo "$response" | tail -n1)
    
    case $http_code in
        200)
            pass "Vault API is responsive (HTTP 200)"
            return 0
            ;;
        429)
            warn "Vault API rate limiting (HTTP 429)"
            return 1
            ;;
        472|473)
            crit "Vault data recovery in progress (HTTP $http_code)"
            return 2
            ;;
        501)
            crit "Vault not initialized (HTTP 501)"
            return 3
            ;;
        503)
            crit "Vault is sealed (HTTP 503)"
            return 3
            ;;
        000)
            crit "Vault API not responding (timeout)"
            return 2
            ;;
        *)
            crit "Vault API returned unexpected status: HTTP $http_code"
            return 2
            ;;
    esac
}

# Check 3: TLS Certificate Validity
check_tls() {
    info "Checking TLS certificate..."
    
    if [ ! -f "$CERT_PATH" ]; then
        crit "TLS certificate not found at $CERT_PATH"
        return 4
    fi
    
    local expiry
    local days_remaining
    
    expiry=$(openssl x509 -enddate -noout -in "$CERT_PATH" 2>/dev/null | cut -d= -f2)
    
    if [ -z "$expiry" ]; then
        crit "Failed to parse TLS certificate"
        return 4
    fi
    
    local expiry_epoch
    local now_epoch
    
    expiry_epoch=$(date -d "$expiry" +%s)
    now_epoch=$(date +%s)
    
    days_remaining=$(( (expiry_epoch - now_epoch) / 86400 ))
    
    if [ $days_remaining -lt 0 ]; then
        crit "TLS certificate has expired"
        return 4
    elif [ $days_remaining -lt 7 ]; then
        warn "TLS certificate expires in $days_remaining days"
        return 1
    elif [ $days_remaining -lt 30 ]; then
        warn "TLS certificate expires in $days_remaining days (renew soon)"
        return 1
    else
        pass "TLS certificate is valid ($days_remaining days remaining)"
        return 0
    fi
}

# Check 4: Seal Status
check_seal_status() {
    info "Checking seal status..."
    
    local response
    local sealed
    
    response=$(curl -sf \
        --connect-timeout "$TIMEOUT" \
        --max-time "$TIMEOUT" \
        "${VAULT_ADDR}/v1/sys/health" 2>/dev/null)
    
    if [ -z "$response" ]; then
        crit "Failed to get seal status"
        return 2
    fi
    
    sealed=$(echo "$response" | jq -r '.sealed' 2>/dev/null || echo "unknown")
    
    case $sealed in
        false)
            pass "Vault is unsealed and ready"
            return 0
            ;;
        true)
            crit "Vault is sealed - requires unseal keys"
            return 3
            ;;
        *)
            warn "Seal status unknown: $sealed"
            return 1
            ;;
    esac
}

# Check 5: Authentication (if token provided)
check_auth() {
    info "Checking authentication..."
    
    if [ -z "$VAULT_TOKEN" ]; then
        info "No VAULT_TOKEN provided - skipping auth check"
        return 0
    fi
    
    local response
    
    response=$(curl -sf \
        --connect-timeout "$TIMEOUT" \
        --max-time "$TIMEOUT" \
        -H "X-Vault-Token: $VAULT_TOKEN" \
        "${VAULT_ADDR}/v1/auth/token/lookup-self" 2>/dev/null)
    
    if [ -n "$response" ]; then
        if echo "$response" | grep -q "errors"; then
            crit "Token validation failed"
            return 2
        else
            pass "Token is valid"
            return 0
        fi
    else
        warn "Token validation failed (no response)"
        return 1
    fi
}

# Check 6: Version Information
check_version() {
    info "Checking version..."
    
    local response
    
    response=$(curl -sf \
        --connect-timeout "$TIMEOUT" \
        --max-time "$TIMEOUT" \
        "${VAULT_ADDR}/v1/sys/version" 2>/dev/null)
    
    if [ -z "$response" ]; then
        warn "Could not retrieve version information"
        return 1
    fi
    
    local version
    version=$(echo "$response" | jq -r '.data.version // "unknown"')
    
    pass "Vault version: $version"
    return 0
}

# Print summary
print_summary() {
    echo ""
    echo "========================================"
    echo "  Vault Health Check Summary"
    echo "========================================"
    echo "  Vault Address: ${VAULT_ADDR}"
    echo "  Checks Passed: ${PASSED}"
    echo "  Warnings:      ${WARNINGS}"
    echo "  Critical:      ${CRITICAL}"
    echo "========================================"
    
    if [ $CRITICAL -gt 0 ]; then
        echo -e "  Status: ${RED}CRITICAL${NC}"
        return 2
    elif [ $WARNINGS -gt 0 ]; then
        echo -e "  Status: ${YELLOW}WARNING${NC}"
        return 1
    else
        echo -e "  Status: ${GREEN}HEALTHY${NC}"
        return 0
    fi
}

# Usage
usage() {
    echo "Vault Health Check Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --verbose     Show all check outputs"
    echo "  --check-tls   Only run TLS certificate check"
    echo "  -h, --help    Show this help"
    echo ""
    echo "Environment Variables:"
    echo "  VAULT_ADDR    Vault server address (default: https://127.0.0.1:8200)"
    echo "  VAULT_TOKEN   Token for authentication check"
    echo "  CERT_PATH     Path to TLS certificate"
    echo "  TIMEOUT       Request timeout in seconds (default: 10)"
}

# Main function
main() {
    local only_tls=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --verbose)
                VERBOSE="true"
                shift
                ;;
            --check-tls)
                only_tls=true
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
    
    echo "========================================"
    echo "  Vault Health Check"
    echo "========================================"
    echo "  Time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  Vault: ${VAULT_ADDR}"
    echo "========================================"
    echo ""
    
    if [ "$only_tls" = "true" ]; then
        check_tls
        exit $?
    fi
    
    # Run all checks
    check_process || true
    check_api || true
    check_tls || true
    check_seal_status || true
    check_auth || true
    check_version || true
    
    print_summary
}

main "$@"
