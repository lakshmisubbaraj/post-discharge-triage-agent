"""Triage business logic.

This is the server-side home for the two "agents" that the browser mockup
currently stubs client-side (analyzeTranscript / draftNote in index.html):

    analyze_transcript(...)  -> Agent 2: triage scoring
    draft_note(...)          -> Agent 3: chart-note drafting

Right now both are the same rule-based/template stubs the frontend uses, ported
to Python so the logic lives behind an API instead of in browser JS. The whole
point of moving them here is that this is where a real Claude call belongs: the
key stays server-side and the endpoint returns the same JSON shape, so the
frontend barely changes. See `analyze_transcript_with_claude` for the seam.

IMPORTANT (mirrors CLAUDE.md): the scorer only reads PATIENT / FAMILY lines,
never AGENT lines, so the agent's own questions ("any chest pain?") don't
register as findings.
"""
from __future__ import annotations

from typing import Any

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

SEVERITY_ORDER = {"red": 0, "orange": 1, "yellow": 2, "green": 3}


def _patient_text(transcript: list[list[str]]) -> str:
    """Join only the PATIENT/FAMILY lines, lowercased — never AGENT lines."""
    return " ".join(
        line for who, line in transcript if who in ("PT", "FAMILY")
    ).lower()


def analyze_transcript(transcript: list[list[str]]) -> dict[str, Any]:
    """Agent 2 (stub): score a transcript into a disposition.

    Returns { severity, label, rationale: [...], flags: [...] } — the same
    shape the frontend and the real Claude endpoint are expected to return.
    """
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


def rank_by_severity(items: list[dict], key=lambda r: r["severity"]) -> list[dict]:
    """Sort a list of dicts red -> orange -> yellow -> green for the queue."""
    return sorted(items, key=lambda i: SEVERITY_ORDER.get(key(i), 99))


# ---------------------------------------------------------------------------
# SEAM FOR THE REAL AGENT (not wired yet)
# Replace the body with a Claude Messages API call using tool-use / structured
# output, prompted with the transcript + patient/encounter FHIR context. Keep
# the return shape identical to analyze_transcript() above so nothing upstream
# changes. Prompts live in ../PROMPTS.md.
# ---------------------------------------------------------------------------
def analyze_transcript_with_claude(transcript, patient, encounter):
    raise NotImplementedError(
        "Wire this to the Anthropic Messages API (see PROMPTS.md). "
        "Must return the same shape as analyze_transcript()."
    )
