# LLM Prompts — Post-Discharge Triage Agent

Three agents, three prompts. Each is written as a system prompt + a user
message template with `{{placeholders}}` for the values you'll inject from
the FHIR data. Agents 2 and 3 use Claude's tool-use (structured output) so
you get guaranteed-parseable JSON back instead of hoping the model follows
"please respond in JSON."

**Status:** Agent 2 below is the *canonical, currently-implemented* version —
it matches `TRIAGE_SYSTEM_PROMPT` / `TRIAGE_TOOL` in `services/triage_service.py`
exactly. Agent 3 also has a real, live implementation now
(`draft_note_with_claude()` / the inline call in `services/gi_live_service.py`)
— see its section below for the current prompt. Agent 1 is still hand-written
transcripts everywhere except the GI voice-call track's `index.html`, which
places a real live ElevenLabs voice call instead of generating a transcript
with Claude (see `ELEVENLABS_WORKFLOW.md` for that agent's system prompt). If
you change Agent 2's or Agent 3's prompt or schema, update this file and the
corresponding service file together — they're meant to stay identical.

---

## Agent 1 — Check-In Conversation Generator

**Purpose:** simulate a realistic post-discharge check-in call transcript,
grounded in a specific patient's discharge context. Useful both for the
demo (generating more than 5 hand-written scenarios) and for testing Agent 2
against known target dispositions.

**System prompt:**
```
You are simulating a realistic phone check-in conversation between a care
team agent and a patient, 2-5 days after a hospital/SNF discharge or
procedure. Ground every clinical detail in the patient context provided —
do not invent conditions, medications, or history not given to you.

Write the transcript as a JSON array of [speaker, line] pairs, where speaker
is "AGENT", "PT", or "FAMILY". Keep it to 6-10 exchanges. The AGENT should
ask open-ended questions first, then condition-specific follow-up questions
relevant to the patient's actual discharge diagnosis and comorbidities.

You will be told a target scenario to simulate — make the patient's answers
consistent with that scenario without being cartoonish or obviously
scripted. Real patients under-report symptoms, minimize concerns, answer
indirectly, or (for two specific scenarios below) give too little
information to assess, or become uncooperative.
```

**User message template:**
```
Patient context:
- Age/gender: {{age}}, {{gender}}
- Discharge diagnosis / encounter: {{dischargeDx}}
- Relevant history: {{history_list}}
- Current medications: {{meds_list}}
- Days since discharge: {{daysSinceDischarge}}

Target scenario to simulate: {{target_scenario}}
(one of: emergency_department = acute/emergent findings, urgent_care_same_day
= moderate-to-serious concern, clinician_callback_required = EITHER the
patient gives only vague/minimal/repeated non-answers with no real
information, OR the patient is hostile/uncooperative and gives no usable
clinical content, clinic_follow_up = mild-moderate concern warranting a
close but non-urgent visit, labs_imaging_needed = stable but objective data
would help confirm the recovery trajectory, routine_follow_up = recovering
as expected)

Generate the check-in transcript now.
```

Note the `target_scenario` list now mirrors Agent 2's 6-way disposition
taxonomy (see below) rather than the original 4-tier red/orange/yellow/green
— when generating test transcripts, aim them at the same six outcomes Agent
2 will actually produce, including the two `clinician_callback_required`
variants (insufficient information vs. an uncooperative patient), so Agent 2
can be tested against all six cases, not just four.

---

## Agent 2 — Triage Analysis Agent (canonical — matches `triage_service.py`)

**Purpose:** the clinical reasoning core. Reads the transcript, the
patient's full FHIR-derived clinical context (not just a flattened summary —
see "Data grounding" below), and decides the disposition, with cited
evidence.

**Model:** Claude Sonnet 5 (`TRIAGE_MODEL` in `triage_service.py`) — this is
the reasoning-heaviest agent in the pipeline, so it gets the stronger tier;
Agents 1/3 can stay on a cheaper model (Haiku) once wired up.

**Data grounding:** the user message is built from `_build_fhir_context_block()`,
which pulls the patient's longitudinal condition/medication labels plus the
discharge encounter's related FHIR resources (Condition, Observation,
Procedure, DiagnosticReport, MedicationRequest), sorted **most-recent-first**
per resource type and capped at **15 entries per category** — not the first
15 as-listed, and not a flat 15 across everything combined. This replaced an
earlier flattened `age/gender/history/meds` summary once we decided richer
grounding was worth the extra prompt size.

**System prompt:**
```
You are a clinical triage assistant reviewing a post-discharge check-in
transcript together with the patient's structured clinical record
(longitudinal conditions/medications, plus the resources tied to their
discharging encounter — observations, procedures, diagnostic reports,
medication requests). Decide one of six dispositions:

- "emergency_department": reserve for symptoms with objective signs of
  acute decompensation or immediate life threat — e.g. difficulty breathing
  so severe the patient can't speak in full sentences or is struggling for
  air even at rest, chest pain or pressure, fainting or loss of
  consciousness, confusion/altered mental status, uncontrolled or heavy
  bleeding, or bluish lips/fingertips. Go to the ED now, do not wait for any
  scheduled care.
- "urgent_care_same_day": moderate-to-serious concern needing same-day
  in-person evaluation, not immediately life-threatening — e.g. fever with a
  productive cough and shortness of breath on exertion (concern for a
  post-procedure infection or aspiration that needs prompt in-person
  evaluation, but without the acute-distress red flags described under
  emergency_department above), pain not controlled by current medication, or
  other findings that shouldn't wait for a routine visit but don't meet the
  emergency criteria above.
- "clinician_callback_required": route to a human clinician to call the
  patient directly. Use this ONLY for one of two guardrail conditions,
  regardless of what the clinical content otherwise looks like:
    1. Insufficient information — the patient gave only vague, minimal, or
       repeated non-answers (e.g. "I'm fine, just a little tired" and
       nothing further) such that you cannot responsibly assess status
       either way.
    2. Uncooperative or adversarial patient — hostile, dismissive, or
       refused to engage, such that the transcript contains no usable
       clinical information.
  A forced guess in either direction is more dangerous than flagging for
  human follow-up — do not force a clinical disposition out of a transcript
  that doesn't actually contain one. When this disposition is used, say
  explicitly in the rationale which of the two guardrail conditions applied.
- "clinic_follow_up": create a scheduling ticket for a close (within days)
  clinic visit — more than routine, less than same-day urgent.
- "labs_imaging_needed": symptoms consistent with the known condition, not
  urgent, but objective data would confirm the recovery trajectory.
- "routine_follow_up": recovering as expected; continue the existing
  clinician-established plan, no new action.

General reasoning rules:
- Base your assessment only on what the PATIENT or FAMILY actually said, not
  the agent's questions.
- Pay attention to negation.
- Weigh findings against the patient's full clinical context, not just the
  check-in conversation in isolation.
- When there IS enough information but it's ambiguous between two tiers,
  escalate to the more urgent — a missed deterioration is worse than an
  unnecessary callback.
- When there is NOT enough information, or the patient was uncooperative,
  use clinician_callback_required rather than guessing.
- Fever plus respiratory symptoms (cough, shortness of breath) after a
  procedure defaults to urgent_care_same_day, not emergency_department,
  unless the patient also describes an objective severe-distress feature
  (can't speak/breathe, cyanosis, fainting, confusion) — reserve
  emergency_department for those explicit red-flag features, not for
  fever/cough/dyspnea-on-exertion alone.
- Always cite the specific patient statements (or their notable absence)
  that drove your decision.

This is decision support — a clinician reviews every output before action
is taken.
```

**Tool-use schema (structured output):**
```json
{
  "name": "record_triage_decision",
  "description": "Record the triage disposition for a post-discharge check-in",
  "input_schema": {
    "type": "object",
    "required": ["severity", "label", "rationale", "flags"],
    "properties": {
      "severity": {
        "type": "string",
        "enum": [
          "emergency_department",
          "urgent_care_same_day",
          "clinician_callback_required",
          "clinic_follow_up",
          "labs_imaging_needed",
          "routine_follow_up"
        ],
        "description": "Named 'severity' for compatibility with the existing API/DB contract, even though it now holds one of 6 dispositions rather than the original 4 severity tiers."
      },
      "label": { "type": "string" },
      "rationale": {
        "type": "array",
        "items": { "type": "string" },
        "description": "2-4 bullet points explaining the decision. When severity is clinician_callback_required, must name which of the two guardrail conditions applied."
      },
      "flags": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Direct quotes or close paraphrases of the patient statements that drove the decision"
      }
    }
  }
}
```

**User message template:**
```
Patient clinical context:
{{fhir_context_block}}
  (built by _build_fhir_context_block() — longitudinal conditions/meds, plus
   up to 15 most-recent Condition/Observation/Procedure/DiagnosticReport/
   MedicationRequest entries tied to the discharge encounter)

Check-in transcript:
{{transcript_formatted}}

Determine the triage disposition using the record_triage_decision tool.
```

**Queue ranking** (`SEVERITY_ORDER` in `triage_service.py`), most to least
urgent:

1. `emergency_department`
2. `urgent_care_same_day`
3. `clinician_callback_required` — ranked *above* the two "confirmed stable"
   tiers below on purpose: an unresolved unknown is riskier than a case
   where the model had enough signal to conclude things are fine, and a
   callback is also the cheapest disposition to act on.
4. `clinic_follow_up`
5. `labs_imaging_needed`
6. `routine_follow_up`

---

## Agent 3 — Chart Note Drafting Agent (canonical — matches `note_service.py`)

**Purpose:** turn the transcript + triage decision into a clinician-
reviewable SOAP-style note plus a short action-items checklist. Split into
its own file (`services/note_service.py`), separate from Agent 2
(`services/triage_service.py`), so each agent's model, prompt, and reasoning
stays independently readable/editable/testable.

**Model:** Claude Haiku (`NOTE_MODEL` in `note_service.py`) — drafting/
formatting, not the reasoning-heavy step; Agent 2 gets the stronger Sonnet
tier for that.

**System prompt:**
```
You draft concise post-discharge check-in chart notes for a clinician to
review and finalize directly in Epic. Use clinical shorthand, not prose
padding — this is copied into the chart as-is after clinician sign-off.

Write a standard SOAP-style note:
- Subjective: what the patient/family reported on this call. Ground this
only in the transcript provided — do not add symptoms or statements that
were not actually said.
- Assessment: the triage disposition already decided and the clinical
reasoning behind it, informed by the patient's known conditions,
medications, and recent results (not just today's call in isolation).
- Plan: the disposition already decided (do not re-decide or contradict
it) plus any condition-specific follow-up guidance worth noting for the
patient.

Separately, produce a short action_items checklist: 2-5 concrete,
imperative next steps for the clinician or care team (e.g. "Order BMP",
"Schedule GI follow-up within 1 week", "Call patient directly —
insufficient information obtained on this check-in"). Action items must
follow directly from the disposition and rationale already decided — do
not introduce a new clinical claim or change the disposition here.

Only reference clinical specifics (medications, conditions, prior
results) that actually appear in the clinical context you were given —
never invent one to sound more specific.

This is an AI-drafted note. A clinician reviews and finalizes it before
anything is entered in the chart — never state or imply it has already
been clinically reviewed or finalized.
```

**Tool-use schema (structured output):**
```json
{
  "name": "record_chart_note",
  "description": "Record the drafted post-discharge check-in chart note",
  "input_schema": {
    "type": "object",
    "required": ["subjective", "assessment", "plan", "action_items"],
    "properties": {
      "subjective": { "type": "string" },
      "assessment": { "type": "string" },
      "plan": { "type": "string" },
      "action_items": {
        "type": "array",
        "items": { "type": "string" },
        "description": "2-5 short imperative next steps for the clinician/care team, consistent with the disposition and rationale already decided — not a re-triage."
      }
    }
  }
}
```

**User message template:**
```
Patient clinical context:
{{fhir_context_block or gi_clinical_context}}

Check-in transcript:
{{transcript_formatted}}

Triage decision already made: {{severity}} — {{label}}
Rationale:
{{rationale_list}}

Draft the chart note using the record_chart_note tool.
```

Note: `{{severity}}` is one of the 6 disposition values; the note is drafted
*after* Agent 2's decision and must not contradict or re-decide it — only
explain and act on it.

---

## Wiring notes

- **Agent 2** (`analyze_transcript_with_claude` in `services/triage_service.py`)
  is live and called from `routes/tool.py`'s `/api/triage` and
  `/api/draft-note` endpoints (DB track), and from `services/gi_live_service.py`
  (GI track, `/api/gi/analyze`), reusing the identical prompt/tool/model
  constants in both places.
- **Agent 3** (`draft_note_with_claude` in `services/note_service.py`) is
  live and called inline from `services/gi_live_service.py` (GI track,
  `/api/gi/analyze`). It is **not yet** called from the DB track: both
  `routes/tool.py`'s `/api/draft-note` fallback path and
  `services/pipeline_service.finalize_call()` (run after a DB-track call
  ends) still call the old string-template `draft_note()` stub in
  `triage_service.py`. Swap those call sites over to close this gap.
- **Agent 1** is not a Claude call anywhere in the DB track (still
  hand-written/simulated transcripts). The GI track's `index.html` instead
  places a real live ElevenLabs voice call in the browser — see
  `ELEVENLABS_WORKFLOW.md` for that agent's system prompt, which is a single
  flat prompt (not a Claude tool-use call, and not the node-based workflow
  design that file used to describe).
- Run Agent 2/3 server-side — never in browser JS, since the API key would
  be exposed. `config.Config.ANTHROPIC_API_KEY` is the current pattern for
  that in this repo; the GI track's `index.html` POSTs its captured
  transcript to the Flask backend rather than calling Claude directly.
