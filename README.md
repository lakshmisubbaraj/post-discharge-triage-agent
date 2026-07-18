# Post-Discharge Triage Agent — Hackathon Mockup

**The problem:** Post-hospitalization/post-procedure readmissions cost health
systems billions a year and are penalized directly under CMS's Hospital
Readmissions Reduction Program. Most of the early warning signs — worsening
symptoms, medication confusion, uncontrolled pain — surface in the days right
after discharge, in a phone check-in that may or may not happen, get
documented inconsistently, or reach the right clinician in time.

**The idea:** An agent places (or receives) a structured check-in call a few
days after discharge, grounded in that specific patient's actual diagnosis,
comorbidities, and medications. It scores the conversation against
condition-specific red flags and routes the patient to one of four outcomes:
**urgent care referral**, **same-day physician callback**, **labs/imaging
recommended**, or **routine follow-up** — with a drafted chart note and a
rationale a clinician can quickly verify.

This is a natural extension of what Abridge already does (ambient
conversation → structured clinical output), just pointed at a post-visit
check-in instead of the exam room.

## What's in this mockup right now

`index.html` is a **single self-contained file** — open it directly in any
browser, no server or install needed. It's built around 5 real patients
pulled from the hackathon's `synthetic-ambient-fhir-25` dataset (their actual
ages, conditions, and medications come from the FHIR data), each showing a
different one of the four triage outcomes.

**Everything about the "intelligence" is currently stubbed, not real Claude
calls:**

- **Call transcripts** are hand-written text standing in for what would, in
  production, be a real telephony transcript or an LLM-simulated check-in
  conversation.
- **Triage scoring** (`analyzeTranscript()` in the `<script>` block) is a
  plain keyword matcher, not an LLM. It's deliberately simple and has known
  limitations (e.g. it can't fully reason about clinical nuance the way
  Claude would) — see the comments in the code for exactly where this gets
  replaced.
- **Chart note drafting** (`draftNote()`) is a string template, not a
  generated note.

Every stubbed section is labeled in the UI itself (yellow "stubbed" tags) so
it's obvious to anyone reviewing the demo what's real data vs. placeholder
logic.

## Wiring in the real Claude API

Two things need to happen to make this real, both intentionally **not**
included yet:

1. **A small backend.** Never call the Anthropic API directly from browser
   JavaScript — your API key would be visible to anyone who views the page
   source. Add a minimal Flask/FastAPI (Python) or Express (Node) server with
   one or two endpoints, e.g.:
   - `POST /api/triage` — takes a transcript + patient FHIR context, calls
     Claude with a structured prompt, returns `{ severity, label, rationale,
     flags }` (same shape `analyzeTranscript()` returns today, so the
     frontend barely has to change).
   - `POST /api/draft-note` — takes the same inputs, returns a drafted note.

   Store the key in a `.env` file (already excluded via `.gitignore` below),
   load it with `python-dotenv`, and have the frontend `fetch()` your own
   backend instead of running `analyzeTranscript()`/`draftNote()` locally.

2. **A structured prompt** that gives Claude the transcript plus the
   patient's `patient_context` and `encounter_fhir` from the dataset, and
   asks for the same JSON shape currently hardcoded — this is where using
   Claude actually pays off over keyword matching: it can understand
   negation ("no chest pain" vs. chest pain), severity language in context,
   and reason about a patient's specific comorbidities rather than matching
   fixed phrases.

## Folder structure

```
post-discharge-triage-agent/
├── index.html      # the mockup — open directly in a browser
├── README.md        # this file
└── .gitignore        # excludes .env, venv/, node_modules/, etc. once you add a backend
```

## Data note

Patients (Ariane R., Latoyia W., Monica H., Traci W., Dick L.) are drawn from
Abridge's fully synthetic `synthetic-ambient-fhir-25` hackathon dataset — no
real patient data anywhere in this project. Conditions and medications shown
per patient come from that dataset's actual FHIR resources; the check-in
transcripts are hand-written for this demo (see above).
