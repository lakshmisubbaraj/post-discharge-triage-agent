"""Smoke + unit tests for the triage backend.

Run:
    pip install -r requirements.txt
    pytest
"""
import pytest

from app import create_app
from config import TestConfig
from extensions import db
from seed_data import seed
from services import triage_service


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


# --- service-level tests ---------------------------------------------------

def test_red_disposition_from_transcript():
    transcript = [
        ["AGENT", "Any chest pain?"],
        ["PT", "I'm short of breath just getting out of bed and my lips look bluish."],
    ]
    result = triage_service.analyze_transcript(transcript)
    assert result["severity"] == "red"
    assert result["flags"]


def test_agent_questions_do_not_trigger_flags():
    """Agent lines must never be scored — only PT/FAMILY lines."""
    transcript = [
        ["AGENT", "Any chest pain or shortness of breath or swelling?"],
        ["PT", "No, none of that, I feel great."],
    ]
    result = triage_service.analyze_transcript(transcript)
    assert result["severity"] == "green"
    assert result["flags"] == []


def test_orange_needs_two_findings():
    transcript = [
        ["PT", "I have some swelling in my ankles and the pain is not really controlled."],
    ]
    result = triage_service.analyze_transcript(transcript)
    assert result["severity"] == "orange"


def test_rank_by_severity_orders_red_first():
    items = [
        {"severity": "green"},
        {"severity": "red"},
        {"severity": "yellow"},
    ]
    ranked = triage_service.rank_by_severity(items, key=lambda i: i["severity"])
    assert [i["severity"] for i in ranked] == ["red", "yellow", "green"]


# --- endpoint tests --------------------------------------------------------

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_list_patients_ranked(client):
    resp = client.get("/api/patients")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 6
    # Ariane (red) should surface first.
    assert data[0]["id"] == "ariane"
    assert data[0]["triage"]["severity"] == "red"
    # Hannah (not yet called, no triage) should trail the ranked patients.
    assert data[-1]["id"] == "hannah"


def test_get_single_patient(client):
    resp = client.get("/api/patients/monica")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "Monica H."
    assert data["triage"]["severity"] == "orange"


def test_get_unknown_patient_404(client):
    resp = client.get("/api/patients/nobody")
    assert resp.status_code == 404


def test_triage_endpoint(client):
    resp = client.post("/api/triage", json={
        "transcript": [["PT", "I have chest pain."]],
    })
    assert resp.status_code == 200
    assert resp.get_json()["severity"] == "red"


def test_triage_endpoint_requires_transcript(client):
    resp = client.post("/api/triage", json={})
    assert resp.status_code == 400


def test_hannah_seeded_without_checkin(client):
    resp = client.get("/api/patients/hannah")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["phone"] == "+16782217469"
    assert data["callStatus"] == "not_called"
    assert data.get("triage") is None


def test_call_workflow_simulated(client):
    """Full call pipeline in simulated mode: start -> transcript -> triage."""
    # No ElevenLabs keys in TestConfig, so this runs the simulated call
    # synchronously (SIM_CALL_DELAY=0).
    resp = client.post("/api/patients/hannah/call")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["mode"] == "simulated"

    resp = client.get("/api/patients/hannah/call")
    call = resp.get_json()
    assert call["status"] == "completed"
    assert len(call["transcript"]) > 0
    # The canned transcript says "more tired than I expected" -> yellow.
    assert call["triage"]["severity"] == "yellow"
    assert "POST-DISCHARGE CHECK-IN NOTE" in call["triage"]["note"]

    # Hannah should now be ranked in the queue (not trailing untriaged).
    queue = client.get("/api/patients").get_json()
    ids = [p["id"] for p in queue]
    assert ids.index("hannah") < ids.index("traci")  # yellow before green


def test_call_conflict_when_in_progress(app, client):
    """A second call while one is in_progress returns 409."""
    from models import CheckIn, Patient

    patient = Patient.query.filter_by(slug="hannah").first()
    patient.encounters[0].check_ins.append(
        CheckIn(status="in_progress", mode="simulated", transcript=[])
    )
    from extensions import db as _db
    _db.session.commit()

    resp = client.post("/api/patients/hannah/call")
    assert resp.status_code == 409


def test_call_unknown_patient_404(client):
    assert client.post("/api/patients/nobody/call").status_code == 404


def test_draft_note_endpoint(client):
    resp = client.post("/api/draft-note", json={
        "patient": {"name": "Test P.", "age": 60, "gender": "male"},
        "encounter": {
            "dischargeDx": "Test dx",
            "daysSinceDischarge": 3,
            "transcript": [["PT", "I feel great, no concerns."]],
        },
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert "POST-DISCHARGE CHECK-IN NOTE" in body["note"]
    assert body["triage"]["severity"] == "green"
