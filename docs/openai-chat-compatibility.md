# Bounded OpenAI Chat Completions compatibility

Reviewed against official OpenAI documentation on October 28, 2026. Academy's
existing transport uses Chat Completions without tools, streaming, retries or
provider-side completion storage. This adjustment changes request parameters
only for the exact `https://api.openai.com/v1` endpoint and these reviewed IDs:

| Model | `reasoning_effort` | `temperature` |
| --- | --- | --- |
| `gpt-6-luna` | `none` | `0` |
| `gpt-6-sol` | `none` | `0` |
| `gpt-6.1-terra` | `none` | `0` |
| `gpt-6.1-sol` | `low` | omitted |
| `gpt-6-astra` | `low` | omitted |

These requests use `max_completion_tokens` with the **unchanged approved token
cap**. OpenAI defines that limit as visible output plus reasoning tokens. Academy
does not add a reasoning allowance, increase quotas, retry truncated output or
fall back to another model. A small selector budget such as 80 tokens should use
an approved nonreasoning configuration such as `gpt-6-luna`; reasoning-required
models can consume that entire budget before returning usable selection text.
Malformed or incomplete selections retain the existing withholding behavior.

Other compatible endpoints, model names, dated snapshots and GPT-5 models keep
their existing payload. New IDs require an explicit compatibility review rather
than inference from a name prefix. This is not model discovery, an availability
guarantee, a change to school policy or permission to call a provider. Teacher
behavior, corpus, model allowlist, current approval, deployment gate and audit
requirements continue to apply. Changing a connection or model still requires
normal reapproval. No live provider call was made during this implementation.

Sources: [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[GPT-6 migration parameters](https://developers.openai.com/api/docs/guides/latest-model),
and [Chat Completions request parameters](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

Mocked regression coverage in `apps/api/tests/test_provider_openai.py` checks the
wire payload, exact endpoint/model boundaries, unchanged caps, TLS hostname
pinning and a single send after an unsuccessful response. The tests never use
real keys or contact a provider.
