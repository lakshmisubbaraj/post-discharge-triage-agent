# Post-Discharge Triage Agent — Hackathon Mockup

**The problem:** Post-hospitalization/post-procedure readmissions cost health
systems billions a year and are penalized directly under CMS's Hospital
Readmissions Reduction Program. Most of the early warning signs — worsening
symptoms, medication confusion, uncontrolled pain — surface in the days right
after discharge, in a phone check-in that may or may not happen, get
documented inconsistently, or reach the right clinician in time.

**The idea:** An agent places (or receives) a structured check-in call a few
days after discharge, grounded in that specific patient's actual diagnosis,
comorbidities, and medications. It reasons about the conversation and routes
the patient to one of six dispositions — **emergency department**, **urgent
care (same day)**, **clinician callback required** (a guardrail for when the
AI can't safely assess the patient at all), **clinic follow-up**, **labs/
imaging needed**, or **routine follow-up** — with a drafted chart note and a
rationale a clinician can quickly verify.

This is a natural extension of what Abridge already does (ambient
conversation → structured clinical output), just pointed at a post-visit
check-in instead of the exam room.

## What's in this repo right now

There are **two parallel demo tracks**, sharing the same underlying Agent 2
(triage) / Agent 3 (note) Claude prompts but with different patients, data
sources, and frontends:

### 1. The GI voice-call demo — `demo.html` / `index.html`

Two single self-contained HTML files, each built around the same **7
patients** grounded in a synthetic GI-procedure dataset
(`synthetic-gi-data/`) — one patient per disposition tier, plus a guardrail
test case (`jordan_test`).

- **`demo.html`** — no voice, no server needed. Open it directly:
  ```
  open demo.html
  ```
  Each patient shows a **pre-baked reference analysis**: real Claude Sonnet
  5 (triage) and Claude Haiku (note) output, generated offline by
  `test_gi_agents.py` against `synthetic-gi-data/` and baked into the
  `PATIENTS` array — not a live call, but not a keyword stub either.
- **`index.html`** — the same UI, plus a **real live voice call** placed in
  the browser via the ElevenLabs `@elevenlabs/client` JS SDK. After the call
  ends, the actual captured transcript is POSTed to the Flask backend's
  `POST /api/gi/analyze`, which re-runs the same Agent 2/3 Claude prompts
  against what was actually said. If the backend isn't running (or the
  ANTHROPIC_API_KEY isn't set), it falls back to that patient's pre-baked
  reference analysis and labels it clearly in the UI so it's never
  ambiguous which one you're looking at.

### 2. The DB-backed demo — Flask + SQLAlchemy backend

5 real patients pulled from the hackathon's `synthetic-ambient-fhir-25`
dataset (Ariane R., Latoyia W., Monica H., Traci W., Dick L.; their actual
ages, conditions, and medications come from the FHIR data), persisted
through a small Flask API (`app.py` + `routes/` + `services/` + `models.py`)
and callable via ElevenLabs (a real Twilio-dialed voice call) or a simulated
canned-transcript call for demoing without credentials. See "The backend"
below.

**Status of the "intelligence" across both tracks:**

- **Agent 2 (triage)** is real end-to-end in both tracks: Claude Sonnet 5,
  tool-use/structured output, reasoning over the transcript plus the
  patient's fuller FHIR-grounded clinical context. `analyze_transcript()`
  (the original keyword matcher) is kept only as a fallback/reference.
- **Agent 3 (note drafting)** is real (Claude Haiku) in the GI track's live
  path, but the DB track's call-completion pipeline
  (`services/pipeline_service.py`) still calls the old string-template stub
  — a known wiring gap, see `CLAUDE.md`.
- **Agent 1 (the check-in conversation itself)** is a real live ElevenLabs
  voice call in `index.html`; in the DB track it's either a real ElevenLabs/
  Twilio phone call or a simulated canned transcript, depending on whether
  ElevenLabs credentials are configured.

## The backend (Flask API)

A small Flask + SQLAlchemy service lives at the project root and serves
**both** demo tracks. It keeps the Anthropic key server-side, never in
browser JavaScript where anyone viewing the page source could read it.

**How it's designed.** The backend follows the standard Flask *application
factory* pattern (`app.py`), with responsibilities cleanly separated:

- **Models (`models.py`)** — SQLAlchemy tables for the DB-track domain:
  `Patient → Encounter → CheckIn → TriageResult`. A patient has a discharge
  encounter (diagnosis, comorbidities, meds), each encounter has a check-in
  "call" (a speaker-labeled transcript), and each check-in produces one triage
  result (severity, label, rationale, flags, and the drafted note). Transcripts
  and list fields are stored as JSON columns so the shapes match what the
  frontend already consumes. The GI track's 7 patients are **not** in these
  tables at all — see below.
- **Services (`services/`)** — one file per responsibility:
  - `triage_service.py` — Agent 2. `analyze_transcript_with_claude()` is the
    real Claude Sonnet 5 tool-use call (FHIR-grounded, 6-way disposition);
    `analyze_transcript()` is the original keyword matcher, kept only as a
    fallback/reference. Scoring rules (real or stub) only read PATIENT/FAMILY
    transcript lines, never the agent's own questions.
  - `note_service.py` — Agent 3. `draft_note_with_claude()` is the real Claude
    Haiku tool-use call drafting a SOAP note + action-items checklist;
    `draft_note()` is the original string-template stub.
  - `fhir_context.py` — shared FHIR-parsing/formatting code used by both
    `triage_service.py` and `note_service.py` (loading a patient's record,
    building the context block Claude sees, formatting a transcript).
  - `call_service.py` — Agent 1's real-world mechanics for the **DB track**:
    dials a real ElevenLabs/Twilio call, or streams a simulated canned
    transcript if credentials aren't configured.
  - `pipeline_service.py` — orchestrates Agent 2 → Agent 3 once a DB-track
    call finishes (currently still calling the old stubs for both — see
    `CLAUDE.md`'s wiring-gap note).
  - `gi_live_service.py` — the **GI track**'s live-analysis entry point.
    Reuses the exact same `TRIAGE_MODEL`/`TRIAGE_SYSTEM_PROMPT`/`TRIAGE_TOOL`
    and `NOTE_MODEL`/`NOTE_SYSTEM_PROMPT`/`NOTE_TOOL` constants from the two
    files above (zero prompt drift), but sources its clinical context from
    `gi_context.py` reading `synthetic-gi-data/` instead of the DB.
- **Routes (`routes/tool.py`)** — the HTTP endpoints both frontends call.

Configuration (`config.py`) is environment-driven and loads secrets from
`.env` via `python-dotenv`; `extensions.py` holds the shared SQLAlchemy
instance so models and the app factory can import it without a circular
dependency. `seed_data.py` populates the database with the 5 DB-track demo
patients, running the triage + note logic at seed time so the queue is
precomputed. The GI track's 7 patients have no equivalent seed step — their
pre-baked reference data is generated by `test_gi_agents.py` and pasted
directly into `demo.html`/`index.html`'s `PATIENTS` array (see `gi_eval_results.json`, gitignored).

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | lists the available endpoints |
| GET | `/health` | liveness check |
| GET | `/api/config` | public frontend config: ElevenLabs agent id, which call modes are available |
| GET | `/api/patients` | DB track: care-team queue, ranked by disposition urgency |
| GET | `/api/patients/<slug>` | DB track: one patient + encounter + transcript + triage result |
| POST | `/api/triage` | DB track: score a transcript → `{ severity, label, rationale, flags }` |
| POST | `/api/draft-note` | DB track: draft a chart note from context + disposition |
| POST | `/api/patients/<slug>/call` | DB track: start a voice check-in call (real ElevenLabs/Twilio if configured, else simulated) |
| GET | `/api/patients/<slug>/call` | DB track: poll the latest call's live transcript/status |
| POST | `/api/patients/<slug>/call/widget` | DB track: start a browser-widget voice check-in |
| POST | `/api/patients/<slug>/call/widget/finish` | DB track: finalize a browser-widget call and run triage |
| POST | `/api/gi/analyze` | **GI track**: run Agent 2 + Agent 3 over a just-captured live voice transcript for one of `index.html`'s 7 GI patients |

### Running the backend

From the project root (no `cd` needed):

```
pip install -r requirements.txt   # Flask, Flask-SQLAlchemy, python-dotenv, etc.
python seed_data.py               # creates + populates triage.db with the 5 DB-track demo patients
python app.py                     # serves on http://127.0.0.1:5001
```

Then open `http://127.0.0.1:5001/api/patients` to see the ranked DB-track
queue as JSON, or open `index.html` in a browser to try the GI track's live
voice call end-to-end (it points at `http://127.0.0.1:5001` by default — see
the `API_BASE` constant near the top of its `<script>` block). Run the test
suite with `pytest`.

The Anthropic key lives in `.env` (gitignored, see `.env.example`) and is
read by `config.py` — no frontend ever sees it. ElevenLabs credentials
(`ELEVENLABS_API_KEY`/`_AGENT_ID`/`_PHONE_NUMBER_ID`) are optional for the
DB track (falls back to a simulated call without them); the GI track's
`index.html` hardcodes its own `ELEVENLABS_AGENT_ID` JS constant instead of
reading it from the backend, currently pointed at the same agent as `.env`.

## Remaining gaps to close

- **`services/pipeline_service.finalize_call()`** (runs after a DB-track
  call ends) still calls the old stub `analyze_transcript()` / `draft_note()`
  instead of the real Claude versions — even though hitting
  `POST /api/triage` / `POST /api/draft-note` directly already uses real
  Claude. Swap those two calls over to close the gap.
- The GI track's pre-baked reference data (baked into `demo.html`/
  `index.html`'s `PATIENTS` array) can silently drift from the live prompts
  if `TRIAGE_SYSTEM_PROMPT`/`NOTE_SYSTEM_PROMPT` change without re-running
  `test_gi_agents.py` and re-baking — this already happened once (see
  `CLAUDE.md`).

This is where Claude earns its keep over keyword matching: understanding
negation ("no chest pain" vs. chest pain), reading severity language in
context, and reasoning about a patient's specific comorbidities rather than
matching fixed phrases.

## Folder structure

```
post-discharge-triage-agent/
├── demo.html                    # GI-track frontend, no voice — open directly in a browser
├── index.html                  # GI-track frontend + real live ElevenLabs voice call
├── app.py                       # Flask app factory, DB setup, root/health routes
├── config.py                    # env-driven config (loads .env; holds ANTHROPIC_API_KEY server-side)
├── extensions.py                # shared SQLAlchemy instance (avoids circular imports)
├── models.py                    # SQLAlchemy models: Patient / Encounter / CheckIn / TriageResult (DB track)
├── routes/
│   ├── __init__.py
│   └── tool.py                  # all agent-callable API endpoints (/api/*), both tracks
├── services/
│   ├── __init__.py
│   ├── triage_service.py        # Agent 2 (triage) — real Claude Sonnet 5 call + old keyword stub
│   ├── note_service.py          # Agent 3 (note drafting) — real Claude Haiku call + old string-template stub
│   ├── fhir_context.py          # shared FHIR loading/formatting used by triage_service + note_service
│   ├── call_service.py          # Agent 1 mechanics for the DB track (real ElevenLabs/Twilio call, or simulated)
│   ├── pipeline_service.py      # orchestrates Agent 2 -> Agent 3 after a DB-track call ends
│   └── gi_live_service.py       # GI track's live-analysis entry point (/api/gi/analyze)
├── gi_context.py                 # clinical context loader for synthetic-gi-data/ (GI track)
├── gi_demo_patients.py            # the 7 GI-track patients' demographics/transcripts + source-file mapping
├── synthetic-gi-data/             # GI-track's synthetic dataset (patients/, procedure-results/)
├── tests/
│   ├── __init__.py
│   └── test_app.py              # pytest suite for services + endpoints
├── seed_data.py                  # populates the DB with the 5 FHIR-grounded DB-track demo patients
├── test_agents.py                # standalone smoke test hitting the real Anthropic API (DB-track patients)
├── test_gi_agents.py             # eval script for the 7 GI-track patients against synthetic-gi-data
├── PROMPTS.md                    # canonical prompts + tool schemas for all 3 agents
├── DESIGN.md                     # full narrative design doc
├── ELEVENLABS_WORKFLOW.md        # the live ElevenLabs agent's system prompt (shared by both tracks' voice calls)
├── requirements.txt
├── .env.example                  # copy to .env and add your ANTHROPIC_API_KEY / ElevenLabs credentials
├── .gitignore                    # excludes .env, venv/, __pycache__/, *.db, gi_eval_results.json, etc.
└── README.md                     # this file
```

## Data note

**DB track**: patients (Ariane R., Latoyia W., Monica H., Traci W., Dick L.)
are drawn from Abridge's fully synthetic `synthetic-ambient-fhir-25`
hackathon dataset. Conditions and medications shown per patient come from
that dataset's actual FHIR resources; the check-in transcripts are
hand-written or captured from a real/simulated voice call (see above).

**GI track**: patients (Helen R., Miguel F., Harriet B., Robert N., Deshawn
W., Yolanda R., plus the guardrail test case Jordan K.) are drawn from a
separate synthetic GI-procedure dataset, `synthetic-gi-data/` — one patient
per disposition tier. Their check-in transcripts are either hand-written
(`demo.html`, and `index.html`'s fallback) or a real live ElevenLabs voice
call (`index.html`).

No real patient data appears anywhere in this project.

## Merging the `hannah` branch into `main`

Work happens on the `hannah` branch; `main` is the shared/stable branch. To
bring your `hannah` changes into `main`:

```
git checkout hannah          # make sure your work is committed first
git status                    # should say "nothing to commit, working tree clean"

git checkout main             # switch to main
git pull origin main          # get the latest main from the remote first

git merge hannah              # merge your branch in
git push origin main          # publish the merged main
```

Pulling `main` before you merge matters — it means you resolve any conflicts
locally rather than having your push rejected.

### If you get merge conflicts

When Git can't auto-combine changes to the same lines, `git merge hannah`
stops and reports the conflicting files. To resolve them:

1. **See what's conflicted:** run `git status` — conflicted files are listed
   under "Unmerged paths".
2. **Open each file.** Git marks the clashing regions like this:

   ```
   <<<<<<< HEAD
   the version currently on main
   =======
   the version from your hannah branch
   >>>>>>> hannah
   ```

3. **Edit the file** to the final version you want, then **delete all three
   marker lines** (`<<<<<<<`, `=======`, `>>>>>>>`). Keep either side, combine
   them, or rewrite entirely — whatever is correct.
4. **Mark it resolved:** `git add <file>` for each file you fixed.
5. **Finish the merge:** `git commit` (Git pre-fills a merge message; just
   save), then `git push origin main`.

Useful escape hatches:

- **Bail out entirely:** `git merge --abort` returns you to the pre-merge
  state, as if you never ran the merge.
- **Take one whole side of a file:** `git checkout --theirs <file>` keeps the
  `hannah` version, `git checkout --ours <file>` keeps the `main` version —
  then `git add <file>`.
- **List still-conflicted files:** `git diff --name-only --diff-filter=U`.

> Tip: this repo has both `origin` and `upstream` remotes pointing at the same
> GitHub repo. Use `origin` for your normal push/pull unless you specifically
> mean to sync with `upstream`.
