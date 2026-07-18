# LLM Prompts — Post-Discharge Triage Agent

Three agents, three prompts. Each is written as a system prompt + a user
message template with `{{placeholders}}` for the values you'll inject from
the FHIR data. Agents 2 and 3 use Claude's tool-use (structured output) so
you get guaranteed-parseable JSON back instead of hoping the model follows
"please respond in JSON."

**Status:** Agent 2 below is the *canonical, currently-implemented* version —
it matches `TRIAGE_SYSTEM_PROMPT` / `TRIAGE_TOOL` in `services/triage_service.py`
exactly. Agents 1 and 3 below are still the original design, not yet ported
into the Flask backend (Agent 1 remains hand-written transcripts; Agent 3
remains the string-template stub in `services/triage_service.py`'s
`draft_note()`). If you change Agent 2's prompt or schema, update both this
file and `triage_service.py` — they're meant to stay identical.

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

- "emergency_department": acute/emergent findings — go to the ED now, do
  not wait for any scheduled care.
- "urgent_care_same_day": moderate-to-serious concern needing same-day
  in-person evaluation, not immediately life-threatening.
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

## Agent 3 — Chart Note Drafting Agent

**Purpose:** turn the transcript + triage decision into a clinician-
reviewable note for the chart. Not yet ported to a real Claude call — see
`draft_note()` in `triage_service.py` for the current string-template stub.

**System prompt:**
```
You draft concise post-discharge check-in notes for clinician review, in
Subjective / Assessment / Plan format. Write in clinical shorthand, not
prose padding. Do not add clinical claims beyond what's in the transcript
and triage decision provided.

Always include a closing line noting this note was AI-drafted from a
check-in call and requires clinician review before being finalized in the
chart — do not omit this regardless of how confident the input data seems.
```

**Tool-use schema:**
```json
{
  "name": "record_chart_note",
  "description": "Record the drafted post-discharge check-in note",
  "input_schema": {
    "type": "object",
    "required": ["subjective", "assessment", "plan"],
    "properties": {
      "subjective": { "type": "string" },
      "assessment": { "type": "string" },
      "plan": { "type": "string" }
    }
  }
}
```

**User message template:**
```
Patient: {{name}}, {{age}}{{gender_initial}}, day {{daysSinceDischarge}} post-discharge
Discharge context: {{dischargeDx}}

Check-in transcript:
{{transcript_formatted}}

Triage decision: {{severity}} — {{label}}
Rationale: {{rationale_list}}

Draft the chart note using the record_chart_note tool.
```

Note: `{{severity}}` here will now be one of the 6 new values, not the
original 4 — the template itself needs no change since it just echoes
whatever the triage step produced.

---

## Wiring notes

- Agent 2 (`analyze_transcript_with_claude` in `services/triage_service.py`)
  is live and called from `routes/tool.py`'s `/api/triage` and
  `/api/draft-note` endpoints.
- Agents 1 and 3 are not yet wired to real Claude calls — their prompts
  above are the design to implement when that work happens.
- Run these server-side — never in browser JS, since the API key would be
  exposed. `config.Config.ANTHROPIC_API_KEY` is the current pattern for
  that in this repo.
