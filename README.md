# NCAcademy

An independent NCE Core education application with schools, classes, assignments, student-authored work and instructor-controlled tutoring. Its administrative workspace follows Proposal Studio's layout and integration-management conventions. Admin, School, Teacher and Student access is enforced by the API. Teachers may be human, managed AI personas or explicitly approved Core agent accounts. Assignment tutoring and Lesson Q&A select approved hints/questions or exact public-source excerpts. Separately approved Homework Help generates explanations through configured triage, tutor and safety models, while students retain authorship of submitted work.

The owner has authorized publication to `NorthCountryEngineer/NCAcademy` and deployment through NCE Core. The canonical frontend is `https://ncacademy.app`, with `https://api.ncacademy.app` as the protected backend origin. The current schema-11 source adds generative Homework Help, metadata-only Core delivery and independent archive monitoring. Source preparation and test results are recorded below; public availability is established only by the deployment acceptance checks. The original NCAcademy archive contained infrastructure scaffolding; [requirements](docs/requirements.md) records the design's boundaries.

## Local preview

Docker Compose is the supported self-contained preview. Run from this directory:

```sh
python3 scripts/setup_local.py
docker compose up --build -d
```

Open `http://localhost:8090`. Choose an explicitly labeled local demonstration identity to inspect instructor, student and administrator views. The demo records are synthetic and stored only in this project's dedicated PostgreSQL volume. Local mode rejects live model traffic. `docker compose down` stops the application and retains local records. Do not use the demo sign-in for an externally accessible service.

## Review the design and package

- [Requirements and deferred decisions](docs/requirements.md)
- [Architecture](docs/architecture.md)
- [School roles, resources and agent teachers](docs/permissions.md)
- [School setup, curriculum and knowledge workflows](docs/school-onboarding.md)
- [All 2,030 official NYSED course options and custom classes](docs/ny-course-catalog.md)
- [New York standards planning and tutoring coverage](docs/ny-curriculum-2026-review.md)
- [Teacher onboarding and standards corpora](docs/teachers-and-standards.md)
- [Student-curated multimedia lessons](docs/student-curated-lessons.md)
- [Student learning preferences and feedback](docs/student-learning-preferences.md)
- [Local student/parent accounts, PII encryption and testing](docs/family-accounts.md)
- [Conversational Homework Help](docs/homework-help.md)
- [Google Classroom assignment companion](docs/google-classroom.md)
- [School and class groups with NCE Core Chat](docs/group-chat.md)
- [Optional ElevenLabs teacher voices](docs/homework-voice.md)
- [Persistent S3-compatible session archive](docs/session-archive.md)
- [Core audit metadata delivery and archive operations](docs/audit-delivery.md)
- [YouTube lessons and the school audit agent](docs/external-media-review.md)
- [API and data contracts](docs/api.md)
- [Model interfaces and instructional controls](docs/model-controls.md)
- [Session evidence and audit controls](docs/audit.md)
- [Deployment requirements and release order](docs/deployment.md)
- [Core issuer migration and stable application identities](docs/core-issuer-migration.md)
- [NCE Core onboarding package and patch](nce/README.md)
- [Operations and recovery](docs/operations.md)
- [Source review and packaging](docs/source-review.md)
- [Verification and production acceptance](docs/verification.md)
- [Changelog](docs/CHANGELOG.md)

The dedicated `amplify-academy-fargate` recipe deploys the Amplify frontend, Fargate API and private PostgreSQL database. Core identity uses `https://core.nnytech.ai`; the canonical callback is `https://ncacademy.app/api/auth/core/callback`. Teacher onboarding, standards corpora, private multimedia lessons and external-media review were released on October 18, 2026 at source `78b48d6ba0e874ff7e76576119ff5e020f3627e0`. This subsequent source revision adds student learning preferences, feedback, conversational Homework Help, optional restricted ElevenLabs voice and a separate session archive worker. Its image reference deliberately remains unresolved until a matching image is verified; release uses Core’s normal exact-commit CI and migration process. See [deployment](docs/deployment.md) for release inputs and verification.

Human School/Admin users can create human or AI teacher profiles with a portrait, biography and common metadata. AI profiles add a structured teaching approach. One profile can be assigned to many classes, with independent grade/year/standards context. Class corpora are automatically assembled from reviewed standards/source packages and class resources, then explicitly reviewed. Creating or approving a profile records teaching configuration; Core identities, grants and actual class teaching authority remain separate. See the [onboarding guide](docs/teachers-and-standards.md).

School creation continues into a resumable setup workspace for logo/custom metadata, a state/custom/no-curriculum choice, grade/year course planning, the teaching team and visible knowledge-build runs. The reviewed-source curriculum agent prepares draft class plans; a human explicitly creates selected classes and approves their course corpora and teacher configurations. The separate complete NYSED 2026–2027 course catalog offers all 2,030 official entries, including Regents subjects, gym/PE, art, band, music, performance and career courses. Courses → Create course also supports custom classes with no state binding in every school. Neither path requires a ready AI corpus. The older 67-pathway standards planning review covers Pre-K through grade 12 and remains a separate preparation layer. Framework/topic reviews remain separate from the two partial science teaching packages; most subjects still need exact alignment and instructional materials before corpus approval. Custom curriculum ingestion remains deferred. See the [comprehensive review](docs/ny-curriculum-2026-review.md) and [school setup](docs/school-onboarding.md).

Groups adds Core-backed class conversations and school discovery. Current enrollment automatically includes students in their required class group. Administrators, teachers and currently authorized class teaching agents can create groups; students can join eligible open groups or request/invited membership. Agent messages require human School/Admin approval. Core stores messages through the application's identity while immutable NCAcademy receipts retain each actual author. Live chat is independently disabled until the existing Core installation's package/grants and API-only runtime inputs are explicitly configured. See [group chat](docs/group-chat.md).

Students can connect their own Google Classroom account to a local class, read published assignments and keep NCAcademy Homework Help alongside them. Native work and submission open in Google Classroom. Imported Google content stays in the private transient assignment reader; help uses the student's own question and approved local course context. The integration defaults off pending dedicated Google OAuth and trusted backend configuration. See [Google Classroom](docs/google-classroom.md).

Course Lesson Q&A runs automatically within a human-approved behavior/corpus/model policy, with shared daily UTC quotas and complete restricted session replay. Assignments, lesson materials and feedback retain human publication approval. The starter NY 2026–2027 high-school environmental-science mapping is partial and supplies 13 mapped standard records and four public teaching summaries. Other states/subjects/years require reviewed adapter packages.

Students can also request private curated lessons from a task description or assignment photos when the teacher's approved policy permits it. Lessons combine cited reading, supported interactive diagrams, optional browser read-aloud and recorded reflections. Photos are normalized/encrypted and visible only to the enrolled student or current authorized reviewers. Models select approved source/media IDs; no generated assignment response is released. See [student-curated lessons](docs/student-curated-lessons.md).

Students complete a nine-question learning-preference intake before new tutoring activity. Preferences remain editable; structured feedback on their own interactions adjusts bounded presentation weights for that student and teacher. Accuracy reports enter the audit evidence for review. Preferences cannot change approved teaching content, factual claims, publication requirements or model authority. See [student learning preferences](docs/student-learning-preferences.md).

Homework Help now supports a separately human-approved school AI assistant for every enrolled class, including human-taught courses, and general study questions. Configured triage, tutor and safety models generate bounded plain-text explanations and guided practice, with coursework references, explicit photo consent, personal feedback and auditable conversation history. Completion/refusal controls reduce risk but cannot guarantee every answer is correct or every assessed-work request is caught. The legacy approved-excerpt flow and assignment/publication controls retain their boundaries. Voice for generated replies remains deferred. The legacy optional voice flow creates a reviewable transcript or reads an exact released teaching reply; students choose a school-approved stock voice per teacher. No personal clone is offered. Current ElevenLabs default voices have account eligibility and retirement limits; see [voice setup](docs/homework-voice.md).

Initial access is restricted to the owner with both Core `admin` and `academy-use` permissions. Live models remain disabled and no student grants are issued until audit retention and access controls are verified. The API uses a restricted database login and has no AWS task role. A separate encrypted, signed S3-compatible archive worker and private Kubernetes storage package are prepared independently of the API. They require operator configuration and verified retention, monitoring and restore before student admission; source packaging does not establish a running archive. Existing operator-run exports and KMS-signed checkpoints remain supported. See [session archiving](docs/session-archive.md).

## Verification

Human application Admins can create platform-local student and parent accounts, replace passwords, assign schools/classes and retain grade/year and lifecycle history. Students and parents sign in with a username/password without a Core identity. Parents require an enrolled child and receive a limited progress view. A separate PII key encrypts private profiles, educational payloads and related audit evidence; production family access fails closed without it. Local family accounts cannot link Google Classroom or use Core Chat/resources. See [account administration, encryption and test instructions](docs/family-accounts.md).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r apps/api/requirements-test.lock.txt
.venv/bin/python -m pip install -r apps/archive/requirements.lock.txt
PYTHONPATH=apps/api:apps/archive .venv/bin/python -m pytest apps/api/tests apps/archive/tests scripts/tests -q
cd apps/web
npm ci --no-audit --no-fund
npm test
npm run build
```

Use Python 3.12+ and Node 22.12+ for release tooling. Container builds supply these runtimes. All tests use synthetic student records and mocked Core/model transports. See the verification record for the checks actually performed and remaining live acceptance steps.

## Data and source

Runtime data, `.env`, signing/vault secrets, dependencies, test reports and generated release archives stay outside Git. Keep the session encryption bootstrap unchanged across image updates and matching database restore. Source archives contain an explicit reviewed source inventory and checksums, never student records. Publication and deployment follow the owner's authorization and Core's exact-commit release checks.

Teachers can submit YouTube lessons and external teaching copies for a separate school audit agent. Administrators control the reviewer model, limits, expiry and automatic clean text approval policy. YouTube requires a human viewing check; students explicitly load approved players. Exact current approvals gate course retrieval and lesson assembly. See [external media review](docs/external-media-review.md).
