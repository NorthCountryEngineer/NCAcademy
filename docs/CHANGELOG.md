# Changelog

## 2026-10-17

- Created independent NCAcademy source with Proposal Studio-style administration, Core identity, courses, assignments, student-authored work and instructor review.
- Added approved-content tutoring selection, bounded model interfaces, immutable submissions and audited student-session replay/export.
- Prepared the authorized release to `NorthCountryEngineer/NCAcademy` through Core's dedicated `amplify-academy-fargate` recipe, using `ncacademy.app`, protected API origin `api.ncacademy.app` and the existing Core issuer.
- Pinned the production API image by ECR digest. Separated owner-only migration and restricted runtime database credentials/execution roles; the API cannot rewrite audit events or change schema and has no AWS task role.
- Documented owner-only initial access, disabled live models, independent retained audit exports and operator-run KMS-signed checkpoints. Student grants remain withheld until audit/access controls are verified; no automatic checkpoint worker is included.
- Updated publication, deployment, verification and recovery instructions. Public deployment completion is established by operational acceptance checks.

Validation: 63 API/script tests with isolated real PostgreSQL privilege checks, 11 frontend tests and 129 Core onboarding checks (113 Core plus 16 GitHub gate) passed. Frontend build, local container health and synthetic coursework/browser workflows passed. See [verification](verification.md) for scope and remaining deployed acceptance.
