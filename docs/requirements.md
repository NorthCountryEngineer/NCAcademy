# Requirements and acceptance boundaries

Updated October 18, 2026 for teacher onboarding and course corpora. The requested outcome is an education service integrated with NCE Core, using Proposal Studio's administrative UX and deployment process, with stricter model behavior and instructor-readable student sessions. The existing school-permissions baseline is deployed at `ncacademy.app`; this subsequent teacher/corpus revision is local.

## Evidence and continuity

The local reference `Downloads/NCAcademy-development` (May 2024) contains a README, license and generic Terraform infrastructure: VPC, ECR, ECS and S3. It contains no usable course application or educational requirements. This implementation preserves the education/GenAI objective from the current request; historical feature parity cannot be established from that archive. The current Proposal Studio source and Core recipes were inspected locally; they are the reference for administrative and release conventions, not a source of customer records or proposal workflows.

## Functional acceptance

| Area | Required behavior |
| --- | --- |
| Identity | NCE Core owns production identities, organization permissions and application registration. No student self-elevation or role picker in production. |
| School access | Admin accesses all application schools; School administrators manage assigned schools/resources; Teachers use school resources and maintain assigned classes; Students consume enrolled coursework and own work. Core organization remains the deployment tenant. |
| Agent teachers | Managed AI profiles can provide automatic public-corpus Lesson Q&A after human approval, without becoming Core principals. External Core agents retain distinct identity, placement, live authority and actual class assignments. Assignments, materials and feedback require human publication approval; agents cannot write student work or administer credentials/roles. |
| Teacher onboarding | Human School/Admin uses a shared form for human/AI portraits, name, biography, title, pronouns, contact and expertise. AI adds structured tone, method, detail, encouragement, question frequency and additional boundaries. Profiles and actual Core identities are separate. |
| Reusable teachers | One stable AI profile can be planned for multiple classes/grades. Course-specific curricula and corpora do not overwrite its personality. Explicit approval captures exact versions; stale contexts cannot be used for tutoring. |
| Standards and knowledge | Automatically build an immutable, cited course corpus from reviewed jurisdiction packages and class materials. Initial mapping is New York high-school environmental science for 2026–2027, with explicit partial coverage. State/subject adapters extend the catalog without changing the onboarding UI. Unsupported applicability is unverified. |
| Curriculum | Instructors create courses, group assignments into modules, enroll students, publish assignment prompts and configure assistance. |
| Student work | Students save their own attempts and submit immutable versions. Generated drafts and automated submission are absent. |
| Tutoring | Assignment-scoped disabled, curated and Socratic assistance requires a student attempt. Separate course Lesson Q&A uses an approved teacher/profile/corpus/model policy and shared daily UTC quotas. Both reserve bounded capacity, audit before release and forbid uncertain automatic retries. |
| Private lesson curation | Enrolled students supply a task description and/or up to two normalized assignment photos. An explicitly approved teacher/model policy assembles a private cited lesson from public course passages, supported interactive diagrams and fixed reflection prompts, with optional browser read-aloud. Images require explicit student confirmation, encrypted evidence and current scoped review. No class publication or assignment completion. |
| Output control | Assignment models select vetted instructor-authored hint/question IDs. Lesson Q&A selects exact approved public teaching excerpts with citations and fixed style prompts. Provider-generated prose, essay writing, tools and document generation never reach students. |
| Administration | Proposal Studio-style organization/settings tabs, named model connections, approved models, write-only key replacement/clear and visible configuration status. |
| Review | Instructor views student attempt, prompt, released hint, refusal and outcome in order; can inspect policy/model versions, usage and export session evidence. |
| Audit | Durable event ordering, hash-chain verification, pre-send marker, fail-closed output release, idempotency, logged reviewer access and explicit uncertain outcomes. |
| Release | Independent source repository, locked dependencies, digest-pinned API image, Amplify frontend, PostgreSQL Compose test path, Core registration/release inputs, deployment and recovery instructions. |

## Initial deployment posture

Use instructor-approved content, text assignments and manual instructor review. Live model traffic stays off; curated guidance can operate without a provider. Production identity belongs exclusively to Core, with additional course membership enforced inside NCAcademy. Keep raw student records out of platform logs. Grant initial production access only to the owner with both `admin` and `academy-use`; issue no student grants until audit controls are verified.

The deployment uses `https://ncacademy.app`, protected API origin `https://api.ncacademy.app` and the canonical Core issuer `https://core.nnytech.ai`. The API receives a restricted PostgreSQL login, while owner-only migration uses a separate secret and execution role. An independent retained audit bucket and KMS-signed checkpoints are operated outside the application; no automatic checkpoint worker or API AWS task role is included.

The additive school migration preserves stable human owners, both reviewed Core issuer aliases, coursework, immutable submissions and historical audit hashes. Core resource assignments are an additional application boundary: a School administrator cannot grant upstream Core access by entering a resource ID. See [permissions](permissions.md) for role, agent and migration details.

## Production decisions retained for owner

Before admitting students, approve retention/deletion rules, checkpoint cadence, reviewer roles and recovery targets; review provider processing/retention terms and student notices; choose models and set price/budget ceilings; review instructor-authored hint banks; approve external audit checkpoint/retention controls and release inputs. These are deployment acceptance decisions. This package does not assert an education/privacy certification.

## Deliberate initial boundaries

No full LMS interoperability (LTI/SCORM/gradebook sync), bulk roster import, arbitrary file/video ingestion, vector search, voice conversation, generated video/audio assets, autonomous grading, proctoring, model training or course marketplace. Teacher portraits and student assignment photos are bounded normalized rasters; class teaching resources are scoped text. Read-aloud uses the browser voice service. The old infrastructure does not establish that these larger features previously existed. Automatic interactive tutoring and private source-based lesson assembly are permitted within the approved profile/corpus/model policy. Shared assignments, lesson materials and feedback still require human publication approval. See [teachers and standards](teachers-and-standards.md) and [private curated lessons](student-curated-lessons.md).

## External source and media review

Teachers must be able to submit and display approved YouTube lessons and supplied external text. Human School/Admin assigns a separate school audit agent with a named model, explicit policy, bounded quotas and review expiry. Clean text/corpus decisions may auto-publish only under an explicitly selected administrator policy; uncertain/flagged/rejected decisions never publish. YouTube requires human video/segment viewing and matching complete transcript evidence because the model sees supplied text/metadata. New external sources fail closed before review. First assignment extends the gate to existing public teaching corpora and direct student class resources; disabling the agent never removes it. Exact source, class suitability, policy and model changes invalidate decisions. Student display, lesson assembly and playback require current approvals; retained session evidence remains available to authorized reviewers. See [external media review](external-media-review.md).
