# Model interfaces and instructional controls

## Model interface

The initial runtime uses an administrator-defined OpenAI-compatible Chat Completions selection interface with manual model allowlists. This is a protocol contract, not a promise that every provider/model implements it. Save/edit does not contact a provider. Connections keep credentials encrypted server-side and return configured status only. Empty replacement preserves the key; explicit clear removes it. Endpoint changes require fresh review and must not forward credentials to untrusted destinations.

The selector is stateless and bounded. It receives an assignment/policy snapshot, the student's attempt/question and the approved choice IDs, then returns one choice ID. It has no browser, tools, uploads, code execution, retrieval or document authoring. The application validates the exact schema locally; schema prompting or provider structured-output support alone grants no trust. No raw provider-generated text is displayed to students. The instructor-authored selected text is the only releaseable assistance.

Where supported, request `store: false` explicitly. This setting is not a zero-retention guarantee: provider abuse monitoring, account terms and processing controls still require review. The provider request/usage schema follows the [official Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create); review [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data) when using that provider. No provider credentials or live calls were used in package tests.

## Fine-grained policy

Bind assistance to an assignment and a versioned approved hint bank. Controls include disabled/curated/Socratic modes, required student attempt, maximum session turns, input/output token bounds, approved connection/model, and bounded selection choices. Disabled means no inference. Curated mode uses stored teacher-authored text without model traffic. Socratic mode permits the model only to select an approved question. Curated mode can use both approved hints and questions. Reject unknown fields/IDs, oversized/free-text responses and invalid/missing usage. A failed selection supplies no unreviewed fallback answer.

Hard turn limits and conservative prompt-byte/output-token reservations bound resource exposure. The reservation ceiling is not a provider tokenizer guarantee. Actual reported usage is recorded separately. They do not establish a currency budget unless connection prices and reservation/accounting are separately implemented. Do not describe token caps as dollar ceilings. No automatic retries or fallback inference are permitted after uncertain billing. A pending operation blocks further calls. A completed withheld operation retains its reservation; a new explicit request with a new idempotency key may be admitted within the remaining limits. Administrators can disable live traffic globally and connections individually.

## Instructional assurance

This architecture prevents a model from introducing newly generated answer prose into student output. It does not validate the pedagogy of teacher-authored content or stop students using outside tools. Review hints before publication, test adversarial requests and configure assistance off for independent assessments. Model selection accuracy affects which approved help is chosen, but cannot bypass the approved-content boundary.

Freeform explanation, essay feedback/rewriting, worked examples and automated grading need separate reviewed capabilities. They must not be enabled by simply changing a prompt. The initial package deliberately gives instructors a conservative, inspectable tutoring surface while preserving their ability to use different administrator-approved model endpoints.
