# Replay Feature Implementation Plan

> Execution: test-driven-development + subagent-driven-development. Tests must fail before implementation. The reliable scope is defined in `docs/replay-evaluation.md`; do not implement arbitrary tool execution or claim generic full-chain checkpoint resume.

## API contract

### `POST /api/traces/{trace_id}/spans/{span_id}/replay`

Required header: `Idempotency-Key: <uuid>`. The UUID is also the new replay Trace ID. Repeating the same key and request returns the persisted result without another upstream call; reusing it for a different request returns 409. A still-running attempt with the same key also returns 409 rather than dispatching twice.

Request:

```json
{
  "confirm": true,
  "overrides": {
    "model": "optional-model",
    "temperature": 0.2,
    "top_p": 0.9,
    "seed": 42,
    "max_tokens": 512
  }
}
```

`confirm` must be literal `true`. Overrides are optional, extra keys are forbidden, numeric values are range checked, and null override values are invalid. No URL, credential, messages, tools, or arbitrary request fields can be overridden.

Successful attempt creation returns **201**, including upstream failures that were persisted as audit evidence:

```json
{
  "replay_trace": {"id": "new-trace-id", "parent_id": "source-trace-id"},
  "replay_span": {"id": "new-llm-span-id", "kind": "llm"},
  "source_span": {"id": "source-span-id"},
  "comparison": {
    "status": "different",
    "content_equal": false,
    "reasoning_equal": false,
    "tool_calls_equal": true,
    "content_similarity": 0.42,
    "content_diff": "--- 原始输出\n+++ 重放输出\n...",
    "attribution_note": "单次一致或差异均不能单独证明模型随机性或幻觉成因；请结合多次实验与事实评估。"
  }
}
```

`replay_trace` and spans use the existing serializer shapes, not reduced ad-hoc objects. `comparison.status` is `same | different | failed`. Replay lineage lives at `replay_trace.metadata.replay` and includes source IDs, request source (`span | legacy_single_llm_trace`), overrides, mode (`llm_span`), and comparison. No original client credential is stored or reused.

Validation before any upstream call:

- 404: trace missing or span does not belong to trace.
- 409: non-LLM span, missing/ambiguous historical payload, no safely reconstructable chat-completions request, idempotency-key request conflict, or an attempt with that key still running.
- 422: confirmation missing/not true, malformed/missing idempotency key, invalid overrides, sensitive fields rejected by the existing sanitized validation handler.
- 429: configured daily token soft budget exhausted.
- 503: every current candidate circuit is open.

Upstream/network errors after dispatch are persisted in the new replay Trace and returned as 201 with `comparison.status=failed`; the UI links to the failed attempt rather than hiding it.

## Capture requirements

`persist_openai_trace` must save the complete effective chat-completions request and normalized response on every LLM Span (`input` and `output`). LLM metadata retains the original requested model from gateway metadata so a replay can run current route matching without confusing a previously rewritten target model with the caller's requested model. This must work for ordinary JSON, SSE, failures, and repeated invocations sharing a Trace ID.

Replay payload selection:

1. Use a deep copy of `span.input` when it is an object with a list `messages`.
2. Otherwise permit `trace.input` only when the Trace has exactly one LLM Span and that input has list `messages` (legacy unambiguous fallback).
3. Otherwise reject 409.
4. Reject top-level credential-like payload keys rather than forwarding them.
5. Determine requested model from an explicit override, else saved original requested-model metadata, else payload/trace/default model. Apply current route target mapping to the outbound body.

## Backend tasks

1. Add `backend/tests/test_replay.py` first and run it to prove failure. Cover independent capture, exact message/parameter reuse, whitelist overrides, current-route selection/key use, no original client credential reuse, confirmation, non-LLM/cross-trace/ambiguous legacy rejection, budget, sensitive-field validation, success lineage/diff, upstream failure persistence, and SSE replay. Run existing gateway/proxy suites as regression gates.
2. Add typed replay schemas, payload reconstruction/comparison helpers, and the endpoint. Reuse gateway candidates, budget, circuit, response parsing, safe cleanup, and persistence semantics rather than adding a second insecure proxy.
3. Extend persistence with optional replay trace context so lineage and capture are committed atomically with the new Trace. Reserve the idempotent replay Trace before dispatch so a crash or concurrent retry cannot silently repeat a paid request. Keep existing callers backward compatible.
4. Run targeted RED -> GREEN, then full backend pytest and Ruff.

## Frontend tasks

1. Add failing tests for direct Span jump, Replay Studio parameter overrides/confirmation, result comparison/link, non-LLM state, and API errors.
2. Add `replaySpan` API client and response types using existing `Trace`/`Span` shapes. The client sends a UUID `Idempotency-Key`; ReplayStudio keeps the same key for retries of an unfinished attempt and creates a new key for a deliberate new experiment.
3. Add a `ReplayStudio` panel on Trace detail for the selected LLM Span. Clearly label the read-only player as zero Token and real replay as potentially paid. Real replay never claims to run tools or resume the full chain.
4. Inputs: optional model, temperature, top_p, seed, max_tokens. Only user-edited fields are sent as overrides. Require a confirmation checkbox before dispatch. Disable duplicate submissions and show progress/error states; a test must hold the first request pending while attempting a second submission.
5. Show original/replay content side by side, equality/similarity/diff, fixed attribution caveat, and a React Router link to the new Trace. Reset transient state on Span change. All rendered evidence goes through existing sensitive-field/text redaction.
6. Run targeted Vitest, then full frontend tests, ESLint, typecheck/build.

## Documentation and verification

- Update README, Guide, API contract, verification report, and the research evaluation. Do not claim replay is unique or that a single diff diagnoses hallucinations.
- Independent spec review and code-quality/security review; fix all important findings and document severity.
- Start isolated real backend/frontend with a local mock upstream. Browser-verify read-only play/pause/direct jump, real replay without external paid calls, parameter override, no tool execution, comparison, new Trace lineage, and failed/unsupported states.
- Run the complete pre-commit gate and commit with identity `AgentTrace Dev <dev@agenttrace.local>` only after all checks pass.
