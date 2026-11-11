# De-identified Homework Help

The de-identified mode keeps a student's original question, homework photographs and conversation inside Academy. The server compiles supported math and public curriculum concepts into a small, validated task description and asks the school-approved model to explain that task. A separate review checks the proposed explanation before the student sees it. This mode requires current human school approval and the scoped Homework model gate, and preserves the encrypted local audit.

The privacy boundary is construction from approved task types, rather than removing names from arbitrary prose. A public math problem can be explained without sending the identity, photograph, answer or learning history of the child asking about it. This distinction follows the conditional requirement in [OpenAI's under-18 guidance](https://developers.openai.com/api/docs/guides/safety-checks/under-18-api-guidance): ZDR is required before processing under-13 personal data. This is a technical interpretation of the bounded content path, not an exemption for parents or a claim of provider approval. Child safety, appropriate disclosures and review remain necessary.

## What leaves Academy

The compiler permits server-defined math task types, tightly bounded operands and diagram parameters, closed curriculum-topic selectors and explanation choices. Supported math includes arithmetic/equal sharing, fractions, rectangle area/perimeter, number ranges, rounding/place value, elapsed time and fixed shape properties. Forty code-owned concept selectors also provide general reading/writing strategies, science, geography and history explanations; they do not forward a private passage or write a student submission. Any image sent by this mode is rendered afresh from the admitted math with fixed labels. It contains no original worksheet pixels, handwriting, filename or upload metadata. Follow-ups reuse only a validated task, closed action and bounded explanation stage; raw previous chat and a student's attempts are not model inputs. Stages select a generic explanation variant rather than conveying correctness or progress.

Only two external phases run: the explanation and its independent review. There is no external teacher handoff. Both phases observe the same content boundary. Raw input must not be sent to an external moderation model, classifier or fallback provider. [OpenAI's safety guidance](https://developers.openai.com/api/docs/guides/safety-best-practices) recommends restricting inputs to trusted, validated choices; asking a model to ignore personal information does not enforce this boundary.

| Information | Local handling | External handling |
| --- | --- | --- |
| Original question, photograph and any OCR text | Authorized local processing and encrypted conversation evidence; OCR text is transient | Excluded |
| Student name, account, school/class/session identifiers and filenames | Existing ownership and access controls | Excluded |
| Answers, scores, errors, progress, learning profile and chat history | Existing local coursework, feedback and audit | Excluded |
| Teacher biography, freeform instructions and private resources | Existing local approved configuration | Excluded |
| Supported task or concept | Server validates its type, numbers and closed topic selector | Canonical task only |
| Diagram | Server draws fixed math primitives from the admitted task | Fresh canonical rendering only |
| Follow-up | Server recognizes a supported closed instruction | Closed instruction and generic stage only |

No stable child identifier, child-derived hash, client IP forwarding, browser cookie or child-linked tracing metadata belongs in this route. Safety identifiers are [recommended rather than required](https://developers.openai.com/api/docs/guides/safety-checks). A shared service identifier must not be described as identifying an individual child, and identifiers must never be rotated to evade enforcement.

## Limits the family will encounter

This is a bounded task assistant. Local OCR and parsing may recognize a supported problem from a clear worksheet. An unrecognized diagram, handwriting, complex story problem or unfamiliar topic can require a simpler typed equation or a local clarification. A recognized reading/writing topic receives general strategy guidance rather than analysis of the private passage or essay. The app retains its local evidence and explains the limitation. It must not send original material because compilation failed. A blocked or ambiguous compilation makes zero provider calls. Repeated closed follow-ups select one of sixteen generic strategies; after the bounded sequence, the app asks for a specific step instead of resending the same fixed hint.

Changing presentation does not authorize sending a diagnosis, disability, age, individual learning profile or performance history. The same task, action and stage must produce identical canonical generation content for two students with different private information. Independent review adds only the candidate generated from that public request. A child's coursework and attempts remain available to the parent in Academy's protected records.

This mode does not deliver unrestricted worksheet vision. Sending original photos, arbitrary student text or conversation history uses the separate `raw_zdr` path and requires current operator-verified ZDR and applicable image controls. Existing configurations without a `privacy_mode` retain that raw-content interpretation; deployment does not widen old approvals.

## Setup and release order

Use the existing school OpenAI connection, select `privacy_mode=deidentified`, save and approve the exact revision as a human School/Admin. New configurations default to de-identified mode; old saved configurations remain raw until explicitly changed and approved. Boundary version 4 requires current human approval. No vendor request, Admin API credential or different provider is part of de-identified setup.

Deploy and compare the code release while model gates remain off. After schema 17, exact API/frontend artifacts, original records, scopes, keys, identities and unrelated services are verified, activate `ALLOW_HOMEWORK_MODELS` and the reviewed school configuration through the authorized operator process. Keep `ALLOW_LIVE_MODELS=false` and `ALLOW_AGENT_TEACHERS=false`. Retain separate activation audit evidence; do not relax code-release preservation checks to hide configuration writes. Missing ZDR proof continues to block the raw-content path.

## Verification

Inspect every generation and review request, including rendered image bytes and metadata. Synthetic inputs should contain distinct names, addresses, filenames, account IDs, teacher instructions, diagnoses, scores and history sentinels. None may reach the transport. Two synthetic students with the same supported task, action and stage must produce identical external generation content, and identical review requests for the same generated candidate. This is an exact egress assertion, not a search for common names.

Reject unknown types and fields, unbounded numbers or arrays, nonfinite values, user-provided vector/image content, unsupported OCR and client claims that a task is already safe. Check school/connection reapproval, revoked source access, duplicate requests, provider failures and replay without automatic inference retry. Browser acceptance covers a recognized worksheet and follow-up, an unsupported input, parent audit replay and the continued raw-content block.

`store:false` remains a request setting, not a ZDR guarantee. [OpenAI's retention documentation](https://developers.openai.com/api/docs/guides/your-data) describes provider retention and feature exceptions. This mode's protection is that private student content never enters that boundary; Academy intentionally retains its own encrypted audit separately.

Run the independent compiler/transport regression with synthetic fixtures:

```sh
PYTHONPATH=apps/api .venv/bin/python -m pytest -q apps/api/tests/test_homework_deidentified_egress.py
```

The mandatory container OCR check draws a synthetic named/numbered worksheet and verifies real Tesseract question selection plus the fresh-diagram boundary. It is separate from mocked transport tests and performs no inference. Full exact-commit CI also runs the application, frontend and container checks before release.
