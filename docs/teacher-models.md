# Teacher model assignment and routing plan

An AI teacher is a stable teaching identity, not a model. Name, portrait, biography, behavior, instructions, student preference weights, class assignments and content-addressed course corpora remain attached to the teacher when its execution model changes. Model policies have their own drafts, immutable revisions and human approvals. Saving or approving a model policy does not rewrite or reapprove the teacher profile, rebuild a corpus or change class permissions.

## Administrator workflow

1. Configure an enabled school integration and its allowed models in **Integrations**. Credentials remain write-only encrypted inputs.
2. Open **Teachers**, select an AI teacher, and choose **Manage models**. Select its primary integrated model. Set separate Lesson Q&A/curation and Homework Help permissions and bounded output limits.
3. Save a model draft, review the exact saved settings and approve it as a human School/Admin. A saved draft leaves the previous approved policy active. Stale versions fail rather than overwriting another administrator's changes. A model swap needs model-policy approval only.
4. To use teacher-specific models in Homework Help, enable **Use each matching AI teacher’s separately approved model settings for text questions** in the school's Homework Help AI configuration, then save/review/approve that school configuration. Existing school approvals retain their previous behavior until this opt-in is explicitly approved.
5. Review model details on Homework Help replies: actual phase/model, integration name, policy revision and normalized token usage. Routing previews show the suggested alternate separately from the model actually used. Price is **not available**, rather than zero, until trustworthy pricing is configured.

No model calls occur during saving, approval or preview. AI teachers and ordinary human teachers cannot approve model policies. An AI persona is still not a Core identity or permission grant. Profile, source/corpus, enrollment, current Core agent authority, safety review, encrypted audit and deployment model gates continue to apply independently.

An enabled policy with a disabled activity blocks that activity. Disabling the entire separately approved model policy explicitly restores legacy execution: the profile's existing lesson configuration and the school Homework Help tutor. A teacher without an approved model policy keeps this legacy behavior. An explicit active policy whose connection changed or became unavailable fails closed; there is no silent provider fallback.

## What executes in this release

The **fixed primary model** handles permitted teacher requests. `fixed` and `preview` are the only accepted routing modes; no automatic-switching mode is accepted. A preview classifies a question locally as `image`, `long_context` (over 2,000 characters), `reasoning` (bounded keyword heuristic), or `simple`, in that precedence order. It maps that class to an administrator-defined candidate/rule. These heuristics are advisory and are not an assessment of educational difficulty or answer quality.

Administrators can configure at most eight school-allowlisted candidates and one rule per request class. Preview never calls a candidate or classifier model. Questions longer than the configured preview bound still use the primary model; their execution receipt records `preview_limit_exceeded`. The administrative preview endpoint rejects input over that bound. The shared school/student daily reservation remains authoritative across models; the teacher's Homework output cap can only reduce the school cap.

Homework triage and independent safety remain school-controlled. If no teacher matches, triage continues to answer directly without an additional tutor call. Teacher-specific Homework model overrides apply to **text only** in this release. Homework photos use exactly the school models named in the student's confirmation, even when a teacher persona participates. A future photo override requires a new consent contract and explicit image-capability approval. Curated lesson pictures have a separate teacher-model image capability attestation; swapping the primary model clears that UI attestation for human review.

The provider prompt receives the same approved teacher behavior, admitted public course sources and bounded student presentation preferences regardless of primary model. Model selection never authorizes source retrieval or changes the teaching/completion boundaries. Lesson Q&A remains an approved-excerpt selector and curation remains a private approved-content assembly; Homework's generative permissions do not widen either activity.

## Execution and retained evidence

`teacher_model_config` is a school-scoped mutable draft/active pointer. `teacher_model_revision` and `teacher_model_approval` are immutable records protected by schema-12 SQL triggers and restricted runtime grants. Approval binds the exact policy hash, connection versions/credential fingerprints and execution boundary `teacher-models-v1`. Administrative descriptors omit endpoints, credentials and credential fingerprints. No API can edit a previous revision or approval.

New requests retain the execution-policy revision/approval/hash and admitted connection snapshot alongside the independent teacher personality/corpus snapshot. Durable phase markers bind the actual model before external I/O; the existing encrypted evidence contains request, visible response, routing decision and normalized usage. No hidden model reasoning is collected. Outbox delivery retains its existing metadata allowlist and does not copy transcripts into Core. Archive retention/worker activation remains separately configured; this feature does not establish a new running archive.

The current policy and connection are checked again before each send and before release. A policy change during an in-flight request withholds output and never resends or switches models. A policy-only model swap preserves previously released Homework history through its immutable approved snapshot. For Homework replay, revoking/changing the executed connection or losing profile, class or source authority can still withhold the reply. Lesson Q&A and curation retain their existing source/read authorization checks. Existing snapshots without a separate model policy preserve their legacy shape and checks.

## Plan for cost-aware live routing

1. **Pricing and capability catalog.** Add administrator-owned, versioned per-model input/output prices, currency, effective dates, context limits, image support, residency/privacy eligibility and maximum output. Display stale or unknown prices explicitly. Import provider prices only through a reviewed update workflow; do not infer free use from missing prices. Keep cost estimates separate from provider-reported billed usage.
2. **School routing policy.** Add an explicitly reviewed `automatic` mode after evaluation. Administrators choose allowed model pools, deterministic task rules, quality floors, per-question estimated-spend caps, daily student/school budgets, escalation thresholds and failure behavior. Teacher-level policies may narrow the school policy. Candidates cannot grant authority, alter personality or admit new data destinations.
3. **Selection and reservation.** Build an immutable route decision from bounded question characteristics and approved capabilities, estimate input/output cost, reserve aggregate budget atomically, then select one eligible model. Begin with deterministic local routing. An optional paid classifier needs its own approval, quota and audit receipt. Uncertain sends remain terminal barriers; escalation is a separately admitted operation only when a previous outcome is definitive, never an automatic resend.
4. **Evaluation before activation.** Replay a synthetic or explicitly authorized de-identified task set across candidate models. Measure learning quality, academic-integrity refusals, citation fidelity, image handling, uncertainty and latency as well as spend. Include cross-school isolation, profile/corpus preservation, stale policy, insufficient funds and provider-failure cases. Preview traces are proposals, not evidence that a cheaper model is suitable.
5. **Execution UI and rollout.** Extend reply details with selected rule, actual model, route-decision ID, price version, reserved estimate and settled reported usage/cost. Add an administrator comparison view for predicted versus actual choices, budgets and quality failures. Enable per school after human approval, begin with a small evaluated model pool, and retain immediate fixed-primary rollback. Student-facing teacher identity remains constant.

Acceptance for automatic routing requires explicit current school/teacher approval, known eligible pricing/capabilities or a deliberately configured unknown-price denial policy, atomic shared budget enforcement, unchanged source/personality admission and immutable before-send routing receipts. This release supplies the separate policy, candidate/rule schema, deterministic preview and execution evidence needed for that work; it does not yet switch live per question or promise cost savings.

## API and verification

- `GET /api/schools/{school_id}/teachers/{profile_id}/models`: saved draft, active approved policy and availability.
- `PUT .../models`: `{expected_version, policy}`; creates an immutable draft revision.
- `POST .../models/approve`: `{expected_version, revision_id}`; approves only the current saved draft.
- `POST .../models/preview`: `{text, has_images}`; evaluates the current saved draft without provider I/O.

Use the locked Python/Node dependencies and synthetic fixtures:

```sh
PYTHONPATH=apps/api:apps/archive ALLOW_LIVE_MODELS=false python -m pytest apps/api/tests/test_teacher_models.py apps/api/tests/test_homework_teacher_models.py apps/api/tests/test_homework_ai.py apps/api/tests/test_homework_ai_open_help.py -q
npm test --prefix apps/web -- --run
npm run build --prefix apps/web
```

Tests verify model swaps preserve exact profile/class/corpus bytes, separate human approval, stale/scope/archived denial, immutable policy SQL protections, actual primary selection, no preview inference, no uncertain resend, historical replay across model swaps and explicit photo/school opt-in boundaries. Production requires the normal owner schema-12 migration before restricted API readiness, exact-commit CI and matching image/frontend acceptance. Existing PII/session keys and operator-owned deployment bindings must be preserved. See [verification](verification.md) for completed results and [deployment](deployment.md) for release controls.
