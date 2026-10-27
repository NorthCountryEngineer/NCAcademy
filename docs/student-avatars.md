# Student avatars and moderation

Students use **Profile** to upload a school avatar. Human Admin/School administrators use **Administrative settings → Student avatars** or the school's **Student avatars** tab to review uploads and configure the scanner. Teachers, teaching agents, parents and other students cannot moderate or reveal quarantined images.

## Student workflow

1. Choose one still PNG, JPEG or WebP, at most 200,000 bytes and 1024 pixels per side. The server decodes and validates it, applies orientation, composites transparency on white and creates a fresh RGB JPEG of at most 512 pixels per side and 96 KiB. Original filenames, embedded location/EXIF, comments and color profiles are not retained with the image.
2. If the school has an available approved image scanner, read the named connection/model notice and explicitly consent to sending the normalized image for moderation. Consent is bound to that exact policy approval; a different provider, model or connection revision requires fresh consent.
3. Upload without an on-screen preview of the unreviewed image. Until admission succeeds, the existing approved avatar remains visible, or the workspace shows initials. A missing scanner, disabled deployment gate, missing consent, quota refusal, malformed result or uncertain operation leaves the upload hidden for human review.
4. Refresh the review status after an interrupted request. Upload request keys are idempotent. A recorded or uncertain model send is never automatically repeated. An administrator can close an interrupted scan as uncertain without resending it.

An avatar is private to the student's school workspace. This version displays the student's approved avatar in their own profile/sidebar; it creates no public image URL or cross-platform student identity. Removing the displayed avatar does not erase encrypted moderation or review history.

## Decisions and administrator evidence

The scanner returns exactly five numeric model risk scores (`sexual`, `violence`, `self_harm`, `hate`, `other_unsafe`) and an uncertainty flag. Sexual screening includes nudity or sexualized content; violence includes violent acts, threatening weapons, blood and injury. Drawings and cartoons are included. The model receives fixed screening instructions and the normalized image, with no student name, filename, school, coursework, biography or chat history.

The server applies the human-approved thresholds. Defaults are 0.20 for sexual content, 0.35 for violence and 0.35 for the other categories. Scores are estimates, not calibrated probabilities or proof of safety.

| Finding | Student display | Administrator workflow |
| --- | --- | --- |
| Sexual score at or above threshold | New image hidden and quarantined | Review the exact image and evidence; approve a false positive or reject with a reason. |
| Violence or another safety score at or above threshold | New image hidden and flagged | Human review required. |
| Uncertain, unsupported, incomplete or failed scan | New image hidden | Manual review; no automatic retry. |
| Complete admitted result below every threshold | Exact image may be displayed | Retain the scan/policy evidence; an administrator may subsequently reject it. |

The metadata-only review queue never loads image thumbnails. Selecting an upload shows its status, image hash and dimensions, consent/admission facts, model risk scores, policy thresholds, fixed server rule explanations, exact policy/model snapshots and human review history. This is observable decision evidence; hidden model reasoning is neither requested nor stored. For example, a report explains that a sexual risk score met the configured threshold and therefore activated the quarantine rule.

Revealing private pixels requires a separate acknowledgement and explicit audited action. Images use authenticated, same-origin responses with private/no-store caching. Approval and rejection require a substantive reason and the exact current review version. Only the student's latest candidate can replace their displayed avatar. An approval cannot attach to different uploaded pixels.

Late responses retain their original immutable evidence. A late clean result cannot publish a closed or rejected candidate. New valid negative evidence arriving after an earlier human approval places that candidate back into hidden review; a human can consider the new evidence and record a fresh override for a false positive. Earlier decisions remain in the history.

## Configure automatic scanning

1. Configure an enabled school model connection under **Integrations**. Verify that the exact model accepts image input and can return the fixed structured response. A model name or image-capability checkbox alone does not establish actual provider capability or moderation quality.
2. Open **Student avatars → Scanner policy**. Select the exact connection/model, explicitly permit image processing and choose the thresholds. Save the draft.
3. Review and approve that exact revision. Connection/credential changes invalidate the approval and student consent binding. The existing deployment live-model gate must also permit inference; saving or approving a policy makes no model call and does not enable that gate.
4. Review the provider's handling of student images before allowing real uploads to that destination. Students receive a named-destination consent notice. With no admitted scanner, uploads still work through the private human-review queue.

Server limits allow five uploads per student per UTC day. Separate scanner-send limits allow at most five sends per student and 100 per school per UTC day, including pending and uncertain sends. A scan caps output at 500 tokens and rejects accounted usage over 12,000 tokens. These are operational limits, not a currency-price guarantee; provider billing remains separate. Tests do not invoke real providers or establish real-model accuracy.

Automatic screening can produce false positives and false negatives. It does not identify people, infer demographic attributes, establish legal conclusions or make disciplinary decisions. Human reviewers control flagged-image publication. Model failures never fall back to displaying the unreviewed upload.

## Encryption and evidence

Every student's normalized pixels, candidate state, upload/scan evidence, review notes and avatar-specific audit payloads use the independently configured PII key, including Core-backed Student identities. They do not use the integration credential vault as a substitute. Existing AES-256-GCM encryption binds tenant, record/owner identity and operation purpose as authenticated context. Structural opaque record/school/owner IDs remain available for authorization; private payloads are ciphertext in PostgreSQL and retained backups.

Schema 15 adds immutable `avatar_policy_revision`, `avatar_policy_approval`, `avatar_upload`, `avatar_scan_send`, `avatar_scan_result`, `avatar_review` and `avatar_scan_closure` records. The encrypted `avatar_pointer` and `avatar_state` are mutable versioned operational state. The separate school policy pointer is mutable; policy revisions and approvals are immutable. Historical images and review evidence cannot be rewritten through the ordinary runtime role. No image bytes or original filenames enter ordinary audit metadata, Core pipeline events, browser storage or public CDN assets.

Current student lifecycle, membership, school scope and session are checked before upload, model send, release and image access. A changed policy, connection, student authority or candidate blocks automatic release. Administrative reveal and decisions use current human school authority. A viewer already holding rendered pixels cannot have those pixels remotely erased; logout/context changes remove the application's local image references.

Keep the PII key and tested recovery process with the existing protected family-account deployment. Avatar evidence follows the application's retention/hold rules; removal from display is distinct from an authorized retention/deletion process. See [family account encryption](family-accounts.md), [audit retention](session-archive.md) and [deployment](deployment.md).

## Verification

Use only benign generated pixel fixtures and mocked numeric moderation results. Do not collect inappropriate images for test fixtures or transmit actual student photographs during release testing.

```sh
PYTHONPATH=apps/api:apps/archive ALLOW_LIVE_MODELS=false .venv/bin/python -m pytest apps/api/tests/test_avatar_scanner.py apps/api/tests/test_student_avatars.py apps/api/tests/test_avatar_security.py -q
npm test --prefix apps/web -- --run src/avatar-api.test.ts src/avatar-ui.test.tsx
npm run build --prefix apps/web
```

The focused checks must cover normalization/metadata removal, all quarantine categories, invalid/duplicate/nonfinite model JSON, unavailable scanner/manual review, exact consent binding, role/tenant/school isolation, independent PII encryption for local and Core Students, upload idempotency, quotas, in-flight revocation, operator closure/late results, explicit reveal, retained human decisions and stale versions. Full CI additionally provisions the isolated PostgreSQL fixture and checks schema/SQL immutability. Browser acceptance uses a separate synthetic school/database and exercises actual upload → hidden queue → evidence/reveal → human decision → student display.

Production acceptance uses the normal Core exact-commit/image release and additive schema-15 migration. Preserve all earlier records, identities, ciphertext, PII binding and audit prefix. A code release, configured model, approved policy and real-provider quality acceptance are separate milestones. Release success does not claim that an unconfigured or disabled scanner is automatically scanning images.
