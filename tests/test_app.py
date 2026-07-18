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
    assert len(data) == 5
    # Ariane (red) should surface first.
    assert data[0]["id"] == "ariane"
    assert data[0]["triage"]["severity"] == "red"


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
