# Vault Server Configuration Documentation

This document provides the complete configuration for HashiCorp Vault server deployment on the Hetzner VPS.

## Configuration File Location

```
/etc/vault.d/vault.hcl
```

## Complete Vault Configuration

```hcl
# Vault Server Configuration for Hetzner VPS
# Single-node deployment with integrated storage

# Storage Backend - Integrated Raft Storage
storage "raft" {
  path    = "/var/lib/vault/data"
  node_id = "vault-node-1"
}

# Listener Configuration - TLS Required
listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  
  # TLS Configuration
  tls_cert_file = "/etc/vault.d/tls/vault.crt"
  tls_key_file  = "/etc/vault.d/tls/vault.key"
  
  # Disable UI if not needed
  # disable_ui = true
  
  # Maximum request size (MB)
  max_request_size = 33554432
}

# API Address - How clients connect
api_addr = "https://your-vault-server.example.com:8200"

# Cluster Address - For Raft consensus
cluster_addr = "https://your-vault-server.example.com:8201"

# UI Configuration
ui = true

# Disable mlock (required for containers/non-root)
disable_mlock = true

# License (if using Vault Enterprise)
# license_path = "/etc/vault.d/vault.hclic"

# Seal Configuration (for auto-unseal)
# seal "awskms" {
#   kms_key_id = "your-kms-key-id"
#   region     = "us-east-1"
# }

# Telemetry Configuration
telemetry {
  disable_hostname = false
  usage_gauge_period = "10m"
  maximum_gauge_cardinality = 500
  disable_dispatched_telemetry = false
}

# Log Configuration
log_level = "info"
log_format = "standard"

# Disable caching of secrets (security option)
# disable_cache = false

# Raw storage endpoint (disable for security)
# disable_raw = true
```

## Configuration Explanation

### Storage Backend

```hcl
storage "raft" {
  path    = "/var/lib/vault/data"
  node_id = "vault-node-1"
}
```

- **backend**: Raft integrated storage (no external storage needed for single-node)
- **path**: Data directory for Vault storage
- **node_id**: Unique identifier for this Vault node

### Listener Configuration

```hcl
listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_cert_file   = "/etc/vault.d/tls/vault.crt"
  tls_key_file    = "/etc/vault.d/tls/vault.key"
}
```

- **address**: Listen address for client connections
- **cluster_address**: Internal cluster communication address (for Raft)
- **tls_cert_file**: Path to TLS certificate
- **tls_key_file**: Path to TLS private key

### Network Configuration

```hcl
api_addr = "https://your-vault-server.example.com:8200"
cluster_addr = "https://your-vault-server.example.com:8201"
```

- **api_addr**: Public API address for clients
- **cluster_addr**: Cluster address for node-to-node communication

### Security Settings

```hcl
disable_mlock = true
```

- **disable_mlock**: Disable memory locking (required for containers/non-root deployments)
- Set to `false` only if running as root with proper permissions

## Hetzner VPS Specific Configuration

Based on Hetzner deployment requirements:

```hcl
# Optimized for Hetzner VPS (CX21: 2 vCPU, 4GB RAM)
storage "raft" {
  path    = "/var/lib/vault/data"
  node_id = "vault-node-1"
  
  # Performance tuning
  snapshot_interval = "2h"
  max_entry_size = 104857600  # 100MB for large secrets
}

listener "tcp" {
  address         = "127.0.0.1:8200"
  cluster_address = "127.0.0.1:8201"
  
  # Use reverse proxy (nginx/haproxy) in front of Vault
  # for additional security and rate limiting
  tls_cert_file = "/etc/vault.d/tls/vault.crt"
  tls_key_file  = "/etc/vault.d/tls/vault.key"
  
  # Reduce resource usage
  max_request_size = 52428800  # 50MB
}

api_addr = "https://vault.your-domain.com:8200"
cluster_addr = "https://vault.your-domain.com:8201"

# Disable UI for production (use API only)
ui = false

# Disable mlock for container compatibility
disable_mlock = true
```

## Directory Permissions

Create required directories with proper permissions:

```bash
# Create directories
sudo mkdir -p /var/lib/vault/data
sudo mkdir -p /var/log/vault
sudo mkdir -p /etc/vault.d/tls

# Set ownership
sudo chown -R vault:vault /var/lib/vault
sudo chown -R vault:vault /var/log/vault
sudo chown -R vault:vault /etc/vault.d

# Set permissions
sudo chmod 700 /var/lib/vault/data
sudo chmod 700 /var/log/vault
sudo chmod 600 /etc/vault.d/tls/vault.key
sudo chmod 644 /etc/vault.d/tls/vault.crt
```

## Service Configuration

See [`vault.service`](config/vault/vault.service) for systemd service file.

## TLS Configuration

See [`vault-tls-config.md`](docs/vault-tls-config.md) for TLS certificate setup.

## Verification

After configuration, verify with:

```bash
# Check configuration syntax
vault validate /etc/vault.d/vault.hcl

# Check configuration is readable
cat /etc/vault.d/vault.hcl | grep -v "^#" | grep -v "^$"
```

## References

- [Vault Configuration](https://developer.hashicorp.com/vault/docs/configuration)
- [Vault Storage Backend](https://developer.hashicorp.com/vault/docs/configuration/storage/raft)
- [Vault Listener Configuration](https://developer.hashicorp.com/vault/docs/configuration/listener/tcp)
