# Infrastructure Constraints - Hetzner S3 & VPS

**Last reviewed: 2026-01-09**

## Overview

This document outlines infrastructure constraints for the bike-data-flow project, specifically focusing on Hetzner S3 storage and VPS compute environments. All tool recommendations and implementation patterns are adapted for these constraints.

---

## Hetzner S3 Storage Constraints

### 1. S3-Compatible Object Storage

**Hetzner S3 (Hetzner Object Storage)** provides S3-compatible object storage with the following characteristics:

#### 1.1 Key Differences from AWS S3

| Feature | AWS S3 | Hetzner S3 |
|---------|----------|-------------|
| Endpoint | `s3.amazonaws.com` | Custom endpoint URL |
| Regions | Multiple global regions | Single region (fsn1) |
| Availability Zones | Multiple | Single availability zone |
| Pricing Model | Tiered storage + request pricing | Flat rate pricing |
| SLA | 99.999999999% durability | 99.9% availability |
| Features | Glacier, lifecycle policies | Basic S3 features only |

#### 1.2 Configuration Requirements

**Endpoint Configuration**:
```python
import boto3
from botocore.client import Config

# Hetzner S3 client configuration
s3_client = boto3.client(
    's3',
    endpoint_url='https://your-hetzner-endpoint.com',
    aws_access_key_id='your-access-key',
    aws_secret_access_key='your-secret-key',
    config=Config(
        signature_version='s3v4',
        s3={'addressing_style': 'path'}  # Required for Hetzner
    ),
    region_name='fsn1'  # Hetzner region
)
```

**Dagster S3 Resource Configuration**:
```python
from dagster_aws.s3.resources import S3Resource

s3_resource = S3Resource(
    endpoint_url='https://your-hetzner-endpoint.com',
    aws_access_key_id='your-access-key',
    aws_secret_access_key='your-secret-key',
    region_name='fsn1',
    s3_path_style_access=True,  # Critical for Hetzner
    bucket_name='your-bucket-name'
)
```

#### 1.3 Unsupported Features

**Features NOT available on Hetzner S3**:
- ❌ S3 Glacier (cold storage)
- ❌ Lifecycle policies (auto-transition to Glacier)
- ❌ S3 Event Notifications (Lambda triggers)
- ❌ S3 Select (SQL queries on objects)
- ❌ Requester Pays buckets
- ❌ Object Lock (WORM storage)
- ❌ Multi-Region Replication

**Workarounds**:
- **Lifecycle policies**: Implement custom cleanup jobs in Dagster
- **Event notifications**: Use Dagster sensors to poll for changes
- **S3 Select**: Download and query with DuckDB instead

---

### 2. Hetzner VPS Compute Constraints

#### 2.1 Resource Limitations

**Typical Hetzner VPS Specs**:
- **CPU**: Shared vCPUs (1-16 cores)
- **RAM**: 2-64 GB
- **Storage**: SSD (20-640 GB)
- **Network**: 1 Gbps (shared)
- **Bandwidth**: 20 TB/month (included)

**Implications for Dagster**:
- Limited parallel processing capacity
- Memory constraints for large datasets
- No auto-scaling (manual scaling required)
- Network bandwidth limits for data transfer

#### 2.2 No Serverless Functions

**Constraint**: Hetzner VPS does not provide serverless compute (like AWS Lambda).

**Impact**:
- ❌ Cannot use AWS Lambda for event-driven processing
- ❌ No automatic scaling based on load
- ❌ No pay-per-use pricing model

**Alternatives**:
- **Systemd services**: Run Dagster daemon as systemd service
- **Docker containers**: Containerize Dagster for easier deployment
- **Process managers**: Use supervisord or systemd for process management

**Systemd Service Configuration**:
```ini
# /etc/systemd/system/dagster-daemon.service
[Unit]
Description=Dagster Daemon
After=network.target

[Service]
Type=simple
User=dagster
WorkingDirectory=/opt/dagster
Environment="PATH=/opt/dagster/venv/bin"
ExecStart=/opt/dagster/venv/bin/dagster-daemon run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 2.3 No Managed Services

**Constraint**: Hetzner provides basic VPS, not managed services.

**Missing Managed Services**:
- ❌ RDS (managed database)
- ❌ ElastiCache (managed Redis)
- ❌ Lambda (serverless functions)
- ❌ EventBridge (event bus)
- ❌ Step Functions (workflow orchestration)
- ❌ CloudWatch (monitoring)

**Self-Managed Alternatives**:
- **Database**: PostgreSQL/MySQL on VPS
- **Cache**: Redis on VPS
- **Monitoring**: Prometheus + Grafana
- **Logging**: ELK Stack (Elasticsearch, Logstash, Kibana)

---

## Adapted Tool Recommendations

### 3. Secrets Management

#### 3.1 HashiCorp Vault (Recommended)

**Why**: Self-hosted, works well on Hetzner VPS, provides enterprise-grade secrets management.

**Installation**:
```bash
# Install Vault on Hetzner VPS
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install vault

# Configure Vault
sudo systemctl start vault
sudo systemctl enable vault
```

**Dagster Integration**:
```python
from dagster import ConfigurableResource
import hvac

class VaultResource(ConfigurableResource):
    url: str = "http://localhost:8200"
    token: str
    mount_point: str = "secret"

    def get_client(self):
        return hvac.Client(url=self.url, token=self.token)

    def get_secret(self, path: str) -> dict:
        client = self.get_client()
        return client.secrets.kv.v2.read_secret_version(
            path=path,
            mount_point=self.mount_point
        )['data']['data']

# Usage
@asset(required_resource_keys={"vault"})
def secure_asset(context):
    s3_creds = context.resources.vault.get_secret("s3/credentials")
    # Use credentials
```

#### 3.2 Ansible Vault (Alternative)

**Why**: Simple, file-based, good for small teams.

**Usage**:
```bash
# Encrypt secrets file
ansible-vault encrypt secrets.yml

# Decrypt at runtime
ansible-vault view secrets.yml
```

**Dagster Integration**:
```python
import subprocess
import yaml

def get_ansible_vault_secret(path: str) -> dict:
    """Decrypt and read Ansible Vault secret"""
    result = subprocess.run(
        ['ansible-vault', 'view', path],
        capture_output=True,
        text=True
    )
    return yaml.safe_load(result.stdout)
```

---

### 4. Monitoring & Observability

#### 4.1 Prometheus + Grafana (Recommended)

**Why**: Self-hosted, works well on Hetzner VPS, industry-standard.

**Installation**:
```bash
# Install Prometheus
wget https://github.com/prometheus/prometheus/releases/download/v2.47.0/prometheus-2.47.0.linux-amd64.tar.gz
tar xvfz prometheus-2.47.0.linux-amd64.tar.gz
cd prometheus-2.47.0.linux-amd64
./prometheus --config.file=prometheus.yml

# Install Grafana
sudo apt install -y software-properties-common
sudo add-apt-repository "deb https://packages.grafana.com/oss/deb stable main"
wget -q -O - https://packages.grafana.com/gpg.key | sudo apt-key add -
sudo apt update
sudo apt install grafana
sudo systemctl start grafana-server
```

**Prometheus Configuration**:
```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'dagster'
    static_configs:
      - targets: ['localhost:8000']

  - job_name: 'node_exporter'
    static_configs:
      - targets: ['localhost:9100']

  - job_name: 'redis'
    static_configs:
      - targets: ['localhost:9121']
```

**Dagster Metrics Exporter**:
```python
from prometheus_client import Counter, Histogram, start_http_server

# Start Prometheus HTTP server
start_http_server(8000)

# Define metrics
asset_runs_total = Counter(
    'dagster_asset_runs_total',
    'Total asset runs',
    ['asset_name', 'status']
)

asset_run_duration = Histogram(
    'dagster_asset_run_duration_seconds',
    'Asset run duration',
    ['asset_name']
)
```

#### 4.2 Loki + Promtail (Alternative)

**Why**: Lightweight, good for log aggregation.

**Installation**:
```bash
# Install Loki
wget https://github.com/grafana/loki/releases/download/v2.9.0/loki-linux-amd64.zip
unzip loki-linux-amd64.zip
./loki-linux-amd64 -config.file=loki-config.yml

# Install Promtail
wget https://github.com/grafana/loki/releases/download/v2.9.0/promtail-linux-amd64.zip
unzip promtail-linux-amd64.zip
./promtail-linux-amd64 -config.file=promtail-config.yml
```

---

### 5. Caching

#### 5.1 Redis (Recommended)

**Why**: Self-hosted, works well on Hetzner VPS, fast in-memory caching.

**Installation**:
```bash
# Install Redis
sudo apt update
sudo apt install redis-server
sudo systemctl start redis-server
sudo systemctl enable redis-server

# Configure Redis
sudo nano /etc/redis/redis.conf
# Set max memory
maxmemory 2gb
maxmemory-policy allkeys-lru
```

**Dagster Integration**:
```python
from dagster import ConfigurableResource
import redis

class RedisCacheResource(ConfigurableResource):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    ttl: int = 3600

    def get_client(self):
        return redis.Redis(host=self.host, port=self.port, db=self.db)

    def get(self, key: str):
        client = self.get_client()
        value = client.get(key)
        return json.loads(value) if value else None

    def set(self, key: str, value: dict):
        client = self.get_client()
        client.setex(key, self.ttl, json.dumps(value))
```

#### 5.2 Memcached (Alternative)

**Why**: Simpler than Redis, good for simple caching.

**Installation**:
```bash
sudo apt install memcached
sudo systemctl start memcached
```

---

### 6. CI/CD

#### 6.1 GitHub Actions (Recommended)

**Why**: Works with any VPS, free for public repos, integrates well with GitHub.

**Deployment to Hetzner VPS**:
```yaml
# .github/workflows/deploy.yml
name: Deploy to Hetzner

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Deploy via SSH
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.HETZNER_HOST }}
          username: ${{ secrets.HETZNER_USER }}
          key: ${{ secrets.HETZNER_SSH_KEY }}
          script: |
            cd /opt/bike-data-flow
            git pull origin main
            pip install -e .
            systemctl restart dagster-daemon
```

#### 6.2 GitLab CI (Alternative)

**Why**: If using GitLab instead of GitHub.

**Deployment to Hetzner VPS**:
```yaml
# .gitlab-ci.yml
deploy:
  stage: deploy
  only:
    - main
  script:
    - apt-get update && apt-get install -y openssh-client
    - eval $(ssh-agent -s)
    - ssh-add <(echo "$HETZNER_SSH_KEY")
    - mkdir -p ~/.ssh
    - echo "$HETZNER_HOST ssh-rsa ..." >> ~/.ssh/known_hosts
    - ssh $HETZNER_USER@$HETZNER_HOST "cd /opt/bike-data-flow && git pull && pip install -e . && systemctl restart dagster-daemon"
```

---

### 7. Infrastructure as Code

#### 7.1 Ansible (Recommended)

**Why**: Agentless, works well with Hetzner VPS, simple YAML syntax.

**Installation**:
```bash
pip install ansible
```

**Playbook Example**:
```yaml
# playbook.yml
---
- name: Configure Dagster Pipeline Server
  hosts: all
  become: yes
  vars:
    dagster_user: dagster
    dagster_home: /opt/dagster

  tasks:
    - name: Install system dependencies
      apt:
        name:
          - python3
          - python3-pip
          - redis-server
          - git
        state: present

    - name: Create dagster user
      user:
        name: "{{ dagster_user }}"
        system: yes
        home: "{{ dagster_home }}"

    - name: Clone repository
      git:
        repo: https://github.com/your-repo/bike-data-flow.git
        dest: "{{ dagster_home }}/bike-data-flow"
        version: main

    - name: Install Python dependencies
      pip:
        requirements: "{{ dagster_home }}/bike-data-flow/requirements.txt"
        executable: pip3

    - name: Configure systemd service
      template:
        src: dagster-daemon.service.j2
        dest: /etc/systemd/system/dagster-daemon.service
      notify: Restart Dagster

    - name: Start Dagster daemon
      systemd:
        name: dagster-daemon
        state: started
        enabled: yes

  handlers:
    - name: Restart Dagster
      systemd:
        name: dagster-daemon
        state: restarted
```

#### 7.2 Terraform (Alternative)

**Why**: If using Hetzner Cloud API for provisioning.

**Hetzner Cloud Provider**:
```hcl
# main.tf
terraform {
  required_providers {
    hetzner = {
      source = "hetznercloud/hetzner"
      version = "~> 1.36"
    }
  }
}

provider "hetzner" {
  token = var.hcloud_token
}

resource "hcloud_server" "dagster" {
  name        = "dagster-server"
  server_type = "cx22"
  image       = "ubuntu-22.04"
  location    = "fsn1"

  ssh_keys = [hcloud_ssh_key.default.id]
}

resource "hcloud_ssh_key" "default" {
  name       = "default"
  public_key = file("~/.ssh/id_rsa.pub")
}
```

---

## Performance Considerations

### 8. Network Bandwidth

**Constraint**: Hetzner VPS includes 20 TB/month bandwidth.

**Implications**:
- Large data transfers may hit bandwidth limits
- Need to optimize data transfer (compression, delta updates)
- Monitor bandwidth usage regularly

**Optimization Strategies**:
```python
# Use compression for S3 uploads
import gzip
import io

def upload_compressed(s3_client, bucket, key, data):
    """Upload compressed data to S3"""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb') as gz:
        gz.write(data.encode('utf-8'))
    buffer.seek(0)

    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=buffer.getvalue(),
        ContentEncoding='gzip'
    )

# Use delta updates instead of full transfers
def upload_delta(s3_client, bucket, key, new_data, old_data):
    """Upload only changed data"""
    delta = new_data[~new_data.isin(old_data)]
    if not delta.empty:
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=delta.to_parquet()
        )
```

### 9. Storage I/O

**Constraint**: SSD performance on Hetzner VPS is shared.

**Implications**:
- High I/O operations may be throttled
- Need to optimize disk usage patterns

**Optimization Strategies**:
```python
# Use in-memory processing before writing to disk
import pandas as pd

def process_in_memory(data):
    """Process data entirely in memory"""
    # All transformations in memory
    df = pd.DataFrame(data)
    df = df.transform(...)
    df = df.filter(...)
    # Write to disk only once at the end
    return df

# Use Parquet for efficient storage
def save_parquet(df, path):
    """Save as Parquet for efficient I/O"""
    df.to_parquet(
        path,
        engine='pyarrow',
        compression='snappy'  # Fast compression
    )
```

---

## Cost Considerations

### 10. Hetzner Pricing Model

**VPS Pricing** (as of 2025):
- **CX22** (2 vCPU, 4 GB RAM): €4.50/month
- **CX32** (4 vCPU, 8 GB RAM): €9.00/month
- **CX42** (8 vCPU, 16 GB RAM): €18.00/month

**S3 Storage Pricing**:
- **Storage**: €0.005/GB/month
- **Traffic**: €0.01/GB (after 20 TB included)
- **Requests**: €0.0001 per 1,000 requests

**Cost Optimization Strategies**:
1. **Right-size VPS**: Monitor resource usage and scale appropriately
2. **Compress data**: Use Parquet/Gzip to reduce storage costs
3. **Clean up old data**: Implement lifecycle policies manually
4. **Optimize queries**: Reduce S3 request count

---

## Security Considerations

### 11. Network Security

**Constraint**: Hetzner VPS requires manual security configuration.

**Recommendations**:
```bash
# Configure firewall (ufw)
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable

# Configure fail2ban for SSH protection
sudo apt install fail2ban
sudo systemctl start fail2ban

# Use SSH keys only
sudo nano /etc/ssh/sshd_config
# PasswordAuthentication no
```

### 12. Data Encryption

**Constraint**: Hetzner S3 supports server-side encryption, but not by default.

**Implementation**:
```python
# Enable server-side encryption
s3_client.put_object(
    Bucket=bucket,
    Key=key,
    Body=data,
    ServerSideEncryption='AES256'
)

# Enable client-side encryption
from cryptography.fernet import Fernet

key = Fernet.generate_key()
cipher_suite = Fernet(key)
encrypted_data = cipher_suite.encrypt(data.encode('utf-8'))

s3_client.put_object(
    Bucket=bucket,
    Key=key,
    Body=encrypted_data
)
```

---

## Backup & Disaster Recovery

### 13. Backup Strategy

**Constraint**: No automated backups on Hetzner VPS.

**Recommendations**:
```bash
# Automated backup script
#!/bin/bash
# backup.sh

DATE=$(date +%Y-%m-%d)
BACKUP_DIR="/backups/$DATE"

# Create backup directory
mkdir -p $BACKUP_DIR

# Backup DuckDB database
cp ~/data/analytics.duckdb $BACKUP_DIR/

# Backup configuration files
cp -r /opt/dagster/config $BACKUP_DIR/

# Upload to S3
aws s3 sync $BACKUP_DIR s3://backup-bucket/$DATE/

# Clean up old backups (keep 30 days)
find /backups -type d -mtime +30 -exec rm -rf {} \;
```

**Schedule with cron**:
```bash
# Add to crontab
0 2 * * * /opt/dagster/scripts/backup.sh
```

---

## Related Memory Files

- [`pipeline-analysis.md`](pipeline-analysis.md) - Current pipeline architecture
- [`improvement-roadmap.md`](improvement-roadmap.md) - Prioritized improvements
- [`data-engineering-best-practices.md`](data-engineering-best-practices.md) - Best practices research

---

*Last Updated: 2026-01-09*
*Infrastructure Analysis: Complete*
