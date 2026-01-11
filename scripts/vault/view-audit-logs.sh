#!/bin/bash
# Vault Audit Log Viewer
# Script for viewing and filtering Vault audit logs
#
# Usage:
#   ./view-audit-logs.sh [OPTIONS]
#
# Options:
#   -f, --file FILE       Path to audit log file or directory
#   -o, --operation TYPE  Filter by operation type (read, write, delete, list, deny)
#   -p, --path PATTERN    Filter by path pattern (regex)
#   -a, --actor ACTOR     Filter by actor (token accessor)
#   -s, --success BOOL    Filter by success status (true/false)
#   -t, --time-range      Filter by time range
#   --from DATE           Start date (ISO format)
#   --to DATE             End date (ISO format)
#   --search TEXT         Search in log content
#   --format FORMAT       Output format: text, json, table (default: text)
#   --limit N             Limit output lines (default: 100)
#   --tail                Real-time log tailing
#   --stats               Show log statistics
#   --anomalies           Detect and show anomalies
#   -h, --help            Show this help message
#
# Examples:
#   ./view-audit-logs.sh -f /var/log/vault/audit.log
#   ./view-audit-logs.sh -f /var/log/vault/ -o read -a token-xxx
#   ./view-audit-logs.sh -f /var/log/vault/ --format table --limit 50
#   ./view-audit-logs.sh -f /var/log/vault/ --anomalies
#   ./view-audit-logs.sh -f /var/log/vault/ --tail

set -euo pipefail

# Default configuration
AUDIT_LOG_PATH="${AUDIT_LOG_PATH:-/var/log/vault/audit.log}"
OUTPUT_FORMAT="text"
LIMIT=100
TAIL_MODE=false
STATS_MODE=false
ANOMALIES_MODE=false

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Function to show help
show_help() {
    head -32 "$0" | tail -26
    exit 0
}

# Function to print colored output
print_status() {
    local status="$1"
    local message="$2"
    case "$status" in
        success) echo -e "${GREEN}[✓]${NC} $message" ;;
        error)   echo -e "${RED}[✗]${NC} $message" ;;
        warning) echo -e "${YELLOW}[!]${NC} $message" ;;
        info)    echo -e "${BLUE}[i]${NC} $message" ;;
        *)       echo "[$status] $message" ;;
    esac
}

# Function to parse JSON with jq (if available)
parse_json() {
    local file="$1"
    local query="$2"
    if command -v jq &> /dev/null; then
        jq -r "$query" "$file"
    else
        print_status warning "jq not installed, using basic parsing"
        grep -o "$query" "$file" | head -1 || echo ""
    fi
}

# Function to filter logs by operation
filter_by_operation() {
    local logs="$1"
    local operation="$2"
    echo "$logs" | grep -i "\"operation\":.*\"$operation\"" || true
}

# Function to filter logs by path
filter_by_path() {
    local logs="$1"
    local pattern="$2"
    echo "$logs" | grep -E "$pattern" || true
}

# Function to filter logs by actor
filter_by_actor() {
    local logs="$1"
    local actor="$2"
    echo "$logs" | grep -i "\"actor\".*\"$actor\"" || true
}

# Function to filter logs by success status
filter_by_success() {
    local logs="$1"
    local success="$2"
    if [ "$success" = "true" ]; then
        echo "$logs" | grep -v '"success": false' || true
    else
        echo "$logs" | grep '"success": false' || true
    fi
}

# Function to format output based on type
format_output() {
    local input="$1"
    local format="$2"

    case "$format" in
        json)
            echo "$input"
            ;;
        table)
            # Convert to a formatted table
            echo "$input" | python3 -c "
import json
import sys

print(f'{'Timestamp':<28} | {'S':<2} | {'Operation':<12} | {'Actor':<20} | {'Path'}')
print('-' * 100)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
        ts = data.get('timestamp', '')[:19]
        op = data.get('operation', '')
        actor = data.get('actor', '')[:20]
        path = data.get('path', '')
        success = '✓' if data.get('success') else '✗'
        print(f'{ts} | {success} | {op:<12} | {actor:<20} | {path}')
    except:
        pass
" 2>/dev/null || echo "$input"
            ;;
        *)
            # Default text format
            echo "$input" | python3 -c "
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
        ts = data.get('timestamp', '')[:19]
        op = data.get('operation', '')
        actor = data.get('actor', '')[:16]
        path = data.get('path', '')
        success = '✓' if data.get('success') else '✗'
        print(f'{ts} | {success} | {op:<12} | {actor:<16} | {path}')
    except:
        print(line)
" 2>/dev/null || echo "$input"
            ;;
    esac
}

# Function to show statistics
show_statistics() {
    local file="$1"
    print_status info "Audit Log Statistics"
    echo "================================================"

    if command -v jq &> /dev/null; then
        local total=$(wc -l < "$file" | tr -d ' ')
        local successful=$(jq 'select(.success == true)' "$file" | wc -l | tr -d ' ')
        local failed=$(jq 'select(.success == false)' "$file" | wc -l | tr -d ' ')

        echo -e "${BOLD}Total Entries:${NC} $total"
        echo -e "${GREEN}Successful:${NC} $successful"
        echo -e "${RED}Failed:${NC} $failed"

        echo ""
        echo -e "${BOLD}Operations by Type:${NC}"
        jq -r '.operation' "$file" | sort | uniq -c | sort -rn | head -10

        echo ""
        echo -e "${BOLD}Top Actors:${NC}"
        jq -r '.actor' "$file" | sort | uniq -c | sort -rn | head -10

        echo ""
        echo -e "${BOLD}Top Paths:${NC}"
        jq -r '.path' "$file" | sort | uniq -c | sort -rn | head -10
    else
        print_status warning "jq required for full statistics"
        echo "Total lines: $(wc -l < "$file")"
    fi
}

# Function to detect anomalies
detect_anomalies() {
    local file="$1"
    print_status info "Anomaly Detection"
    echo "================================================"

    if ! command -v jq &> /dev/null; then
        print_status error "jq is required for anomaly detection"
        return 1
    fi

    # Check for excessive failures
    echo -e "${BOLD}Failed Operations:${NC}"
    jq 'select(.success == false)' "$file" | jq -r '.operation, .error_message' | paste - - | head -20

    # Check for denied access
    local denied=$(jq 'select(.operation == "deny")' "$file" | jq -s 'length')
    if [ "$denied" -gt 0 ]; then
        echo ""
        print_status warning "Found $denied denied access attempts"
        jq 'select(.operation == "deny")' "$file" | jq -r '.path, .actor' | paste - - | head -10
    fi

    # Check for access to sensitive paths
    echo ""
    echo -e "${BOLD}Sensitive Path Access:${NC}"
    jq 'select(.path | test("production|admin|sys|policies"))' "$file" | jq -r '.timestamp, .path, .actor' | paste - - - | head -10

    # Check for unusual hours (2 AM - 5 AM)
    echo ""
    echo -e "${BOLD}Operations During Off-Hours (2 AM - 5 AM):${NC}"
    jq 'select(.timestamp |.[11:13] | tonumber | . >= 2 and . < 5)' "$file" | jq -r '.timestamp, .operation, .actor' | paste - - - | head -10
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--file)
            AUDIT_LOG_PATH="$2"
            shift 2
            ;;
        -o|--operation)
            OPERATION_FILTER="$2"
            shift 2
            ;;
        -p|--path)
            PATH_PATTERN="$2"
            shift 2
            ;;
        -a|--actor)
            ACTOR_FILTER="$2"
            shift 2
            ;;
        -s|--success)
            SUCCESS_FILTER="$2"
            shift 2
            ;;
        --from)
            FROM_DATE="$2"
            shift 2
            ;;
        --to)
            TO_DATE="$2"
            shift 2
            ;;
        --search)
            SEARCH_TEXT="$2"
            shift 2
            ;;
        --format)
            OUTPUT_FORMAT="$2"
            shift 2
            ;;
        --limit)
            LIMIT="$2"
            shift 2
            ;;
        --tail)
            TAIL_MODE=true
            shift
            ;;
        --stats)
            STATS_MODE=true
            shift
            ;;
        --anomalies)
            ANOMALIES_MODE=true
            shift
            ;;
        -h|--help)
            show_help
            ;;
        *)
            print_status error "Unknown option: $1"
            show_help
            ;;
    esac
done

# Validate audit log path
if [ ! -e "$AUDIT_LOG_PATH" ]; then
    print_status error "Audit log path not found: $AUDIT_LOG_PATH"
    exit 1
fi

# Read all logs
if [ -d "$AUDIT_LOG_PATH" ]; then
    # Directory - read all log files
    LOGS=$(find "$AUDIT_LOG_PATH" -name "*.log*" -type f -exec cat {} \; 2>/dev/null || true)
else
    # Single file
    LOGS=$(cat "$AUDIT_LOG_PATH" 2>/dev/null || true)
fi

if [ -z "$LOGS" ]; then
    print_status warning "No audit log entries found"
    exit 0
fi

# Apply filters
if [ -n "${OPERATION_FILTER:-}" ]; then
    LOGS=$(filter_by_operation "$LOGS" "$OPERATION_FILTER")
fi

if [ -n "${PATH_PATTERN:-}" ]; then
    LOGS=$(filter_by_path "$LOGS" "$PATH_PATTERN")
fi

if [ -n "${ACTOR_FILTER:-}" ]; then
    LOGS=$(filter_by_actor "$LOGS" "$ACTOR_FILTER")
fi

if [ -n "${SUCCESS_FILTER:-}" ]; then
    LOGS=$(filter_by_success "$LOGS" "$SUCCESS_FILTER")
fi

if [ -n "${SEARCH_TEXT:-}" ]; then
    LOGS=$(echo "$LOGS" | grep -i "$SEARCH_TEXT" || true)
fi

# Show statistics if requested
if [ "$STATS_MODE" = true ]; then
    # Save filtered logs to temp file for statistics
    TEMP_FILE=$(mktemp)
    echo "$LOGS" > "$TEMP_FILE"
    show_statistics "$TEMP_FILE"
    rm -f "$TEMP_FILE"
    exit 0
fi

# Detect anomalies if requested
if [ "$ANOMALIES_MODE" = true ]; then
    # Save filtered logs to temp file for anomaly detection
    TEMP_FILE=$(mktemp)
    echo "$LOGS" > "$TEMP_FILE"
    detect_anomalies "$TEMP_FILE"
    rm -f "$TEMP_FILE"
    exit 0
fi

# Real-time tail mode
if [ "$TAIL_MODE" = true ]; then
    if [ -f "$AUDIT_LOG_PATH" ]; then
        print_status info "Tailing audit log: $AUDIT_LOG_PATH"
        tail -n +1 -f "$AUDIT_LOG_PATH" | while IFS= read -r line; do
            echo "$line" | python3 -c "
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
        ts = data.get('timestamp', '')[:19]
        op = data.get('operation', '')
        actor = data.get('actor', '')[:16]
        path = data.get('path', '')
        success = '✓' if data.get('success') else '✗'
        print(f'{ts} | {success} | {op:<12} | {actor:<16} | {path}')
    except:
        print(line)
" 2>/dev/null || echo "$line"
        done
    else
        print_status error "Tail mode only works with single files, not directories"
        exit 1
    fi
    exit 0
fi

# Format and display output
echo "$LOGS" | format_output - "$OUTPUT_FORMAT" | head -n "$LIMIT"

# Show count if limited
TOTAL_LINES=$(echo "$LOGS" | wc -l)
if [ "$TOTAL_LINES" -gt "$LIMIT" ]; then
    echo ""
    print_status info "Showing $LIMIT of $TOTAL_LINES entries"
fi
