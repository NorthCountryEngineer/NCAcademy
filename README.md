# NCAcademy

An independent NCE Core education application with schools, classes, assignments, student-authored work and instructor-controlled tutoring. Its administrative workspace follows Proposal Studio's layout and integration-management conventions. Admin, School, Teacher and Student access is enforced by the API. Teachers may be human, managed AI personas or explicitly approved Core agent accounts. Models select approved assignment hints/questions or exact approved public-source lesson excerpts; they cannot supply newly generated prose to students.

The owner has authorized publication to `NorthCountryEngineer/NCAcademy` and deployment through NCE Core. The canonical frontend is `https://ncacademy.app`, with `https://api.ncacademy.app` as the protected backend origin. Source preparation and test results are recorded below; public availability is established only by the deployment acceptance checks. The original NCAcademy archive contained infrastructure scaffolding; [requirements](docs/requirements.md) records the design's boundaries.

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
- [Teacher onboarding and standards corpora](docs/teachers-and-standards.md)
- [Student-curated multimedia lessons](docs/student-curated-lessons.md)
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

The dedicated `amplify-academy-fargate` recipe deploys the Amplify frontend, Fargate API and private PostgreSQL database. Core identity uses `https://core.nnytech.ai`; the canonical callback is `https://ncacademy.app/api/auth/core/callback`. The school-permissions baseline was released on October 18, 2026 at source `c15d5068121bdb18e778976198430249c38c1098`. This revision adds teacher onboarding, standards corpora, private multimedia lessons and external-media review. Its manifest pins a separately verified immutable private ECR image; release uses Core’s normal exact-commit CI and migration process. See [deployment](docs/deployment.md) for release inputs and verification.

Human School/Admin users can create human or AI teacher profiles with a portrait, biography and common metadata. AI profiles add a structured teaching approach. One profile can be assigned to many classes, with independent grade/year/standards context. Class corpora are automatically assembled from reviewed standards/source packages and class resources, then explicitly reviewed. Creating or approving a profile records teaching configuration; Core identities, grants and actual class teaching authority remain separate. See the [onboarding guide](docs/teachers-and-standards.md).

Course Lesson Q&A runs automatically within a human-approved behavior/corpus/model policy, with shared daily UTC quotas and complete restricted session replay. Assignments, lesson materials and feedback retain human publication approval. The starter NY 2026–2027 high-school environmental-science mapping is partial and supplies 13 mapped standard records and four public teaching summaries. Other states/subjects/years require reviewed adapter packages.

Students can also request private curated lessons from a task description or assignment photos when the teacher's approved policy permits it. Lessons combine cited reading, supported interactive diagrams, optional browser read-aloud and recorded reflections. Photos are normalized/encrypted and visible only to the enrolled student or current authorized reviewers. Models select approved source/media IDs; no generated assignment response is released. See [student-curated lessons](docs/student-curated-lessons.md).

Initial access is restricted to the owner with both Core `admin` and `academy-use` permissions. Live models remain disabled and no student grants are issued until audit retention and access controls are verified. The API uses a restricted database login and has no AWS task role. Independent retained exports and KMS-signed checkpoints are operator-run; this release has no automatic checkpoint worker.

## Verification

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r apps/api/requirements-test.lock.txt
PYTHONPATH=apps/api .venv/bin/python -m pytest apps/api/tests scripts/tests -q
cd apps/web
npm ci --no-audit --no-fund
npm test
npm run build
```

Use Python 3.12+ and Node 22.12+ for release tooling. Container builds supply these runtimes. All tests use synthetic student records and mocked Core/model transports. See the verification record for the checks actually performed and remaining live acceptance steps.

## Data and source

Runtime data, `.env`, signing/vault secrets, dependencies, test reports and generated release archives stay outside Git. Keep the session encryption bootstrap unchanged across image updates and matching database restore. Source archives contain an explicit reviewed source inventory and checksums, never student records. Publication and deployment follow the owner's authorization and Core's exact-commit release checks.

Teachers can submit YouTube lessons and external teaching copies for a separate school audit agent. Administrators control the reviewer model, limits, expiry and automatic clean text approval policy. YouTube requires a human viewing check; students explicitly load approved players. Exact current approvals gate course retrieval and lesson assembly. See [external media review](docs/external-media-review.md).
