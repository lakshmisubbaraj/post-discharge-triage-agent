"""SQLAlchemy models for the post-discharge triage domain.

Entity map:
    Patient      -- a discharged patient with FHIR-grounded demographics/context
    Encounter    -- the discharge event (diagnosis, meds, comorbidities)
    CheckIn      -- a post-discharge check-in "call" with a transcript
    TriageResult -- the disposition produced for a check-in (severity + note)

The transcript, history, meds, rationale, and flags are stored as JSON columns
so this mirrors the shapes the frontend already consumes without needing extra
join tables for a hackathon-scale mockup.
"""
from datetime import datetime

from extensions import db


class Patient(db.Model):
    __tablename__ = "patients"

    id = db.Column(db.Integer, primary_key=True)
    # Stable external id used by the demo/frontend (e.g. "ariane").
    slug = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    gender = db.Column(db.String(16), nullable=False)

    encounters = db.relationship(
        "Encounter", back_populates="patient", cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.slug,
            "name": self.name,
            "age": self.age,
            "gender": self.gender,
        }


class Encounter(db.Model):
    __tablename__ = "encounters"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)

    discharge_dx = db.Column(db.String(512), nullable=False)
    history = db.Column(db.JSON, nullable=False, default=list)  # list[str]
    meds = db.Column(db.JSON, nullable=False, default=list)     # list[str]
    days_since_discharge = db.Column(db.Integer, nullable=False, default=0)

    patient = db.relationship("Patient", back_populates="encounters")
    check_ins = db.relationship(
        "CheckIn", back_populates="encounter", cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "dischargeDx": self.discharge_dx,
            "history": self.history or [],
            "meds": self.meds or [],
            "daysSinceDischarge": self.days_since_discharge,
        }


class CheckIn(db.Model):
    __tablename__ = "check_ins"

    id = db.Column(db.Integer, primary_key=True)
    encounter_id = db.Column(db.Integer, db.ForeignKey("encounters.id"), nullable=False)

    # Speaker-labeled transcript: list of [speaker, line] pairs.
    transcript = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    encounter = db.relationship("Encounter", back_populates="check_ins")
    triage_result = db.relationship(
        "TriageResult",
        back_populates="check_in",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "transcript": self.transcript or [],
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }


class TriageResult(db.Model):
    __tablename__ = "triage_results"

    id = db.Column(db.Integer, primary_key=True)
    check_in_id = db.Column(db.Integer, db.ForeignKey("check_ins.id"), nullable=False)

    severity = db.Column(db.String(16), nullable=False)  # red|orange|yellow|green
    label = db.Column(db.String(128), nullable=False)
    rationale = db.Column(db.JSON, nullable=False, default=list)  # list[str]
    flags = db.Column(db.JSON, nullable=False, default=list)      # list[str]
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    check_in = db.relationship("CheckIn", back_populates="triage_result")

    def to_dict(self):
        return {
            "id": self.id,
            "severity": self.severity,
            "label": self.label,
            "rationale": self.rationale or [],
            "flags": self.flags or [],
            "note": self.note,
        }
