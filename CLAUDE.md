# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hackathon mockup of a **post-discharge triage agent**: after a patient is discharged, an agent runs a check-in "call", scores the conversation against condition-specific red flags, and routes to one of four dispositions (red / orange / yellow / green) with a rationale and a drafted chart note. Pitched as an extension of Abridge's ambient-conversation → structured-clinical-output product.

There is **no build system, no framework, no package manifest**. Two runnable artifacts only.

## Running

**The mockup (`index.html`)** — fully self-contained, all logic client-side. Open directly in a browser:
```
open index.html
```
No server, no install. The "intelligence" is stubbed (see below), so nothing to run besides the browser.

**The API smoke test (`test_agents.py`)** — the only piece that hits the real Anthropic API:
```
pip install requests python-dotenv
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env    # gitignored
python test_agents.py                          # all 3 agents, 1 patient (~3 calls)
python test_agents.py --agent triage           # single agent (1 call)
python test_agents.py --patient dick --severity orange
python test_agents.py --model claude-sonnet-5  # quality pass; default is claude-haiku-4-5 for cost
```
There is no test runner or lint config — `test_agents.py` is a hand-run smoke script, not a unit suite. It talks to the Messages API directly over `requests` (no `anthropic` SDK dependency) and prints an estimated cost after each run.

## Architecture

Three agents + one orchestration layer. Same conceptual pipeline appears twice — once stubbed in the browser, once real against the API:

| Agent | Real form (`PROMPTS.md`, `test_agents.py`) | Stub in `index.html` |
|---|---|---|
| 1. Check-In Conversation | Claude generates a speaker-labeled transcript from patient context | Hand-written transcripts in the `PATIENTS` array |
| 2. Triage Analysis | Claude tool-use call → `{severity, label, rationale, flags}` | `analyzeTranscript()` — keyword matcher |
| 3. Chart Note Drafting | Claude tool-use call → `{subjective, assessment, plan}` | `draftNote()` — string template |
| Orchestration ("Care Team Queue") | Backend runs all 3 per patient, ranks by severity | Client-side JS in the `<script>` block |

**Disposition taxonomy** (severity order for queue ranking): 🔴 red = urgent care referral → 🟠 orange = physician callback today → 🟡 yellow = labs/imaging recommended → 🟢 green = routine follow-up. When borderline, the triage prompt deliberately escalates to the more urgent tier.

**Canonical prompts live in `PROMPTS.md`.** `test_agents.py` inlines condensed copies of those same prompts and tool schemas — if you change a prompt or schema, update both. `DESIGN.md` is the full narrative design doc; `README.md` covers the demo + the plan to wire in real Claude.

## Conventions specific to this repo

- **Patient data is duplicated and must stay in sync.** The same 5 FHIR-grounded patients (Ariane R., Latoyia W., Monica H., Traci W., Dick L.) exist as the `PATIENTS` array in `index.html` and as `DEMO_PATIENTS` in `test_agents.py`. Edit both. All demographics/conditions/meds come from the synthetic `synthetic-ambient-fhir-25` dataset — do not invent clinical facts; only the transcripts are hand-written.
- **The triage stub only scores PATIENT/FAMILY lines, never AGENT lines.** `analyzeTranscript()` was hand-tuned so the agent's own questions ("any chest pain?") don't trigger a positive finding, and to avoid tripping on in-sentence negations. Preserve this if editing the matcher. The real fix for this class of problem is the LLM call — that's the whole point of Agent 2.
- **Never call the Anthropic API from browser JS.** The key must stay server-side. The intended production path (per `README.md`/`DESIGN.md`) is a small Flask/FastAPI or Express backend exposing `POST /api/triage` and `POST /api/draft-note`, returning the same JSON shapes the stubs return today so the frontend barely changes.
- **Stubbed logic is labeled in the UI** with yellow "stubbed" tags — keep that honesty if you extend the mockup.
- Agents 2 and 3 use Claude **tool-use / structured output** (`tool_choice` forcing the tool) so responses come back as validated JSON, not parsed strings.
