# Changelog

## Unreleased local school permissions — October 18, 2026

- Added Admin, School, Teacher and Student application scopes with school resources, class ownership and explicit sharing. Core organization remains the deployment tenant; local grants never replace live Core capabilities.
- Added approved Core agent-teacher placements and a separate short-lived Bearer/introspection contract. Agents retain their own identities, classes and audit attribution, with human approval before proposed student-visible content is published. No agent may submit student work or administer roles/secrets.
- Prepared additive Default-school membership/resource scoping while retaining human identity aliases, existing record IDs/data, immutable submissions and historical audit hashes. School/session exports are partial; full tenant chain export remains Admin-only.
- Updated current operating references to `https://core.nnytech.ai` while preserving historical release/issuer records. Prepared an optional, disabled-by-default Core recipe path for agent authentication; no cloud configuration, credential issuance, GitHub publication or deployment is implied by these local changes.
- Reviewed compatibility with the completed Core domain upgrade. Aligned the supplemental recipe's full permission definitions with the Academy manifest and strengthened its registration regression check; the Core supplement and explicit installation upgrade remain separate release requirements.
- Updated the loopback container smoke workflow to recognize Teacher fixtures while retaining legacy Instructor compatibility.

## 2026-10-17

- Built and pinned the ARM64 API image containing the identity-preserving Core issuer upgrade for exact-source CI/release admission. Runtime issuer, database, service credentials and owner-only admission remain subject to the coordinated operator rollout.

- Prepared an additive Core authority-binding table and operator-only issuer backfill that preserve existing application user IDs, enrollment, immutable submissions and audit history. Login and live sessions validate the exact bound owner; no email/name linking is performed.
- Added reviewed no-write plans, complete subject-inventory/tenant/collision checks, transactional alias/session cleanup, an appended durable migration receipt and idempotent retry. Runtime bindings permit SELECT/INSERT only. Both reviewed issuer aliases support configuration rollback. See [issuer migration](core-issuer-migration.md) for schema, maintenance and verification requirements. This source preparation does not assert a deployed issuer change.

- Created independent NCAcademy source with Proposal Studio-style administration, Core identity, courses, assignments, student-authored work and instructor review.
- Added approved-content tutoring selection, bounded model interfaces, immutable submissions and audited student-session replay/export.
- Prepared the authorized release to `NorthCountryEngineer/NCAcademy` through Core's dedicated `amplify-academy-fargate` recipe, using `ncacademy.app`, protected API origin `api.ncacademy.app` and the existing Core issuer.
- Pinned the production API image by ECR digest. Separated owner-only migration and restricted runtime database credentials/execution roles; the API cannot rewrite audit events or change schema and has no AWS task role.
- Documented owner-only initial access, disabled live models, independent retained audit exports and operator-run KMS-signed checkpoints. Student grants remain withheld until audit/access controls are verified; no automatic checkpoint worker is included.
- Updated publication, deployment, verification and recovery instructions. Public deployment completion is established by operational acceptance checks.

Validation: 63 API/script tests with isolated real PostgreSQL privilege checks, 11 frontend tests and 129 Core onboarding checks (113 Core plus 16 GitHub gate) passed. Frontend build, local container health and synthetic coursework/browser workflows passed. See [verification](verification.md) for scope and remaining deployed acceptance.

Issuer-binding patch validation: 87 API/script tests passed with the optional PostgreSQL role test skipped; source-package validation passed. Independent synthetic PostgreSQL 16.15 validation passed 20 schema/migration checks and three targeted final-guard checks. No publication, production database operation, DNS or deployed issuer change was performed by this source preparation.
