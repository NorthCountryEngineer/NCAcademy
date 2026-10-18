# Student learning preferences and feedback

This source revision follows the deployed teacher/media release `78b48d6ba0e874ff7e76576119ff5e020f3627e0`. It adds personal presentation preferences; it does not train a model or change a teacher's shared approved profile.

## Intake and settings

After their first authenticated Student visit, students complete nine short questions before new tutoring, Lesson Q&A or private lesson curation. Existing students without preferences see the same intake on their next visit. Identity remains owned by Core; intake cannot grant school/class access. Questions use structured choices, including neutral answers, rather than freeform system instructions or a diagnosis.

| Dimension | Preference captured |
| --- | --- |
| Reading | How much written explanation helps |
| Diagrams | How helpful visual explanations are |
| Video | Interest in reviewed video lessons when available |
| Audio | Interest in optional read-aloud |
| Pace | Slower steps through a topic or a brisker presentation |
| Detail | Brief guidance or more supporting detail |
| Encouragement | Quieter or more encouraging language |
| Question frequency | Fewer check-ins or more reflective questions |
| Structure | Flexible exploration or explicit steps |

These are preferences for the current learner, not fixed learning-style labels or evidence of ability. All nine answers can be neutral. Students can return to settings, change their answers or reset adaptation for future interactions. Historical preferences, feedback and teaching evidence remain preserved. No medical, disability or other sensitive explanation is requested.

## Long-term adaptation

The explicit intake supplies the student's baseline. Subsequent structured style feedback supplies bounded adjustments for that student and teacher. An AI teacher reused across classes keeps its stable profile identity; one student's feedback never changes another student's experience or the shared approved personality. The server calculates the effective weights deterministically and records the exact snapshot used for each interaction.

Each 0–4 intake choice maps to a 0–100 baseline in increments of 25. Adaptation uses the latest 128 feedback records for the same student, teacher and explicit preference revision; all older records remain retained. For each dimension with explicit style targets, the mean target's difference from the baseline is capped at ±20, then smoothed by `n / (n + 4)`, rounded and clamped to 0–100. Ratings without a style target cannot shift that dimension. Weights are recomputed from this baseline and window rather than repeatedly adding deltas, so duplicate requests and long histories cannot compound unbounded changes. Reset or an explicit settings edit begins a new adaptation epoch. Unprofiled assignment guidance is scoped to its course.

Weights influence selection and presentation only inside the already admitted capabilities and content. A video preference cannot admit an unreviewed video. Audio remains an explicit optional action. Assignment help still selects approved hints/questions and requires the student's own attempt. Lesson Q&A still renders approved source passages; private curation still assembles supported approved sources/media. Model output cannot introduce arbitrary prose or answer a student's assignment by changing preference weights. Available media and the current approved teacher/course policy take precedence.

The profile and effective context are checked before new assistance. Changes during a pending request cannot silently release an answer under an obsolete preference snapshot. Retries retain the existing idempotency and uncertain-send rules. Preference changes never reset tutoring quotas or permit another provider call for a prior key.

## Interaction feedback

Students can rate accuracy and helpfulness and optionally request structured style changes on their own released assignment-chat, Lesson Q&A and curated-lesson interactions. Feedback is bound to the immutable turn, current student ownership and enrolled course. One idempotent feedback record per interaction prevents repeated clicks or retries from amplifying an adjustment. Another student, foreign school/tenant, revoked enrollment or unreleased interaction cannot supply feedback for that turn.

New feedback on a turn from an older explicit preference epoch is refused, so it cannot repopulate a reset baseline. Changed assignment/profile/corpus authority or a revoked source can also make an interaction unavailable for new feedback. Existing recorded evidence remains intact; a new currently admitted response can receive new feedback.

Accuracy feedback records a concern for authorized review. Students can explicitly choose “Not sure yet”; the stored accuracy is null and marked not assessed, rather than inventing a numeric rating. Low ratings of 1 or 2 flag review. No rating establishes truth, rewrites approved sources or automatically changes a factual response. Personality adaptation uses the student's explicit style choices; accuracy and helpfulness ratings cannot weaken safeguards. Feedback and exact before/after weight evidence enter the audit trail without rewriting the original session. Authorized session reviewers can examine those events under the existing audit permissions.

## Evidence, migration and release

An additive owner migration installs schema 5 and database immutability protection for preference revisions and student feedback. Existing identities, submissions, terminal turns and historical audit bytes remain intact. Readiness checks the current marker; login/readiness do not run migration. A compatible new API and frontend must ship together.

Preference revisions, feedback, effective weights and the existing student-visible chat/lesson evidence are covered by the separate [session archive](session-archive.md). Archive availability is an operational acceptance requirement, not implied by API readiness. No hidden model reasoning is collected.
