# NCAcademy

An independent NCE Core education application with schools, classes, assignments, student-authored work and instructor-controlled tutoring. Its administrative workspace follows Proposal Studio's layout and integration-management conventions. Admin, School, Teacher and Student access is enforced by the API. Teachers may be human users or explicitly approved Core agent accounts. Models may select approved hints/questions; they cannot supply newly generated prose to students.

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

The dedicated `amplify-academy-fargate` recipe deploys the Amplify frontend, Fargate API and private PostgreSQL database. The manifest pins the private ECR image by digest. Core identity uses `https://core.nnytech.ai`; the canonical callback is `https://ncacademy.app/api/auth/core/callback`. See [deployment](docs/deployment.md) for release inputs and verification. The school-permissions change is prepared locally; its source changes do not deploy themselves or activate agent credentials.

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
