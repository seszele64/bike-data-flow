# Vault TLS Certificate Configuration

This document describes TLS certificate requirements and configuration for HashiCorp Vault server.

## TLS Requirements

**TLS is required** for all Vault production deployments. Without TLS, Vault will refuse to start.

## Certificate Requirements

### General Requirements

| Property | Requirement |
|----------|-------------|
| Format | PEM or DER |
| Private Key | RSA or ECDSA (256-bit minimum) |
| Certificate | X.509 v3 |
| Validity | Recommended 90 days (Let's Encrypt) |
| Common Name (CN) | Should match the Vault API address |

### Certificate Paths

```
/etc/vault.d/tls/
├── vault.crt      # TLS certificate (chmod 0644)
├── vault.key      # Private key (chmod 0600)
└── ca.crt         # CA certificate (optional, if using custom CA)
```

### File Permissions

```bash
# Set proper permissions
sudo chmod 0644 /etc/vault.d/tls/vault.crt
sudo chmod 0600 /etc/vault.d/tls/vault.key
sudo chown root:root /etc/vault.d/tls/*

# Verify permissions
ls -la /etc/vault.d/tls/
# -rw-r--r-- root root vault.crt
# -rw------- root root vault.key
```

## Let's Encrypt Integration

### Automated Certificate Management

For Let's Encrypt certificates, use certbot:

```bash
# Install certbot
sudo apt update
sudo apt install certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d vault.your-domain.com

# Certificate files will be at:
# /etc/letsencrypt/live/vault.your-domain.com/fullchain.pem
# /etc/letsencrypt/live/vault.your-domain.com/privkey.pem

# Copy to Vault TLS directory
sudo cp /etc/letsencrypt/live/vault.your-domain.com/fullchain.pem /etc/vault.d/tls/vault.crt
sudo cp /etc/letsencrypt/live/vault.your-domain.com/privkey.pem /etc/vault.d/tls/vault.key

# Set permissions
sudo chmod 0644 /etc/vault.d/tls/vault.crt
sudo chmod 0600 /etc/vault.d/tls/vault.key
```

### Auto-Renewal Setup

Let's Encrypt certificates expire after 90 days. Set up automatic renewal:

```bash
# Add to crontab
sudo crontab -e

# Add this line (runs twice daily)
0 0,12 * * * certbot renew --quiet
```

### Renewal Script with Vault Reload

Create `/etc/letsencrypt/renewal-hooks/post/vault-reload.sh`:

```bash
#!/bin/bash
# Post-renewal hook for Vault

# Copy new certificates
cp /etc/letsencrypt/live/vault.your-domain.com/fullchain.pem /etc/vault.d/tls/vault.crt
cp /etc/letsencrypt/live/vault.your-domain.com/privkey.pem /etc/vault.d/tls/vault.key

# Set permissions
chmod 0644 /etc/vault.d/tls/vault.crt
chmod 0600 /etc/vault.d/tls/vault.key

# Reload Vault to pick up new certificates
systemctl reload vault

echo "$(date): Vault TLS certificates renewed and reloaded"
```

Make executable:
```bash
sudo chmod +x /etc/letsencrypt/renewal-hooks/post/vault-reload.sh
```

## Self-Signed Certificates (Development Only)

For development/testing environments:

```bash
# Generate private key and certificate
sudo mkdir -p /etc/vault.d/tls
sudo openssl req -new -newkey rsa:2048 -days 365 -nodes -x509 \
  -keyout /etc/vault.d/tls/vault.key \
  -out /etc/vault.d/tls/vault.crt \
  -subj "/C=US/ST=State/L=City/O=Organization/CN=vault.local"

# Set permissions
sudo chmod 0600 /etc/vault.d/tls/vault.key
sudo chmod 0644 /etc/vault.d/tls/vault.crt
```

## Certificate Renewal Procedures

### Manual Renewal

```bash
# Check certificate expiration
openssl x509 -enddate -noout -in /etc/vault.d/tls/vault.crt

# If using Let's Encrypt
sudo certbot renew

# If manual certificate update
sudo cp new-cert.crt /etc/vault.d/tls/vault.crt
sudo cp new-key.key /etc/vault.d/tls/vault.key

# Reload Vault
sudo systemctl reload vault
```

### Automated Renewal Monitoring

Create monitoring script `/usr/local/bin/monitor-tls.sh`:

```bash
#!/bin/bash
# Monitor TLS certificate expiration

CERT_PATH="/etc/vault.d/tls/vault.crt"
DAYS_THRESHOLD=30

EXPIRY=$(openssl x509 -enddate -noout -in "$CERT_PATH" 2>/dev/null | cut -d= -f2)
EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s)
NOW_EPOCH=$(date +%s)
DAYS_REMAINING=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))

if [ $DAYS_REMAINING -lt $DAYS_THRESHOLD ]; then
    echo "WARNING: TLS certificate expires in $DAYS_REMAINING days"
    exit 1
fi

echo "OK: TLS certificate valid for $DAYS_REMAINING days"
exit 0
```

Add to crontab:
```bash
# Run daily check
0 2 * * * /usr/local/bin/monitor-tls.sh
```

## Common Name (CN) Requirements

The certificate's Common Name (CN) must match the hostname clients use to connect to Vault:

```
Certificate CN: vault.your-domain.com
VAULT_ADDR:     https://vault.your-domain.com:8200
```

## Troubleshooting

### Certificate Verification

```bash
# Check certificate details
openssl x509 -text -noout -in /etc/vault.d/tls/vault.crt

# Verify certificate matches private key
openssl x509 -noout -modulus -in /etc/vault.d/tls/vault.crt | openssl md5
openssl rsa -noout -modulus -in /etc/vault.d/tls/vault.key | openssl md5
```

### Common Errors

| Error | Solution |
|-------|----------|
| `tls: bad certificate` | Check certificate chain and CA |
| `connection reset` | Verify TLS version compatibility |
| `certificate expired` | Renew certificate |
| `CN mismatch` | Update CN or use correct hostname |

## References

- [Vault TLS Configuration](https://developer.hashicorp.com/vault/docs/configuration/listener/tcp#tls-configuration)
- [Let's Encrypt](https://letsencrypt.org/)
