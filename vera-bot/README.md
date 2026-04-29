# Vera Bot — magicpin AI Challenge

## Approach

**Trigger-routed prompt templates**: Every composed message starts from the trigger kind. The trigger determines the CTA shape, the urgency framing, and which compulsion levers get priority. A `research_digest` trigger gets an open-ended curiosity CTA; a `recall_due` trigger gets a binary `Reply 1/2` booking slot.

**Signal extraction before LLM call**: `extract_facts()` in `composer.py` pre-computes 7 scoreable facts (CTR gap vs peer, top digest item, active offer, lapsed patient count, months since last visit, seasonal beat, top trend signal) *before* the LLM call. This keeps the prompt concrete and prevents the model from fabricating numbers — it only has real numbers to work with.

**Post-LLM validation**: `validators.py` enforces structural correctness (valid CTA enum, no taboo words, no repeat body, not too many CTA phrases) after every LLM response. Invalid outputs surface as errors rather than silent bad messages.

**Conversation FSM**: `fsm.py` classifies each merchant reply (auto_reply, intent_yes, intent_no, question, hostile, neutral) and routes accordingly. Auto-replies get one soft nudge, then a graceful exit. Explicit YES routes directly to action/confirmation mode without re-pitching. 3+ unanswered neutral turns exit cleanly.

## Model

**Groq `llama-3.3-70b-versatile` at temperature=0** — fast inference, deterministic output, strong instruction-following for structured JSON generation.

## Key design decisions

- **Why trigger routing matters**: Different triggers require fundamentally different message shapes. A generic "prompt with all context" produces mediocre output for every trigger; a trigger-specific template hits the right lever for the right moment.
- **Why fact extraction happens before LLM**: LLMs hallucinate when asked to compute (CTR gap arithmetic, months-since-date) inside the generation. Pre-computing grounds the prompt in verified numbers.
- **How auto-reply detection works**: The store tracks the last 3 merchant turns. If the same message string appears in prior turns from the same party, it's flagged as auto-reply. Two detections in one conversation → graceful exit.

## Run locally

```bash
cd vera-bot
pip install -r requirements.txt
cp .env.example .env
# edit .env — set GROQ_API_KEY
uvicorn main:app --host 0.0.0.0 --port 8080
```

Test:
```bash
curl http://localhost:8080/v1/healthz
curl http://localhost:8080/v1/metadata
```

## Deploy with Docker

```bash
docker build -t vera-bot .
docker run -p 8080:8080 --env-file .env vera-bot
```

One-click: push the image to Railway or Render, set `GROQ_API_KEY` as an env var, expose port 8080.
