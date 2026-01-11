# Deployment Documentation

**Feature**: 001-hashicorp-vault-integration  
**Last Updated**: 2026-01-11

This document covers deployment procedures for the bike-data-flow project with HashiCorp Vault on Hetzner VPS.

---

## Table of Contents

1. [Server Requirements](#server-requirements)
2. [Hetzner VPS Setup](#hetzner-vps-setup)
3. [Vault Server Deployment](#vault-server-deployment)
4. [TLS Certificate Configuration](#tls-certificate-configuration)
5. [Systemd Service Configuration](#systemd-service-configuration)
6. [Application Deployment](#application-deployment)
7. [Monitoring Setup](#monitoring-setup)
8. [Security Hardening](#security-hardening)

---

## Server Requirements

### Minimum Specifications

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 1 vCPU | 2 vCPU |
| RAM | 2 GB | 4 GB |
| Storage | 20 GB SSD | 50 GB SSD |
| Network | 100 Mbps | 1 Gbps |

### Recommended Plan

**Hetzner CCX Series:**
- CCX21: 2 vCPU, 8 GB RAM, 80 GB SSD - €23.99/month
- CCX31: 4 vCPU, 16 GB RAM, 160 GB SSD - €47.98/month

---

## Hetzner VPS Setup

### 1. Initial Server Setup

```bash
# Connect to server
ssh root@your-server-ip

# Update system
apt update && apt upgrade -y

# Create deployment user
adduser deploy
usermod -aG sudo deploy

# Set up SSH key authentication
mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
echo "ssh-ed25519 AAAA... your-key" >> /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh

# Disable password authentication
sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd
```

### 2. Configure Firewall

```bash
# Install UFW
apt install ufw -y

# Set default policies
ufw default deny incoming
ufw default allow outgoing

# Allow SSH
ufw allow ssh
ufw allow 22/tcp

# Allow Vault HTTPS
ufw allow 8200/tcp

# Allow application ports
ufw allow 8000/tcp  # Dagster
ufw allow 3000/tcp  # Dagster UI

# Enable firewall
ufw --force enable
```

### 3. Configure Time Synchronization

```bash
# Install chrony
apt install chrony -y
systemctl enable chrony
systemctl start chrony

# Verify synchronization
chronyc tracking
```

---

## Vault Server Deployment

### 1. Install Vault

```bash
# Download Vault
cd /tmp
wget https://releases.hashicorp.com/vault/1.15.0/vault_1.15.0_linux_amd64.zip
unzip vault_1.15.0_linux_amd64.zip
mv vault /usr/local/bin/
chmod +x /usr/local/bin/vault

# Verify installation
vault --version
```

### 2. Create Required Directories

```bash
# Create data and log directories
mkdir -p /var/lib/vault/data
mkdir -p /var/log/vault
mkdir -p /etc/vault.d
mkdir -p /var/backups/vault

# Set ownership
chown -R vault:vault /var/lib/vault
chown -R vault:vault /var/log/vault
chown -R vault:vault /var/backups/vault
chown -R vault:vault /etc/vault.d

# Set permissions
chmod 700 /var/lib/vault/data
chmod 700 /var/backups/vault
```

### 3. Create Vault Configuration

Create `/etc/vault.d/vault.hcl`:

```hcl
# Storage backend (Raft for single-node)
storage "raft" {
  path = "/var/lib/vault/data"
  node_id = "vault-1"
}

# Listener configuration
listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = "false"
  
  # TLS certificates (from Let's Encrypt)
  tls_cert_file = "/etc/letsencrypt/live/vault.example.com/fullchain.pem"
  tls_key_file  = "/etc/letsencrypt/live/vault.example.com/privkey.pem"
  
  # Performance tuning
  max_request_size     = 33554432  # 32 MB
  max_request_duration = "90s"
}

# API and cluster addresses
api_addr      = "https://vault.example.com:8200"
cluster_addr  = "https://vault.example.com:8201"

# Disable mlock (required for non-root)
disable_mlock = true

# Logging
log_level = "info"
log_format = "json"

# Telemetry
telemetry {
  prometheus_retention_time = "12h"
  disable_hostname = false
}
```

### 4. Initialize Vault

```bash
# Start Vault briefly to initialize
export VAULT_ADDR="https://vault.example.com:8200"
export VAULT_CACERT="/etc/letsencrypt/live/vault.example.com/cert.pem"

# Run initialization (one-time only)
vault operator init \
    -key-shares=5 \
    -key-threshold=3 \
    -format=json > /root/vault-init.json

# Save unseal keys securely!
# Key shards should be distributed to trusted individuals
cat /root/vault-init.json

# Unseal Vault
vault operator unseal <UNSEAL_KEY_1>
vault operator unseal <UNSEAL_KEY_2>
vault operator unseal <UNSEAL_KEY_3>

# Verify status
vault status
```

### 5. Configure Vault

```bash
# secrets engine v2
vault secrets Enable KV enable -path=secret kv-v2

# Enable AppRole authentication
vault auth enable approle

# Create Dagster policy
vault policy write dagster-secrets -<<'EOF'
# Read access to all bike-data-flow secrets
path "secret/data/bike-data-flow/*" {
  capabilities = ["read", "list"]
}

# Read access to health endpoint
path "sys/health" {
  capabilities = ["read"]
}
EOF

# Create AppRole role
vault write auth/approle/role/dagster \
    token_policies="dagster-secrets" \
    token_ttl=1h \
    token_max_ttl=4h

# Get role ID (needed for application)
vault read auth/approle/role/dagster/role-id

# Generate secret ID (NOTE: Can only be retrieved once!)
vault write -f auth/approle/role/dagster/secret-id
```

---

## TLS Certificate Configuration

### Using Let's Encrypt

```bash
# Install Certbot
apt install certbot python3-certbot-apache -y

# Obtain certificate
certbot certonly \
    --manual \
    --preferred-challenges=dns \
    --email admin@example.com \
    --domains "vault.example.com" \
    --agree-tos

# Verify certificates
ls -la /etc/letsencrypt/live/vault.example.com/
# Should contain: cert.pem, chain.pem, fullchain.pem, privkey.pem

# Set up automatic renewal
echo "0 0,12 * * * certbot renew --quiet" | crontab -
```

### Certificate Renewal Hook

Create `/etc/letsencrypt/renewal-hooks/post/reload-vault.sh`:

```bash
#!/bin/bash
systemctl reload vault
```

Make it executable:
```bash
chmod +x /etc/letsencrypt/renewal-hooks/post/reload-vault.sh
```

---

## Systemd Service Configuration

### Create Vault Service

Create `/etc/systemd/system/vault.service`:

```ini
[Unit]
Description=HashiCorp Vault
Documentation=https://developer.hashicorp.com/vault/docs
After=network.target
Requires=network-online.target

[Service]
Type=notify
User=vault
Group=vault
ExecStart=/usr/local/bin/vault server -config=/etc/vault.d/vault.hcl
ExecReload=/bin/kill -HUP $MAINPID
KillMode=process
Restart=on-failure
RestartSec=5
TimeoutStartSec=30
TimeoutStopSec=30

# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/vault /var/log/vault /var/backups/vault
PrivateTmp=yes

# Resource limits
LimitNOFILE=65536
LimitMEMLOCK=infinity

# Environment
Environment=VAULT_ADDR=https://vault.example.com:8200
Environment=VAULT_LOG_FORMAT=json

[Install]
WantedBy=multi-user.target
```

### Enable and Start

```bash
# Reload systemd
systemctl daemon-reload

# Enable service
systemctl enable vault

# Start service
systemctl start vault

# Check status
systemctl status vault

# View logs
journalctl -u vault -f
```

### Auto-Unseal Monitor (Optional)

For automatic unsealing, create `/etc/systemd/system/vault-unseal-monitor.service`:

```ini
[Unit]
Description=Vault Unseal Monitor
After=vault.service

[Service]
Type=oneshot
User=vault
Group=vault
ExecStart=/usr/local/bin/vault-unseal.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

---

## Application Deployment

### 1. Deploy Application

```bash
# Connect as deploy user
ssh deploy@your-server-ip

# Clone repository
git clone https://github.com/your-org/bike-data-flow.git
cd bike-data-flow

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e ".[prod]"

# Copy environment file
cp .env.example .env
# Edit .env with actual values
```

### 2. Configure Environment

Edit `.env`:

```bash
# Enable Vault
VAULT_ENABLED=true
VAULT_ADDR=https://vault.example.com:8200
VAULT_ROLE_ID=your-role-id
VAULT_SECRET_ID=your-secret-id

# Application settings
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO
```

### 3. Set Up Systemd for Dagster

Create `/etc/systemd/system/dagster.service`:

```ini
[Unit]
Description=Dagster Pipeline
After=network.target

[Service]
Type=simple
User=deploy
Group=deploy
WorkingDirectory=/home/deploy/bike-data-flow
Environment="PATH=/home/deploy/bike-data-flow/venv/bin"
Environment="DAGSTER_HOME=/home/deploy/.dagster"
Environment="VAULT_ADDR=https://vault.example.com:8200"
ExecStart=/home/deploy/bike-data-flow/venv/bin/dagster dev

Restart=on-failure
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=dagster

[Install]
WantedBy=multi-user.target
```

### 4. Start Services

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable and start Dagster
sudo systemctl enable dagster
sudo systemctl start dagster

# Check status
sudo systemctl status dagster
sudo systemctl status vault

# View logs
journalctl -u dagster -f
journalctl -u vault -f
```

---

## Monitoring Setup

### 1. Prometheus Metrics

Enable Vault telemetry:

```hcl
# Add to vault.hcl
telemetry {
  prometheus_retention_time = "12h"
  disable_hostname = false
  usage_gauge_period = "5m"
}
```

Reload Vault:
```bash
sudo systemctl reload vault
```

### 2. Health Check Script

Create `/usr/local/bin/vault-health-check.sh`:

```bash
#!/bin/bash

# Check Vault status
STATUS=$(curl -s $VAULT_ADDR/v1/sys/health)

# Parse response
SEALED=$(echo $STATUS | jq -r '.sealed')
INITIALIZED=$(echo $STATUS | jq -r '.initialized')
ACTIVE=$(echo $STATUS | jq -r '.active')

# Alert if sealed
if [ "$SEALED" = "true" ]; then
    echo "CRITICAL: Vault is sealed!"
    exit 2
fi

# Alert if not initialized
if [ "$INITIALIZED" = "false" ]; then
    echo "CRITICAL: Vault is not initialized!"
    exit 2
fi

# Alert if not active
if [ "$ACTIVE" = "false" ]; then
    echo "WARNING: Vault is not active (standby mode)"
    exit 1
fi

echo "OK: Vault is healthy"
exit 0
```

### 3. Log Rotation

Create `/etc/logrotate.d/vault`:

```
/var/log/vault/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 640 vault vault
    sharedscripts
    postrotate
        systemctl reload vault > /dev/null 2>&1 || true
    endscript
}
```

---

## Security Hardening

### 1. SSH Hardening

Edit `/etc/ssh/sshd_config`:

```
Port 22022
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
```

### 2. Fail2Ban

```bash
apt install fail2ban -y

# Configure jail.local
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 3

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 3600

[nginx-http-auth]
enabled = true
filter = nginx-http-auth
port = 8200
logpath = /var/log/nginx/error.log
```

### 3. AppArmor/SELinux

Ensure Vault is properly confined if using AppArmor or SELinux.

### 4. Regular Security Audits

```bash
# Run periodically
vault audit list
vault policy list
vault auth list
```

---

## Backup Configuration

### 1. Automated Backups

Create `/etc/cron.d/vault-backup`:

```
# Daily backup at 2 AM
0 2 * * * root /usr/local/bin/vault-backup.sh >> /var/log/vault/backup.log 2>&1
```

Create `/usr/local/bin/vault-backup.sh`:

```bash
#!/bin/bash

BACKUP_DIR="/var/backups/vault"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/vault-backup-$TIMESTAMP.snap"

# Export VAULT_TOKEN or use AppRole
export VAULT_ADDR="https://vault.example.com:8200"

# Create Raft snapshot
curl -s -X GET \
    -H "X-Vault-Token: $VAULT_TOKEN" \
    $VAULT_ADDR/v1/sys/storage/raft/snapshot \
    > $BACKUP_FILE

# Upload to object storage
# (Add Hetzner Object Storage upload here)

# Cleanup old backups
find $BACKUP_DIR -name "*.snap" -mtime +30 -delete

echo "Backup complete: $BACKUP_FILE"
```

### 2. Verify Backups

```bash
# Test backup restoration periodically
vault-backup-verify.sh
```

---

## Additional Resources

- [Vault Server Configuration](vault-server-config.md)
- [Vault TLS Configuration](vault-tls-config.md)
- [Vault Unseal Management](vault-unseal-management.md)
- [Vault Audit Logging](vault-audit-logging.md)
- [Vault Backup & Recovery](vault-backup-recovery.md)
- [Vault Troubleshooting](vault-troubleshooting.md)
