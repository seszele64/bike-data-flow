# Feature Specification: HashiCorp Vault Secrets Management

**Feature Branch**: `001-hashicorp-vault-integration`  
**Created**: 2026-01-10  
**Status**: Draft  
**Input**: User description: "Implement HashiCorp Vault for secure secrets management. Replace environment variables and .env files with HashiCorp Vault for secure secrets storage. Configure Vault on Hetzner VPS and integrate with Dagster resources."

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Deploy and Configure HashiCorp Vault Server (Priority: P1)

As a DevOps engineer, I need a secure, properly configured HashiCorp Vault instance running on the Hetzner VPS so that I can store and manage all application secrets in a centralized, secure location.

**Why this priority**: This is the foundational capability that enables all other secrets management features. Without a running Vault server, no secrets can be stored or retrieved.

**Independent Test**: Can be tested by verifying the Vault server is running, responding to health checks, and initialized with proper security configuration.

**Acceptance Scenarios**:

1. **Given** a Hetzner VPS with sufficient resources, **When** I deploy the Vault server, **Then** the server MUST respond to health checks within acceptable latency thresholds.
2. **Given** an uninitialized Vault instance, **When** I initialize it with appropriate security parameters, **Then** I MUST receive unseal keys and a root token stored securely.
3. **Given** a running Vault instance, **When** I configure it with TLS, **Then** all API communications MUST be encrypted.

---

### User Story 2 - Integrate Vault with Dagster Resources (Priority: P1)

As a data engineer, I need Dagster resources to securely retrieve secrets from Vault so that my pipelines can access database credentials, API keys, and other sensitive configuration without hardcoding them.

**Why this priority**: This directly enables the migration from environment variables to Vault, ensuring Dagster pipelines can function with centralized secrets management.

**Independent Test**: Can be tested by running a Dagster job that requires Vault-retrieved secrets and verifying successful execution.

**Acceptance Scenarios**:

1. **Given** a Vault server with stored secrets, **When** a Dagster job requests a secret, **Then** the secret value MUST be returned securely without exposing it in logs.
2. **Given** configured Vault credentials in Dagster, **When** I run a pipeline that requires multiple secrets, **Then** all secrets MUST be retrieved successfully.
3. **Given** a Vault connection failure, **When** a Dagster job attempts to retrieve secrets, **Then** it MUST fail gracefully with a clear error message.

---

### User Story 3 - Migrate Secrets from Environment Variables to Vault (Priority: P1)

As a security engineer, I need to identify all secrets currently stored in environment variables and .env files and migrate them to Vault so that sensitive credentials are no longer exposed in plain text configuration files.

**Why this priority**: This addresses the core security vulnerability of storing secrets in environment variables and achieves the primary goal of the feature.

**Independent Test**: Can be tested by scanning the codebase for secrets and verifying they are only present in Vault.

**Acceptance Scenarios**:

1. **Given** existing .env files with secrets, **When** I migrate them to Vault, **Then** the secrets MUST be accessible via Vault API.
2. **Given** migrated secrets in Vault, **When** I remove them from .env files, **Then** applications MUST continue functioning by retrieving from Vault.
3. **Given** the codebase, **When** I scan for hardcoded secrets, **Then** there MUST be zero secrets found in environment variable patterns.

---

### User Story 4 - Implement Secret Rotation Policies (Priority: P2)

As a security administrator, I need automated secret rotation for critical credentials so that compromised credentials have a limited exposure window and security compliance requirements are met.

**Why this priority**: Secret rotation is important for security compliance but can be implemented after the foundational Vault integration is stable.

**Independent Test**: Can be tested by configuring rotation for a test secret and verifying it changes according to the defined schedule.

**Acceptance Scenarios**:

1. **Given** a secret stored in Vault with rotation configured, **When** the rotation period elapses, **Then** the secret value MUST be updated automatically.
2. **Given** an application using a rotating secret, **When** the secret rotates, **Then** the application MUST continue functioning without manual intervention.
3. **Given** secret rotation history, **When** I review it, **Then** I MUST be able to see all previous secret versions and their rotation dates.

---

### User Story 5 - Configure Access Control and Policies (Priority: P2)

As a security engineer, I need fine-grained access control policies in Vault so that different applications and users have access only to the secrets they require.

**Why this priority**: Proper access control ensures least-privilege security principles and prevents unauthorized access to sensitive credentials.

**Independent Test**: Can be tested by attempting to access secrets with different policy configurations and verifying access is granted or denied appropriately.

**Acceptance Scenarios**:

1. **Given** configured Vault policies, **When** an entity with limited policy attempts to access unauthorized secrets, **Then** access MUST be denied.
2. **Given** configured Vault policies, **When** an entity with appropriate policy requests access, **Then** access MUST be granted.
3. **Given** policy changes in Vault, **When** access is tested, **Then** changes MUST take effect without requiring Vault restart.

---

### User Story 6 - Implement Backup and Disaster Recovery (Priority: P2)

As a DevOps engineer, I need to backup and restore Vault data so that secrets can be recovered in case of hardware failure or data corruption.

**Why this priority**: Ensuring secrets are recoverable is critical for business continuity and disaster recovery planning.

**Independent Test**: Can be tested by performing a backup, simulating data loss, and restoring from backup.

**Acceptance Scenarios**:

1. **Given** a running Vault instance, **When** I perform a snapshot backup, **Then** all secrets and configuration MUST be included in the backup.
2. **Given** a complete backup, **When** I restore to a new Vault instance, **Then** all secrets MUST be present and accessible.
3. **Given** backup retention policies, **When** backup age exceeds retention period, **Then** old backups MUST be automatically cleaned up.

---

### User Story 7 - Enable Audit Logging (Priority: P3)

As a security auditor, I need comprehensive audit logs of all Vault access and operations so that I can investigate security incidents and demonstrate compliance.

**Why this priority**: Audit logging is essential for security monitoring and compliance but can be implemented after core functionality is proven.

**Independent Test**: Can be tested by performing operations in Vault and verifying they appear in audit logs with complete metadata.

**Acceptance Scenarios**:

1. **Given** audit logging enabled, **When** secrets are accessed, **Then** each access MUST be logged with timestamp, accessor, and secret path.
2. **Given** audit log configuration, **When** I review logs, **Then** I MUST be able to filter by user, time range, and operation type.
3. **Given** audit log retention requirements, **When** log age exceeds retention period, **Then** logs MUST be archived or deleted according to policy.

---

### Edge Cases

- What happens when Vault becomes unavailable during application runtime?
- How does the system handle secrets that fail to decrypt due to key rotation?
- What happens when multiple applications attempt to update the same secret simultaneously?
- How are emergency access procedures handled when normal authentication fails?
- What happens when unseal keys are lost or compromised?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide centralized secrets storage accessible via a secure API.
- **FR-002**: System MUST encrypt all secrets at rest using industry-standard encryption algorithms.
- **FR-003**: System MUST support multiple authentication methods for accessing secrets.
- **FR-004**: System MUST support fine-grained access control policies for secret access.
- **FR-005**: System MUST provide audit logging of all secret access and management operations.
- **FR-006**: System MUST support secret versioning to track changes over time.
- **FR-007**: System MUST provide backup and restore capabilities for disaster recovery.
- **FR-008**: System MUST integrate with Dagster resource system for secrets retrieval.
- **FR-009**: System MUST support secret rotation with configurable schedules.
- **FR-010**: System MUST provide health check endpoints for monitoring.
- **FR-011**: Secrets MUST be accessible programmatically without manual intervention.
- **FR-012**: System MUST support single-node deployment as specified.

### Key Entities

- **Secret**: Represents a stored credential or configuration value with metadata including creation date, modification date, and current version.
- **Access Policy**: Defines permissions for entities to access or modify specific secret paths.
- **Authentication Method**: Mechanism for verifying entity identity (e.g., tokens, AppRole, Kubernetes auth).
- **Vault Configuration**: Server settings including storage backend, listener configuration, and security settings.
- **Audit Log**: Record of all operations including timestamp, accessor, operation type, and outcome.
- **Secret Rotation Policy**: Configuration defining rotation frequency and automatic rotation behavior.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All production secrets MUST be stored in Vault and retrievable programmatically.
- **SC-002**: Zero secrets MUST remain in environment variables or .env files in production.
- **SC-003**: Dagster pipelines MUST successfully retrieve secrets from Vault without manual intervention.
- **SC-004**: Secret retrieval latency MUST not exceed 500ms for 95% of requests.
- **SC-005**: System MUST maintain 99.9% availability for Vault API access.
- **SC-006**: Backup and restore procedures MUST be tested and documented.
- **SC-007**: All secret access operations MUST be logged for audit purposes.
- **SC-008**: Secret rotation MUST be configured for all credentials with rotation requirements.

### Assumptions

- The Hetzner VPS has sufficient resources to run Vault (minimum 2GB RAM recommended).
- The deployment environment supports TLS certificates for Vault communication.
- Existing Dagster resources can be modified to use Vault-based secrets.
- Team members have access to Vault unseal keys and root token stored in a secure location.
- Network connectivity between Dagster deployment and Vault server is reliable.
- **Rotation Scope Assumption**: Database credentials and API keys will require 90-day rotation cycles; other secrets will rotate annually or on-demand.
- **Audit Retention Assumption**: Audit logs will be retained for 90 days to meet standard compliance requirements, with the option to archive to cold storage for longer retention.

### Clarifications Status

The following clarifications have been addressed:

1. ✅ **High Availability**: Single-node deployment confirmed.
2. ✅ **Latency Requirements**: < 500ms acceptable secret retrieval latency.
3. ✅ **Secret Categories**: All secrets should be stored in Vault.

The following clarifications remain open:

4. **Rotation Scope**: Which secrets require automatic rotation, and what rotation periods are acceptable?
5. **Audit Retention**: How long should audit logs be retained for compliance purposes?
