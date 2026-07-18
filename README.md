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

## The backend (Flask API)

A small Flask + SQLAlchemy service now lives at the project root. It moves the
triage logic off the browser and behind an API, which is the prerequisite for
using real Claude: the Anthropic key must stay server-side, never in browser
JavaScript where anyone viewing the page source could read it.

**How it's designed.** The backend follows the standard Flask *application
factory* pattern, with three responsibilities cleanly separated:

- **Models (`models.py`)** — SQLAlchemy tables for the domain:
  `Patient → Encounter → CheckIn → TriageResult`. A patient has a discharge
  encounter (diagnosis, comorbidities, meds), each encounter has a check-in
  "call" (a speaker-labeled transcript), and each check-in produces one triage
  result (severity, label, rationale, flags, and the drafted note). Transcripts
  and list fields are stored as JSON columns so the shapes match what the
  frontend already consumes.
- **Services (`services/triage_service.py`)** — the business logic. This is the
  server-side home of the two "agents" the browser currently stubs:
  `analyze_transcript()` (Agent 2, triage scoring) and `draft_note()` (Agent 3,
  note drafting). The scoring still uses the same keyword rules as the mockup —
  including the rule that **only PATIENT/FAMILY lines are scored, never the
  agent's own questions** — but it now sits behind a clean function boundary.
  `analyze_transcript_with_claude()` is the marked seam where the real
  Anthropic call drops in, returning the identical JSON shape so nothing
  upstream changes.
- **Routes (`routes/tool.py`)** — the HTTP endpoints the frontend (or an agent)
  calls as tools. These return the same JSON the browser stubs produce today,
  so the frontend can swap its local `analyzeTranscript()`/`draftNote()` calls
  for `fetch()` calls with almost no other change.

Configuration (`config.py`) is environment-driven and loads secrets from
`.env` via `python-dotenv`; `extensions.py` holds the shared SQLAlchemy
instance so models and the app factory can import it without a circular
dependency. `seed_data.py` populates the database with the same 5 demo patients
described above, running the triage + note logic at seed time so the queue is
precomputed.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | lists the available endpoints |
| GET | `/health` | liveness check |
| GET | `/api/patients` | care-team queue, ranked 🔴 → 🟠 → 🟡 → 🟢 |
| GET | `/api/patients/<slug>` | one patient + encounter + transcript + triage result |
| POST | `/api/triage` | score a transcript → `{ severity, label, rationale, flags }` |
| POST | `/api/draft-note` | draft a chart note from context + disposition |

### Running the backend

From the project root (no `cd` needed):

```
pip install -r requirements.txt   # Flask, Flask-SQLAlchemy, python-dotenv, etc.
python seed_data.py               # creates + populates triage.db with the 5 demo patients
python app.py                     # serves on http://127.0.0.1:5001
```

Then open `http://127.0.0.1:5001/api/patients` to see the ranked queue as JSON.
Run the test suite with `pytest`.

The Anthropic key lives in `.env` (gitignored) and is read by `config.py` — the
frontend never sees it.

## Making it real with Claude

To swap the stub for a real Claude call:

1. **Implement `analyze_transcript_with_claude()`** in
   `services/triage_service.py` against the Anthropic Messages API, using the
   prompts in `PROMPTS.md` and tool-use / structured output. Keep the return
   shape identical to `analyze_transcript()` so the routes, models, and
   frontend are unaffected.
2. **Point the frontend at the API.** Replace the browser's local
   `analyzeTranscript()`/`draftNote()` calls with `fetch()` calls to
   `POST /api/triage` and `POST /api/draft-note`.

This is where Claude earns its keep over keyword matching: understanding
negation ("no chest pain" vs. chest pain), reading severity language in
context, and reasoning about a patient's specific comorbidities rather than
matching fixed phrases.

## Folder structure

```
post-discharge-triage-agent/
├── index.html            # the frontend mockup — open directly in a browser
├── app.py                # Flask app factory, DB setup, root/health routes
├── config.py             # env-driven config (loads .env; holds ANTHROPIC_API_KEY server-side)
├── extensions.py         # shared SQLAlchemy instance (avoids circular imports)
├── models.py             # SQLAlchemy models: Patient / Encounter / CheckIn / TriageResult
├── routes/
│   ├── __init__.py
│   └── tool.py           # the agent-callable API endpoints (/api/*)
├── services/
│   ├── __init__.py
│   └── triage_service.py # triage scoring + note drafting (real-Claude seam here)
├── tests/
│   ├── __init__.py
│   └── test_app.py       # pytest suite for services + endpoints
├── seed_data.py          # populates the DB with the 5 FHIR-grounded demo patients
├── test_agents.py        # standalone smoke test hitting the real Anthropic API
├── PROMPTS.md            # canonical prompts + tool schemas for the 3 agents
├── DESIGN.md             # full narrative design doc
├── requirements.txt
├── .env.example          # copy to .env and add your ANTHROPIC_API_KEY
├── .gitignore            # excludes .env, venv/, __pycache__/, *.db, etc.
└── README.md             # this file
```

## Data note

Patients (Ariane R., Latoyia W., Monica H., Traci W., Dick L.) are drawn from
Abridge's fully synthetic `synthetic-ambient-fhir-25` hackathon dataset — no
real patient data anywhere in this project. Conditions and medications shown
per patient come from that dataset's actual FHIR resources; the check-in
transcripts are hand-written for this demo (see above).

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
