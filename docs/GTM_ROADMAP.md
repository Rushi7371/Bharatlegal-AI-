# BharatLegal AI — Go-to-Market Product Roadmap

**Product ambition:** A commercially launchable, India-first legal operating platform for advocates and law firms. This is not an MVP-only roadmap. Existing code is the baseline and must be evolved without discarding working functionality.

## Product wedge and target customer
- Initial customer: independent advocates and small/mid-sized Indian law firms.
- Initial paid workflow: understand a matter's documents, research the relevant law from authoritative sources, and prepare a reviewable first draft.
- Expand later to corporate legal teams and other professional users after the advocate workflow is reliable.
- Product promise: Research → Understand → Draft → Manage → Prepare for filing.
- The product is an independent legal-workflow tool, not a court/government service. Advocates retain professional responsibility for advice and filing.

## Non-negotiable product standards
1. Primary-source grounding and traceable citations; never fabricate legislation, case names, paragraphs, citations or procedural deadlines.
2. Explicit jurisdiction, applicable-date and uncertainty handling, including the IPC/BNS transition.
3. Confidentiality and security by design: authentication, least privilege, encryption, tenant isolation, auditability, retention and deletion controls.
4. Human review and approval before legal documents are treated as final.
5. Accessible mobile-first workflows and reliable desktop experience.
6. Measurable quality: benchmarked legal accuracy, document extraction quality, latency, availability and user outcomes.

## Roadmap and release gates

### 0. Production foundation and audit — IN PROGRESS
- Inventory existing routes, code, provider configuration, deployment, logs and data flows.
- Fix release-blocking defects; establish automated tests, staging, CI checks, health/readiness checks and rollback procedure.
- Validate AI provider connectivity and failure handling without exposing secrets.
- **Gate:** repeatable build; automated API/UI smoke tests; no unresolved critical defects; operational runbook.

### 1. Core legal assistant — HARDEN
- Stable provider abstraction, timeout/retry/circuit-breaker behaviour and usage telemetry.
- Structured responses with legal issue, applicable law/date, assumptions, next steps and source references.
- English, Hindi and Marathi quality checks; safe fallback when language or law is uncertain.
- **Gate:** curated evaluation set passes agreed legal accuracy and safety thresholds; real provider calls tested in staging.

### 2. Indian legal research engine — NEXT MAJOR BUILD
- Start with a curated, versioned corpus of central legislation and Maharashtra materials; expand to other states and courts.
- Ingest only permitted/authoritative materials; preserve source URL, issuing body, publication/effective dates, version and retrieval timestamp.
- Search by exact citation, phrase, topic, court, date, jurisdiction and legal issue.
- Retrieval-augmented generation with paragraph-level source links, pinpoint references and quote verification.
- Track amendments, repeals, commencement and savings provisions.
- **Gate:** benchmarked retrieval recall and citation correctness; every answer can be traced to source material; stale/ambiguous law is flagged.

### 3. Matter and document intelligence
- Secure matter workspaces; multi-file upload; OCR for scans; extraction of parties, dates, events, claims, obligations and issues.
- Chronology, issue matrix, missing-document checklist, contradictions and cross-document comparison.
- No permanent storage until access control, encryption, retention and deletion are implemented.
- **Gate:** representative PDF/DOCX/scanned-file benchmark, extraction quality report and security review.

### 4. Professional drafting and contract intelligence
- Jurisdiction- and court-aware templates; clause library; drafting from approved facts and sources.
- Contract review, clause risk, obligations, renewal/termination dates, redlines and version history.
- Source/citation verification, placeholders for unknown facts and advocate approval workflow.
- **Gate:** lawyer-reviewed template set and evaluation; complete audit trail for generated and edited drafts.

### 5. Advocate workspace and practice operations
- Matters, clients, tasks, hearing dates, limitation reminders, notes, document vault and team collaboration.
- Calendar and notification integrations only with explicit permissions.
- **Gate:** end-to-end matter workflow tested by pilot firms; tenant isolation and access controls verified.

### 6. Identity, security, billing and administration
- Authentication, MFA, role-based access, organisation/tenant management and secure session lifecycle.
- Encryption in transit and at rest, secrets management, audit logs, backups, restore drills and incident response.
- Subscription plans, payment gateway, invoices, usage quotas, support and admin console.
- Privacy notices, terms, data processing and retention/deletion controls reviewed by qualified counsel.
- **Gate:** independent security assessment; backup/restore and incident exercises; billing reconciliation; privacy/legal review.

### 7. Commercial launch and scale
- Advocate design-partner programme, onboarding, training, support and feedback loops.
- Pricing validated through paid pilots, not guessed from competitor pricing.
- Product analytics: activation, time-to-value, weekly active professionals, repeat matter workflows, retention, conversion and support burden.
- Performance/load testing, monitoring, alerting, disaster recovery and release management.
- **Gate:** paid pilot acceptance, stable reliability, support readiness and documented launch/rollback plan.

## Current verified baseline (2026-11-09)
- Existing FastAPI backend and web/mobile PWA are deployed on Railway.
- Existing routes include AI chat, document analysis, drafting, health and a curated Maharashtra public-source catalogue.
- Document extraction currently handles text-based PDF, DOCX, TXT and Markdown, with a 12 MB request limit; scanned-PDF OCR is not implemented.
- Research currently provides source links/search entry points and a Maharashtra source catalogue. It is not yet a searchable, versioned, indexed legal corpus or a case-law database.
- The deployed environment has one web service and no attached persistent volume or database visible in the environment inventory.
- The service has provider key variable names configured, but variable presence alone does not prove provider credentials, quotas or model calls work.
- Railway deployment success confirms build/deploy status only. It does not prove legal accuracy, security, AI provider health or full user workflows.
- A duplicate translation-script declaration was found in the mobile page and removed in the latest source commits. The latest source commits still require deployment and post-deploy verification.

## Immediate execution order
1. Verify the two latest source commits deploy successfully.
2. Run production smoke tests for web, mobile, health, chat, drafting, upload/analysis and Maharashtra source endpoints; record actual HTTP outcomes and AI provider results.
3. Add automated backend tests and CI gates; create a staging environment before schema/security changes.
4. Design and implement the persistent data foundation (PostgreSQL; vector retrieval only where justified), tenant model, authentication and secure file storage before storing real client matters.
5. Build the first authoritative, versioned Indian legal corpus and citation-grounded retrieval pipeline.
6. Pilot the end-to-end research → document understanding → drafting workflow with advocates.

## Decision rule
Do not label a feature “complete” because code exists or a deployment is green. Use three statuses:
- **Implemented:** code/UI/API exists.
- **Verified:** automated and realistic workflow tests pass.
- **Launch-ready:** quality, security, privacy, performance, support and commercial gates pass.
