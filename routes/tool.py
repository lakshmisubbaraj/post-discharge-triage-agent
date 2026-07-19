"""HTTP endpoints that expose the triage pipeline as callable "tools".

These are the tools the agent (or the frontend) invokes. They intentionally
return the same JSON shapes the browser stubs produce today, so the frontend
can swap its local analyzeTranscript()/draftNote() calls for fetch() calls
against these endpoints with almost no other change.

Endpoints
    GET  /api/patients                 -- care-team queue, ranked by disposition
    GET  /api/patients/<slug>          -- one patient + encounter + check-in + result
    POST /api/triage                   -- score a transcript -> disposition (Claude Sonnet 5)
    POST /api/draft-note               -- draft a chart note from a disposition
    POST /api/gi/analyze               -- live Agent 2 + Agent 3 for a GI voice check-in transcript

NOTE: /triage and /draft-note now call the real triage_service.analyze_transcript_with_claude()
(Sonnet 5, FHIR-grounded, 6-way disposition taxonomy) instead of the old
analyze_transcript() keyword stub. This changes /triage's request contract:
it now requires a "patient" object (with an "id"/slug) in addition to
"transcript", since the real agent looks up the patient's full FHIR record by
slug rather than scoring the transcript in isolation.

/api/gi/analyze is a separate, parallel path for the 7 GI demo patients in
index.html's standalone frontend (not backed by the Patient/Encounter DB
tables at all — those patients only exist as a JS array). It runs the same
Agent 2 / Agent 3 prompts against a live ElevenLabs call's captured
transcript instead of a canned one. See services/gi_live_service.py.
"""
from flask import Blueprint, jsonify, request

from extensions import db
from models import Patient
from services import call_service, gi_live_service, triage_service

bp = Blueprint("tool", __name__, url_prefix="/api")


def _latest_check_in(patient):
    encounter = patient.encounters[0] if patient.encounters else None
    if encounter and encounter.check_ins:
        return encounter.check_ins[-1]  # most recent call
    return None


def _serialize_patient(patient):
    """Flatten a Patient + its latest encounter/check-in/result into the
    single object shape the frontend expects."""
    encounter = patient.encounters[0] if patient.encounters else None
    check_in = _latest_check_in(patient)
    result = check_in.triage_result if check_in else None

    data = patient.to_dict()
    if encounter:
        enc = encounter.to_dict()
        enc.pop("id", None)  # don't clobber the patient's slug id
        data.update(enc)
        data["encounterId"] = encounter.id
    if check_in:
        data["transcript"] = check_in.transcript
        data["callStatus"] = check_in.status
        data["callMode"] = check_in.mode
    else:
        data["transcript"] = []
        data["callStatus"] = "not_called"
        data["callMode"] = None
    if result:
        data["triage"] = result.to_dict()
    return data


@bp.get("/patients")
def list_patients():
    """Return all patients ranked by disposition urgency (see SEVERITY_ORDER
    in services/triage_service.py for the 6-way ranking)."""
    patients = [_serialize_patient(p) for p in Patient.query.all()]
    ranked = triage_service.rank_by_severity(
        [p for p in patients if p.get("triage")],
        key=lambda p: p["triage"]["severity"],
    )
    # Patients without a triage result (edge case) go at the end.
    untriaged = [p for p in patients if not p.get("triage")]
    return jsonify(ranked + untriaged)


@bp.get("/patients/<slug>")
def get_patient(slug):
    patient = Patient.query.filter_by(slug=slug).first()
    if patient is None:
        return jsonify(error="patient not found"), 404
    return jsonify(_serialize_patient(patient))


@bp.get("/config")
def config():
    """Public front-end config: which call modes are available and the public
    ElevenLabs agent id for the browser voice widget."""
    from flask import current_app
    return jsonify(
        elevenlabsAgentId=current_app.config.get("ELEVENLABS_AGENT_ID"),
        widgetEnabled=call_service.widget_enabled(),
        phoneCallEnabled=call_service.elevenlabs_configured(),
    )


@bp.post("/patients/<slug>/call/widget")
def start_widget_call(slug):
    """Open a browser-widget voice check-in for this patient.

    The frontend then runs the ElevenLabs widget conversation client-side and
    calls the /finish endpoint when it ends.
    Returns: { checkInId, agentId }
    """
    patient = Patient.query.filter_by(slug=slug).first()
    if patient is None:
        return jsonify(error="patient not found"), 404
    if not patient.encounters:
        return jsonify(error="patient has no encounter on file"), 400

    existing = _latest_check_in(patient)
    if existing and existing.status == "in_progress":
        return jsonify(error="a call is already in progress for this patient"), 409

    from flask import current_app
    check_in = call_service.start_widget_call(patient)
    return jsonify(
        checkInId=check_in.id,
        agentId=current_app.config.get("ELEVENLABS_AGENT_ID"),
    ), 201


@bp.post("/patients/<slug>/call/widget/finish")
def finish_widget_call(slug):
    """Called when the widget conversation ends. Pulls the transcript and runs
    triage. May be polled: returns status='in_progress' while the transcript
    is still processing.
    Body (optional): { conversationId }
    Returns: { status, transcript, triage? }
    """
    patient = Patient.query.filter_by(slug=slug).first()
    if patient is None:
        return jsonify(error="patient not found"), 404

    check_in = _latest_check_in(patient)
    if check_in is None or check_in.mode not in ("widget", "widget_sim"):
        return jsonify(error="no widget call to finish"), 400

    payload = request.get_json(silent=True) or {}
    check_in = call_service.finalize_widget_call(
        check_in, conversation_id=payload.get("conversationId")
    )

    return jsonify(
        status=check_in.status,
        mode=check_in.mode,
        transcript=check_in.transcript or [],
        triage=check_in.triage_result.to_dict() if check_in.triage_result else None,
    )


@bp.post("/patients/<slug>/call")
def start_call(slug):
    """Tool: start a voice check-in call to the patient's phone.

    Uses ElevenLabs (real phone call) when credentials are configured,
    otherwise runs a simulated call that streams a canned transcript.
    Returns: { checkInId, status, mode }
    """
    patient = Patient.query.filter_by(slug=slug).first()
    if patient is None:
        return jsonify(error="patient not found"), 404
    if not patient.encounters:
        return jsonify(error="patient has no encounter on file"), 400

    live = call_service.elevenlabs_configured()
    if live and not patient.phone:
        return jsonify(error="patient has no phone number on file"), 400

    existing = _latest_check_in(patient)
    if existing and existing.status == "in_progress":
        return jsonify(error="a call is already in progress for this patient"), 409

    try:
        check_in = call_service.start_call(patient)
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 502

    return jsonify(
        checkInId=check_in.id, status=check_in.status, mode=check_in.mode
    ), 201


@bp.get("/patients/<slug>/call")
def call_status(slug):
    """Poll the latest call for a patient: live transcript + status.

    The frontend hits this every couple of seconds during a call. For live
    ElevenLabs calls this also pulls the newest transcript from their API.
    Returns: { status, mode, transcript, triage? }
    """
    patient = Patient.query.filter_by(slug=slug).first()
    if patient is None:
        return jsonify(error="patient not found"), 404

    check_in = _latest_check_in(patient)
    if check_in is None:
        return jsonify(status="not_called", transcript=[], triage=None)

    if check_in.mode == "live":
        check_in = call_service.refresh_live_call(check_in)

    return jsonify(
        status=check_in.status,
        mode=check_in.mode,
        transcript=check_in.transcript or [],
        triage=check_in.triage_result.to_dict() if check_in.triage_result else None,
    )


@bp.post("/triage")
def triage():
    """Tool: score a check-in transcript into a disposition, using Claude
    Sonnet 5 grounded in the patient's full FHIR record.

    Body: { "patient": {"id": "<slug>", ...}, "transcript": [["PT", "..."], ...] }
    ("encounter" may also be included but is currently unused by the real
    agent, which pulls its own FHIR context by patient slug instead.)
    Returns: { severity, label, rationale, flags }
    """
    payload = request.get_json(silent=True) or {}
    transcript = payload.get("transcript")
    patient = payload.get("patient")
    encounter = payload.get("encounter") or {}

    if not isinstance(transcript, list):
        return jsonify(error="'transcript' (list of [speaker, line]) is required"), 400
    if not patient or not patient.get("id"):
        return jsonify(error="'patient' object with an 'id' (slug) is required"), 400

    try:
        result = triage_service.analyze_transcript_with_claude(transcript, patient, encounter)
    except Exception as e:  # network error, missing API key, bad patient slug, etc.
        return jsonify(error=f"triage agent failed: {e}"), 502

    return jsonify(result)


@bp.post("/draft-note")
def draft_note():
    """Tool: draft a chart note from patient context + a disposition.

    Body: { "patient": {...}, "encounter": {...}, "triage": {...} }
    If "triage" is omitted, it is computed from "encounter.transcript" via
    the real Claude-based triage agent.
    Returns: { note }
    """
    payload = request.get_json(silent=True) or {}
    patient = payload.get("patient")
    encounter = payload.get("encounter")
    if not patient or not encounter:
        return jsonify(error="'patient' and 'encounter' objects are required"), 400

    result = payload.get("triage")
    if result is None:
        transcript = encounter.get("transcript", [])
        try:
            result = triage_service.analyze_transcript_with_claude(transcript, patient, encounter)
        except Exception as e:
            return jsonify(error=f"triage agent failed: {e}"), 502

    note = triage_service.draft_note(patient, encounter, result)
    return jsonify(note=note, triage=result)


@bp.post("/gi/analyze")
def gi_analyze():
    """Tool: run live Agent 2 (triage) + Agent 3 (note) over a just-captured
    ElevenLabs voice check-in transcript for one of the 7 GI demo patients in
    index.html's standalone frontend.

    These patients are NOT in the Patient/Encounter DB — they're a plain JS
    array in index.html — so this endpoint takes the transcript directly
    rather than looking anything up via the DB-backed /triage or /draft-note
    endpoints above.

    Body: {
      "slug": "helen",
      "transcript": [["AGENT", "..."], ["PT", "..."], ...],
      "patient": {"name": "...", "age": 76, "gender": "female",
                  "discharge_dx": "...", "days_since_discharge": 6}
        -- optional, only used as a fallback context source for patients
           with no backing synthetic-gi-data record (e.g. jordan_test)
    }
    Returns: { triage: {severity, label, rationale, flags},
               note: {subjective, assessment, plan, action_items},
               groundedInFhir: bool }
    """
    payload = request.get_json(silent=True) or {}
    slug = payload.get("slug")
    transcript = payload.get("transcript")
    patient_info = payload.get("patient") or {}

    if not slug:
        return jsonify(error="'slug' is required"), 400
    if not isinstance(transcript, list) or not transcript:
        return jsonify(error="'transcript' (non-empty list of [speaker, line]) is required"), 400

    try:
        triage_result, note_result, grounded = gi_live_service.analyze_live_call(
            slug, transcript, patient_info
        )
    except Exception as e:  # network error, missing API key, bad slug, etc.
        return jsonify(error=f"live GI analysis failed: {e}"), 502

    return jsonify(triage=triage_result, note=note_result, groundedInFhir=grounded)
