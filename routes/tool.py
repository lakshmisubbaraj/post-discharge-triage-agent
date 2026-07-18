"""HTTP endpoints that expose the triage pipeline as callable "tools".

These are the tools the agent (or the frontend) invokes. They intentionally
return the same JSON shapes the browser stubs produce today, so the frontend
can swap its local analyzeTranscript()/draftNote() calls for fetch() calls
against these endpoints with almost no other change.

Endpoints
    GET  /api/patients                 -- care-team queue, ranked by severity
    GET  /api/patients/<slug>          -- one patient + encounter + check-in + result
    POST /api/triage                   -- score a transcript -> disposition
    POST /api/draft-note               -- draft a chart note from a disposition
"""
from flask import Blueprint, jsonify, request

from extensions import db
from models import Patient
from services import call_service, triage_service

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
    """Return all patients ranked red -> orange -> yellow -> green."""
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
    """Tool: score a check-in transcript into a disposition.

    Body: { "transcript": [["PT", "..."], ["AGENT", "..."], ...] }
    Returns: { severity, label, rationale, flags }
    """
    payload = request.get_json(silent=True) or {}
    transcript = payload.get("transcript")
    if not isinstance(transcript, list):
        return jsonify(error="'transcript' (list of [speaker, line]) is required"), 400

    result = triage_service.analyze_transcript(transcript)
    return jsonify(result)


@bp.post("/draft-note")
def draft_note():
    """Tool: draft a chart note from patient context + a disposition.

    Body: { "patient": {...}, "encounter": {...}, "triage": {...} }
    If "triage" is omitted, it is computed from "encounter.transcript".
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
        result = triage_service.analyze_transcript(transcript)

    note = triage_service.draft_note(patient, encounter, result)
    return jsonify(note=note, triage=result)
