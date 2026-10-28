# Changelog

## October 28, 2026 — student names and grade-aware enrollment

- Replaced opaque student labels in human class-management enrollment with names, optional preferred names, usernames and current school grade/year. The general school directory and agent-facing roster retain their existing privacy boundary.
- Added bounded name/username search, class-grade defaults, explicit other-grade/all-grade and unset-grade filters, and paginated choices. Already enrolled, inactive and other-school students are excluded; existing enrollment permissions and deliberate cross-grade placement remain unchanged.
- Student search terms stay in protected JSON request bodies; audited, noncached responses return no contacts, birth dates, notes, family links or historical placements. No schema, key, identity or external-provider change is introduced.

## October 28, 2026 — complete Grade 3 mathematics mapping

- Added the exact New York 2026–2027 Grade 3 mathematics package: 44 content/component records and eight Mathematical Practices, 27 original teaching references and a nine-unit local sequence, with official source hashes and page citations. Other grades retain their existing framework preparation; complete mapping does not claim a full published lesson or assessment bank. See [scope, sources and setup](ny-grade3-mathematics.md).
- Added exact-grade curriculum planning and corpus coverage indicators while retaining explicit human corpus/profile/model approval, class authority, student authorship and existing historical evidence. Preparation introduces no schema or permission change and makes no provider call.
- Added narrowly reviewed GPT-6 Chat Completions parameters only for exact OpenAI endpoint/model matches, with unchanged approved output caps and no retries or fallbacks. Other compatible endpoints retain their existing request contract. See [transport compatibility](openai-chat-compatibility.md).
- Fetch the identical pinned Python base from Docker’s official ECR Public mirror after a shared CodeBuild Docker Hub HTTP429. CI explicitly selects Docker Hub to avoid ECR Public’s shared-runner anonymous data limit; only the registry prefix is selectable, with the OCI digest fixed. The OCI index and AMD64/ARM64 manifests, application dependencies, native role overlay and runtime behavior are unchanged.

## October 27, 2026 — practice-game display controls

- Added a full-screen toolbar and expanded-window fallback around the existing approved practice player, with an Exit button and native Escape handling. F and Alt+Enter shortcuts are available while the toolbar has focus; the pinned game's own focused keyboard behavior remains unchanged.
- Kept every attested player/runtime byte, the runtime contract and digest, lesson approval, imported snapshot and scoring behavior unchanged. This presentation layer adds no schema migration, new approval or automatic game operation.

## October 27, 2026 — administrative Game Studio launch

- Added persistent Game Studio shortcuts to human Admin/School settings after exact school-connection verification; saved creator readiness survives reloads and scoped save/verify actions refresh the same-page shortcut without external automatic requests.
- Connected creator links request Game Studio's own Core PKCE sign-in automatically through an exact host and `login=auto`, without forwarding Academy tokens, roles or local family identities.
- Restricted creator links to human administrators and reject duplicate, foreign or token-bearing launch parameters. Teachers retain public-catalog draft preparation only.
- Preserved imported game definitions, approvals, the existing player/runtime contract and encrypted scoring history; this navigation change adds no migration or game/score rewrite.

## October 27, 2026 — host-owned game assignment points

- Added independent per-class game scoring policies with 25/100/custom scales, proportional/completion/explicit arcade-target rules, rounding and repeat-attempt settings.
- Added server-admitted attempts, immutable encrypted observations and calculations, student history and separate human-reviewed point awards. Browser gameplay remains provisional evidence.
- Bound scoring to exact game/publication/policy/runtime snapshots with idempotent result submission, current class authority and additive schema-16 protections.
- Updated the retained player wrapper's runtime contract to require renewed human game review; the fixed arcade engine and existing evidence remain unchanged. See [configuration and verification](game-assignment-scoring.md).

## October 27, 2026 — student avatars and moderation

- Added private student avatar uploads and approved-only workspace display with bounded, metadata-free raster normalization.
- Added a school-approved image-scanning interface, exact provider/policy consent, fixed quarantine rules and bounded single-send execution. Sexual content remains hidden; violence, other safety concerns and uncertainty require human review.
- Added an administrator review queue with explicit sensitive-image reveal, risk-score/threshold explanations, immutable model/policy/image evidence, reasoned decisions and interrupted/late-scan handling.
- Added independent PII encryption for all student avatar evidence and additive schema-15 SQL protections; earlier identities, ciphertext and audit history are preserved.

## October 26, 2026 — Game Studio integration

- Add a school-scoped public Game Studio connection, explicit catalog/descriptor checks and separate top-level authoring navigation.
- Import exact published game data into immutable Academy packages; associate drafts with class learning objectives and lesson labels, and require human review before student use.
- Play approved snapshots in Academy's fixed opaque-origin sandbox without external accounts, nickname boards, scores, storage or arbitrary game code. Recheck current publication and class authority before student playback.
- Document the next steps for course-aware conversational authoring, educational engine extensions and reviewed game selection in curated lessons.

## Administrative workspace — October 26, 2026

- Reworked administrative settings around Proposal Studio's searchable integration marketplace, administrative agent and skill catalogs, while retaining Academy's tutoring, Homework Help, audit and deployment policy controls.
- Added versioned administrative job profiles and reusable skills over fixed school-readiness, reviewed-curriculum planning and advisory model-review actions. Saving starts no work; runs retain accepted definitions and cannot publish teaching content, change policy or grant authority.
- Added school-scoped Agent activity for recorded administration and existing agent workflows. Legacy student work stays protected behind the separate authorized audit views.
- Added schema-13 revision/run protections, safe operational projections, explicit pricing/budget admission and no automatic resend after uncertain provider calls.

## Course teacher selection — October 23, 2026

- Fixed course configuration listing staff accounts as the only teacher choices. Class teachers now selects reusable school human/AI profiles and reflects assignments already made in Teachers, including profiles with pending teaching preparation.
- Kept the responsible staff account under Class administration. Profile selection shares the existing class-assignment and knowledge-workflow path without granting an AI persona account permissions or publishing authority.
- Unchanged selections preserve approvals, model policies and other class assignments. Changed assignments retain the existing review requirements; no profile, corpus or model is automatically approved.


## Teacher model policies and routing foundations — October 23, 2026

- Added a prominent AI-teacher Models workspace with independently saved/reviewed/approved school-integrated primary models and per-activity limits. Model swaps preserve teacher metadata, profile approval, assigned classes, student preferences and course corpora.
- Added scoped immutable model-policy revisions/approvals and schema-12 SQL protection. Active-policy/connection changes withhold in-flight replies without provider fallback; policy-only swaps retain approved Homework history.
- Added explicit school opt-in for teacher-specific Homework text models. Existing approvals/defaults and named photo destinations remain compatible; independent triage, safety and shared student budgets remain mandatory.
- Added fixed-primary execution receipts and bounded no-call routing previews with allowlisted candidates/rules. Automatic per-question switching and price/budget settlement are documented as subsequent implementation phases; unknown cost is explicit.
- Documented administration, APIs, privacy/audit boundaries, release migration and cost-aware routing acceptance in [teacher models](teacher-models.md).


## Independent teacher profile review — October 23, 2026

- Removed the dependency on assigned class corpus approval from **Approve current profile**. Human administrators can review a human or AI teacher's biography, picture and teaching approach while class knowledge preparation is pending.
- Kept immutable versioned profile/context approval, current school authority and separate teaching-material review. Pending corpora remain unavailable to class tutoring; profile approval grants no identity or teaching permissions.
- Clarified readiness guidance and added regression coverage for the approval action, unchanged teaching guards and reapproval after class materials change.

## Enrollment-independent Homework Help chat — October 21, 2026

- Made Homework Help a provider-neutral conversation workspace with a left conversation list, selected chat, new conversation and photo-capable composer. It no longer silently falls back to a class-selection flow when AI setup is unavailable.
- Removed course enrollment as a prerequisite for general help. Triage can answer directly in its own model operation, with independent safety review and no separate tutor call, or bring in a matching approved school AI teacher.
- Added explicit school approval for public AI-teacher personas outside enrolled classes. Protected coursework, corpus access and class-only audit remain enrollment-scoped; school review retains complete private evidence.
- Advanced the generation approval boundary while preserving configuration/session IDs, immutable historical records, encryption, quotas, current-authority checks and no-retry handling for uncertain model sends. Existing school approvals require human review of the new behavior. No new schema or Core grants are introduced.
- Updated configuration/review copy, API contracts, documentation and mocked regressions. Production activation still requires approved school model connections and the separate live-model gate.

## Complete NYSED course choices and independent custom classes — October 20, 2026

- Added every one of the 2,030 official 2026–2027 NYSED course entries across 25 subject areas, preserving codes, exact source text, reporting levels and indicators. PE, arts, band, orchestra, chorus, dance, theater, practical subjects and CTE are included without teaching-readiness filters.
- Added searchable, paginated course selection, subject/level filters and verified Regents course discovery. Retained both official source workbooks and deterministic offline source parity checks, including all 48 state-assessment rows and 12 Regents course codes.
- Made Custom course an explicit supported creation path with no inherited school/NYS binding. Optional standards alignment stays separate; explicit null clears a binding on edit and omission preserves existing historical metadata.
- Added server-owned catalog provenance and exact-source validation for direct human saves and human publication of agent proposals. Class creation requires no ready corpus or AI approval. Existing teaching, school/class authority and student-work controls remain intact.
- Updated guides, API/OpenAPI, CI source checks, tests and source/container packaging. Released through the normal NCE Core workflow on October 20, 2026.

## Generative Homework Help and durable audit delivery — October 20, 2026

- Added a human-approved school AI assistant with separate triage, tutoring and safety-model configuration. It serves enrolled human/AI-taught courses and general study questions, supports explicit course/general correction, bounded conversation history, approved optional public grounding and server-owned references.
- Added generated plain-text explanations and guided practice behind completion/refusal checks and independent output review. Retained narrower assignment/Q&A/curated-material publication rules. Added three-model photo capability/consent, personal preference feedback, current-authority/snapshot checks, durable phase markers and no uncertain inference retries.
- Added private session/history UI, School/Admin full review, class-scoped teacher/agent review and scoped metadata-delivery status. Schema 11 encrypts new local-family evidence and protects immutable revisions, approvals, sessions, send markers and sealed terminal messages without rewriting earlier audit bytes.
- Added a transactional metadata-only Core pipeline outbox, independent least-privilege dispatcher, stable idempotent delivery, leases/backoff and immutable receipts. Core receives no transcript/photo/identity/credential payloads. The separate archive adds independent-checkpoint continuity, durable success receipts and freshness monitoring; scheduled examples remain suspended pending actual deployment acceptance.
- Updated API/OpenAPI, encryption, operating/recovery and deployment documentation, mocked regressions and local source packaging. The application, schema-11 migration and frontend were released on October 20, 2026. Live inference and the separately packaged background workers remain unconfigured.

## New York curriculum scope review — October 18, 2026

- Added four reviewed 2026–2027 domain catalogs covering Pre-K through grade 12, core academic subjects, languages and language support, computing, health/finance, academic arts and career studies. Local electives, state bands, proficiency levels and practical limitations remain explicit.
- Added broad subject selection and reference-topic planning alongside the existing partial science teaching packages. Reference-only snapshots retain source evidence and missing alignment/material gaps; teaching approval and student-output controls remain unchanged.
- Added Pre-K context, a searchable grouped subject picker, larger bounded school plans and continued 100-course publication batches.
- Documented 2026 examination transitions, finance/climate phase dates, World Languages and WIDA changes, secure science materials, accessibility and pending graduation proposals. Added a reproducible grade/topic CSV and source register. This local revision makes no production or provider call.
- Reset the release image input to the normal unresolved source-preparation marker; the deployed baseline image cannot serve these new backend bundles. Affected corpus snapshots require refresh and human review. No new schema migration is needed.

## Local student and parent accounts — October 18, 2026

- Added human Admin account/profile creation, password replacement, versioned school/class enrollment, explicit grade/year/lifecycle transitions and retained progress snapshots. Parents are optional, require an enrolled local child and receive a limited own-family progress dashboard.
- Added platform-only username/password sign-in, encrypted salted scrypt verifiers, temporary-password change, session rotation/revocation, database attempt limits and bounded expensive hashing. Local accounts cannot receive staff roles or bind/sync with Core or Google.
- Added a separate purpose-bound AES-256-GCM PII key for private profiles, credentials, educational payloads and related audit evidence. Ciphertext remains the original hashed audit evidence; authorized replay exposes a separate private projection. Added schema 10 immutable encrypted family events and raw-store/tamper/authority tests plus encryption/recovery/test documentation.
- Prepared matching source locally. A dedicated API-only production PII secret input, schema-10 migration, matching immutable image and normal deployment acceptance remain required; no real student account, secret injection, push or deployment is implied.

## Voice activation deferred — October 18, 2026

- Recorded the owner's decision to defer live voice setup and credential lookup while retaining the configuration, voice selector, recording/transcript review, exact-reply playback hooks and mocked tests. This documentation-only update changes no runtime or production configuration.

## Google Classroom companion source revision — October 18, 2026

- Added a per-class student workspace with private Google OAuth connection, current active-course linking, published assignment reader, native Google Classroom links and compact NCAcademy Homework Help alongside it. No whole-site iframe or published Classroom add-on is claimed.
- Added a fixed-endpoint read-only transport, minimal scopes, login-bound one-use OAuth state/PKCE, encrypted personal credentials/mappings, current school/class/Google enrollment checks and release-time revision checks. No Google content is automatically sent to models, corpora, generic session replay or the archive.
- Added safe operation evidence, explicit refresh/reconnect handling and local erasure before remote revocation, including owner cleanup after enrollment loss. Schema 9 protects receipts while preserving previous records and audit history.
- Prepared disabled-by-default configuration and documented Google's OAuth/admin prerequisites and the independent trusted Core runtime configuration required for activation. Local mocked verification creates no Google account connection, cloud change, push or deployment.

## NCE Core Chat groups source revision — October 18, 2026

- Added school discovery, open/request/invited group membership and group creation for human administrators/teachers and currently assigned Core teaching agents. Required class groups derive student membership directly from current enrollment; removal revokes reads, sends and receipt access.
- Integrated Core conversation/message APIs through the NCAcademy installation, with exact local author/delivery receipts, bounded plain text/replies, cursor polling and immutable membership evidence. Unknown upstream messages are withheld. Agent publications require current human School/Admin approval and renewed original-agent authority checks.
- Added durable create/send markers, explicit original-key message recovery and fail-closed uncertain room creation. The web never adopts a room based on title/topic, adds native Core participants or automatically replays uncertain operations.
- Added schema 8 immutable chat evidence, sealed attempts and additive migration/readiness without rewriting historical records. Chat enablement is independent of live models/agent admission and requires an explicit existing Core package/installation and trusted API runtime configuration upgrade. Local tests use mocked Core; no push or deployment is implied.

## School curriculum and onboarding source revision — October 18, 2026

- Added a resumable school setup workspace with normalized logo, custom metadata, versioned state/custom/no-curriculum configuration and the shared human/AI teacher onboarding form. AI profiles support bounded advisory teaching instructions and may remain unassigned base personas.
- Added a deterministic reviewed-source curriculum planning agent, immutable draft plans, exact grade/subject/year course selections and transactional idempotent course/corpus materialization. School reconfiguration preserves existing classes and historical plan evidence.
- Added a reviewed partial New York high-school earth/space-science package alongside environmental science. Grade scheduling is local; state standards versions, source-review dates and academic-year applicability are recorded independently.
- Added visible school knowledge-workflow receipts for class-corpus preparation and teacher assignment, with actual sources/gaps, snapshot references and human-review links. Setup grants no Core authority, enables no live models and publishes no generated student materials.
- Added schema 7 immutable workflow protections and readiness, preserving prior evidence. Custom curriculum import remains a declared extension point; no live crawling, provider call, public push or production deployment is implied by local staging.

## Homework Help and optional voice source revision — October 18, 2026

- Added student-owned conversations with enrolled-course triage/clarification, approved AI teacher handoff, private homework photos, bounded same-persona follow-up context and server coursework references. It reuses the existing approved-source lesson lifecycle and learning-feedback controls.
- Added immutable message/outcome records, explicit same-key recovery before lesson admission, shared tutoring limits and independent daily Homework storage allowances. School/Admin review discovers pre-triage conversations; teachers retain class-only evidence.
- Added school ElevenLabs configuration in the existing integration UX, per-student teacher voice choice, explicit microphone/transcript review and protected exact-released-text read-aloud. Fixed verified stock IDs exclude owner/private clones; documented provider eligibility and December 31, 2026 retirement limit the current catalog.
- Added schema 6 protection/readiness and archive refusal of accidental audio bytes/ciphertext. Recordings remain memory-only; transcripts and exact visible/voice processing evidence enter the durable audit chain. No live speech/model calls, push or deployment is implied by source preparation.

## Student learning preferences and session archive source revision — October 18, 2026

- Added a nine-question first-student-visit intake, neutral choices, editable settings and reset. New tutoring admissions require completed intake at the API as well as in the browser.
- Added own-interaction accuracy/helpfulness feedback and explicit style targets in assignment chat, Lesson Q&A and private curated lessons. Personal, bounded teacher-specific adaptation retains immutable revisions and exact interaction weight snapshots; accuracy concerns remain review evidence.
- Added schema 5 immutable preference/feedback protection and current-schema readiness without rewriting historical identities, submissions, terminal turns or audit bytes. Preference changes revalidate around send/release; existing idempotency and uncertain-send controls remain in force.
- Packaged a separate encrypted, signed S3-compatible session archive worker, private Kubernetes SeaweedFS option, restricted reader/writer examples and independently trusted recovery workflow. It adds no archive credentials or AWS role to the API. Deployment, retention, backup and monitoring require independent acceptance.
- Based this source preparation on released `78b48d6ba0e874ff7e76576119ff5e020f3627e0`. The new manifest intentionally has no deployable image pin until a matching API image is built and verified. No publication or cloud deployment is implied by these changes.

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
