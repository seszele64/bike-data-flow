# Tasks: Retry Logic with Exponential Backoff

**Feature Branch**: `002-retry-logic`  
**Date**: 2026-01-11  
**Generated**: From speckit.tasks command

---

## Implementation Overview

This feature implements retry logic with exponential backoff for transient failures and circuit breaker pattern to prevent cascade failures. Uses Tenacity library for battle-tested retry functionality.

**Tech Stack**: Python 3.9-3.12, Tenacity (>=8.0.0), boto3 (1.33.13), requests

**Source Module**: `wrm_pipeline/wrm_pipeline/retry/`

---

## Phase 1: Setup

Project initialization and dependency configuration.

### Story Goal

Establish the project structure and dependencies for the retry module.

### Independent Test Criteria

- [x] T001 Create retry module directory structure at `wrm_pipeline/wrm_pipeline/retry/`
- [x] T002 Create test directory at `wrm_pipeline_tests/unit/retry/`
- [x] T003 Add tenacity to project dependencies in `wrm_pipeline/pyproject.toml`

### Tasks

- [x] T001 Create retry module directory structure with `__init__.py`
- [x] T002 Create test directory structure with `__init__.py` files
- [x] T003 Add tenacity (>=8.0.0) to project dependencies

---

## Phase 2: Foundational

Core configuration classes and exceptions used by all retry decorators.

### Story Goal

Implement base configuration dataclasses and custom exceptions that power all retry and circuit breaker functionality.

### Independent Test Criteria

- [x] T004 Import and instantiate `RetryConfiguration` with default values
- [x] T005 Import and instantiate `CircuitBreakerConfiguration` with default values
- [x] T006 Raise and catch `RetryExhaustedException` with correct attributes
- [x] T007 Raise and catch `CircuitOpenException` with correct attributes

### Tasks

- [x] T004 Create `wrm_pipeline/wrm_pipeline/retry/config.py` with `RetryConfiguration` dataclass
- [x] T005 Create `wrm_pipeline/wrm_pipeline/retry/exceptions.py` with `RetryExhaustedException` and `CircuitOpenException`
- [x] T006 Create `wrm_pipeline/wrm_pipeline/retry/circuit_breaker.py` with `CircuitBreakerConfiguration` and `CircuitState` enum
- [x] T007 Update `wrm_pipeline/wrm_pipeline/retry/__init__.py` to export all public APIs

---

## Phase 3: [US1] Tenacity Library Integration

Integrate Tenacity library for retry functionality with exponential backoff.

### Story Goal

Successfully integrate Tenacity library and verify basic retry decorators work with configurable exceptions.

### Independent Test Criteria

- [x] T008 Import tenacity and configure basic retry decorator
- [x] T009 Apply decorator to function and verify retries occur on configured exceptions
- [x] T010 Verify exponential backoff delays are applied between retries

### Implementation Strategy

Implement the base retry decorator pattern using Tenacity's wait_exponential_jitter and stop_after_attempt.

### Tasks

- [x] T008 [P] Create `wrm_pipeline/wrm_pipeline/retry/tenacity_base.py` with tenacity integration utilities
- [x] T009 [P] [US1] Implement `with_retry` base decorator in `wrm_pipeline/wrm_pipeline/retry/decorators.py`
- [x] T010 [US1] Verify tenacity exponential backoff configuration in tests

---

## Phase 4: [US2] S3 Retry Decorator

Custom retry decorator for S3 operations handling AWS-specific exceptions.

### Story Goal

Create a retry decorator specifically designed for S3 operations that handles throttling, rate limits, and network issues.

### Independent Test Criteria

- [x] T011 Apply `@with_s3_retry` decorator to function and verify retries on ThrottlingException
- [x] T012 Verify retry decorator uses S3-specific preset (5 attempts, 1s base, 30s max, 1s jitter)
- [x] T013 Verify non-retryable exceptions (AccessDenied, NoSuchKey) are raised immediately

### Implementation Strategy

Build on tenacity base to create S3-specific decorator with AWS exception handling.

### Tasks

- [x] T011 [P] [US2] Implement `with_s3_retry` decorator in `wrm_pipeline/wrm_pipeline/retry/decorators.py`
- [x] T012 [P] [US2] Define S3-specific retryable exceptions (ThrottlingException, ProvisionedThroughputExceededException, etc.)
- [x] T013 [US2] Add non-retryable exception filter for S3 operations
- [x] T014 [US2] Create `RetryPresets.S3_UPLOAD` and `RetryPresets.S3_DOWNLOAD` configurations

---

## Phase 5: [US3] API Retry Decorator

Custom retry decorator for HTTP API calls handling network issues and server errors.

### Story Goal

Create a retry decorator for external API calls that handles 5xx errors, timeouts, and respects Retry-After headers.

### Independent Test Criteria

- [x] T015 Apply `@with_api_retry` decorator to function and verify retries on 503 errors
- [x] T016 Verify retry decorator uses API-specific preset (3 attempts, 0.5s base, 10s max, 0.5s jitter)
- [x] T017 Verify 4xx errors (except 429) are not retried
- [x] T018 Verify Retry-After header is respected when `respect_retry_after=True`

### Implementation Strategy

Build HTTP-specific decorator with requests library exception handling and Retry-After support.

### Tasks

- [x] T015 [P] [US3] Implement `with_api_retry` decorator in `wrm_pipeline/wrm_pipeline/retry/decorators.py`
- [x] T016 [P] [US3] Define HTTP-specific retryable exceptions (ConnectionError, Timeout, 5xx HTTPError)
- [x] T017 [US3] Implement Retry-After header handling for 429 responses
- [x] T018 [US3] Create `RetryPresets.API_CALL` configuration

---

## Phase 6: [US4] Circuit Breaker Pattern

Implement circuit breaker state machine with OPEN, HALF_OPEN, and CLOSED states.

### Story Goal

Implement thread-safe circuit breaker that prevents cascade failures by tracking consecutive failures.

### Independent Test Criteria

- [x] T019 Create circuit breaker and verify initial state is CLOSED
- [x] T020 Simulate N failures and verify circuit transitions to OPEN state
- [x] T021 Verify OPEN state immediately rejects requests without attempting operation
- [x] T022 Verify recovery timeout elapses and circuit transitions to HALF_OPEN
- [x] T023 Verify HALF_OPEN success transitions to CLOSED, failure transitions back to OPEN

### Implementation Strategy

Implement state machine with thread-safe locking for concurrent access patterns.

### Tasks

- [x] T019 [P] [US4] Implement `CircuitBreaker` class in `wrm_pipeline/wrm_pipeline/retry/circuit_breaker.py`
- [x] T020 [P] [US4] Implement state transition logic (CLOSED → OPEN, OPEN → HALF_OPEN → CLOSED)
- [x] T021 [US4] Implement thread-safe state management with locking
- [x] T022 [US4] Implement recovery timeout checking in state transitions
- [x] T023 [US4] Add structured logging for circuit breaker state changes

---

## Phase 7: [US5] S3 Operation Helpers

Ready-to-use retry wrappers for S3 client methods.

### Story Goal

Provide convenient functions for common S3 operations with retry already configured.

### Independent Test Criteria

- [x] T024 Call `retry_s3_upload` and verify it handles throttling exceptions
- [x] T025 Call `retry_s3_download` and verify it handles connection errors
- [x] T026 Verify non-retryable errors are raised immediately without retry

### Tasks

- [x] T024 [P] [US5] Implement `retry_s3_upload` helper in `wrm_pipeline/wrm_pipeline/retry/operations.py`
- [x] T025 [P] [US5] Implement `retry_s3_download` helper in `wrm_pipeline/wrm_pipeline/retry/operations.py`
- [x] T026 [US5] Add comprehensive exception mapping for S3 client errors

---

## Phase 8: [US7] Unit Tests

Comprehensive unit tests for retry logic and circuit breaker.

### Story Goal

Verify all retry and circuit breaker functionality with high test coverage.

### Independent Test Criteria

- [x] T027 All retry logic tests pass including edge cases
- [x] T028 Circuit breaker state transition tests pass
- [x] T029 Exponential backoff timing tests verify correct delays
- [x] T030 Test coverage for retry module exceeds 90%

### Tasks

- [x] T027 [P] [US7] Create unit tests for `RetryConfiguration` validation in `wrm_pipeline_tests/unit/retry/test_config.py`
- [x] T028 [P] [US7] Create unit tests for S3 retry decorator in `wrm_pipeline_tests/unit/retry/test_s3_retry.py`
- [x] T029 [P] [US7] Create unit tests for API retry decorator in `wrm_pipeline_tests/unit/retry/test_api_retry.py`
- [x] T030 [P] [US7] Create unit tests for circuit breaker in `wrm_pipeline_tests/unit/retry/test_circuit_breaker.py`

---

## Phase 9: Polish & Cross-Cutting Concerns

Final refinements, documentation updates, and integration verification.

### Tasks

- [x] T031 Update `wrm_pipeline/wrm_pipeline/retry/__init__.py` with complete public API exports
- [x] T032 Create example usage file at `wrm_pipeline/wrm_pipeline/retry/examples.py`
- [x] T033 Verify module integrates correctly with existing pipeline code
- [x] T034 Run full test suite and verify all tests pass

---

## Dependencies

```
Phase 1 (Setup) ──┬──► Phase 2 (Foundational) ──┬──► Phase 3 (US1)
                  │                             │
                  │                             ├──► Phase 4 (US2)
                  │                             │
                  │                             ├──► Phase 5 (US3)
                  │                             │
                  │                             ├──► Phase 6 (US4)
                  │                             │
                  │                             ├──► Phase 7 (US5)
                  │                             │
                  │                             └──► Phase 8 (US7) ──► Phase 9 (Polish)
```

### Parallel Execution Opportunities

- **T008, T011, T015, T019**: Base implementations can be developed in parallel
- **T009, T012, T016, T020**: Decorator implementations are independent
- **T027-T030**: Test files can be written in parallel with implementation

### Story Completion Order

1. **Phase 3 (US1)**: Foundation for all retry decorators
2. **Phase 4 (US2)**: S3 operations (primary use case from spec)
3. **Phase 5 (US3)**: API calls (secondary use case)
4. **Phase 6 (US4)**: Circuit breaker (prevents cascade failures)
5. **Phase 7 (US5)**: Convenience helpers (builds on US2)
6. **Phase 8 (US7)**: Tests (can run after each story)

---

## Implementation Strategy Summary

### MVP Scope (Phase 3 + Phase 4 + Phase 6 + Phase 8)

The minimum viable product consists of:
- Tenacity integration (US1)
- S3 retry decorator (US2)
- Circuit breaker (US4)
- Unit tests (US7)

This delivers the core value: resilient S3 operations with cascade failure prevention.

### Incremental Delivery

1. **Sprint 1**: Setup + Foundational + US1 + US2 + US4 (core functionality)
2. **Sprint 2**: US3 (API calls) + US5 (helpers)
3. **Sprint 3**: US7 (tests) + Polish

---

## Summary

| Metric | Value |
|--------|-------|
| Total Tasks | 34 |
| Setup Phase | 3 tasks |
| Foundational Phase | 4 tasks |
| User Story Tasks | 23 tasks |
| Polish Phase | 4 tasks |
| Parallelizable Tasks | ~15 tasks |
| MVP Tasks | ~18 tasks (Phases 1-2, 3, 4, 6, 8) |
| **Status** | **✅ ALL 34 TASKS COMPLETED** |

---

*Tasks generated by speckit.tasks command*
