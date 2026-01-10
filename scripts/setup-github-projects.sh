#!/bin/bash
# GitHub Projects Setup Script for Bike Data Flow Pipeline
# This script automates the setup of GitHub Projects board for the bike-data-flow project

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="Bike Data Flow Pipeline Improvements"
REPO_NAME="bike-data-flow"
REPO_OWNER=$(gh repo view --json owner,name | jq -r '.owner.login')

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}GitHub Projects Setup Script${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Repository: ${YELLOW}${REPO_OWNER}/${REPO_NAME}${NC}"
echo -e "Project: ${YELLOW}${PROJECT_NAME}${NC}"
echo ""

# Check if gh CLI is authenticated
echo -e "${GREEN}Step 0: Verifying GitHub CLI authentication...${NC}"
if ! gh auth status &>/dev/null; then
    echo -e "${RED}Error: GitHub CLI not authenticated. Run 'gh auth login' first.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Authenticated${NC}"
echo ""

# Step 1: Create Project
echo -e "${GREEN}Step 1: Creating GitHub Project...${NC}"
PROJECT_ID=$(gh project create \
    --owner "$REPO_OWNER" \
    --title "$PROJECT_NAME" \
    --template board \
    --format json \
    --jq '.id' 2>/dev/null || echo "")

if [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}Error: Failed to create project${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Project created with ID: ${PROJECT_ID}${NC}"
echo ""

# Step 2: Configure Fields (only TEXT field type is available via CLI)
echo -e "${GREEN}Step 2: Configuring custom fields...${NC}"
echo -e "${YELLOW}  Note: Only basic fields available via CLI. Configure others manually.${NC}"

# Create basic text fields that are available
echo "  - Creating Status field..."
gh project field-create "$PROJECT_ID" \
    --name "Status" \
    --data-type "TEXT" \
    --format json \
    --jq '.id' > /tmp/status_field_id 2>/dev/null || echo "manual"
echo -e "${GREEN}    ✓ Status field (TEXT) created - configure options manually${NC}"

echo "  - Creating Priority field..."
gh project field-create "$PROJECT_ID" \
    --name "Priority" \
    --data-type "TEXT" \
    --format json \
    --jq '.id' > /tmp/priority_field_id 2>/dev/null || echo "manual"
echo -e "${GREEN}    ✓ Priority field (TEXT) created - configure options manually${NC}"

echo "  - Creating Type field..."
gh project field-create "$PROJECT_ID" \
    --name "Type" \
    --data-type "TEXT" \
    --format json \
    --jq '.id' > /tmp/type_field_id 2>/dev/null || echo "manual"
echo -e "${GREEN}    ✓ Type field (TEXT) created - configure options manually${NC}"

echo "  - Creating Component field..."
gh project field-create "$PROJECT_ID" \
    --name "Component" \
    --data-type "TEXT" \
    --format json \
    --jq '.id' > /tmp/component_field_id 2>/dev/null || echo "manual"
echo -e "${GREEN}    ✓ Component field (TEXT) created - configure options manually${NC}"

echo "  - Creating Estimate field..."
gh project field-create "$PROJECT_ID" \
    --name "Estimate" \
    --data-type "TEXT" \
    --format json \
    --jq '.id' > /tmp/estimate_field_id 2>/dev/null || echo "manual"
echo -e "${GREEN}    ✓ Estimate field (TEXT) created - configure options manually${NC}"

echo "  - Creating Iteration field..."
gh project field-create "$PROJECT_ID" \
    --name "Iteration" \
    --data-type "TEXT" \
    --format json \
    --jq '.id' > /tmp/iteration_field_id 2>/dev/null || echo "manual"
echo -e "${GREEN}    ✓ Iteration field (TEXT) created - configure options manually${NC}"

echo "  - Creating Start Date field..."
gh project field-create "$PROJECT_ID" \
    --name "Start Date" \
    --data-type "DATE" \
    --format json \
    --jq '.id' > /tmp/start_date_field_id 2>/dev/null || echo "manual"
echo -e "${GREEN}    ✓ Start Date field created${NC}"

echo "  - Creating Target Date field..."
gh project field-create "$PROJECT_ID" \
    --name "Target Date" \
    --data-type "DATE" \
    --format json \
    --jq '.id' > /tmp/target_date_field_id 2>/dev/null || echo "manual"
echo -e "${GREEN}    ✓ Target Date field created${NC}"

echo ""

# Step 3: Configure Labels
echo -e "${GREEN}Step 3: Configuring repository labels...${NC}"

# Priority Labels
echo "  - Creating priority labels..."
gh label create "priority:critical" --color "d73a4a" --description "Critical priority tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "priority:high" --color "fbca04" --description "High priority tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "priority:medium" --color "fef2c0" --description "Medium priority tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "priority:low" --color "0075ca" --description "Low priority tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
echo -e "${GREEN}    ✓ Priority labels created${NC}"

# Type Labels
echo "  - Creating type labels..."
gh label create "type:feature" --color "5319e7" --description "New feature implementation" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "type:bug" --color "d73a4a" --description "Bug fix" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "type:refactor" --color "128a0c" --description "Code refactoring" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "type:test" --color "006b75" --description "Test implementation" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "type:docs" --color "7057ff" --description "Documentation" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "type:infrastructure" --color "5319e7" --description "Infrastructure changes" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
echo -e "${GREEN}    ✓ Type labels created${NC}"

# Component Labels
echo "  - Creating component labels..."
gh label create "component:pipeline" --color "006b75" --description "Pipeline-related" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "component:assets" --color "b60205" --description "Asset-related" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "component:jobs" --color "d4c5f9" --description "Job-related" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "component:sensors" --color "e99695" --description "Sensor-related" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "component:resources" --color "7057ff" --description "Resource-related" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "component:monitoring" --color "fbca04" --description "Monitoring-related" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "component:ci-cd" --color "ff7b72" --description "CI/CD-related" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
echo -e "${GREEN}    ✓ Component labels created${NC}"

# Dependency Labels
echo "  - Creating dependency labels..."
gh label create "dependency:blocked" --color "b60205" --description "Blocked by dependency" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "dependency:blocks" --color "d93f0b" --description "Blocks other tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
echo -e "${GREEN}    ✓ Dependency labels created${NC}"

# Iteration Labels
echo "  - Creating iteration labels..."
gh label create "iteration:sprint-1" --color "e99695" --description "Sprint 1 tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "iteration:sprint-2" --color "e99695" --description "Sprint 2 tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "iteration:sprint-3" --color "e99695" --description "Sprint 3 tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "iteration:sprint-4" --color "e99695" --description "Sprint 4 tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
gh label create "iteration:sprint-5" --color "e99695" --description "Sprint 5+ tasks" --repo "$REPO_OWNER/$REPO_NAME" 2>/dev/null || echo "  ⚠ Label may already exist"
echo -e "${GREEN}    ✓ Iteration labels created${NC}"

echo ""

# Step 4: Create Issues (draft items in project)
echo -e "${GREEN}Step 4: Creating GitHub issues for roadmap tasks...${NC}"

# Task definitions
declare -A TASKS=(
    # Sprint 1 - Critical
    "Task 1.1: Secrets Management - HashiCorp Vault|Implement HashiCorp Vault for secure secrets management. Replace environment variables and .env files with HashiCorp Vault for secure secrets storage. Configure Vault on Hetzner VPS and integrate with Dagster resources.|priority:critical,type:infrastructure,component:resources,iteration:sprint-1|16-24|TBD|TBD|TBD"
    "Task 1.2: Retry Logic - Exponential Backoff|Implement exponential backoff retry logic with circuit breaker. Add retry logic with exponential backoff for transient failures (network timeouts, S3 rate limits). Implement circuit breaker pattern for repeated failures.|priority:critical,type:feature,component:assets,iteration:sprint-1|8-16|TBD|TBD|TBD"
    "Task 1.3: Dead Letter Queue (DLQ)|Implement Dead Letter Queue for failed records. Create DLQ mechanism to store failed records for later analysis and reprocessing. Implement DLQ structure in Hetzner S3 and create reprocessing job.|priority:critical,type:feature,component:assets,component:jobs,iteration:sprint-1|16-24|TBD|TBD|TBD"
    "Task 1.4: Unit Tests - Core Assets|Implement unit tests for core assets (80%+ coverage). Create comprehensive unit test suite for all assets, jobs, and sensors. Achieve 80%+ code coverage.|priority:critical,type:test,component:assets,iteration:sprint-1|24-40|TBD|TBD|TBD"
    # Sprint 2 - High
    "Task 2.1: Caching Layer - Redis|Implement Redis caching layer. Add Redis caching to reduce repeated S3 calls and cache intermediate results. Configure Redis on Hetzner VPS and integrate with Dagster.|priority:high,type:feature,component:resources,component:assets,iteration:sprint-2|16-24|TBD|TBD|TBD"
    "Task 2.2: Business Logic Validation|Implement business logic validation layer. Add business rules validation beyond schema validation. Validate coordinates, bike counts, and timestamp sequences.|priority:high,type:feature,component:assets,iteration:sprint-2|16-24|TBD|TBD|TBD"
    "Task 2.3: Structured Logging - structlog|Implement structured logging with correlation IDs. Replace basic logging with structured logging using structlog. Add correlation IDs for request tracing.|priority:high,type:feature,component:resources,iteration:sprint-2|8-16|TBD|TBD|TBD"
    "Task 2.4: CI/CD Pipeline - GitHub Actions|Implement CI/CD pipeline with GitHub Actions. Create automated CI/CD pipeline for testing and deployment to Hetzner VPS.|priority:high,type:infrastructure,component:ci-cd,iteration:sprint-2|16-24|TBD|TBD|TBD"
    "Task 2.5: Unit Tests - Jobs and Sensors|Implement unit tests for jobs and sensors. Extend unit test coverage to jobs and sensors. Complete test suite to 80%+ coverage.|priority:high,type:test,component:jobs,component:sensors,iteration:sprint-2|16-24|TBD|TBD|TBD"
    # Sprint 3 - Medium
    "Task 3.1: Monitoring & Observability - Prometheus + Grafana|Implement monitoring and observability stack. Deploy Prometheus and Grafana for metrics collection and visualization. Create dashboards for pipeline health.|priority:medium,type:feature,component:monitoring,iteration:sprint-3|24-32|TBD|TBD|TBD"
    "Task 3.2: Parallel Processing - multiprocessing/Dask|Implement parallel processing for large datasets. Add parallel processing capabilities using multiprocessing or Dask for improved performance on large datasets.|priority:medium,type:feature,component:assets,iteration:sprint-3|16-24|TBD|TBD|TBD"
    "Task 3.3: Infrastructure as Code - Ansible|Implement Infrastructure as Code with Ansible. Create Ansible playbooks for reproducible infrastructure provisioning on Hetzner VPS.|priority:medium,type:infrastructure,component:ci-cd,iteration:sprint-3|16-24|TBD|TBD|TBD"
    "Task 3.4: Integration Tests|Implement integration tests for end-to-end pipeline. Create integration tests that verify the entire pipeline from data ingestion to analytics.|priority:medium,type:test,component:pipeline,iteration:sprint-3|16-24|TBD|TBD|TBD"
    # Sprint 4 - Low
    "Task 4.1: Materialized Views - DuckDB|Implement materialized views for common queries. Create DuckDB materialized views for frequently executed queries to improve performance.|priority:low,type:feature,component:assets,iteration:sprint-4|8-16|TBD|TBD|TBD"
    "Task 4.2: Data Lineage Tracking|Implement data lineage tracking. Track data transformations through the pipeline for auditability and debugging.|priority:low,type:feature,component:assets,iteration:sprint-4|16-24|TBD|TBD|TBD"
    "Task 4.3: Advanced Data Quality - Great Expectations|Implement advanced data quality checks with Great Expectations. Add anomaly detection and data profiling using Great Expectations.|priority:low,type:feature,component:assets,component:monitoring,iteration:sprint-4|24-32|TBD|TBD|TBD"
    # Sprint 5+ - Low
    "Task 5.1: Performance Tuning|Optimize pipeline performance. Identify and optimize performance bottlenecks in the pipeline.|priority:low,type:refactor,component:pipeline,iteration:sprint-5|16-24|TBD|TBD|TBD"
    "Task 5.2: Documentation Updates|Update and improve project documentation. Ensure all documentation is up-to-date and comprehensive.|priority:low,type:docs,component:pipeline,iteration:sprint-5|8-16|TBD|TBD|TBD"
    "Task 5.3: Security Hardening|Implement additional security measures. Add security hardening measures to protect the pipeline and data.|priority:low,type:infrastructure,component:ci-cd,iteration:sprint-5|16-24|TBD|TBD|TBD"
    "Task 5.4: Disaster Recovery Procedures|Implement disaster recovery procedures. Create and document disaster recovery procedures for the pipeline.|priority:low,type:infrastructure,component:ci-cd,iteration:sprint-5|16-24|TBD|TBD|TBD"
    "Task 5.5: API Rate Limiting|Implement API rate limiting and throttling. Handle external API rate limits gracefully to prevent throttling and ensure reliable data fetching.|priority:low,type:feature,component:assets,iteration:sprint-5|8-16|TBD|TBD|TBD"
    "Task 5.6: Data Archival and Purging|Implement data lifecycle management with archival and purging. Create automated data archival and purging procedures to manage storage costs and retention policies.|priority:low,type:feature,component:pipeline,iteration:sprint-5|16-24|TBD|TBD|TBD"
)

ISSUE_COUNT=0
for task in "${TASKS[@]}"; do
    IFS='|' read -r -a task_parts <<< "$task"
    TITLE="${task_parts[0]}"
    DESCRIPTION="${task_parts[1]}"
    LABELS="${task_parts[2]}"
    ESTIMATE="${task_parts[3]}"
    ASSIGNEE="${task_parts[4]}"
    START_DATE="${task_parts[5]}"
    TARGET_DATE="${task_parts[6]}"
    
    echo "  - Creating issue: $TITLE"
    
    # Create issue
    ISSUE_NUMBER=$(gh issue create \
        --repo "$REPO_OWNER/$REPO_NAME" \
        --title "$TITLE" \
        --body "$DESCRIPTION" \
        --label "$LABELS" \
        --assignee "$ASSIGNEE" \
        --format json \
        --jq '.number' 2>/dev/null || echo "0")
    
    if [ "$ISSUE_NUMBER" != "0" ]; then
        ISSUE_COUNT=$((ISSUE_COUNT + 1))
        echo -e "${GREEN}    ✓ Issue #$ISSUE_NUMBER created${NC}"
        
        # Add issue to project using item-add
        gh project item-add "$PROJECT_ID" \
            --owner "$REPO_OWNER" \
            --item-id "$ISSUE_NUMBER" \
            --format json \
            --jq '.id' > /tmp/item_id 2>/dev/null || echo "  ⚠ Failed to add to project"
    else
        echo -e "${YELLOW}    ⚠ Issue may already exist or failed to create${NC}"
    fi
done

echo ""
echo -e "${GREEN}✓ Created $ISSUE_COUNT issues${NC}"
echo ""

# Summary
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Project URL: ${YELLOW}https://github.com/${REPO_OWNER}/${REPO_NAME}/projects${NC}"
echo ""
echo -e "${GREEN}Next Steps (Manual via GitHub Web Interface):${NC}"
echo "1. Open the project board at the URL above"
echo "2. Configure field options (Status, Priority, Type, Component, Iteration as SINGLE_SELECT)"
echo "3. Add iterations manually (Sprint 1-5) in project settings"
echo "4. Create views: Team Backlog (table), Iteration Planning (board), Roadmap (timeline)"
echo "5. Configure automation rules for item status changes"
echo "6. Set up charts for progress visualization"
echo "7. Share the project with your team"
echo ""
echo -e "${GREEN}Field Configuration Required (manual):${NC}"
echo "  - Status: Backlog, Todo, In Progress, In Review, Done, Blocked, Cancelled"
echo "  - Priority: Critical, High, Medium, Low"
echo "  - Type: Feature, Bug, Refactor, Test, Docs, Infrastructure"
echo "  - Component: Pipeline, Assets, Jobs, Sensors, Resources, Infrastructure, Monitoring, CI/CD"
echo ""
echo -e "${GREEN}Iterations to create manually:${NC}"
echo "  - Sprint 1: $(date -d '+1 day' +%Y-%m-%d) to $(date -d '+14 days' +%Y-%m-%d) - Critical Foundation"
echo "  - Sprint 2: $(date -d '+15 days' +%Y-%m-%d) to $(date -d '+28 days' +%Y-%m-%d) - Production Readiness"
echo "  - Sprint 3: $(date -d '+29 days' +%Y-%m-%d) to $(date -d '+42 days' +%Y-%m-%d) - Observability & Performance"
echo "  - Sprint 4: $(date -d '+43 days' +%Y-%m-%d) to $(date -d '+56 days' +%Y-%m-%d) - Advanced Features"
echo "  - Sprint 5+: $(date -d '+57 days' +%Y-%m-%d) onwards - Optimization & Polish"
echo ""
