# Tasks: HashiCorp Vault Secrets Management Integration

**Input**: Design documents from `/specs/001-hashicorp-vault-integration/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: No test tasks included (not explicitly requested in spec.md)

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Create vault module directory structure in wrm_pipeline/wrm_pipeline/vault/
- [x] T002 [P] Create __init__.py files for vault package in wrm_pipeline/wrm_pipeline/vault/__init__.py
- [x] T003 [P] Add hvac dependency to wrm_pipeline/pyproject.toml
- [x] T004 [P] Create migration module directory in wrm_pipeline/wrm_pipeline/migration/
- [x] T005 [P] Create __init__.py for migration package in wrm_pipeline/wrm_pipeline/migration/__init__.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T006 Create Pydantic models in wrm_pipeline/wrm_pipeline/vault/models.py
- [x] T007 [P] Create custom exceptions in wrm_pipeline/wrm_pipeline/vault/exceptions.py
- [x] T008 [P] Create VaultClient wrapper class with caching in wrm_pipeline/wrm_pipeline/vault/client.py

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Deploy and Configure HashiCorp Vault Server (Priority: P1) 🎯 MVP

**Goal**: Deploy a secure, properly configured HashiCorp Vault instance on the Hetzner VPS with TLS and proper initialization.

**Independent Test**: Verify Vault server responds to health checks, is initialized with proper security, and API communications are encrypted with TLS.

### Implementation for User Story 1

- [x] T009 Create server configuration documentation for /etc/vault.d/vault.hcl in docs/vault-server-config.md
- [x] T010 [US1] Create systemd service file for Vault in config/vault/vault.service
- [x] T011 [US1] Create TLS certificate configuration documentation in docs/vault-tls-config.md
- [x] T012 [US1] Create Vault initialization script in scripts/vault/init-vault.sh
- [x] T013 [US1] Create unseal key management documentation in docs/vault-unseal-management.md
- [x] T014 [US1] Create health check verification script in scripts/vault/health-check.sh

**Checkpoint**: User Story 1 complete - Vault server can be deployed and verified

---

## Phase 4: User Story 2 - Integrate Vault with Dagster Resources (Priority: P1)

**Goal**: Create a Dagster resource that securely retrieves secrets from Vault for pipeline execution without hardcoding credentials.

**Independent Test**: Run a Dagster job that requires Vault-retrieved secrets and verify successful execution.

### Implementation for User Story 2

- [x] T015 [US2] Create VaultSecretsResource class in wrm_pipeline/wrm_pipeline/vault/resource.py
- [x] T016 [US2] Implement get_secret method with caching in wrm_pipeline/wrm_pipeline/vault/resource.py
- [x] T017 [US2] Implement get_database_credentials convenience method in wrm_pipeline/wrm_pipeline/vault/resource.py
- [x] T018 [US2] Implement get_api_key method in wrm_pipeline/wrm_pipeline/vault/resource.py
- [x] T019 [US2] Implement list_secrets method in wrm_pipeline/wrm_pipeline/vault/resource.py
- [x] T020 [US2] Implement health_check method in wrm_pipeline/wrm_pipeline/vault/resource.py
- [x] T021 [US2] Update wrm_pipeline/wrm_pipeline/vault/__init__.py to export VaultSecretsResource
- [x] T022 [US2] Update wrm_pipeline/wrm_pipeline/definitions.py to include vault resource
- [x] T023 [US2] Create example asset using Vault resource in wrm_pipeline/wrm_pipeline/assets/vault_example.py

**Checkpoint**: User Story 2 complete - Dagster pipelines can retrieve secrets from Vault

---

## Phase 5: User Story 3 - Migrate Secrets from Environment Variables to Vault (Priority: P1)

**Goal**: Identify all secrets in .env files and environment variables, migrate them to Vault, and update application code to use Vault.

**Independent Test**: Scan codebase for secrets and verify they are only present in Vault.

### Implementation for User Story 3

- [x] T024 [US3] Create secret scanner script in wrm_pipeline/wrm_pipeline/migration/scan_secrets.py
- [x] T025 [US3] Create environment variable detector in wrm_pipeline/wrm_pipeline/migration/detect_env.py
- [x] T026 [US3] Create Vault secret writer in wrm_pipeline/wrm_pipeline/migration/write_to_vault.py
- [x] T027 [US3] Create migration orchestrator in wrm_pipeline/wrm_pipeline/migration/migrate.py
- [x] T028 [US3] Create path structure validator in wrm_pipeline/wrm_pipeline/migration/validate_paths.py
- [x] T029 [US3] Create migration documentation in docs/secret-migration-guide.md
- [x] T030 [US3] Update existing assets to use Vault resource instead of environment variables in wrm_pipeline/wrm_pipeline/assets/
- [x] T031 [US3] Remove sensitive values from .env files after migration verification

**Checkpoint**: User Story 3 complete - All secrets are stored in Vault, not environment variables

---

## Phase 6: User Story 4 - Implement Secret Rotation Policies (Priority: P2)

**Goal**: Configure automated secret rotation for critical credentials with configurable schedules.

**Independent Test**: Configure rotation for a test secret and verify it changes according to the defined schedule.

### Implementation for User Story 4

- [ ] T032 [US4] Create SecretRotationPolicy model in wrm_pipeline/wrm_pipeline/vault/models.py
- [ ] T033 [US4] Create rotation scheduler in wrm_pipeline/wrm_pipeline/vault/rotation.py
- [ ] T034 [US4] Implement scheduled rotation logic in wrm_pipeline/wrm_pipeline/vault/rotation.py
- [ ] T035 [US4] Create rotation history tracker in wrm_pipeline/wrm_pipeline/vault/rotation.py
- [ ] T036 [US4] Create rotation configuration documentation in docs/vault-rotation-config.md
- [ ] T037 [US4] Add rotation methods to VaultClient in wrm_pipeline/wrm_pipeline/vault/client.py

**Checkpoint**: User Story 4 complete - Secrets can be rotated automatically

---

## Phase 7: User Story 5 - Configure Access Control and Policies (Priority: P2)

**Goal**: Create fine-grained access control policies in Vault for different applications and users with least-privilege principles.

**Independent Test**: Attempt to access secrets with different policy configurations and verify access is granted or denied appropriately.

### Implementation for User Story 5

- [x] T038 [US5] Create AccessPolicy model in wrm_pipeline/wrm_pipeline/vault/models.py
- [x] T039 [US5] Create PolicyRule model in wrm_pipeline/wrm_pipeline/vault/models.py
- [x] T040 [US5] Create policy manager in wrm_pipeline/wrm_pipeline/vault/policies.py
- [x] T041 [US5] Implement HCL policy generation in wrm_pipeline/wrm_pipeline/vault/policies.py
- [x] T042 [US5] Create dagster-secrets policy file in config/vault/policies/dagster-secrets.hcl
- [x] T043 [US5] Create readonly-secrets policy file in config/vault/policies/readonly-secrets.hcl
- [x] T044 [US5] Create admin-secrets policy file in config/vault/policies/admin-secrets.hcl
- [x] T045 [US5] Create policy management documentation in docs/vault-policies.md
- [x] T046 [US5] Update VaultClient to support policy operations in wrm_pipeline/wrm_pipeline/vault/client.py

**Checkpoint**: User Story 5 complete - Fine-grained access control is configured

---

## Phase 8: User Story 6 - Implement Backup and Disaster Recovery (Priority: P2)

**Goal**: Backup and restore Vault data for disaster recovery in case of hardware failure or data corruption.

**Independent Test**: Perform a backup, simulate data loss, and restore from backup successfully.

### Implementation for User Story 6

- [ ] T047 [US6] Create backup script in scripts/vault/backup-vault.sh
- [ ] T048 [US6] Create restore script in scripts/vault/restore-vault.sh
- [ ] T049 [US6] Create snapshot manager in wrm_pipeline/wrm_pipeline/vault/backup.py
- [ ] T050 [US6] Implement automated backup scheduling in wrm_pipeline/wrm_pipeline/vault/backup.py
- [ ] T051 [US6] Create backup retention policy manager in wrm_pipeline/wrm_pipeline/vault/backup.py
- [ ] T052 [US6] Configure Hetzner Object Storage integration in wrm_pipeline/wrm_pipeline/vault/backup.py
- [ ] T053 [US6] Create backup documentation in docs/vault-backup-recovery.md
- [ ] T054 [US6] Document emergency recovery procedures in docs/vault-emergency-recovery.md

**Checkpoint**: User Story 6 complete - Backup and restore procedures are tested and documented

---

## Phase 9: User Story 7 - Enable Audit Logging (Priority: P3)

**Goal**: Enable comprehensive audit logging of all Vault access and operations for security monitoring and compliance.

**Independent Test**: Perform operations in Vault and verify they appear in audit logs with complete metadata.

### Implementation for User Story 7

- [ ] T055 [US7] Create AuditLog model in wrm_pipeline/wrm_pipeline/vault/models.py
- [ ] T056 [US7] Create AuditOperation enum in wrm_pipeline/wrm_pipeline/vault/models.py
- [ ] T057 [US7] Enable Vault audit device configuration in config/vault/audit-devices.hcl
- [ ] T058 [US7] Create audit log parser in wrm_pipeline/wrm_pipeline/vault/audit.py
- [ ] T059 [US7] Implement audit log filtering in wrm_pipeline/wrm_pipeline/vault/audit.py
- [ ] T060 [US7] Create audit log viewer script in scripts/vault/view-audit-logs.sh
- [ ] T061 [US7] Configure audit log retention in wrm_pipeline/wrm_pipeline/vault/audit.py
- [ ] T062 [US7] Create audit logging documentation in docs/vault-audit-logging.md

**Checkpoint**: User Story 7 complete - All secret access operations are logged for audit purposes

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T063 [P] Update wrm_pipeline/README.md with Vault integration documentation
- [ ] T064 [P] Create comprehensive API documentation in docs/vault-api.md
- [ ] T065 Create performance benchmark script in scripts/vault/benchmark-latency.py
- [ ] T066 [P] Add type hints and docstrings to all vault module files
- [ ] T067 Update .env.example with VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID variables
- [ ] T068 Create troubleshooting guide in docs/vault-troubleshooting.md
- [ ] T069 Update deployment documentation with Vault requirements in docs/deployment.md
- [ ] T070 [P] Run quickstart.md verification checklist from specs/001-hashicorp-vault-integration/quickstart.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-9)**: All depend on Foundational phase completion
  - User stories can proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Phase 10)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P1)**: Can start after Foundational (Phase 2) - May integrate with US1 but should be independently testable
- **User Story 3 (P1)**: Can start after Foundational (Phase 2) - Depends on US2 for testing, independently testable
- **User Story 4 (P2)**: Can start after Foundational (Phase 2) - May integrate with US1/US2 but independently testable
- **User Story 5 (P2)**: Can start after Foundational (Phase 2) - No story dependencies, independently testable
- **User Story 6 (P2)**: Can start after Foundational (Phase 2) - May integrate with US1 but independently testable
- **User Story 7 (P3)**: Can start after Foundational (Phase 2) - No story dependencies, independently testable

### Within Each User Story

- Models before services
- Services before resources
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, all user stories can start in parallel (if team capacity allows)
- Models within a story marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members

---

## Parallel Example: User Story 2

```bash
# Launch model and exception creation in parallel:
Task: "Create Pydantic models in wrm_pipeline/wrm_pipeline/vault/models.py"
Task: "Create custom exceptions in wrm_pipeline/wrm_pipeline/vault/exceptions.py"

# Launch VaultClient and VaultSecretsResource in parallel:
Task: "Create VaultClient wrapper class with caching in wrm_pipeline/wrm_pipeline/vault/client.py"
Task: "Create VaultSecretsResource class in wrm_pipeline/wrm_pipeline/vault/resource.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Test User Story 1 independently
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Add User Story 2 → Test independently → Deploy/Demo
4. Add User Story 3 → Test independently → Deploy/Demo
5. Add User Stories 4-7 as needed → Test independently → Deploy/Demo
6. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (Vault server deployment)
   - Developer B: User Story 2 (Dagster resource)
   - Developer C: User Story 3 (Secret migration)
3. Stories complete and integrate independently

---

## Summary

| Metric | Value |
|--------|-------|
| **Total Tasks** | 70 |
| **Parallelizable Tasks** | 15 (marked with [P]) |
| **MVP Tasks (US1)** | 14 (T001-T014) |
| **P1 Story Tasks** | 31 (T001-T031) |
| **P2 Story Tasks** | 27 (T032-T054) |
| **P3 Story Tasks** | 8 (T055-T062) |
| **Polish Tasks** | 8 (T063-T070) |

### Tasks Per User Story

| User Story | Priority | Task Count | Tasks |
|------------|----------|------------|-------|
| US1: Deploy Vault Server | P1 | 6 | T009-T014 |
| US2: Dagster Integration | P1 | 9 | T015-T023 |
| US3: Migrate Secrets | P1 | 8 | T024-T031 |
| US4: Secret Rotation | P2 | 6 | T032-T037 |
| US5: Access Control | P2 | 9 | T038-T046 |
| US6: Backup & Recovery | P2 | 8 | T047-T054 |
| US7: Audit Logging | P3 | 8 | T055-T062 |

### Independent Test Criteria

- **US1**: Vault server responds to health checks within acceptable latency, initialized with TLS
- **US2**: Dagster job retrieves secrets from Vault and executes successfully
- **US3**: Codebase contains zero hardcoded secrets, all secrets accessible via Vault API
- **US4**: Test secret rotates according to configured schedule
- **US5**: Access denied for unauthorized paths, granted for authorized paths
- **US6**: Backup contains all secrets, restore to new instance succeeds
- **US7**: Operations appear in audit logs with complete metadata

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
