# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hackathon **post-discharge triage agent**: after a patient is discharged, an agent runs a check-in "call", reasons about the conversation against the patient's clinical context, and routes to one of six dispositions with a rationale and a drafted chart note. Pitched as an extension of Abridge's ambient-conversation → structured-clinical-output product.

There is **no build system, no framework manifest beyond `requirements.txt`**. Three runnable pieces: the browser mockup, the API smoke-test script, and a Flask backend.

## Running

**The mockup (`index.html`)** — fully self-contained, all logic client-side. Open directly in a browser:
```
open index.html
```
No server, no install. The "intelligence" here is still stubbed (4-tier keyword matcher, predates the 6-way redesign below) — it's a standalone demo artifact, not wired to the backend.

**The API smoke test (`test_agents.py`)** — hand-run script hitting the real Anthropic API:
```
pip install requests python-dotenv
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env    # gitignored
python test_agents.py                          # all 3 agents, 1 patient (~3 calls)
python test_agents.py --agent triage           # single agent (1 call)
python test_agents.py --patient dick --severity orange
```
No test runner or lint config — this is a smoke script, not a unit suite. Talks to the Messages API directly over `requests` (no `anthropic` SDK dependency) and prints an estimated cost after each run.

**The Flask backend (`app.py` + `routes/` + `services/` + `models.py`)** — the real API surface:
```
pip install -r requirements.txt
python seed_data.py          # create + populate the DB with the 5 demo patients
flask --app app run          # or: python app.py
```
Endpoints: `GET /health`, `GET /api/patients`, `GET /api/patients/<slug>`, `POST /api/triage`, `POST /api/draft-note`.

## Architecture

Three agents + one orchestration layer:

| Agent | Real form | Stub / older form |
|---|---|---|
| 1. Check-In Conversation | Claude generates a speaker-labeled transcript from patient context | Hand-written transcripts in `index.html`'s `PATIENTS` array / `test_agents.py`'s `DEMO_PATIENTS` |
| 2. Triage Analysis | **Live**: `services/triage_service.analyze_transcript_with_claude()` — Claude **Sonnet 5** tool-use call, reasoning over the transcript plus the patient's fuller FHIR context (see below) | `analyze_transcript()` in the same file — old keyword matcher, kept as fallback/reference only |
| 3. Chart Note Drafting | Not yet ported to Claude | `draft_note()` — string template |
| Orchestration ("Care Team Queue") | `routes/tool.py` + `rank_by_severity()` in `services/triage_service.py` | Client-side JS in `index.html`'s `<script>` block |

**⚠️ Wiring gap:** `routes/tool.py`'s `/triage` and `/draft-note` handlers currently call `triage_service.analyze_transcript()` (the old stub), not `analyze_transcript_with_claude()` (the real one). Swap that call in both handlers to actually put Agent 2 live.

### Disposition taxonomy (6-way, replacing the original 4-tier red/orange/yellow/green)

Ranked by queue priority (`SEVERITY_ORDER` in `services/triage_service.py`):

1. `emergency_department` — acute/emergent, go now.
2. `urgent_care_same_day` — moderate-to-serious, same-day in-person eval.
3. `clinician_callback_required` — **not a clinical severity tier** — a guardrail outcome for when the AI can't responsibly assess the patient at all: (a) insufficient information (vague/repeated non-answers), or (b) an uncooperative/hostile patient with no usable clinical content in the transcript. Ranked *above* the two "confirmed stable" tiers below deliberately: an unresolved unknown is treated as more urgent than a case where the model actually had enough signal to conclude things are fine, because a forced guess in either direction is riskier than flagging for human follow-up. Also the cheapest disposition to act on (just a callback), so ranking it promptly costs little even when it turns out to be nothing.
4. `clinic_follow_up` — scheduling ticket for a close (within-days) clinic visit.
5. `labs_imaging_needed` — stable, but objective data would confirm the trajectory.
6. `routine_follow_up` — recovering as expected, no new action.

The API/DB contract still uses the field name `severity` for this value, even though it's semantically a disposition rather than a severity level — kept for compatibility with `routes/tool.py` (`p["triage"]["severity"]`) and `models.TriageResult.to_dict()` rather than touching the wire format. `SEVERITY_ORDER` also still maps the old `red/orange/yellow/green` values (to the corresponding new ranks) during the transition, in case anything is still calling the old stub.

**Backend model**: Sonnet 5 for triage specifically (`TRIAGE_MODEL` constant in `triage_service.py`) — the reasoning-heaviest agent gets the stronger model. `config.py`'s `ANTHROPIC_MODEL` (default Haiku) still governs whatever calls Agents 1/3 once those are wired up.

**FHIR context depth**: the real triage call no longer uses just the flattened `Encounter.history`/`Encounter.meds` the DB stores — `load_fhir_record()` in `triage_service.py` reads the patient's full record straight from the sibling `synthetic-ambient-fhir-25/synthetic-ambient-fhir-25.jsonl` dataset file (by patient slug → `PATIENT_IDS` → dataset `patient_id`), pulling longitudinal conditions/medications plus the encounter's related Condition/Observation/Procedure/DiagnosticReport/MedicationRequest resources. Each resource category is capped at the **15 most recent** entries (sorted by whatever date field that FHIR resource type actually uses — they differ: `effectiveDateTime` for Observations, `authoredOn` for MedicationRequests, `performedPeriod` for Procedures, etc.) to keep prompt size bounded on patients with hundreds of resources (e.g. a multi-day admission), while still surfacing the clinically freshest data rather than an arbitrary/positional slice.

**Two safety guardrails** (see the system prompt in `TRIAGE_SYSTEM_PROMPT`): insufficient information and uncooperative/adversarial patient both route to `clinician_callback_required` rather than forcing a guess. When triggered, the model is instructed to name which guardrail applied inside the `rationale` array (no separate schema field — kept the shape identical to the old 4-key stub).

**Canonical prompts**: `PROMPTS.md` documents the *original* 4-tier/3-agent prompt design and is now stale relative to the 6-way taxonomy above — needs a pass to match `TRIAGE_SYSTEM_PROMPT` in `triage_service.py` before relying on it as the source of truth. `DESIGN.md` is the full narrative design doc (also predates this redesign). `README.md` covers the demo + backend plan.

## Conventions specific to this repo

- **Patient data now lives in three places, not two.** The flattened demographics (`PATIENTS` in `index.html`, `DEMO_PATIENTS` in `test_agents.py`, and the seeded `Patient`/`Encounter` rows via `seed_data.py`) are still the same 5 people (Ariane R., Latoyia W., Monica H., Traci W., Dick L.) and must stay in sync per the original convention. Separately, `services/triage_service.py`'s `load_fhir_record()` reads the *same* patients' full FHIR data directly from the dataset file at request time — this is a new, independent read path, not something seeded into the DB.
- **The old triage stub only scores PATIENT/FAMILY lines, never AGENT lines** — preserved in the real Claude prompt too (`analyze_transcript_with_claude`'s system prompt explicitly instructs this), since it's what stops the agent's own questions ("any chest pain?") from being misread as findings.
- **Never call the Anthropic API from browser JS.** The key stays server-side via `config.Config.ANTHROPIC_API_KEY`.
- Agent 2's real call uses Claude **tool-use / structured output** (`tool_choice` forcing the tool) so responses come back as validated JSON, not parsed strings — same pattern to use whenever Agents 1/3 get wired up for real.
