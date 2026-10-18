# Changelog

## External media review source revision — October 18, 2026

- Added class YouTube lessons and supplied external teaching copies, strict canonical video identities, pinned bounded public metadata transport and click-to-load privacy-enhanced players. Raw iframe HTML, playlist/timestamp URLs, transcript scraping and pre-click third-party media loads are excluded.
- Added a separate school audit-agent assignment, bounded fixed findings, current exact-source reviews and configurable clean text/corpus automatic approval. YouTube retains human viewing/transcript checks because model coverage is supplied text/metadata only. Audit models receive no administrative API powers or student/private records.
- Added a permanent school public-teaching-corpus review gate on assignment, idempotent import, source/policy/model/class-context invalidation, expiry/revocation, current retrieval/send/release/history/playback checks and historical evidence preservation.
- Added schema 4 immutable external snapshots, policy revisions, results and publication decisions, sealed review jobs, additive audit receipt and readiness admission. Existing schema markers, identities, submissions and audit bytes are preserved. Prepared the matching schema-4 API/frontend for the normal exact-source production release.

## Private student-curated lessons source revision — October 18, 2026

- Added a student course activity for written task context and/or two normalized assignment photos, with named-model photo confirmation and private lesson history. Explicit human-approved curation/image options default off; existing profile approvals are preserved.
- Added one-call text/image source curation with strict document/associated visual/reflection IDs, cited reading, four source-linked interactive science diagrams, optional browser read-aloud and audited student reflections. No generated assignment response, shared publication, arbitrary model markup or grading is introduced.
- Added independent image verification/metadata stripping, encrypted admitted photo evidence, protected current student/reviewer reads, wire-request fingerprints and cipher-free replay/exports. Blocked pre-send photos retain metadata only. Invalid provider prose is retained as a hash.
- Shared Q&A/curation daily UTC quotas, pending-send blocks and idempotency; added cross-activity conflict checks, exact route-specific upload bounds and before-send/release revalidation. Kept publication, model enablement and Core/cloud deployment unchanged.

## Teacher onboarding and standards source revision — October 18, 2026

- Added school-scoped human/AI teacher onboarding with a shared portrait/name/biography/contact/expertise form and structured AI behavior. Stable profiles support multiple classes and grades, exact existing identity links, version conflicts and reversible archival.
- Added explicit human approval of immutable teacher-profile revisions and course corpus snapshots. Profile metadata never creates a Core account, credential or permission grant. Existing school/class authorization remains mandatory.
- Added state/subject/year/grade curriculum metadata, a reviewed New York 2026–2027 high-school environmental-science adapter, automatic corpus snapshots and bounded, cited retrieval. Standards versions are independent of school years; unsupported or expired mappings remain unverified. Coverage is explicitly partial.
- Added schema 3 protection for teaching snapshots at the ORM and SQLite/PostgreSQL trigger layers, an additive audit receipt and readiness admission. Historical records, identity bindings and audit bytes remain unchanged.
- Added automatic course Lesson Q&A within human-approved behavior, corpus and model policy. Managed personas have no Core identity or Teacher API permissions; external Core-bound teachers retain actual teaching authority and independent fresh Core checks. Assignments, materials and feedback retain human publication approval.
- Lesson Q&A selects exact approved public-source excerpts with citations and fixed style prompts; provider prose and teacher-only resources cannot reach students. Assignment tutoring retains approved hint/question IDs and a student attempt. Both pin exact context, reserve before send, revalidate before send/release and preserve full reviewer evidence.
- Added shared student/course daily UTC tutoring quotas across profiles, durable idempotency, cross-day pending-send blocking and terminal assignment/lesson turn immutability. Added distinct lesson replay/export without fictitious student drafts.
- Packaged a separate Core existing-binding authorization supplement against the reviewed deployed Core baseline. It changes no live Core checkout, issues no credentials and is required only for Core-bound selected-teacher tutoring.
- Based this revision on school-permissions release `c15d5068121bdb18e778976198430249c38c1098`; a separately verified immutable API image, exact-source CI and owner migration are required for this release.

## Released school permissions — October 18, 2026

- Added Admin, School, Teacher and Student application scopes with school resources, class ownership and explicit sharing. Core organization remains the deployment tenant; local grants never replace live Core capabilities.
- Added approved Core agent-teacher placements and a separate short-lived Bearer/introspection contract. Agents retain their own identities, classes and audit attribution, with human approval before proposed student-visible content is published. No agent may submit student work or administer roles/secrets.
- Prepared additive Default-school membership/resource scoping while retaining human identity aliases, existing record IDs/data, immutable submissions and historical audit hashes. School/session exports are partial; full tenant chain export remains Admin-only.
- Updated current operating references to `https://core.nnytech.ai` while preserving historical release/issuer records. Prepared an optional, disabled-by-default Core recipe path for agent authentication; no cloud configuration, credential issuance, GitHub publication or deployment is implied by these local changes.
- Reviewed compatibility with the completed Core domain upgrade. Aligned the supplemental recipe's full permission definitions with the Academy manifest and strengthened its registration regression check; the Core supplement and explicit installation upgrade remain separate release requirements.
- Updated the loopback container smoke workflow to recognize Teacher fixtures while retaining legacy Instructor compatibility.

Release completion: source `c15d5068121bdb18e778976198430249c38c1098` was published and deployed through Core. All four normal release steps succeeded. Sixteen public checks passed, and the read-only schema-2 probe verified preserved owner/identity/record/audit bytes. Live models and agent admission remained disabled. The subsequent local teacher revision above is separate from that release.

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
