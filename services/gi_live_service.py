"""Live Agent 2 / Agent 3 analysis for a just-completed GI voice check-in.

This is the missing hand-off the frontend was stubbing out: index.html's live
ElevenLabs voice call captures a REAL transcript client-side, but had no way
to get Agent 2 (triage reasoning) or Agent 3 (chart note) to actually reason
over it — the UI could only ever show each patient's pre-baked reference
analysis (from gi_eval_results.json / test_gi_agents.py), regardless of what
was actually said on the call.

This module closes that gap using the exact same prompts/tool schemas/models
already proven against these patients in test_gi_agents.py, which itself
imports byte-identical prompts from services/triage_service.py (Agent 2,
Claude Sonnet 5) and services/note_service.py (Agent 3, Claude Haiku) — so a
live call gets the same reasoning quality as the offline eval, just pointed
at whatever was actually said instead of a canned transcript.

Clinical grounding: the 6 original demo patients (helen/miguel/harriet/
robert/deshawn/yolanda) have a real FHIR-shaped record in
synthetic-gi-data/ (via gi_context.build_gi_clinical_context), looked up by
slug -> source_file -> pt_id using gi_demo_patients.GI_DEMO_PATIENTS as the
mapping. Patients added straight into the frontend without a backing dataset
file (e.g. the "jordan_test" guardrail test case) fall back to a minimal
context built from whatever the frontend sent (name/age/gender/discharge_dx/
days_since_discharge) — thinner grounding, but the agent still reasons over
the real transcript rather than not running at all.
"""
from __future__ import annotations

from typing import Any

import requests

from config import Config
from gi_context import build_gi_clinical_context, pt_id_from_source_file
from gi_demo_patients import GI_DEMO_PATIENTS
from services.fhir_context import API_URL, API_VERSION, format_transcript
from services.note_service import NOTE_MODEL, NOTE_SYSTEM_PROMPT, NOTE_TOOL
from services.triage_service import TRIAGE_MODEL, TRIAGE_SYSTEM_PROMPT, TRIAGE_TOOL

_SLUG_TO_SOURCE_FILE = {p["slug"]: p["source_file"] for p in GI_DEMO_PATIENTS}


def _call_claude_tool(model: str, system: str, tool: dict, user_message: str, max_tokens: int) -> dict[str, Any]:
    if not Config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — check your .env file.")
    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": Config.ANTHROPIC_API_KEY,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
            "messages": [{"role": "user", "content": user_message}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    tool_block = next(b for b in data["content"] if b["type"] == "tool_use")
    return tool_block["input"]


def _build_context(slug: str, patient_info: dict | None) -> tuple[str, bool]:
    """Returns (context_block, grounded_in_dataset). grounded is False for
    patients with no backing synthetic-gi-data file (context is then built
    only from whatever the frontend sent)."""
    source_file = _SLUG_TO_SOURCE_FILE.get(slug)
    if source_file:
        pt_id = pt_id_from_source_file(source_file)
        return build_gi_clinical_context(pt_id), True

    info = patient_info or {}
    gender_abbr = "F" if info.get("gender") == "female" else "M"
    lines = [
        f"Patient: {info.get('name', slug)}, {info.get('age', '?')}{gender_abbr}",
        f"Days since discharge: {info.get('days_since_discharge', '?')}",
        f"Discharge context: {info.get('discharge_dx', 'not recorded')}",
        "(No structured FHIR/procedure-result record is available for this "
        "patient — reason only from the check-in transcript and this "
        "minimal context; do not invent history, medications, or prior "
        "results that aren't stated here or in the transcript.)",
    ]
    return "\n".join(lines), False


def analyze_live_call(
    slug: str, transcript: list[list[str]], patient_info: dict | None = None
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Run the real Agent 2 (triage) then Agent 3 (note) over a just-captured
    live transcript. Returns (triage_dict, note_dict, grounded_in_dataset).

    triage_dict shape: { severity, label, rationale, flags } — identical to
    TRIAGE_TOOL's output, the same shape already used by the frontend's
    pre-baked PATIENTS[].triage.
    note_dict shape: { subjective, assessment, plan, action_items } —
    identical to NOTE_TOOL's output / PATIENTS[].note.
    """
    context, grounded = _build_context(slug, patient_info)
    transcript_text = format_transcript(transcript)

    triage = _call_claude_tool(
        TRIAGE_MODEL,
        TRIAGE_SYSTEM_PROMPT,
        TRIAGE_TOOL,
        (
            f"Patient clinical context:\n{context}\n\n"
            f"Check-in transcript:\n{transcript_text}\n\n"
            "Determine the triage disposition using the record_triage_decision tool."
        ),
        max_tokens=1024,
    )

    rationale_text = "\n".join(f"- {r}" for r in triage.get("rationale", []))
    note = _call_claude_tool(
        NOTE_MODEL,
        NOTE_SYSTEM_PROMPT,
        NOTE_TOOL,
        (
            f"Patient clinical context:\n{context}\n\n"
            f"Check-in transcript:\n{transcript_text}\n\n"
            f"Triage decision already made: {triage['severity']} — {triage['label']}\n"
            f"Rationale:\n{rationale_text}\n\n"
            "Draft the chart note using the record_chart_note tool."
        ),
        max_tokens=768,
    )

    return triage, note, grounded
