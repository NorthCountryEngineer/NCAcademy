# Optional Homework Help voice

Live voice setup and credential lookup are deferred at the owner's request on October 18, 2026. Keep the configuration, stock-voice selector, recording/transcript review, exact-reply playback interfaces and mocked tests as scaffolding. Text remains the default; resume provider setup and live acceptance only when requested.

Homework Help keeps voice under the same student, class, approved teacher, source and learning-preference boundaries as written help. Students can select a school-approved stock voice for each AI teacher. That personal choice follows the stable teacher profile across its assigned classes and grades; it does not edit the shared teacher or another student's settings. Choosing text-only clears the personal voice selection. Voice does not add an unrestricted conversational agent or allow an answer-writing model.

The browser requests microphone access only after the student's action. A recording is capped at 30 seconds, converted to mono PCM16 WAV and explicitly submitted with a provider-processing confirmation. The API checks the real WAV structure, duration, sample rate and size and rewrites the PCM frames to remove recording metadata. ElevenLabs transcription produces an editable question draft. Students review and explicitly send that text through normal Homework Help; transcription never automatically contacts the teacher or changes student work.

Read-aloud accepts an owned released `lesson_turn` ID and a selected stock voice. The API derives the exact approved teaching text from the stored response, including a curated lesson's `narration_text`. It accepts no caller-supplied text. Current class enrollment, teacher approval and authority, corpus/source review, model connection, deployment gate, personal learning-preference revision and school voice configuration must still match. Revalidation occurs before external transmission, release, receipt replay and protected audio playback. A student clicks to request and play audio; the interface does not autoplay or continuously listen. Speech synthesis sends the approved text to the provider; the application cannot independently prove every sound in provider-generated audio is a perfect transcription of that text.

## Provider and stock catalog

The integration uses only `https://api.elevenlabs.io`, with public DNS validation, pinned destination IP and verified TLS hostname. Redirects, environment proxies, retries, arbitrary endpoints, voice cloning, voice design, account-wide voice listing, public preview URLs and vendor conversation agents are excluded.

Catalog `documented-defaults-2026-10` contains George (`JBFqnCBsd6RMkjVDRZzb`) and Sarah (`EXAVITQu4vr4xnSDxMaL`). Both names and IDs appear in [ElevenLabs' own TTS examples](https://github.com/elevenlabs/plugin/blob/main/skills/general/text-to-speech/SKILL.md). Before each synthesis, the server reads metadata for the exact independently allowed ID, using the encrypted provider credential, and requires the returned exact ID and `category: premade`; owned or mixed voices fail. No account-discovered voice, including the owner's clone, can enter the catalog. A failed metadata lookup withholds synthesis before student teaching text is sent.

This catalog has a known availability limit. ElevenLabs states that [Default voices are limited to accounts created before March 2026 and retire on December 31, 2026](https://elevenlabs.io/docs/help-center/product/voices/my-voices/what-are-default-voices). The application disables this catalog from that date. Its published replacement table does not by itself establish exact replacement IDs or synthetic-stock provenance, so replacements are not silently substituted. Before production enablement, verify that the configured account can access these exact stock voices. A durable replacement requires a reviewed source change with independently verified public stock provenance. A public voice name, shared voice, generated voice or professional-clone category is insufficient. The documented catalog does not establish successful live synthesis or a free service plan.

The current [speech endpoint](https://elevenlabs.io/docs/api-reference/text-to-speech/convert) receives `eleven_multilingual_v2`, a bounded approved text and MP3 output. The [transcription endpoint](https://elevenlabs.io/docs/api-reference/speech-to-text/convert) receives `scribe_v2`, multipart WAV, disabled diarization/audio-event tagging and no timestamps. Both use the documented `enable_logging` query parameter. Although the single-voice API documents an optional API-key header, an unauthenticated read-only metadata probe returned 401 on October 18, 2026; the application therefore authenticates only its exact allowed-ID lookup. No live transcription or synthesis was used for implementation tests.

## Administrative setup and retention

A human Admin or School administrator configures the school through `/api/admin/voice`, using the usual write-only credential pattern. API responses expose only credential status. Replacement keys are sealed in the existing vault; changing the connection version invalidates pending speech. Administrators choose the allowed stock IDs, enable voice and set the per-student daily school budget: at most 50 combined operations, 900 recording seconds and 30,000 spoken characters; defaults are 20, 300 and 12,000. Limits apply across courses, teachers and both directions. They supplement the existing tutoring quotas and do not authorize any new teaching content.

The provider's zero-retention mode is enterprise-only according to its endpoint documentation. An administrator may explicitly request it; provider rejection fails closed. Otherwise the administrator must acknowledge the provider's retention policy before enabling voice. The student confirmation explains that audio or teaching text goes to ElevenLabs. Reusing an existing API credential does not establish enterprise eligibility, student-data processing terms, account stock access or a zero-retention guarantee. Voice remains disabled until configured and the existing deployment live-model gate is enabled.

Raw microphone audio exists in bounded request/provider memory only; NCAcademy does not persist a recording, copy it into audit, or archive its bytes. The audit contains recording hash, duration and size, student consent, context and preference snapshots, durable send marker, visible transcript, exact teaching text, stock verification, outcome and playback access. The transcript is student-visible evidence and participates in the existing session archive. Generated read-aloud MP3 is sealed in the owned operation record for protected, explicit playback; its hash and byte count, not audio or ciphertext, enter audit. These generated clips follow the application's session-record retention policy and are not public media.

Operations reserve quota and persist a send marker before the billable request. Exact request-key retries return the existing receipt; changed inputs conflict. Ambiguous network outcomes withhold output and never automatically retry. A process interrupted after its send marker leaves an unresolved operation that blocks another operation for that student's school until reviewed. Receipt lookup by request key lets the browser reconcile an uncertain submission without resending a recording or synthesis request. Audit records show the released transcript and teaching text, not hidden model reasoning.

## API contract

Student routes derive school scope from the stored course. The student must already have completed the nine-question intake.

| Route | Purpose |
| --- | --- |
| `GET /api/courses/{course}/voice?teacher_profile_id=…` | Availability, school stock descriptors, personal selection/version and recording bounds. |
| `PUT /api/courses/{course}/voice-preference` | `{teacher_profile_id, voice_id: string\|null, expected_version}`; append an immutable personal revision. |
| `POST /api/courses/{course}/voice/transcriptions` | `{teacher_profile_id, idempotency_key, consent: true, audio_base64}`; WAV only, at most 3 MiB and 30 seconds. |
| `POST /api/courses/{course}/voice/speech` | `{teacher_profile_id, turn_id, idempotency_key, consent: true}`; exact released teaching text only. |
| `GET /api/courses/{course}/voice/operations?teacher_profile_id=…&idempotency_key=…` | Reconcile an uncertain request without a new provider call. |
| `GET /api/courses/{course}/voice/operations/{id}` | Own current operation receipt. |
| `GET /api/courses/{course}/voice/operations/{id}/audio` | Current authorized audio; `audio/mpeg`, private no-store. |

Receipts contain `id`, `kind`, `status`, `created_at`, `transcript`, `audio_url` and `error`. Pending or withheld receipts contain neither usable transcript nor playable URL. Unsupported microphone browsers can use typed questions and the same ordinary learning workflow.
