# Engineering Manager's Log

**Name:** Poojitha G  
**Time spent:** ~2 hours (60 min build, 30 min test/verify, 30 min wrap-up)

---

## How I broke the work down

1. **Read the entire scaffold first** (README, stub, server, fixtures, env.example) before writing a line — understanding the auth model (read token vs. write token) and the routing table up front shaped every downstream decision.

2. Decomposed into five discrete units and worked them in order:
   - `TriageClient` (HTTP wrapper, token-scoped)
   - `classify_email` (LLM classification with prompt-injection defence)
   - `plan_actions` (pure routing, no side effects)
   - `_enrich_action` (LLM drafting of reply/alert content, separated from routing)
   - `execute` + `triage_inbox` (gate + orchestrator)

3. Wrote the tests **before** wiring the orchestrator — having `FakeClient` and injectable stubs forced me to nail the interfaces before implementation details crept in.

4. Runner (`run_triage.py`) last: once the core was solid, the CLI was just display logic around `triage_inbox`.

---

## Where I ran things in parallel

- Fetched all five repo files simultaneously (README, stub, server, emails, env.example) in one batch rather than sequentially — saved the first round-trip of back-and-forth.
- While writing `triage_skill.py`, mentally ran the 8 fixture emails through the routing table in parallel to spot edge cases (e-007 prompt injection, e-008 ambiguous billing/sales).
- Tests and the runner were mentally "specced" before `triage_skill.py` was finished — so writing them after the core was just transcription, not design work.

---

## One time the AI was wrong, and how I caught it

The LLM stub in `plan_actions` initially wanted to call `_draft_reply_body` inline — making the function impure and untestable without a live API key. The stub's own docstring says "Pure and deterministic — no network, no LLM, no side effects." I caught it by reading the requirement literally, then restructured so `plan_actions` only does routing (pure) and LLM enrichment happens in a separate `_enrich_action` step called from `triage_inbox`.

The benefit: every `plan_actions` test runs in milliseconds with zero credentials — and the separation is cleaner anyway.

---

## What I deliberately cut to fit the 2 hours

- **Retry logic / error handling on LLM calls.** If the Anthropic API times out, the run fails. Acceptable for a prototype; production would need exponential backoff.
- **Structured logging / audit trail to disk.** The `/_audit` endpoint captures side effects server-side, so I leaned on that instead of building my own log store.
- **A web UI for the HITL gate.** The approver is a CLI prompt. In production this would be a card surfaced in Slack or a web dashboard — the interface is injected, so swapping it is a one-liner.
- **Batch/async LLM calls.** Each email is classified then enriched sequentially. With 8 emails this is fine; at inbox scale you'd want `asyncio` + concurrent API calls.
- **Confidence scores.** The classifier just returns a label. A production version would include a confidence score and a fallback review queue for low-confidence classifications.

---

## The design decision I'm proudest of

**Two-layer token discipline, enforced at the client boundary.**

The write token is held by `TriageClient._write_headers()`, which raises `PermissionError` if `write_token` is `None`. Spam emails plan zero actions, so `execute()` is never called for them — the write token is provably unreachable on the spam path, not just "we promise not to call it."

Combined with the human-in-the-loop gate in `execute(approved=bool)`: even if a bug in the orchestrator passed `approved=True` erroneously, a client constructed without a write token would hard-fail before any network call. The security property is layered — you need both approval *and* a write-capable client. Neither alone is sufficient, and the two checks are enforced at different boundaries (logic vs. HTTP).

The approver callable is also fully injectable, which means the CLI prompt, a Slack card, and a unit test stub are all the same interface — no special-casing needed.
