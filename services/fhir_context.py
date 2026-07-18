"""Shared FHIR context access, used by both Agent 2 (triage_service.py) and
Agent 3 (note_service.py) so neither agent has to re-implement dataset
loading or resource-summarization logic — and so a fix to one doesn't
silently drift out of sync with a duplicate copy in the other file.

This module owns exactly one job: given a demo patient slug, return that
patient's clinical context as a prompt-ready string. It has no opinion about
triage or note-drafting — those stay in their own agent-specific files.
"""
from __future__ import annotations

import json
import os

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

MAX_RESOURCES_PER_TYPE = 15  # per FHIR resource category, most-recent-first

DATASET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "synthetic-ambient-fhir-25", "synthetic-ambient-fhir-25.jsonl"
)
PATIENT_IDS = {
    "ariane": "7bd9e5b0-5d4b-f10d-9579-f4813faf9cdc",
    "latoyia": "1be66dc9-cf0b-cb78-e88e-ada9a9a5405b",
    "monica": "b504cdf2-e13b-979e-9c4a-95456823e3dd",
    "traci": "5e93dd7e-1639-0886-8d0e-80ac11f2785c",
    "dick": "374e68b2-ee15-0852-cd48-3c7b6fd8e114",
}

DATE_FIELD_CANDIDATES = [
    "effectiveDateTime", "occurrenceDateTime", "performedDateTime",
    "authoredOn", "recordedDate", "onsetDateTime", "abatementDateTime",
    "issued", "started",
]
PERIOD_FIELD_CANDIDATES = ["performedPeriod", "effectivePeriod", "onsetPeriod"]


def _resource_date(r: dict) -> str:
    """Best-effort sortable date string from any FHIR resource shape in this
    dataset. Falls back to '' (sorts last/oldest) if none found."""
    for field in DATE_FIELD_CANDIDATES:
        if field in r and r[field]:
            return r[field]
    for field in PERIOD_FIELD_CANDIDATES:
        period = r.get(field)
        if isinstance(period, dict):
            return period.get("end") or period.get("start") or ""
    return ""


def _most_recent_first(resources: list[dict]) -> list[dict]:
    return sorted(resources, key=_resource_date, reverse=True)


def _summarize_resource(r: dict) -> str:
    rtype = r.get("resourceType", "")
    code = r.get("code") or r.get("medicationCodeableConcept")
    display = None
    if isinstance(code, dict):
        codings = code.get("coding", [])
        if codings:
            display = codings[0].get("display")
        display = display or code.get("text")
    if not display and "medicationReference" in r:
        display = "(medication ordered — name not resolved; dataset only includes a reference)"
    display = display or "(unspecified)"

    extra = ""
    if "valueQuantity" in r:
        vq = r["valueQuantity"]
        extra = f" = {vq.get('value')} {vq.get('unit', '')}".rstrip()
    elif "valueString" in r:
        extra = f" = {r['valueString']}"
    elif "valueCodeableConcept" in r:
        vcodings = r["valueCodeableConcept"].get("coding", [])
        if vcodings:
            extra = f" = {vcodings[0].get('display')}"
    return f"{rtype}: {display}{extra}"


def load_fhir_record(patient_slug: str):
    """Pull the real patient_context + encounter_fhir straight from the
    synthetic-ambient-fhir-25 dataset file, by demo patient slug."""
    pid = PATIENT_IDS.get(patient_slug)
    if pid is None:
        raise ValueError(f"No known FHIR patient_id mapping for slug '{patient_slug}'")
    with open(DATASET_PATH) as f:
        for line in f:
            rec = json.loads(line)
            if rec["metadata"]["patient_id"] == pid:
                return rec["patient_context"], rec["encounter_fhir"]
    raise ValueError(f"Patient '{patient_slug}' ({pid}) not found in {DATASET_PATH}")


def build_fhir_context_block(patient_context: dict, encounter_fhir: dict) -> str:
    patient = patient_context.get("patient", {})
    name = patient.get("name", [{}])[0]
    full_name = (" ".join(name.get("given", [])) + " " + name.get("family", "")).strip()
    gender = patient.get("gender", "unknown")
    birth_date = patient.get("birthDate", "unknown")

    longitudinal = patient_context.get("longitudinal_summary", {})
    condition_labels = longitudinal.get("condition_labels", [])
    medication_labels = longitudinal.get("medication_labels", [])

    encounter = encounter_fhir.get("encounter", {})
    enc_type_list = encounter.get("type", [])
    encounter_type = "unknown"
    if enc_type_list:
        codings = enc_type_list[0].get("coding", [])
        if codings:
            encounter_type = codings[0].get("display", "unknown")

    related = encounter_fhir.get("related_resources", {})
    related_lines = []
    for rtype, resources in related.items():
        shown = _most_recent_first(resources)[:MAX_RESOURCES_PER_TYPE]
        related_lines.extend("  - " + _summarize_resource(r) for r in shown)
        omitted = len(resources) - len(shown)
        if omitted > 0:
            related_lines.append(f"  - ...({omitted} more {rtype} entries not shown)")

    lines = [
        f"Patient: {full_name}, {gender}, born {birth_date}",
        f"Known conditions (longitudinal): {', '.join(condition_labels) or 'none recorded'}",
        f"Known medications (longitudinal): {', '.join(medication_labels) or 'none recorded'}",
        f"This encounter type: {encounter_type}",
        "Most recent resources recorded at this encounter (up to 15 per category):",
    ]
    lines.extend(related_lines or ["  (none)"])
    return "\n".join(lines)


def format_transcript(transcript: list[list[str]]) -> str:
    return "\n".join(f"{who}: {line}" for who, line in transcript)
