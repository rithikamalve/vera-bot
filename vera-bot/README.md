# Vera Bot — magicpin AI Challenge

## Approach

**Trigger-routed composition**: Every message starts from the trigger kind. The kind determines the leading signal, the CTA shape, the compulsion lever, and what context to block. A `research_digest` trigger quotes exact numbers from the digest title and uses a curiosity CTA. A `recall_due` trigger sends as the merchant to the customer, uses available slot times from the payload, and ends with "Reply 1 / Reply 2". A `seasonal_perf_dip` with `is_expected_seasonal: true` frames the dip as normal and suggests a countermeasure — it does not alarm. Each of the 27 trigger kinds in the expanded dataset has a specific routing instruction.

**Context isolation rule**: For non-research triggers, a hard isolation rule blocks the LLM from referencing category digest items, research papers, or clinical statistics. This prevents a common failure mode where renewal or perf_dip messages bleed in irrelevant research findings. Research triggers have the inverse rule: all numbers must come verbatim from the digest item.

**Signal extraction before the LLM call**: `extract_facts()` pre-computes 7 facts (CTR gap vs peer, top digest item, active offer, lapsed count, months since last visit, seasonal beat, top trend signal) before the prompt is built. The LLM only sees verified numbers — it has nothing to fabricate.

**Compulsion enforcement**: Every compose call requires the model to name and apply one lever from: specificity, loss_aversion, social_proof, effort_externalization, curiosity, reciprocity, binary_commit. This is a hard instruction in the task section, not a suggestion. The lever named appears in the rationale field of every output.

**Post-LLM validation**: `validators.py` enforces structural correctness after every LLM response — valid CTA enum, no taboo words for the merchant's category, no verbatim repeat of a prior message body, no double CTA phrases.

**Conversation FSM**: `fsm.py` classifies each merchant reply into one of six states: auto_reply, intent_yes, intent_no, question, hostile, neutral. Auto-replies are detected via WhatsApp Business phrase patterns on turn 1, then by repeated message content. They get one soft nudge ("Looks like an auto-reply — reply YES when you're free") then a graceful exit. Explicit YES routes to `intent_yes_followthrough` — no re-pitching, no re-quoting CTR. Hostile messages end immediately.

**Parallel tick execution**: The `/v1/tick` endpoint runs in two passes. Pass 1 selects candidates via fast in-memory checks (expiry, suppression, merchant/category/customer lookup, active conversation window). Pass 2 runs all LLM calls concurrently via `asyncio.gather`. 20 sequential LLM calls at ~3s each would be 60s (timeout). Parallel execution brings it to ~3s total.

## Model

**GPT-4o-mini (primary) / Groq llama-3.3-70b-versatile (fallback)** at `temperature=0` — deterministic output, strong structured JSON generation. Primary model is OpenAI GPT-4o-mini; Groq is the fallback if OpenAI fails.

## Tradeoffs

**Why GPT-4o-mini over a larger model**: Latency. With 20 parallel tick calls, a slower model pushes total tick time toward the 30s timeout. GPT-4o-mini at ~2-3s per call fits cleanly. Quality tradeoff is mitigated by the structured prompt: the model doesn't need to reason freely — it follows per-kind routing instructions with pre-computed facts.

**Why trigger routing matters**: A generic "here's all the context, write a message" prompt produces mediocre output for every trigger kind. Trigger-specific instructions hit the right signal at the right moment: a `gbp_unverified` trigger leads with the 30% visibility uplift, not CTR. A `supply_alert` names the batch numbers and demands immediate action, not a soft ask.

**Why fact extraction precedes the LLM**: LLMs hallucinate when asked to compute inside generation (CTR gap arithmetic, months-since-date, delta percentages). Pre-computing grounds the prompt in verified numbers. `-0.50` as a delta renders as `-50%` in the prompt — the model never sees the raw float.

**In-memory store tradeoff**: Fastest possible context retrieval, zero dependencies. The cost is that a server restart loses all context. Mitigated by Render's "Always On" setting and a healthz cron ping during the evaluation window.

## Run locally

```bash
cd vera-bot
pip install -r requirements.txt
cp .env.example .env
# set OPENAI_API_KEY and GROQ_API_KEY in .env
uvicorn main:app --host 0.0.0.0 --port 8080
```

```bash
curl http://localhost:8080/v1/healthz
curl http://localhost:8080/v1/metadata
```

## Deploy

```bash
docker build -t vera-bot .
docker run -p 8080:8080 --env-file .env vera-bot
```

Deployed on Render. Set `OPENAI_API_KEY`, `GROQ_API_KEY`, `TEAM_NAME`, `TEAM_EMAIL` as environment variables. Port 8080.
