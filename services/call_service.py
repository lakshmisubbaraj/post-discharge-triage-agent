"""Voice check-in call orchestration (Agent 1's real-world mechanics only).

Target workflow:
    Call button → ElevenLabs agent dials the patient (via a Twilio number
    imported into ElevenLabs) → patient talks → ElevenLabs transcribes →
    the agent's LLM (configure Claude in the agent settings) reasons →
    ElevenLabs synthesizes speech → patient hears the response.
    Meanwhile the frontend polls our API and renders the transcript live.

Two modes, chosen automatically:

  LIVE       — ELEVENLABS_API_KEY / _AGENT_ID / _PHONE_NUMBER_ID all present.
               We start an outbound call via the ElevenLabs Twilio endpoint
               and poll the conversation endpoint for the transcript.
               Docs: https://elevenlabs.io/docs/api-reference/twilio/outbound-call
                     https://elevenlabs.io/docs/api-reference/conversations/get

  SIMULATED  — any credential missing. A background thread streams a canned
               transcript line-by-line into the DB so the entire pipeline
               (live UI updates → triage → note → queue re-rank) can be
               demoed with zero external services.

This file only owns the call itself — dialing/simulating, polling, and
streaming the transcript into the DB. Once a call ends, it hands off to
services/pipeline_service.finalize_call(), which runs Agent 2 (triage) then
Agent 3 (note) in sequence and persists the TriageResult. That hand-off is
intentionally not implemented in this file — see pipeline_service.py.
"""
from __future__ import annotations

import threading
import time

import requests
from flask import current_app

from extensions import db
from models import CheckIn
from services import pipeline_service

ELEVENLABS_BASE = "https://api.elevenlabs.io"

# Canned patient-side conversation used in SIMULATED mode. Written so the
# keyword triage stub lands on "yellow" (fatigue worth a lab recheck) —
# a nice demo of a live call changing the queue.
SIMULATED_TRANSCRIPT = [
    ["AGENT", "Hi, this is the care team's automated check-in calling after your appendectomy. Do you have a minute to talk about how recovery is going?"],
    ["PT", "Sure, yes. Overall it's been going okay."],
    ["AGENT", "Great. How is the pain around the incision sites — controlled with the acetaminophen and ibuprofen?"],
    ["PT", "Yes, mostly. I only needed one dose yesterday."],
    ["AGENT", "Any fever, chills, or redness or discharge around the incisions?"],
    ["PT", "No fever or anything like that. The incisions look clean."],
    ["AGENT", "How are your energy levels and appetite?"],
    ["PT", "Appetite is fine. I am still more tired than I expected, though — I needed a nap both afternoons."],
    ["AGENT", "That can be normal after surgery, but it's worth confirming with basic labs given day three. Anything else you're worried about?"],
    ["PT", "No, that's everything."],
    ["AGENT", "Thanks — I'll pass this along to your care team, and someone will follow up about the labs."],
]


def elevenlabs_configured() -> bool:
    """True when a real outbound *phone* call is possible (needs a number)."""
    cfg = current_app.config
    return all(
        cfg.get(k)
        for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "ELEVENLABS_PHONE_NUMBER_ID")
    )


def widget_enabled() -> bool:
    """True when the in-browser voice widget can run (just needs an agent id).

    No phone number or carrier required — the widget talks through the
    browser mic/speakers.
    """
    return bool(current_app.config.get("ELEVENLABS_AGENT_ID"))


def _fetch_conversation(conversation_id: str):
    """GET a single ElevenLabs conversation (transcript + status)."""
    cfg = current_app.config
    resp = requests.get(
        f"{ELEVENLABS_BASE}/v1/convai/conversations/{conversation_id}",
        headers={"xi-api-key": cfg["ELEVENLABS_API_KEY"]},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _latest_conversation_id() -> str | None:
    """Most recent conversation for our agent (used when the widget doesn't
    hand us a conversation id directly)."""
    cfg = current_app.config
    resp = requests.get(
        f"{ELEVENLABS_BASE}/v1/convai/conversations",
        headers={"xi-api-key": cfg["ELEVENLABS_API_KEY"]},
        params={"agent_id": cfg["ELEVENLABS_AGENT_ID"], "page_size": 1},
        timeout=10,
    )
    resp.raise_for_status()
    convos = resp.json().get("conversations") or []
    return convos[0].get("conversation_id") if convos else None


def _map_transcript(raw) -> list[list[str]]:
    return [
        ["AGENT" if t.get("role") == "agent" else "PT", t.get("message") or ""]
        for t in (raw or [])
        if t.get("message")
    ]


def _build_agent_prompt(patient, encounter) -> str:
    """Condition-specific system prompt for the voice agent (Agent 1)."""
    return (
        "You are a warm, concise post-discharge check-in caller for a care team. "
        f"You are calling {patient.name}, a {patient.age}-year-old {patient.gender}, "
        f"{encounter.days_since_discharge} days after: {encounter.discharge_dx}. "
        f"Relevant history: {', '.join(encounter.history or []) or 'none'}. "
        f"Medications: {', '.join(encounter.meds or []) or 'none'}. "
        "Ask condition-specific recovery questions one at a time (pain, wound/symptom "
        "changes, medications, red-flag symptoms relevant to this discharge). Keep "
        "turns short and spoken-friendly. Do not give a diagnosis; close by saying "
        "the care team will review the conversation."
    )


# ---------------------------------------------------------------------------
# Starting a call
# ---------------------------------------------------------------------------

def start_call(patient) -> CheckIn:
    """Create a CheckIn and start a live or simulated call for this patient."""
    encounter = patient.encounters[0]

    if elevenlabs_configured():
        check_in = CheckIn(status="in_progress", mode="live", transcript=[])
        encounter.check_ins.append(check_in)
        db.session.commit()
        _start_elevenlabs_call(patient, encounter, check_in)
    else:
        check_in = CheckIn(status="in_progress", mode="simulated", transcript=[])
        encounter.check_ins.append(check_in)
        db.session.commit()
        _start_simulated_call(check_in.id)

    return check_in


def _start_elevenlabs_call(patient, encounter, check_in):
    """Kick off a real outbound phone call through ElevenLabs + Twilio."""
    cfg = current_app.config
    resp = requests.post(
        f"{ELEVENLABS_BASE}/v1/convai/twilio/outbound-call",
        headers={"xi-api-key": cfg["ELEVENLABS_API_KEY"]},
        json={
            "agent_id": cfg["ELEVENLABS_AGENT_ID"],
            "agent_phone_number_id": cfg["ELEVENLABS_PHONE_NUMBER_ID"],
            "to_number": patient.phone,
            # Per-call prompt override so the agent asks condition-specific
            # questions. Requires enabling prompt overrides in the agent's
            # Security settings in the ElevenLabs dashboard.
            "conversation_initiation_client_data": {
                "conversation_config_override": {
                    "agent": {
                        "prompt": {"prompt": _build_agent_prompt(patient, encounter)},
                        "first_message": (
                            f"Hi {patient.name.split()[0]}, this is your care team's "
                            "check-in call. Is now an okay time to talk for a couple "
                            "of minutes?"
                        ),
                    }
                }
            },
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        check_in.status = "failed"
        db.session.commit()
        raise RuntimeError(f"ElevenLabs call failed: HTTP {resp.status_code} {resp.text[:300]}")

    data = resp.json()
    check_in.conversation_id = data.get("conversation_id")
    db.session.commit()


def _start_simulated_call(check_in_id: int):
    """Stream the canned transcript into the DB, then hand off to the
    triage/note pipeline.

    delay==0 (tests) runs synchronously; otherwise a daemon thread paces the
    lines so the UI's polling visibly updates live.
    """
    app = current_app._get_current_object()
    delay = app.config.get("SIM_CALL_DELAY", 1.2)

    def run():
        with app.app_context():
            for i in range(len(SIMULATED_TRANSCRIPT)):
                if delay:
                    time.sleep(delay)
                check_in = db.session.get(CheckIn, check_in_id)
                # Reassign (not append) so SQLAlchemy detects the JSON change.
                check_in.transcript = SIMULATED_TRANSCRIPT[: i + 1]
                db.session.commit()
            pipeline_service.finalize_call(check_in_id)

    if delay == 0:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

def refresh_live_call(check_in: CheckIn) -> CheckIn:
    """Pull the latest transcript for a live ElevenLabs call and update the
    DB. Called from the GET status endpoint on each frontend poll."""
    if check_in.mode != "live" or check_in.status != "in_progress":
        return check_in
    if not check_in.conversation_id:
        return check_in

    cfg = current_app.config
    resp = requests.get(
        f"{ELEVENLABS_BASE}/v1/convai/conversations/{check_in.conversation_id}",
        headers={"xi-api-key": cfg["ELEVENLABS_API_KEY"]},
        timeout=10,
    )
    if resp.status_code >= 400:
        return check_in  # transient — keep last known state

    data = resp.json()
    transcript = [
        ["AGENT" if t.get("role") == "agent" else "PT", t.get("message") or ""]
        for t in (data.get("transcript") or [])
        if t.get("message")
    ]
    if transcript:
        check_in.transcript = transcript
        db.session.commit()

    # ElevenLabs statuses: initiated / in-progress / processing / done / failed
    if data.get("status") == "done":
        pipeline_service.finalize_call(check_in.id)
    elif data.get("status") == "failed":
        check_in.status = "failed"
        db.session.commit()

    return check_in


# ---------------------------------------------------------------------------
# Browser voice widget (no phone number needed)
# ---------------------------------------------------------------------------

def start_widget_call(patient) -> CheckIn:
    """Open a check-in for a browser-widget voice conversation.

    The actual voice happens client-side in the ElevenLabs widget; we just
    track the check-in and, when the frontend reports the call ended, pull the
    transcript and run triage.
    """
    encounter = patient.encounters[0]
    check_in = CheckIn(status="in_progress", mode="widget", transcript=[])
    encounter.check_ins.append(check_in)
    db.session.commit()
    return check_in


def finalize_widget_call(check_in: CheckIn, conversation_id: str | None = None):
    """Pull the widget conversation's transcript and run triage.

    Returns the check_in. If ElevenLabs credentials aren't available (or the
    transcript can't be fetched yet), falls back to the simulated transcript so
    the demo still completes. If the real conversation is still processing,
    leaves the check-in in_progress so the frontend keeps polling.
    """
    if check_in.status == "completed":
        return check_in

    cfg = current_app.config
    api_key = cfg.get("ELEVENLABS_API_KEY")

    # No API key → we can't read the real transcript. Use the canned one so
    # the pipeline (triage + note + queue) still demonstrably runs.
    if not api_key:
        check_in.transcript = SIMULATED_TRANSCRIPT
        check_in.mode = "widget_sim"
        finalize_call(check_in.id)
        return db.session.get(CheckIn, check_in.id)

    try:
        cid = conversation_id or check_in.conversation_id or _latest_conversation_id()
        if not cid:
            return check_in  # nothing to read yet; keep polling
        check_in.conversation_id = cid
        data = _fetch_conversation(cid)
    except requests.RequestException:
        return check_in  # transient; frontend will poll again

    status = data.get("status")
    transcript = _map_transcript(data.get("transcript"))
    if transcript:
        check_in.transcript = transcript
        db.session.commit()

    if status == "done":
        finalize_call(check_in.id)
    elif status == "failed":
        check_in.status = "failed"
        db.session.commit()
    return db.session.get(CheckIn, check_in.id)


def finalize_call(check_in_id: int):
    """Run Agent 2 (triage) + Agent 3 (note) on the final transcript."""
    check_in = db.session.get(CheckIn, check_in_id)
    if check_in is None or check_in.triage_result is not None:
        return

    encounter = check_in.encounter
    patient = encounter.patient

    analysis = triage_service.analyze_transcript(check_in.transcript or [])
    note = triage_service.draft_note(
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
