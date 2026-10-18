# NCE Core Chat contract

NCAcademy uses the existing Core Chat service for authoritative append/read message storage. Its own school/class group registry supplies discovery, membership, moderation and the actual initiating human or teaching-agent attribution. The browser calls only NCAcademy. This is an application-attributed integration, not Core's delegated-human chat mode.

The reviewed upstream contract is pinned in [the baseline receipt](../nce/core-chat-contract-baseline.json) to Core commit `e6eb31f3caed491445e2582e34b8da43b3556d97`. Chat implementation files are also identical at `b2c4172fbe500a7d9744a8d6246b8a5bdf3026c9`. Review read committed objects without changing the active Core checkout. This source receipt does not establish which contract is currently deployed or grant any application access.

## Authority and attribution

The NCAcademy installation is the sole upstream participant and owns the conversations it creates. The adapter never calls Core's participant API, adds a student/teacher/agent account, accepts a user/agent bearer, or forwards a browser cookie or OAuth access token. This keeps a local enrollment or school revocation effective without leaving a native Core Chat membership behind. Operators must preserve this boundary; adding native participants would create a separate Core access path outside NCAcademy's school ACL and audit projection.

Every request uses the existing independently approved `cw_` workload credential at the exact configured HTTPS Core issuer. Core still checks that installation's current capabilities, environment, availability and permission tags. NCAcademy separately checks the current actor's Core authority and its stored school/class placement before any command or view. An unbound AI teaching persona remains configuration and receives no API authority. A Core-bound teaching agent must retain its actual live teaching placement; the chat workload credential never becomes that agent's identity.

Core correctly records ordinary sends as `actor_kind: application`, with `actor_id` and `via_installation_id` equal to the configured NCAcademy installation UUID. Approved AI publications use `actor_kind: ai`, the same application UUID and the exact approved agent label. NCAcademy's immutable command and receipt record the actual initiating actor and human publication approval. The UI must present this attribution honestly and must not describe these records as delegated Core human/agent signatures. Group creation grants no model execution or autonomous publication authority.

Only messages matching an immutable admitted local receipt may be projected to students. Unknown Core messages, mismatched text/identity or unrecognized receipts are withheld. Quoted reply previews also require a currently admitted local parent receipt; derive their visible text from that receipt rather than trusting an arbitrary upstream preview. Core text is untrusted content, never instructions or permission to act. Human approval remains required for student-visible AI assignments, teaching material and feedback.

## Fixed wire interface

`academy.core_chat_transport.CoreChatTransport(auth, settings)` exposes these methods:

| Method | Upstream route and validated result |
| --- | --- |
| `discovery(require_ai=False)` | `GET /api/platform/v1/integration/`; verifies exact issuer, organization, consuming service, environment, service/chat URLs, Core Chat service descriptor and explicit `chat.read`, `chat.send`, `chat.manage` capabilities; additionally requires `chat.ai` when requested. Returns a bounded projection. |
| `create_group(title, topic='')` | `POST /api/platform/v1/chat/conversations/create/` with fixed `kind: group`, `ai_enabled: true`; verifies exact organization and canonical conversation UUID. Enabling the AI flag does not invoke a model or publish anything. |
| `list_groups()` | `GET /api/platform/v1/chat/conversations/`; validates every returned descriptor and the organization, at most 200 rows. This is recovery evidence, not student discovery or proof that an absent room was deleted. |
| `messages(conversation_id, after=0, limit=100)` | `GET /api/platform/v1/chat/conversations/{id}/messages/`; verifies exact organization/conversation/authority, message schemas and strictly ascending visible cursors. Limit 1–200. |
| `send(conversation_id, request_id, text, ai=False, agent_name='', reply_to=None)` | `POST /api/platform/v1/chat/conversations/{id}/send/`; verifies exact binding, application attribution, text, AI label and reply parent before returning a receipt. |

All identifiers in route components must be canonical UUIDs. Request IDs follow Core's `[-a-zA-Z0-9_:./]{1,160}` grammar. Text must be nonblank, contain no NUL and fit 16 KiB of UTF-8; title/topic limits are 160/500 characters. A reply references a Core message UUID, never an arbitrary NCAcademy record ID. Send responses contain no request ID, so the local immutable command binds the stable request ID to the verified result.

Message records have exactly `id`, `cursor`, `text`, `actor_kind`, `actor_id`, `origin_issuer`, `via_installation_id`, `agent_name`, `sender_name`, `created`, `reply_to` and `reply_preview`. The wrapper includes exact `organization_id`, `conversation_id` and `authority_issuer`. Reads additionally contain `messages` and `next_cursor`; sends contain `message` and boolean `created`. The adapter checks local authority origins, valid timestamps, positive bounded cursors, UTF-8 text/metadata limits, unique IDs and permitted shapes. It does not turn upstream display names into local identity authority.

The reviewed discovery response does **not** contain an installation UUID. Its service/environment binding proves the consuming service placement; successful sends separately prove the configured installation through both actor fields. Read/create wrappers likewise cannot prove owner identity alone. An operator must provision the exact intended workload and installation mapping; a local recovery/adoption operation needs separate app-ownership verification rather than trusting a title match.

## Bounded transport and uncertain outcomes

The adapter uses the existing Auth HTTP client, configured without environment proxies, upstream cookie acceptance or redirects. It sends only explicit application authorization and JSON. Paths are fixed internally; there is no arbitrary URL, participant endpoint or caller-supplied authorization header. HTTPX's transport has no automatic retries. Per-request connect/pool/read timeouts and a 25-second streaming deadline apply. Decoded JSON is bounded to 64 KiB for creation/discovery, 1 MiB for the recovery list, 128 KiB for send receipts and 4 MiB for message pages. Extremely escaped/big pages may require a smaller page limit. Duplicate JSON keys, nonfinite constants, malformed JSON, hostile schemas and mismatched authorities fail closed.

`CoreChatFailure` exposes only static `code`, `status_code` and `uncertain` fields. It never exposes provider bodies, exception text, workload values or credentials. Known 4xx refusals are distinguished from mutations whose outcome lacks a verified receipt. Redirects, 5xx, timeouts and malformed successful mutation responses are uncertain; no automatic replay is performed.

Core send is idempotent for the conversation, sender and stable request ID: exact retries return the same message and changed text/reply/identity return 409. A send marker must commit locally before external I/O, and completion audit/receipt must commit before display. Recovery uses that exact stored request, with fresh authority and publication checks.

Core conversation creation has **no** idempotency key. An interrupted creation can leave an upstream orphan. Persist its attempt before I/O, retain an uncertain state, and never silently create a second conversation. The web recovery action deliberately refuses uncertain creation: the current Core joined list provides neither ownership nor sole-participant proof, so matching even the exact stored title/topic cannot safely adopt a room. An operator must reconcile the upstream orphan outside the student application before a separately reviewed repair. The joined list is capped at 200 with no pagination and is not an adoption authority. A connection never attempted, or explicitly refused before acceptance, can be connected later through the manager's explicit action; no uncertain create is retried.

Core polling is cursor based and rechecks current authorization on each request. `next_cursor` advances over scanned messages, including messages hidden by tags; an empty visible page can still advance. The native Core browser polls every three seconds, but NCAcademy's own UI controls its polling. The service does not require WebSockets, Redis or a message broker.

## Operator deployment prerequisites

Local tests and source packaging do not grant access or configure the running Core instance. Before enabling real traffic:

1. Review the installed NCAcademy marketplace package, preserving its exact existing identity contract. Add dependency `core-chat` and declared capabilities `chat.read`, `chat.send`, `chat.manage`; include `chat.ai` only for the approved AI-publication path. Version and explicitly upgrade the existing installation with the intended grants. Application source must never silently add grants or roles.
2. Approve Core Chat availability and the NCAcademy application's required environment/tag access. Create or select its own environment/deployment-scoped workload credential through Core's normal operator process; never reuse a different service's credential. Supply the actual installation UUID as `CORE_CHAT_INSTALLATION_ID`, distinct from `CORE_SERVICE_ID`.
3. Keep `CORE_CHAT_ENABLED` independently false by default. An explicitly reviewed trusted recipe must inject it and the existing runtime-secret `core_workload_token` only into the enabled API. The current teacher-agent supplement injects this token only when `allow_agent_teachers` is enabled; that is not an independent human-chat enablement contract. Review an independent chat recipe option or equivalent operator-owned injection before production use. No application AWS task role, student credential or migration-task workload secret is needed.
4. Verify discovery, app-only group creation, exact application attribution, local roster revocation, human-approved AI publication, cursor recovery, audit retention and restore in an authorized acceptance environment. Core's message store has no added retention/legal-hold/export guarantee from this adapter; preserve NCAcademy's immutable receipts and independent retained audit archive.

No Core source patch is required for local group discovery or joining. The reviewed API supplies message storage and app ownership; local registry membership remains the only student enrollment path. No live Core request, group membership change, message send, secret creation or cloud configuration was performed for the adapter's mocked tests.
