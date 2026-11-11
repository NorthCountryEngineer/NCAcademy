# Homework Help privacy modes and interface checks

New Homework Help configurations default to **Private homework processing**
(`privacy_mode: deidentified`). Administrators continue choosing and approving the
school models through the normal settings page. Original-data mode
(`privacy_mode: raw_zdr`) remains a separate option with its protected operator
verification requirements. Existing configurations without an explicit mode keep
their original-data interpretation until an administrator saves a new revision.
Changing a saved configuration still requires current human approval.

In private homework processing, the student uses the same question box and photo
upload. Academy reads supported content locally and prepares a fixed learning task.
Original images, free-form questions, private chat history, teacher instructions and
student personality notes are not forwarded to the model. The school question
assistant and independent safety reviewer receive the prepared learning details;
teacher persona/model overrides are not used for these calls. Selected coursework
and the original conversation remain subject to Academy's existing local access,
encryption and audit controls.

The bounded task interface supports recognized arithmetic, equal-sharing word
problems, fractions, geometry and a closed set of science, reading and history
concepts. It is not a general text-redaction service. Unsupported or ambiguous
content produces a saved, locally generated clarification asking for an equation
or topic. It does not forward that text unchanged, display an internal task
language, or offer a checkbox to approve arbitrary redacted/raw text. The original
draft and attached photos remain available so the student can clarify the task.
A clear numbered worksheet question can proceed through the same upload flow.
The server owns extraction, task validation and disclosure boundaries.

Students see a short explanation that original photos and chat stay in Academy,
that only supported learning details go to AI, and that school staff can review
saved work. Photo consent covers local reading and school review in this mode.
Original-data mode continues naming approved photo destinations and requiring the
applicable provider controls. AI mistakes, safe reporting and trusted-adult
support remain disclosed in both modes. A local clarification is labeled
**Private question check**, rather than claiming that an AI generated it.

Retained worksheets stay scoped to the selected conversation. Switching chats
clears photo confirmation and the prior chat's draft/context. A provider-policy
change requires fresh confirmation. An uncertain send remains locked across
reload; **Refresh history** only reads saved results and never repeats inference.
A saved local clarification discovered during recovery preserves the current
question/photo draft. Reports and human follow-up resolution keep their separate
opaque request identities and read-only recovery flow.

## Local verification

Use Node 22 and the repository's existing web dependencies:

```sh
cd apps/web
npm test -- --run src/homework-deidentified.test.tsx src/private-homework.test.tsx src/homework-safety.test.tsx src/homework-safety-interactions.test.tsx src/homework-student-controls.test.tsx
npm run build
```

The focused tests use synthetic fixtures and mocked requests. They cover default
and historical mode mapping, local photo confirmation, policy-change handling,
draft preservation after clarification, cross-conversation worksheet isolation,
read-only recovery after uncertain sends, and the unchanged safety report flow.
The composer keeps its bounded scroll region and separate Send action row when
photos are attached. Browser acceptance must additionally verify visible Send
controls at the target viewport, a supported numbered worksheet upload and
follow-up, unsupported-content clarification, chat switching, concern reporting
and human review. These checks do not establish eligibility to send original
child content to a provider; that remains specific to original-data mode.
