"""Post-discharge check-in pipeline orchestration.

Agent 1 (the call itself — owned by services/call_service.py: ElevenLabs/
Twilio dialing, simulated-call threading, live-call polling) hands off to
Agent 2 (triage) then Agent 3 (chart note) in sequence once a transcript is
complete. That hand-off lives here rather than in call_service.py, so that
file only owns call mechanics and doesn't also own the agent pipeline —
mirrors the same "each agent stays independent" split as
triage_service.py / note_service.py / fhir_context.py.
"""
from __future__ import annotations

from extensions import db
from models import CheckIn, TriageResult
from services import note_service, triage_service


def finalize_call(check_in_id: int) -> None:
    """Run Agent 2 (triage) then Agent 3 (note), in sequence, on a finished
    check-in's transcript, and persist the resulting TriageResult.

    Called by services/call_service.py once a call (simulated or live) has
    ended — see _start_simulated_call() and refresh_live_call() there.
    """
    check_in = db.session.get(CheckIn, check_in_id)
    if check_in is None or check_in.triage_result is not None:
        return

    encounter = check_in.encounter
    patient = encounter.patient

    # Agent 2, then Agent 3 — sequential, since Agent 3 drafts its note from
    # the disposition Agent 2 already decided.
    analysis = triage_service.analyze_transcript(check_in.transcript or [])
    note = note_service.draft_note(
        patient.to_dict(), encounter.to_dict(), analysis
    )
    check_in.triage_result = TriageResult(
        severity=analysis["severity"],
        label=analysis["label"],
        rationale=analysis["rationale"],
        flags=analysis["flags"],
        note=note,
    )
    check_in.status = "completed"
    db.session.commit()
