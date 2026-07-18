"""Triage business logic.

This is the server-side home for the two "agents" the browser mockup
originally stubbed client-side (analyzeTranscript / draftNote in index.html):

    analyze_transcript(...)             -> Agent 2 stub: keyword-based scoring
    analyze_transcript_with_claude(...) -> Agent 2 real: Claude-based reasoning
    draft_note(...)                     -> Agent 3: chart-note drafting

`analyze_transcript_with_claude` is the real implementation, using Claude
Sonnet 5 with the patient's fuller FHIR context (not just the flattened
history/meds the DB stores) and a 6-way disposition taxonomy instead of the
old 4-tier red/orange/yellow/green. The old `analyze_transcript()` keyword
stub is kept as a zero-cost fallback/reference — not called by the real path.

IMPORTANT (mirrors CLAUDE.md): the scorer only reads PATIENT / FAMILY lines,
never AGENT lines, so the agent's own questions ("any chest pain?") don't
register as findings.

Wiring note: `routes/tool.py` currently calls `analyze_transcript()` (the old
stub), not `analyze_transcript_with_claude()`. Swap that call (in both the
/triage and /draft-note handlers) to actually put this live.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests

from config import Config

# ---------------------------------------------------------------------------
# Old rule-based stub (kept as fallback/reference, not used by the real path)
# ---------------------------------------------------------------------------
RED_FLAGS = [
    "short of breath just getting out of bed", "bluish", "blue lips", "chest pain",
    "chest tightness", "tightness when i take a deep breath", "coughing blood",
    "confused", "fainted", "can't breathe", "cant breathe",
]
ORANGE_FLAGS = [
    "swelling in my ankles", "swelling", "poorly controlled", "not really controlled",
    "7 out of 10", "8 out of 10", "9 out of 10", "dizzy", "numbness",
    "weakness in your legs", "fever",
]
YELLOW_FLAGS = [
    "blood sugar", "low 200s", "thirsty", "blood sugars",
    "still look pale", "more tired than i expected",
]


def _patient_text(transcript: list[list[str]]) -> str:
    """Join only the PATIENT/FAMILY lines, lowercased — never AGENT lines."""
    return " ".join(
        line for who, line in transcript if who in ("PT", "FAMILY")
    ).lower()


def analyze_transcript(transcript: list[list[str]]) -> dict[str, Any]:
    """Agent 2 (old stub): score a transcript into a disposition via keyword
    matching. Kept for reference/fallback — see analyze_transcript_with_claude
    for the real implementation."""
    text = _patient_text(transcript)
    matched = {
        "red": [f for f in RED_FLAGS if f in text],
        "orange": [f for f in ORANGE_FLAGS if f in text],
        "yellow": [f for f in YELLOW_FLAGS if f in text],
    }

    if matched["red"]:
        return {
            "severity": "red",
            "label": "Urgent care referral",
            "rationale": [
                "Patient reports worsening dyspnea and possible cyanosis since "
                "discharge — concerning for respiratory decompensation.",
                "Recent hospitalization was specifically for hypoxemic pneumonia, "
                "raising the stakes of any respiratory change.",
                "Recommend same-day evaluation; do not wait for scheduled follow-up.",
            ],
            "flags": matched["red"],
        }
    if len(matched["orange"]) >= 2:
        return {
            "severity": "orange",
            "label": "Physician callback today",
            "rationale": [
                "Multiple moderate-concern symptoms reported that are not yet "
                "emergent but warrant same-day clinician review.",
                "Given the patient's comorbidities, these findings should not wait "
                "until the next scheduled visit.",
                "Recommend physician or NP callback within 24 hours to reassess and "
                "adjust the care plan.",
            ],
            "flags": matched["orange"],
        }
    if matched["yellow"]:
        return {
            "severity": "yellow",
            "label": "Labs recommended",
            "rationale": [
                "Reported symptoms track with the patient's underlying condition "
                "and are worth confirming with objective data.",
                "A basic metabolic panel / repeat glucose check would clarify "
                "whether the current regimen is adequate.",
                "No acute red flags reported; this can be arranged through routine "
                "channels rather than urgent referral.",
            ],
            "flags": matched["yellow"],
        }
    return {
        "severity": "green",
        "label": "Routine follow-up",
        "rationale": [
            "No red-flag or moderate-concern symptoms reported during this check-in.",
            "Patient describes steady or improving status consistent with expected "
            "recovery trajectory.",
            "Schedule standard follow-up per care plan; no escalation needed at "
            "this time.",
        ],
        "flags": [],
    }


def draft_note(patient: dict, encounter: dict, result: dict) -> str:
    """Agent 3 (stub): assemble a chart note from context + disposition."""
    flags_text = (
        ", ".join(f'"{f}"' for f in result["flags"])
        if result["flags"]
        else "none reported"
    )
    gender_abbr = "F" if patient["gender"] == "female" else "M"
    rationale = "\n".join("- " + r for r in result["rationale"])
    return (
        "POST-DISCHARGE CHECK-IN NOTE\n"
        f"Patient: {patient['name']}, {patient['age']}{gender_abbr}\n"
        f"Days since discharge: {encounter['daysSinceDischarge']}\n"
        f"Discharge context: {encounter['dischargeDx']}\n\n"
        "Subjective: Patient reached by phone for scheduled post-discharge "
        "check-in.\n"
        f"Key statements flagged during call: {flags_text}.\n\n"
        f"Assessment: {result['label']}.\n"
        f"{rationale}\n\n"
        f"Plan: {result['label']} — see disposition above. Care team notified "
        "via queue.\n\n"
        "⚠️ This note was generated by rule-based demo logic, not a "
        "clinician-reviewed LLM output. Not for real clinical use."
    )


# ---------------------------------------------------------------------------
# Queue ranking — 6-way disposition order (agreed with Hannah)
# Unknown-risk ("clinician_callback_required") ranks ABOVE confirmed-stable
# dispositions: a forced guess is more dangerous than an unresolved unknown,
# so unresolved cases get seen promptly even though they're also the
# cheapest/fastest to act on (just a callback).
# ---------------------------------------------------------------------------
SEVERITY_ORDER = {
    "emergency_department": 0,
    "urgent_care_same_day": 1,
    "clinician_callback_required": 2,
    "clinic_follow_up": 3,
    "labs_imaging_needed": 4,
    "routine_follow_up": 5,
    # Old 4-tier values kept mapped during the transition, in case anything
    # is still calling the old analyze_transcript() stub before routes/tool.py
    # is switched over to analyze_transcript_with_claude().
    "red": 0,
    "orange": 1,
    "yellow": 4,
    "green": 5,
}


def rank_by_severity(items: list[dict], key=lambda r: r["severity"]) -> list[dict]:
    """Sort a list of dicts by disposition urgency for the care team queue."""
    return sorted(items, key=lambda i: SEVERITY_ORDER.get(key(i), 99))


# ---------------------------------------------------------------------------
# Real Agent 2 — Claude-based triage reasoning
# ---------------------------------------------------------------------------
TRIAGE_MODEL = "claude-sonnet-5"  # stronger reasoning tier for this agent specifically
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


def _build_fhir_context_block(patient_context: dict, encounter_fhir: dict) -> str:
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


def _format_transcript(transcript: list[list[str]]) -> str:
    return "\n".join(f"{who}: {line}" for who, line in transcript)


TRIAGE_SYSTEM_PROMPT = (
    "You are a clinical triage assistant reviewing a post-discharge check-in "
    "transcript together with the patient's structured clinical record "
    "(longitudinal conditions/medications, plus the resources tied to their "
    "discharging encounter — observations, procedures, diagnostic reports, "
    "medication requests). Decide one of six dispositions:\n\n"
    "- \"emergency_department\": acute/emergent findings — go to the ED now, "
    "do not wait for any scheduled care.\n"
    "- \"urgent_care_same_day\": moderate-to-serious concern needing same-day "
    "in-person evaluation, not immediately life-threatening.\n"
    "- \"clinician_callback_required\": route to a human clinician to call the "
    "patient directly. Use this ONLY for one of two guardrail conditions, "
    "regardless of what the clinical content otherwise looks like:\n"
    "    1. Insufficient information — the patient gave only vague, minimal, "
    "or repeated non-answers (e.g. \"I'm fine, just a little tired\" and "
    "nothing further) such that you cannot responsibly assess status either "
    "way.\n"
    "    2. Uncooperative or adversarial patient — hostile, dismissive, or "
    "refused to engage, such that the transcript contains no usable clinical "
    "information.\n"
    "  A forced guess in either direction is more dangerous than flagging for "
    "human follow-up — do not force a clinical disposition out of a "
    "transcript that doesn't actually contain one. When this disposition is "
    "used, say explicitly in the rationale which of the two guardrail "
    "conditions applied.\n"
    "- \"clinic_follow_up\": create a scheduling ticket for a close (within "
    "days) clinic visit — more than routine, less than same-day urgent.\n"
    "- \"labs_imaging_needed\": symptoms consistent with the known condition, "
    "not urgent, but objective data would confirm the recovery trajectory.\n"
    "- \"routine_follow_up\": recovering as expected; continue the existing "
    "clinician-established plan, no new action.\n\n"
    "General reasoning rules:\n"
    "- Base your assessment only on what the PATIENT or FAMILY actually said, "
    "not the agent's questions.\n"
    "- Pay attention to negation.\n"
    "- Weigh findings against the patient's full clinical context, not just "
    "the check-in conversation in isolation.\n"
    "- When there IS enough information but it's ambiguous between two tiers, "
    "escalate to the more urgent — a missed deterioration is worse than an "
    "unnecessary callback.\n"
    "- When there is NOT enough information, or the patient was "
    "uncooperative, use clinician_callback_required rather than guessing.\n"
    "- Always cite the specific patient statements (or their notable absence) "
    "that drove your decision.\n\n"
    "This is decision support — a clinician reviews every output before "
    "action is taken."
)

TRIAGE_TOOL = {
    "name": "record_triage_decision",
    "description": "Record the triage disposition for a post-discharge check-in",
    "input_schema": {
        "type": "object",
        "required": ["severity", "label", "rationale", "flags"],
        "properties": {
            "severity": {
                "type": "string",
                "enum": [
                    "emergency_department", "urgent_care_same_day",
                    "clinician_callback_required", "clinic_follow_up",
                    "labs_imaging_needed", "routine_follow_up",
                ],
                "description": (
                    "Named 'severity' for compatibility with the existing API/DB "
                    "contract, even though it now holds one of 6 dispositions "
                    "rather than the original 4 severity tiers."
                ),
            },
            "label": {"type": "string"},
            "rationale": {"type": "array", "items": {"type": "string"}},
            "flags": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def analyze_transcript_with_claude(transcript: list[list[str]], patient: dict, encounter: dict) -> dict[str, Any]:
    """Agent 2 (real): Claude Sonnet 5 tool-use call reasoning over the
    transcript plus the patient's fuller FHIR context. Returns the same
    { severity, label, rationale, flags } shape as analyze_transcript(), so
    routes/tool.py and models.TriageResult need no changes — only `severity`
    now holds one of the 6 new disposition values instead of the old 4.
    """
    if not Config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — check your .env file.")

    patient_context, encounter_fhir = load_fhir_record(patient["id"])
    fhir_block = _build_fhir_context_block(patient_context, encounter_fhir)

    user_message = (
        f"Patient clinical context:\n{fhir_block}\n\n"
        f"Check-in transcript:\n{_format_transcript(transcript)}\n\n"
        "Determine the triage disposition using the record_triage_decision tool."
    )

    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": Config.ANTHROPIC_API_KEY,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": TRIAGE_MODEL,
            "max_tokens": 1024,
            "system": TRIAGE_SYSTEM_PROMPT,
            "tools": [TRIAGE_TOOL],
            "tool_choice": {"type": "tool", "name": "record_triage_decision"},
            "messages": [{"role": "user", "content": user_message}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    tool_block = next(b for b in data["content"] if b["type"] == "tool_use")
    return tool_block["input"]
