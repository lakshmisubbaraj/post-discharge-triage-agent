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

NOTE: /triage and /draft-note now call the real triage_service.analyze_transcript_with_claude()
(Sonnet 5, FHIR-grounded, 6-way disposition taxonomy) instead of the old
analyze_transcript() keyword stub. This changes /triage's request contract:
it now requires a "patient" object (with an "id"/slug) in addition to
"transcript", since the real agent looks up the patient's full FHIR record by
slug rather than scoring the transcript in isolation.
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
