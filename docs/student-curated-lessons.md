# Student-curated multimedia lessons

This local revision lets an enrolled student ask their class's AI teacher to assemble a private lesson from a task description, assignment photos, or both. It extends the approved automatic tutoring policy. The teacher selects and sequences reviewed public course passages, supported interactive diagrams and reflection prompts. It does not write an assignment response, submit work or publish new materials to the class. Shared assignments, lesson materials and feedback still require human publication approval.

## Student workflow

Open the class and choose **Build a lesson for me**. Select an available class teacher, describe the difficult concept and optionally add up to two assignment photos. Preview/remove photos before sending. Select a focused overview or one idea at a time, with optional diagrams and read-aloud controls. Reading is always included.

Photos require both the teacher's approved image policy and the student's explicit confirmation to send the selected photos to the displayed school-approved model. A description can be used without photos. The browser accepts local PNG/JPEG/WebP files up to 5 MB each, converts them into bounded JPEGs and removes original metadata. The API independently verifies image type, decoded dimensions, corruption, single-frame format and size, then stores only a fresh metadata-free RGB JPEG. PDFs, SVG, remote image URLs and animated media are excluded. A photo that cannot meet the bound needs a closer crop or a written description.

The private lesson contains cited reading, supported interactive diagrams, reflection prompts and a final question to bring the ideas together. Diagram nodes can be selected with a pointer or keyboard; text equivalents remain available. Optional read-aloud uses the same teaching text through the browser's voice service after a button click. Voice availability and processing depend on that browser/service; this is not downloadable audio, a cloned teacher voice or generated video. No speech begins automatically. Unsupported topics retain their reading/reflection lesson without an invented diagram.

Students may mark a section viewed and save their own reflections. These actions enter the learning record; they do not grade or submit assignment work. A recorded click or read-aloud request is not proof of attention or understanding. Class enrollment and current school authority limit history and photo access.

## Teacher and model approval

Human School/Admin enables **private lesson curation** in the AI teacher's model policy, explicitly enables assignment images only after verifying the chosen model supports image input, sets the output/reservation limits and approves the new profile version. Both options default off for existing profiles. The selected connection/model, current teacher approval and exact public course corpus remain mandatory. Changing class materials requires corpus approval followed by teacher-profile reapproval.

Managed personas have no Core account or Teacher API permission grant. Core-bound teachers retain actual class authority, active placement, agent admission and independent fresh Core binding authorization. A revoked bound profile never becomes a managed persona. All modes require the operator's live-model gate; enabling a profile option does not turn live traffic on.

Q&A and private lesson curation share per-student/class daily UTC request and conservative reservation limits across all teacher profiles. Image encoded bytes and bounded output contribute to the reservation. Image requests can require a larger explicitly approved daily cap; the form leaves it visible and editable. Reservations are neither provider-tokenizer guarantees nor dollar budgets. The stricter admitted policy for the day remains effective, teacher switching does not reset it and any unresolved pending request blocks new sends across days. There is no automatic provider retry after uncertainty.

## Grounding and media contract

One bounded model call selects only document IDs, associated approved visual IDs and fixed reflection IDs from the admitted pool. Images are supplied to that call as context; no separate OCR call or generated transcription is exposed to the student. Mixed photo/text and photo-only requests can match a bounded approved public-source pool. Text requests use bounded course retrieval. No match results in no lesson rather than invented teaching content.

The server validates the complete selection schema, source/visual association, normalized usage and current authority before release. It renders source-owned titles and exact passage slices with content/excerpt hashes and citations. Additional prose, tools, unknown IDs, mismatched visuals, malformed output and changed/revoked context are withheld. Student messages, assignment images and their visible instructions are untrusted data, never system policy. Source approval does not establish that a passage is pedagogically suitable for every assessment; humans review the corpus and configure assistance appropriately.

The initial visual library covers carbon reservoirs, the water cycle, reef ecosystems and resource conservation/recycling, tied to the reviewed NASA/NOAA/EPA source records in the NY environmental-science starter corpus. Descriptors include versioned captions, nodes, edges, provenance and fingerprints. Unknown sources or teacher-only materials cannot acquire a visual through a matching keyword. Historical lessons retain their exact descriptor and source evidence rather than silently replacing it when the library changes. New subjects/states require reviewed public teaching sources and, when useful, another reviewed visual adapter.

Image input uses the existing compatible Chat Completions transport's typed text/image content parts and embedded image data. The protocol was checked against [the official image guide](https://developers.openai.com/api/docs/guides/images-vision) and [Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create). Compatibility is manually approved for the school's selected model; it is not inferred from its name or verified through a live test. The transport retains the existing endpoint/DNS/TLS pinning, redirect/proxy restrictions, bounded response and no-retry behavior. Provider processing terms remain part of model admission; a `store: false` request is not a zero-retention guarantee.

## Private photos and audit evidence

Only normalized photos admitted for a model request are retained, encrypted with the existing vault bootstrap in the immutable terminal lesson-turn record. A pre-send block retains hashes/metadata without photo binaries. Raw originals, EXIF and public asset URLs are never stored. Authorized image reads decrypt and verify the retained hash, enforce current student ownership/enrollment or session-review authority and append an access event. Responses are private, noncacheable JPEGs. No general upload bucket or new AWS task permission is required.

Audit events and replay/export contain photo IDs, names, dimensions, hashes and scoped image endpoints, never embedded pixels or encrypted payloads. The sanitized prompt records image references; the exact wire request hash plus retained normalized images permits request reconstruction. Invalid provider prose is represented by its hash rather than retaining possible photo/secret echoes. Valid selections, actual source candidates, media descriptors, teacher/corpus/model policies, usage, reservations, send/release checks, student-visible bundle and reflections are recorded. Hidden model reasoning is never collected.

Reviewer replay distinguishes private curated lessons, shows the exact released multimedia bundle and offers an explicit retained-photo view. Reviewer access/exports and photo reads are audited. Session JSON does not embed binary photos; controlled image retrieval and the encrypted database backup preserve those separately. Independent retained checkpoints and appropriate backup/retention policies remain production requirements. The stable vault bootstrap must be preserved for image recovery as well as credentials.

## Packaging and activation

The existing schema-3 `lesson_turn` lifecycle provides pending-send and terminal immutability; no new database table or schema migration is introduced by this extension. The owner migration remains the normal admission step. Locked Pillow supplies server-side image verification/normalization; it does not contact a media provider. API request limits increase only for the exact curation POST route, with the same bound applied to actual chunked bytes and declared length. Other routes retain their smaller limit.

The API and frontend must be rebuilt together and released with a new immutable image digest and exact-source CI. The current work is local; no production gate, credential or cloud deployment is changed. The previously included Core supplement is still required only for Core-bound selected teachers. See [verification](verification.md) for actual completed checks and [deployment](deployment.md) for activation requirements.

## Reviewed YouTube sources

Private lesson sections may include a reference to an approved YouTube lesson selected from the public corpus. Students explicitly acknowledge and load it through current server authorization; saved bundles contain no persistent embed URL. Reviewer replay shows historical evidence without loading a player. A changed, expired or revoked source is withheld when students reopen history. School audit-agent assignment introduces schema-4 external-review protections in addition to the existing lesson lifecycle. See [external media review](external-media-review.md).
