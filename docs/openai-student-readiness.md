# OpenAI student tutoring readiness

NCAcademy integrates directly with OpenAI. Application safeguards and OpenAI's
account approval are separate requirements. This is a technical verification
procedure, not a claim of provider approval or complete legal compliance.

## Provider approval and readback

The [under-18 guidance](https://developers.openai.com/api/docs/guides/safety-checks/under-18-api-guidance)
requires additional safeguards for minors and ZDR before processing under-13
personal data. Homework Help treats students conservatively as children,
including accounts without birth dates. A course name does not establish age.

Request eligibility through the sales/account-team route in
[OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).
Identify the educational use, under-13 users, worksheet images, exact project,
model, Chat Completions endpoint and processing tier. Use synthetic examples;
never attach student records or keys. Confirm image-safety exceptions,
model-specific prompt caching, retention conditions and reporting obligations.

`store:false`, disabled API-call logging, a school approval and a note claiming
ZDR cannot grant provider approval. Project `none` and Modified Abuse Monitoring
do not satisfy this integration's ZDR gate. Enhanced retention modes require
their additional processing/storage controls; the verifier refuses them until
those controls are separately implemented and reviewed.

An operator uses a separate Admin API key for retention readback and the selected
inference key for project/model identity checks. Do not put the Admin key in
Academy, Git, chat or the school form. Keep credential files private and remove
temporary exports after verification. The following tool makes only metadata
GETs, with no inference, configuration changes or approval request:

```sh
python scripts/verify_openai_student_controls.py \
  --admin-key-file /private/operator/openai-admin-key \
  --inference-key-file /private/operator/selected-inference-key \
  --connection-id CONNECTION_ID \
  --credential-fingerprint CURRENT_ENCRYPTED_CONNECTION_FINGERPRINT \
  --organization-id ORGANIZATION_ID --project-id PROJECT_ID \
  --model gpt-6-luna \
  --approval-evidence-file /private/operator/openai-approval-and-conditions.txt \
  --image-inputs-reviewed --valid-for-days 7 \
  --output /private/operator/openai-student-approvals.json
```

Select the same server-owned connection snapshot and inference credential. The
tool requires effective `zero_data_retention`, matching provider-reported
organization/project and model availability, plus image-handling review. It
records hashes of approval evidence and nonsecret readback. A private inference
key hash lets the API verify the actual decrypted key before sending; that hash
is never transmitted as provider request metadata. Failed or ambiguous checks
create no usable manifest. The tool cannot establish conditions omitted from
the approval evidence or make an organization eligible for ZDR.

Deploy the private manifest through exactly one trusted operator input:
`OPENAI_STUDENT_APPROVALS_FILE` or `OPENAI_STUDENT_APPROVALS_JSON`. Protect it
from application-user writes. Current Core hosting needs explicit operator
runtime configuration for that input and `ALLOW_HOMEWORK_MODELS`; school forms
configure neither. Preserve existing accounts, keys and identities around
activation. The Admin key is never a runtime input.

Evidence expires within 30 days. Reverify before expiry and when provider
conditions, project, model permissions or credentials change. Same-authority
renewal preserves its binding; changed authority requires human school
reapproval and photo confirmation. Missing, expired or mismatched proof blocks
student OpenAI processing. This is a trusted operator boundary, not a guarantee
against a deployment administrator forging a configuration assertion.

## Application safeguards

| Control | Enforcement |
| --- | --- |
| Separate authority | Protected operator proof, exact human school approval and the runtime model gate are all required. |
| Exact destination | Approved OpenAI organization/project headers and actual-key/connection fingerprints are checked before send. |
| Data minimization | Prompts omit original photo filenames and unnecessary message/session/identity metadata. Opaque routing references remain. Freeform text and image pixels still require ZDR. |
| Provider state | Bounded Chat Completions with `store:false`; no hosted threads, files, vector stores, tools, background execution or automatic inference retry. Provider model/cache/image exceptions still apply. |
| Abuse correlation | Stable HMAC-derived safety identifiers replace names, emails and raw student IDs in request metadata. |
| Child boundary | Child-readable disclosure, conservative safety instructions, no assessed-work completion, dangerous instructions, sexual exploitation or encouragement of secrecy from trusted adults. |
| Review | The existing independent review checks input risks and proposed responses. Fixed risk codes create human-review reports without another model call. |
| Reporting | Concern reporting covers every recorded message status, without resending inference or changing personality weights. |
| Escalation | Human Admin/School safety inbox with fixed priorities and immutable review/follow-up receipts. No external person is contacted automatically. |
| Evidence | Academy retains encrypted, access-controlled transcripts and safety evidence. Provider ZDR does not delete Academy's deliberately retained audit history. |

The operator must establish its privacy/consent basis and staff response process,
including monitoring high-priority reports. An administrator-created account or
model approval does not substitute for that responsibility. These controls do
not guarantee perfect classification, correctness or safety.

## Testing

Synthetic tests cover missing/expired/misbound proof, credential rotation,
reapproval, photo confirmation, opaque request metadata, safety report ownership,
immutable review, encrypted raw storage and no automatic resend. They never call
OpenAI or establish real ZDR eligibility.

```sh
PYTHONPATH=apps/api:apps/archive .venv/bin/python -m pytest -q \
  apps/api/tests/test_openai_student_privacy.py \
  apps/api/tests/test_provider_openai.py \
  apps/api/tests/test_homework_local_models.py \
  apps/api/tests/test_homework_safety.py \
  scripts/tests/test_verify_openai_student_controls.py
npm test --prefix apps/web -- --run
npm run build --prefix apps/web
```

Browser acceptance uses a synthetic worksheet and follow-up, student concern,
staff review, follow-up and resolution. Unknown outcomes recover by reading
recorded evidence. Full exact-commit CI and container checks precede release.
Live child acceptance additionally requires verified OpenAI approval and the
explicit activation steps above.
