<!--
Sync Impact Report
==================
Version Change: N/A → 1.0.0 (Initial Constitution)

Modified Principles:
- All principles are newly established

Added Sections:
- Core Principles (8 principles)
- Performance Standards
- Security Requirements
- Development Workflow
- Governance

Removed Sections:
- None

Templates Requiring Updates:
- ✅ .specify/templates/plan-template.md - Constitution Check section will reference new principles
- ✅ .specify/templates/spec-template.md - Requirements align with performance and security principles
- ✅ .specify/templates/tasks-template.md - Task categorization reflects new principles
- ⚠ N/A - No command files found in .specify/templates/commands/

Follow-up TODOs:
- None - All placeholders filled with concrete values
-->

# Bike Data Flow Constitution

## Core Principles

### I. Performance-First

All systems must meet established performance targets: API response time <500ms (p95), page load time <2s (FCP), cache hit rate >80%. Performance is a non-negotiable requirement that drives architectural decisions. Use connection pooling, multi-layer caching (Edge + Redis + Browser), and query optimization to achieve targets. Monitor performance metrics continuously and optimize when thresholds are exceeded.

**Rationale**: Performance directly impacts user experience and system scalability. Slow APIs cause user abandonment, while efficient caching reduces database load and operational costs. Established targets align with Core Web Vitals standards and ensure the dashboard remains responsive under load.

### II. Data Quality & Validation

All data must be validated at multiple pipeline stages using Pydantic and Pandera schemas. Implement hash-based duplicate detection, type conversion, and data cleaning. Maintain data lineage tracking and processing success monitoring. Never bypass validation for convenience.

**Rationale**: Data quality is foundational for reliable analytics and user trust. Invalid or duplicate data leads to incorrect insights, wasted processing resources, and user confusion. Multi-stage validation catches errors early, while lineage tracking enables debugging and accountability.

### III. Event-Driven Architecture

The pipeline uses sensor-based automatic processing triggered by new data arrivals. Zero manual intervention is required once the pipeline is enabled. Implement intelligent duplicate detection, partition-aware processing, and state management via cursor mechanism. Sensors monitor S3 for new raw data files every 30 seconds.

**Rationale**: Event-driven architecture eliminates manual toil, reduces latency between data arrival and availability, and scales automatically with data volume. Sensor-based triggering ensures timely processing while duplicate detection prevents redundant work.

### IV. Serverless & Scalable

Deploy on Vercel serverless platform with automatic scaling. Use Next.js 14+ App Router with Route Handlers for API endpoints. Configure regional deployment (fra1, waw1) for European users. Leverage Edge functions for global distribution and automatic SSL/TLS. Zero-config deployment is required.

**Rationale**: Serverless architecture reduces operational overhead, provides automatic scaling for variable traffic patterns, and eliminates infrastructure management. Regional deployment minimizes latency for target users, while Edge functions enable global distribution with lower cold start times.

### V. Public Access

No authentication is required for bike-sharing data access. The dashboard is publicly accessible to all users. Implement rate limiting (100 req/min via token bucket algorithm) to prevent abuse, but do not gate content behind authentication. Use CORS configuration to allow necessary origins.

**Rationale**: Public bike-sharing data is a civic resource that should be freely accessible. Authentication creates unnecessary friction for users and adds complexity. Rate limiting protects against abuse while maintaining open access.

### VI. Modern Tech Stack

Use Next.js 14+ for serverless API routes and SSR/SSG, shadcn/ui for modern accessible components, Plotly.js for interactive visualizations, and DuckDB for analytical processing. Follow established conventions documented in `.claude/memory/conventions/`. Prefer typed languages (TypeScript) and modern frameworks.

**Rationale**: Modern tech stack provides built-in optimizations, community support, and long-term maintainability. Next.js offers automatic performance optimizations, shadcn/ui ensures accessibility compliance, Plotly.js provides Python/JavaScript parity for analytics, and DuckDB enables direct S3 querying without ETL.

### VII. Observability

Implement structured logging with Pino including request ID tracing. Monitor API performance, cache hit/miss ratios, database query times, and error rates. Use Vercel Analytics for Core Web Vitals and real user monitoring. Track uptime, resource utilization, and user engagement metrics.

**Rationale**: Observability enables proactive issue detection, performance optimization, and data-driven decision making. Structured logging with request IDs enables distributed tracing, while comprehensive monitoring ensures performance targets are met and issues are resolved quickly.

### VIII. Security

Validate all inputs using Zod schemas. Implement rate limiting via token bucket algorithm (100 req/min). Configure CORS to allow only necessary origins. Use Vercel environment variables for secret management. Provide read-only S3 credentials for dashboard access. Implement CSRF protection for mutation endpoints.

**Rationale**: Security protects against abuse, data breaches, and unauthorized access. Input validation prevents injection attacks, rate limiting mitigates DoS, CORS controls cross-origin access, secret management prevents credential exposure, and read-only credentials limit blast radius.

## Performance Standards

All systems must meet the following performance targets:

| Metric | Target | Measurement |
|---------|---------|--------------|
| API Response Time | <500ms (p95) | Vercel Analytics |
| Page Load Time | <2s (FCP) | Core Web Vitals |
| Time to Interactive | <3s (TTI) | Core Web Vitals |
| Cache Hit Rate | >80% | Redis/Upstash Analytics |
| Error Rate | <0.1% | Vercel Analytics |

Performance targets are non-negotiable. Systems failing to meet targets must be optimized before deployment. Use connection pooling, materialized views, data sampling for large datasets, and multi-layer caching to achieve targets.

**Rationale**: Performance standards ensure consistent user experience and system reliability. Clear targets enable objective measurement and drive architectural decisions. Failing to meet targets degrades user experience and increases operational costs.

## Security Requirements

All systems must implement the following security measures:

- Input validation using Zod schemas for all API endpoints
- Rate limiting via token bucket algorithm (100 requests per minute)
- CORS configuration allowing only necessary origins
- Secret management via Vercel environment variables (never commit secrets)
- Read-only S3 credentials for dashboard data access
- CSRF protection for mutation endpoints (POST, PUT, DELETE, PATCH)
- Structured logging of security events without exposing sensitive data

Security is a cross-cutting concern that must be considered at all stages of development. Review security implications for all code changes.

**Rationale**: Security requirements protect against common attack vectors and ensure data integrity. Input validation prevents injection, rate limiting mitigates abuse, CORS controls access, secret management prevents credential exposure, read-only credentials limit blast radius, and CSRF protection prevents cross-site request forgery.

## Development Workflow

### Code Review

All pull requests must undergo code review before merging. Reviewers must verify compliance with constitution principles, performance targets, and security requirements. Use the Constitution Check section in implementation plans as a gate before proceeding with development.

### Testing

Tests are optional unless explicitly requested in feature specifications. When tests are required, write tests first and ensure they fail before implementation (TDD). Use pytest for Python tests and appropriate testing frameworks for TypeScript/React.

### Deployment

Git push to main branch triggers automatic Vercel deployment. Preview deployments are created for pull requests. All deployments must pass health checks at `/api/health`. Monitor deployment logs and performance metrics after each deployment.

### Documentation

Update relevant memory files in `.claude/memory/` when making architectural decisions or changing conventions. Document new patterns, performance optimizations, and security measures. Keep README.md and other documentation up to date.

**Rationale**: Structured development workflow ensures code quality, enables knowledge sharing, and prevents regressions. Code review catches issues early, testing validates functionality, automated deployment reduces toil, and documentation preserves institutional knowledge.

## Governance

### Amendment Procedure

Constitution amendments require documentation of the proposed change, rationale, and impact analysis. Amendments must be approved via pull request with at least one reviewer endorsement. Version must be incremented according to semantic versioning rules. All dependent templates and documentation must be updated to reflect changes.

### Versioning Policy

Constitution version follows semantic versioning (MAJOR.MINOR.PATCH):

- MAJOR: Backward incompatible governance changes, principle removals, or redefinitions
- MINOR: New principle or section added, or materially expanded guidance
- PATCH: Clarifications, wording improvements, typo fixes, or non-semantic refinements

### Compliance Review

All pull requests and code reviews must verify compliance with constitution principles. Complexity must be justified in the Complexity Tracking section of implementation plans when constitution violations are necessary. Use memory files in `.claude/memory/` for runtime development guidance on conventions, architecture decisions, and best practices.

### Constitution Supremacy

This constitution supersedes all other practices and guidelines. When conflicts arise between this constitution and other documentation, the constitution takes precedence. Update conflicting documentation to align with constitution principles.

**Version**: 1.0.0 | **Ratified**: 2026-01-09 | **Last Amended**: 2026-01-09
