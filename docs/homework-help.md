# Homework Help

Homework Help is a private conversational workspace for an enrolled student. It identifies a course, brings in that course's approved AI teacher and keeps the question, routing decision, attachments, teacher selection and released teaching evidence in the student's learning record. Students may correct the course or choose another admitted teacher on a subsequent message.

The triage component is deliberately narrow: it ranks the names, descriptions and subject labels of currently enrolled courses that have an enabled, human-approved private lesson teacher. A distinctive course match can be selected automatically. A follow-up can continue the current course and stable teacher. Ambiguous matches ask the student; a photograph without written context always asks for an explicit course before any model send. Triage makes no external model call, reads no image content and cannot grant access to a different course or teacher.

## Conversation and teaching boundary

After handoff, the service invokes the existing private curated-lesson operation. It uses the current approved behavior, public teaching corpus, external-media decisions, model connection and image capability. The model chooses existing source, visual and reflection IDs. The server assembles exact approved passages and fixed prompts. It cannot display provider-generated assignment answers, solve an uploaded worksheet, write an essay or edit student work.

A follow-up includes the latest question plus at most three earlier student questions, each truncated to 1,000 characters. Earlier questions must belong to this conversation, course and teacher, and their associated teaching turns must still be released and currently admitted. They are included only when the complete input fits the teacher's approved input limit. No other course's conversation, teacher-only materials, photos or model-generated text enters that context. The immutable context snapshot records the exact included message IDs and text.

Course links are created by the server. Relevant assignment links are selected from the currently enrolled course using assignment titles/module labels, with a maximum of three. They point to the application's course and assignment views, using `/#course=<id>&assignment=<id>`. Approved source citations remain attached to each teaching passage. The student cannot supply a destination URL or another student's submission as a reference.

Homework Help shares all private-lesson course quotas with Lesson Q&A and curated lessons. Opening a new conversation, changing teacher or using a different activity does not reset the student's UTC course budget. The same intake and bounded personal learning weights apply. A released underlying lesson turn can receive the existing structured accuracy, helpfulness and presentation feedback.

## Pictures and privacy

A question can include up to two assignment images through the same bounded image input and normalization utilities as private curated lessons. The service keeps normalized JPEG images encrypted in the student-owned message; audit events contain names, dimensions and content hashes rather than image bytes or ciphertext. The student's scoped image endpoint requires current student school membership. The model receives a photograph only after explicit student confirmation and an approved image-capable teacher policy. A clarification response has not sent the image to a model.

The browser retains the proposed question and photographs while asking for a course/teacher choice. Confirming the destination creates a new immutable message with its own idempotency key. Current teacher approval and image/model policies are checked again by the existing lesson lifecycle immediately before send and release.

## Durable records, retries and review

Conversations are immutable `homework_session` records pinned to one school and student. Each incoming question is an immutable `homework_message`, including the routing decision, attachment metadata, encrypted attachment storage, original question and exact bounded conversation context. Its private lesson key links to an existing sealed `lesson_turn`; an immutable `homework_outcome` stores the exact validated turn pointer after admission. Normal history reads load that one turn rather than transferring every course conversation and encrypted photograph. Interrupted receipt recovery uses one database-filtered exact-key lookup, rejects ambiguous matches, and an explicit POST reconciliation seals the recovered pointer. Provider transport and terminal release evidence remain in the established course lesson session.

The service commits the incoming message and handoff evidence before invoking the lesson operation. A normal admission refusal before a lesson turn exists produces an immutable `homework_outcome` with a final blocked status and an explicit no-send audit event. A process interruption after saving the question but before lesson admission is shown as not started and can be resumed only through an explicit student action, using the original stored payload and lesson key. That operation commits its send marker before external I/O and its terminal audit before release. Exact duplicate message requests reuse the same lesson key. Changed input under the same key returns 409. An uncertain provider result is never automatically retried. A missing homework receipt after a durable lesson result can be reconciled without a second provider call.

Student history rechecks current course, approved teacher, actual bound-agent authority and current source reviews before showing released teaching content. Revoked content is projected as withheld; historical records remain intact. The student can still review their own original question and school-scoped private upload.

School/Admin humans can discover the school's conversations through the audited review catalog and read an entire conversation through the separately audited review endpoint. Their protected image reads also cover private photographs retained before course clarification. Teachers receive only the existing course-specific lesson session and its handoff event, avoiding access to unrelated pre-triage or other-course questions. Full tenant audit exports and the separate retained archive contain the Homework Help events. The per-conversation review projection is a partial chain and does not replace an independently retained complete tenant chain.

## API

All routes require normal authenticated application access and mutation CSRF protection. Inputs forbid additional keys. The optional index school filter is checked against current human Student school membership.

| Operation | Route | Input/result |
| --- | --- | --- |
| Current routing catalog and conversations | `GET /api/homework-help?school_id=<id>` | Enrolled courses, currently admitted teachers, and up to 100 own conversation descriptors |
| Start/reconcile conversation | `POST /api/homework-help/sessions` | `{school_id?, idempotency_key}`; school may be inferred only when unambiguous |
| Explicitly resume a saved, unsent question | `POST /api/homework-help/sessions/{session_id}/messages/{message_id}/resume` | `{}`; original stored payload/key, no action for an existing pending/terminal lesson |
| Own conversation | `GET /api/homework-help/sessions/{session_id}` | Descriptor and immutable message projections |
| Ask/clarify/correct course | `POST /api/homework-help/sessions/{session_id}/messages` | `{message, images?, image_use_confirmed?, course_id?, teacher_profile_id?, idempotency_key}` |
| Own normalized picture | `GET /api/homework-help/sessions/{session_id}/messages/{message_id}/images/{image_id}` | Private, no-store JPEG; audited access |
| Human school administrator review catalog | `GET /api/homework-help/review-sessions?school_id=<id>` | Up to 100 currently authorized school conversation descriptors, including pre-triage sessions |
| Human school administrator review | `GET /api/homework-help/sessions/{session_id}/review` | Safe immutable message/lesson evidence and partial session events; audited access |

Session descriptors expose their original idempotency key. Message projections expose their original key, `status`, `error`, `resume_available`, `triage`, existing curated `turn`, attachment descriptors and server references, allowing the browser to reconcile an uncertain response through a GET. Message text is bounded to 10,000 characters; the approved teacher limit can be lower. The image-aware request route is bounded to 400,000 actual HTTP bytes. Conversation creation is limited to 30 per student per UTC day, with at most 100 messages per conversation. An independent allowance caps new Homework Help questions at 100 and normalized photographs at 10 MiB per student per UTC day across all schools and conversations. Clarifications and blocked questions count toward storage admission; exact idempotent retries do not. The allowance uses recorded scalar byte metadata and never decrypts historical pictures to calculate usage.

`status` is `clarify` before an unambiguous course/teacher selection or follows the underlying lesson's `pending`, `blocked`, `withheld` or `released` state. A stored question with no lesson turn and no terminal outcome is `pending` with `resume_available: true`; the browser explains that it has not started and requires an explicit Resume question action. A linked pending/uncertain lesson and a terminal blocked outcome never permit a new send. `turn.id` is the existing learning-feedback target. A rejected ownership lookup returns 404; missing current class/teacher permissions return 403; stale idempotency use returns 409; malformed or oversized typed context returns 422/413.

Voice integration uses the same currently admitted, released teaching text. See the voice integration documentation for the explicit approved synthetic voice catalog, provider consent, bounded text/audio and transport controls. Homework Help does not create a separate realtime model channel or bypass the lesson release decision.
