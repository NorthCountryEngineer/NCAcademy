# Requirements and acceptance boundaries

Updated October 18, 2026 for local school-permissions development. The requested outcome is an education service integrated with NCE Core, using Proposal Studio's administrative UX and deployment process, with stricter model behavior and instructor-readable student sessions. The existing service uses `ncacademy.app` as the canonical frontend. This permissions revision is prepared locally; its publication and cloud adoption are separate release actions.

## Evidence and continuity

The local reference `Downloads/NCAcademy-development` (May 2024) contains a README, license and generic Terraform infrastructure: VPC, ECR, ECS and S3. It contains no usable course application or educational requirements. This implementation preserves the education/GenAI objective from the current request; historical feature parity cannot be established from that archive. The current Proposal Studio source and Core recipes were inspected locally; they are the reference for administrative and release conventions, not a source of customer records or proposal workflows.

## Functional acceptance

| Area | Required behavior |
| --- | --- |
| Identity | NCE Core owns production identities, organization permissions and application registration. No student self-elevation or role picker in production. |
| School access | Admin accesses all application schools; School administrators manage assigned schools/resources; Teachers use school resources and maintain assigned classes; Students consume enrolled coursework and own work. Core organization remains the deployment tenant. |
| Agent teachers | Approved Core agent accounts have their own identity, placement and class assignments. Every request checks live Core authority and local scope. Agent-authored student-facing changes require human approval; agents cannot write student work or administer credentials/roles. |
| Curriculum | Instructors create courses, group assignments into modules, enroll students, publish assignment prompts and configure assistance. |
| Student work | Students save their own attempts and submit immutable versions. Generated drafts and automated submission are absent. |
| Tutoring | Assignment-scoped disabled, curated and Socratic assistance; student attempt required, bounded turns and model context/output tokens, explicit model binding. |
| Output control | Model output selects only a vetted instructor-authored hint/question ID. No model prose, answers, essays, tools or document generation reach students. |
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

No full LMS interoperability (LTI/SCORM/gradebook sync), bulk roster import, file/video ingestion, vector search, voice, autonomous grading, proctoring, model training or course marketplace. These need separate scoped designs. The old infrastructure does not establish that they previously existed. A subsequent open-ended tutoring mode must undergo separate threat modeling and evaluations; it cannot replace the fixed-content release barrier silently.
