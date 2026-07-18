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
from services import triage_service

bp = Blueprint("tool", __name__, url_prefix="/api")


def _serialize_patient(patient):
    """Flatten a Patient + its latest encounter/check-in/result into the
    single object shape the frontend expects."""
    encounter = patient.encounters[0] if patient.encounters else None
    check_in = encounter.check_ins[0] if (encounter and encounter.check_ins) else None
    result = check_in.triage_result if check_in else None

    data = patient.to_dict()
    if encounter:
        enc = encounter.to_dict()
        enc.pop("id", None)  # don't clobber the patient's slug id
        data.update(enc)
        data["encounterId"] = encounter.id
    if check_in:
        data["transcript"] = check_in.transcript
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
