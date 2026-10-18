# NCE Core onboarding

This directory defines NCAcademy's fixed deployment recipe for NCE Core. Core changes are reviewed in an isolated checkout; the active NCE Core and Proposal Studio checkouts are preserved. See [deployment requirements](../docs/deployment.md) for current release status and admission requirements.

## Inspected contract and supplied change

Core baseline: `b2c4172fbe500a7d9744a8d6246b8a5bdf3026c9`. The original `amplify-fargate` manifest requires both an API image and a browser image. Its trusted template fixes `studio.runtime` API/worker/migration commands, API readiness on port 8000 and browser health on port 8010. Application manifests cannot override these commands, ports, templates or IAM roles. Core accepts arbitrary application identifiers but its original trusted CI policies cover only existing Core and Proposal Studio repositories. An unchanged Proposal Studio recipe cannot start NCAcademy correctly.

[core-onboarding.patch](core-onboarding.patch) adds the separate fixed `amplify-academy-fargate` recipe. [core-patch-baseline.json](core-patch-baseline.json) identifies the exact baseline, patch hash, changed files and offline validation. The patch contains the trusted runner/validator changes, application/portal choice and database migration, exact repository CI policy, a dedicated API/PostgreSQL infrastructure template, documentation and regression tests. It provisions no browser service, worker, object bucket or application task role. The original Proposal Studio recipe is retained.

| Contract | Academy value |
| --- | --- |
| Frontend | `apps/web`, locked npm build → `dist/index.html` |
| API/migration | One approved immutable private ECR image, ARM64 |
| API command | `python -m academy.runtime api` |
| Migration command | `python -m academy.migrate` |
| API admission | Port 8000, `/api/ready` |
| Runtime | UID/GID 10001, read-only root, task scratch at `/tmp` and `/app/.local` |
| Identity | `/api/auth/core/callback`, public PKCE and paired confidential introspection client |
| Permissions | `academy-use`, `academy-school`, `academy-teach`, `academy-audit`; Core adds reserved human `admin` |
| Source policy | `NorthCountryEngineer/NCAcademy`, `.github/workflows/ci.yml`, `NCAcademy required checks` |

The owner-approved source repository is `NorthCountryEngineer/NCAcademy`. A different target requires updating the explicit Core CI policy before release.

## Supplemental school and agent permissions

The frozen onboarding patch above describes the original recipe adoption. The supplemental [school-agent-permissions.patch](school-agent-permissions.patch) targets reviewed Core `79e9d5284c087fbcce67eb34b3efcfe46f6fbfa6`, after that adoption. [Its separate baseline](school-agent-patch-baseline.json) records the exact patch hash, changed files and offline validation. Do not apply the original onboarding patch twice or silently rebase either patch onto unrelated active Core changes.

The supplement adds `academy-school` to Academy's simulated registration vocabulary and extends the operator-owned Academy configuration with `allow_agent_teachers`, default false. The trusted runner maps it to a constrained `AllowAgentTeachers` parameter. Only when enabled does ECS inject `CORE_WORKLOAD_TOKEN` from the existing approved runtime secret's `core_workload_token` JSON key into the API. Migration receives `ALLOW_AGENT_TEACHERS=false` and no workload token. The existing Basic introspection SSM parameter, API/migration execution-role resources and absence of an application AWS task role remain unchanged. Model inference is an independent switch.

Core retains the installed identity-contract guard: a later source manifest cannot silently add permissions to an existing installation. Before adopting this revision, review an explicit package/installation upgrade to the exact `identity` block from the reviewed `nce-deploy.json`, including permission names. Core's installation-upgrade path creates `academy-school` while retaining external installation/service/deployment/client IDs. Preserve current role/tag grants; a new permission definition must not automatically assign School access to members. The supplemental patch does not bypass this guard or mutate a live installation.

Validate without changing a Core checkout:

```sh
python3 scripts/test_school_agent_patch.py --core "/absolute/path/to/reviewed-Core-79e9d528"
```

The script verifies both patch hashes, requires the exact supplemental baseline, checks/applies/reverse-checks only in a temporary export, then runs the existing offline Core/cloud/CI checks in an installed image with networking disabled. `--apply-only` checks patch integrity/application without tests. A different Core revision requires a reviewed rebase and new baseline; the script never changes the active checkout or calls AWS.

## Validate the patch without modifying Core

The reproduction script checks Core's local HEAD, exports it to a temporary directory, checks/applies the patch there, and runs the regression suites in an already-installed local test image with networking disabled. It never applies the patch to the original checkout and never pulls/builds an image or calls AWS.

```sh
python3 scripts/test_core_patch.py --core "/absolute/path/to/NCE Core"
```

The default local image is `nce-core-ci-tests:20261016`, with the Core dependency runtime at `/app/.venv/bin/python`. Use `--image` for another already installed compatible image. The active Core checkout's uncommitted changes are intentionally excluded from its clean HEAD export. A different Core HEAD requires explicit rebase/review of this patch; do not force application against an unreviewed revision.

## Operator configuration

After reviewing and applying the patch through Core's own release process, enable `amplify-academy-fargate` for the intended platform/environment and approve the repository. The separate operator-owned `academy_fargate_apps` map requires:

```json
{
  "NorthCountryEngineer/NCAcademy": {
    "image_repository": "ACCOUNT.dkr.ecr.REGION.amazonaws.com/PLATFORM-academy-api",
    "runtime_secret_arn": "OWNER_APPROVED_RUNTIME_SECRET_ARN",
    "certificate_arn": "OWNER_APPROVED_REGIONAL_CERTIFICATE_ARN",
    "api_host": "api.academy.example.invalid",
    "allow_live_models": false,
    "allow_agent_teachers": false
  }
}
```

These examples are intentionally invalid placeholders. The real image repository, secret and certificate must belong to the approved platform account/region/prefix. `allow_live_models` and `allow_agent_teachers` are independent optional booleans, false by default, controlled by Core's operator settings; application manifests cannot enable either. The runtime secret contains a stable random `session_secret` used for sessions and encrypted vault records. Its optional `core_workload_token` key is required before agent authentication can be enabled and must belong to the approved consuming installation/environment. Core transfers its separate confidential Basic client credential directly to the deployment's SSM parameter. ECS injects secret values only into the backend boundary. No keys belong in `nce-deploy.json`, frontend variables, logs, CI or chat.

Before agent admission, the operator separately approves the Core account/binding, roles, consuming application/environment/task, registered resources and workload-compatible tags; the human School/Admin approves exact Academy placements/classes. The owner-only service tag is not loosened automatically. A local school resource assignment never substitutes for Core authorization. See [school permissions](../docs/permissions.md).

The template reuses Core's existing scoped Fargate provisioning role and application permissions boundary. Separate API and migration execution roles pull only the approved API image, inject assigned secrets and write logs. Application code has no AWS task role. Private PostgreSQL connections use `verify-full` and the RDS trust bundle.

The retained owner credential is injected exclusively into the controlled migration task. A separate generated retained `DatabaseRuntimeSecret` holds the API's `academy_runtime` credential. The API execution role cannot read the owner secret. Migration receives `ACADEMY_RUNTIME_DB_USERNAME` and `ACADEMY_RUNTIME_DB_PASSWORD`, bootstraps that login after schema creation, and restricts API audit events to SELECT/INSERT. The API cannot create schemas or update/delete audit events. Migration's execution role remains inside existing platform pass-role/secret namespaces. Independent retained checkpoints still remain a production student-admission requirement. See [deployment requirements](../docs/deployment.md) and [audit limits](../docs/audit.md).

## Source preparation and release

The original onboarding and school/agent recipe changes above have already been adopted by the October 18 school-permissions release. The later local teacher revision includes a separate [existing-binding authorization supplement](teacher-binding-authorization.patch) against Core `e6eb31f3caed491445e2582e34b8da43b3556d97`. Its [baseline receipt](teacher-binding-authorization-baseline.json) records exact file/patch hashes and 30 offline Core checks plus 33 mocked Academy adapter checks. These are scoped tests, not a full Core CI run or production/PostgreSQL acceptance.

The new read-only `POST /api/platform/v1/agent-bindings/authorize/` authorizes a selected existing agent binding under its own installation's workload credential, current account/definition/roles and resource/task/environment policy. It issues no agent credentials and changes no account, placement, grant or cloud setting. It is required for Core-bound selected-teacher tutoring and fails closed if absent. Application-managed AI personas have no Core principal and do not require this supplement for approved public-corpus Lesson Q&A. Release the supplement through Core's normal process before enabling the bound-teacher path. The active Core source and deployed service were preserved during this work.

```sh
python3 scripts/check_package.py
python3 scripts/package_source.py
```

The source archive and SHA256 inventory are written under ignored `build/`. Runtime data, private `.env`, credentials, dependency directories, frontend build output and Git history are excluded. [Source packaging](../scripts/package_source.py) uses explicit source directories and rejects source symlinks and secret-like runtime files. The archive includes this patch and review baseline.

The manifest has the explicit unresolved marker `REQUIRES_IMMUTABLE_PRIVATE_ECR_DIGEST`. Source validation permits this documented preparation state; release validation rejects it:

```sh
python3 scripts/check_package.py --release
```

For a later authorized release, publish the locally tested API image into an approved immutable private ECR repository, replace that marker with the actual image digest, commit the final source and require that exact commit's successful push CI. Apply and deploy the reviewed Core patch and pin the resulting trusted runner commit before preparing NCAcademy's Core release. Core will create the initial resources with the API stopped, register identity, configure secrets, run migration, wait for API health and publish the separately built frontend. Migration failure keeps the API stopped. Review retention, least privilege, restore verification, Core login, tenant/course access and live provider controls before admitting real student data.

The local Docker base tags intentionally follow the chosen Python 3.12, Node 22 and nginx release families. Capture reviewed immutable base-image digests and refresh vulnerability checks when making the production release. This local source package does not claim a production vulnerability scan or cloud acceptance test.
