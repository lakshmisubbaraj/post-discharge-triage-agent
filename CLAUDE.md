# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hackathon **post-discharge triage agent**: after a patient is discharged, an agent runs a check-in "call", reasons about the conversation against the patient's clinical context, and routes to one of six dispositions with a rationale and a drafted chart note. Pitched as an extension of Abridge's ambient-conversation → structured-clinical-output product.

There is **no build system, no framework manifest beyond `requirements.txt`**. Two parallel demo tracks share the same Agent 2/3 prompts but have separate data/patients and separate frontends:

1. **DB-backed track (original)** — 5 patients grounded in the `synthetic-ambient-fhir-25` dataset (Ariane R., Latoyia W., Monica H., Traci W., Dick L.), persisted through Flask + SQLAlchemy (`Patient`/`Encounter`/`CheckIn`/`TriageResult`), called via ElevenLabs (real Twilio dial) or a simulated canned-transcript thread.
2. **Standalone GI voice-call track (newer)** — 7 GI patients (helen, miguel, harriet, robert, deshawn, yolanda, jordan_test) grounded in `synthetic-gi-data/`, living entirely as a client-side `PATIENTS` array in `index.html`/`demo.html` — never seeded into the DB. `index.html` places a real live ElevenLabs voice call in the browser; `demo.html` is the same UI with voice stripped out, showing each patient's pre-baked reference analysis instead.

## Running

**`demo.html`** — fully self-contained, no voice, all logic client-side. Open directly in a browser:
```
open demo.html
```
No server, no install. Shows the 7 GI patients with pre-baked triage/note results (generated offline by `test_gi_agents.py` against real Claude calls, not a keyword stub — see "GI voice-call track" below). This is the safe fallback demo path if the voice integration isn't working.

**`index.html`** — same UI and same 7 GI patients as `demo.html`, plus a real live ElevenLabs voice call in the browser (via the `@elevenlabs/client` JS SDK, not the `<elevenlabs-convai>` widget — the widget only exposes lifecycle events, not a usable transcript). Open directly in a browser:
```
open index.html
```
For the call button to actually place a voice call you need `ELEVENLABS_AGENT_ID` configured (hardcoded in `index.html` as a JS constant, currently `agent_3901kxvhbaadf6y9kwc413n03v2g` — the same agent ID as `.env`'s `ELEVENLABS_AGENT_ID`, so it's one ElevenLabs agent shared by both demo tracks). After the call ends, `index.html` POSTs the real captured transcript to the Flask backend's `/api/gi/analyze` (see below) so Agent 2/3 reason over what was actually said; if the backend isn't running or the call fails, it falls back to that patient's pre-baked reference analysis and labels it as such in the UI (amber "⚠️ Reference analysis" vs. green "✓ Live analysis").

**The API smoke test (`test_agents.py`)** — hand-run script hitting the real Anthropic API against the **5 DB-track ambient-FHIR patients**:
```
pip install requests python-dotenv
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env    # gitignored
python test_agents.py                          # all 3 agents, 1 patient (~3 calls)
python test_agents.py --agent triage           # single agent (1 call)
python test_agents.py --patient dick --severity orange
```
No test runner or lint config — this is a smoke script, not a unit suite. Talks to the Messages API directly over `requests` (no `anthropic` SDK dependency) and prints an estimated cost after each run.

**The GI eval script (`test_gi_agents.py`)** — the equivalent smoke/eval script for the **7 GI-track patients**, run against `synthetic-gi-data/` via `gi_context.build_gi_clinical_context()`:
```
python test_gi_agents.py
```
Its output (`gi_eval_results.json`, gitignored) is what gets baked into `index.html`/`demo.html`'s `PATIENTS` array as each patient's pre-baked reference `triage`/`note` fields — regenerate and re-bake this if `TRIAGE_SYSTEM_PROMPT`/`NOTE_SYSTEM_PROMPT` change, since the pre-baked reference data will otherwise silently drift from what a fresh live call would produce (this already happened once: Miguel's baked-in reference still shows `emergency_department` from before the ED-vs-urgent-care prompt fix below).

**The Flask backend (`app.py` + `routes/` + `services/` + `models.py`)** — the real API surface, serving both tracks:
```
pip install -r requirements.txt
python seed_data.py          # create + populate the DB with the 5 DB-track demo patients
flask --app app run          # or: python app.py
```
Endpoints: `GET /health`, `GET /api/config`, `GET /api/patients`, `GET /api/patients/<slug>`, `POST /api/triage`, `POST /api/draft-note`, `POST /api/patients/<slug>/call`, `GET /api/patients/<slug>/call`, `POST /api/patients/<slug>/call/widget`, `POST /api/patients/<slug>/call/widget/finish`, `POST /api/gi/analyze` (the GI-track live-analysis endpoint used by `index.html`).

## Architecture

Three agents + one orchestration layer. Agents 2 and 3 are split into their own service files (`services/triage_service.py`, `services/note_service.py`), sharing FHIR-parsing code via `services/fhir_context.py`:

| Agent | Real form | Stub / older form |
|---|---|---|
| 1. Check-In Conversation | DB track: `services/call_service.py` dials a real ElevenLabs/Twilio call, or streams a simulated canned transcript. GI track: `index.html` places a real live ElevenLabs browser voice call via the `@elevenlabs/client` SDK, capturing the actual transcript. | Hand-written transcripts in `demo.html`/`index.html`'s `PATIENTS` array, `test_agents.py`'s `DEMO_PATIENTS`, and `gi_demo_patients.py`'s `GI_DEMO_PATIENTS` |
| 2. Triage Analysis | **Live**: `services/triage_service.analyze_transcript_with_claude()` — Claude **Sonnet 5** tool-use call, reasoning over the transcript plus the patient's fuller FHIR context (see below). Called directly by `routes/tool.py`'s `/api/triage`/`/api/draft-note`, and by `services/gi_live_service.py` (reusing the identical `TRIAGE_MODEL`/`TRIAGE_SYSTEM_PROMPT`/`TRIAGE_TOOL`) for the GI track's `/api/gi/analyze`. | `analyze_transcript()` in the same file — old keyword matcher, kept as fallback/reference only |
| 3. Chart Note Drafting | **Live** (partially): `services/note_service.draft_note_with_claude()` — Claude Haiku tool-use call. Used inline by `services/gi_live_service.py` for the GI track's `/api/gi/analyze`. **Not** yet called by the DB track's `routes/tool.py` `/api/draft-note` fallback path (see wiring gap below). | `draft_note()` in the same file — string template, still the only thing `services/pipeline_service.py` calls |
| Orchestration ("Care Team Queue") | `routes/tool.py` + `rank_by_severity()` in `services/triage_service.py` (DB track); `services/pipeline_service.finalize_call()` (DB track, runs after a call ends); `services/gi_live_service.analyze_live_call()` (GI track, runs Agent 2 then Agent 3 in sequence) | Client-side JS in `demo.html`/`index.html`'s `<script>` block |

**⚠️ Current wiring gap** (this replaces an earlier gap in `routes/tool.py` that has since been fixed — its `/triage` and `/draft-note` handlers already correctly call `analyze_transcript_with_claude()`): `services/pipeline_service.finalize_call()` — invoked by `services/call_service.py` once a DB-track call (live or simulated) finishes — still calls the **old stubs**, `triage_service.analyze_transcript()` and `note_service.draft_note()`, not the real Claude versions. So a DB-track voice call that completes end-to-end today still gets keyword-stub triage + a templated note, even though hitting `POST /api/triage`/`POST /api/draft-note` directly on the same patient gets the real Claude reasoning. Swap `pipeline_service.finalize_call()`'s two calls over to `analyze_transcript_with_claude()` / `draft_note_with_claude()` to close this gap.

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

**`emergency_department` vs. `urgent_care_same_day` calibration**: these two were getting confused in practice — a patient reporting fever + productive cough + exertional/at-rest dyspnea after a procedure (no other red-flag features) was being over-triaged to `emergency_department`. `TRIAGE_SYSTEM_PROMPT` now spells out that `emergency_department` requires an *objective severe-distress feature* (can't speak/breathe, chest pain, fainting, confusion, cyanosis, uncontrolled bleeding), and explicitly defaults fever+cough+dyspnea-without-those-features to `urgent_care_same_day` instead. Note the pre-baked reference data for the GI-track's "miguel" patient in `index.html`/`demo.html`'s `PATIENTS` array predates this fix and still shows the old (wrong) `emergency_department` call — regenerate via `test_gi_agents.py` and re-bake if you need that reference copy corrected too, since it's a separate static artifact from the live prompt.

**Canonical prompts**: `PROMPTS.md` documents Agent 2's prompt and schema and is kept in sync with `TRIAGE_SYSTEM_PROMPT`/`TRIAGE_TOOL` in `triage_service.py` — treat any drift between them as a bug. `DESIGN.md` is the full narrative design doc. `README.md` covers the demo + backend plan. `ELEVENLABS_WORKFLOW.md` documents the actual live ElevenLabs agent system prompt (a single flat prompt, not a node-based workflow) shared by both demo tracks' voice calls.

## Conventions specific to this repo

- **Patient data now lives in (at least) four places.** DB-track: the flattened demographics (`DEMO_PATIENTS` in `test_agents.py` and the seeded `Patient`/`Encounter` rows via `seed_data.py`) are the same 5 people (Ariane R., Latoyia W., Monica H., Traci W., Dick L.) and must stay in sync per the original convention; separately, `services/triage_service.py`'s `load_fhir_record()` reads those same patients' full FHIR data directly from `synthetic-ambient-fhir-25/` at request time — an independent read path, not seeded into the DB. GI-track: the `PATIENTS` array is duplicated verbatim between `demo.html` and `index.html` (7 patients: helen, miguel, harriet, robert, deshawn, yolanda, jordan_test) and mirrored again (with source-file mappings) in `gi_demo_patients.py`'s `GI_DEMO_PATIENTS` for the eval script — none of these 7 patients exist in the DB at all. `services/gi_live_service.py`/`gi_context.py` read that same GI track's clinical grounding directly from `synthetic-gi-data/` at request time.
- **The old triage stub only scores PATIENT/FAMILY lines, never AGENT lines** — preserved in the real Claude prompt too (`analyze_transcript_with_claude`'s system prompt explicitly instructs this), since it's what stops the agent's own questions ("any chest pain?") from being misread as findings.
- **Never call the Anthropic API from browser JS.** The key stays server-side via `config.Config.ANTHROPIC_API_KEY` — this is why the GI track's live analysis (`index.html` → `/api/gi/analyze` → `services/gi_live_service.py`) goes through the Flask backend rather than calling Claude directly from the browser.
- Agent 2 and Agent 3's real calls both use Claude **tool-use / structured output** (`tool_choice` forcing the tool) so responses come back as validated JSON, not parsed strings — same pattern to use wherever the remaining stub call sites (`pipeline_service.finalize_call()`, see the wiring gap above) get swapped over to the real implementations.
