# School and class groups

Platform-local students and parents cannot use Core-backed groups; API reads/writes are denied and entry points are hidden. Their accounts and private discussions are not synchronized to Core. This guide describes the remaining Core-authenticated staff/student/agent integration. Local students retain their own Homework Help and enrolled coursework. A separate local group-message backend is not included in the family-account revision. See [local family boundaries](family-accounts.md).

Groups incorporates NCE Core Chat into NCAcademy's school workspace. Core stores the conversation messages; NCAcademy controls school discovery, current class membership, author evidence and AI publication review. The browser never receives the Core workload credential. This feature is separate from private Homework Help and does not run a model.

## Membership and management

Each class has one required class group. Class creation, curriculum publication and enrollment register it transactionally; opening Groups also registers existing classes. Membership is derived from current enrollment and teaching ownership on every request. An enrolled student does not need to join and cannot leave the required class conversation. Removing enrollment or school access removes chat access on the next request, including history and pending sends.

Human Admin and School administrators manage groups in their permitted schools. Teachers may create groups and manage those they own, plus class groups they currently teach. Approved Core-bound teaching agents may create and manage groups only within their currently assigned classes. A saved AI persona alone has no API identity or group permissions. Creating a group does not start an agent or invoke a model.

School groups are discoverable by current school members. Class-scoped groups additionally require current class access. Open groups allow immediate joining; approval groups record a request for their manager; private groups appear only to their managers, members, invitees or requesters. Managers can invite, approve, reject or remove eligible members. Joining never grants school membership, course enrollment, teaching authority or a Core capability. Names and roles are shown without exposing member email addresses or credentials.

Required class groups follow the roster rather than invitations. Administrators must change the actual enrollment to remove a student from one. Optional group membership remains subordinate to current school and course access.

The current directory returns at most 100 eligible groups and 500 course choices, with required class groups first. It filters ineligible/private scopes before the cap and explicitly reports truncated results. This bounded first version has no pagination/search over omitted groups. The creation allowance is also bounded; class registration remains available independently of optional-group creation limits.

## Conversations and AI teachers

Group messages support bounded plain text, replies, actual local author labels and cursor-based history. Text is rendered as inert text. There are no attachments, arbitrary embeds, link previews, message editing or deletion. The active visible group polls with a bounded cursor; every request checks current authorization.

Human messages can be sent directly. A teaching agent's message creates an immutable pending proposal; it is not sent to Core or shown to students until a current human School/Admin approves that exact content. A Teacher alone cannot approve AI publication. Approval rechecks the original agent's current Core binding, local placement, class access and immutable message context. Revocation or changes withhold publication. The same review requirements apply to explicit retries. This preserves the existing human approval rule for shared student-visible AI material; automatic private tutoring remains a separate approved-content workflow.

The Core installation is the sole upstream participant. Core attributes ordinary messages to the NCAcademy application, and approved agent messages to that application's AI sender. NCAcademy's immutable command and delivery receipt identify the actual authenticated human or teaching agent and the human approver. These are application audit records, not delegated Core user signatures. Only upstream messages matching an exact admitted local receipt are displayed. Unknown or mismatched Core messages are withheld, including untrusted reply previews. See the [Core wire contract](core-chat-contract.md).

## Receipts, audit and recovery

The API admits at most 200 new message commands per actor per UTC day. A message is limited to 4,000 characters and 16 KiB of UTF-8. Each operation retains an immutable command, exact text/hash, author/authority/scope snapshot, sealed send attempt, outcome and verified delivery evidence. Membership changes and AI publication decisions retain immutable evidence. Send markers commit before Core I/O; completion evidence and audit commit before display. History reads record scope, returned count and withheld count.

If a send response is lost, the browser keeps the opaque request key rather than resending automatically. Check its receipt, then explicitly retry the original command only when the server permits it. Core's stable request ID makes that same sender/content/reply idempotent. Changing a draft does not change an admitted command. A pending or uncertain attempt is never silently replaced with a fresh message.

Core room creation has no idempotency key. Disabled chat leaves the local group pending without an external call. A manager can explicitly connect a pending or provably rejected/unsent creation after configuration is corrected. An ambiguous creation stays unavailable for student use and requires operator reconciliation. A matching room title/topic is insufficient proof of application ownership and sole participation; the application never adopts it or creates another room automatically. The current Core API supplies no safe self-service repair for this case.

Schema 8 protects commands, receipts, proposals and membership events from update/delete in the ORM and SQLite/PostgreSQL triggers. Send attempts can move once from pending to terminal and are then sealed. The owner-only additive migration preserves earlier identities, coursework and audit history. Hash chains still require an independently retained trusted checkpoint. Existing full tenant audit export includes group command text and outcomes for authorized Admin review; Core message retention and an NCAcademy evidence archive are separate operational responsibilities.

## Enabling Core Chat

`CORE_CHAT_ENABLED=false` is the default, independent of live models and agent admission. Local demonstration authentication refuses live Core Chat. There is no production offline chat fallback; an unavailable Core service leaves conversations unavailable.

An operator must explicitly upgrade the existing NCAcademy installation with the `core-chat` dependency and `chat.read`, `chat.send`, `chat.manage` grants, plus `chat.ai` for approved agent publication. Configure its canonical `CORE_CHAT_INSTALLATION_ID` and its own environment-scoped workload secret through an approved API-only runtime path. The existing recipe's agent-only secret switch is insufficient for independent human chat enablement. The detailed [contract and prerequisites](core-chat-contract.md) identify this release dependency. No application source silently changes Core grants, installs packages, provisions credentials or alters native participants.

Local verification uses synthetic students and a mocked Core transport, with no live Core or model calls. Before enabling real use, verify the deployed contract, capabilities, exact application attribution, roster revocation, AI approval, interrupted operations and independent retained audit recovery. Source preparation does not establish a deployed Core Chat connection.
