"""Photo intake for the GI voice-call track — turns a patient-submitted
symptom photo into a short, clinically-focused text description that Agent 2
(triage) and Agent 3 (note) can reason over alongside the check-in
transcript.

Why a separate text-description step rather than passing the raw image into
Agent 2/3 directly: those are tool-use calls built around a single text user
message (see gi_live_service.analyze_live_call), and a voice-call transcript
can't capture something like "how much blood" or "what did the wound look
like" nearly as reliably as a photo can — but is also easy for a patient to
under- or over-describe verbally. One multimodal Claude call turns the photo
into a plain-text objective description up front; that description then gets
folded into the same transcript-shaped context Agent 2/3 already expect,
rather than requiring either agent's prompt/tool schema to change shape.

Only called from services/gi_live_service.py, for the GI voice-call track's
frontend (index.html) — the only demo track with an in-call photo upload
flow. See routes/tool.py's POST /api/gi/photo for the HTTP entry point.
"""
from __future__ import annotations

import requests

from config import Config
from services.fhir_context import API_URL, API_VERSION

IMAGE_MODEL = "claude-sonnet-5"  # vision-capable; same tier as Agent 2 (triage)

IMAGE_SYSTEM_PROMPT = (
    "You are assisting a clinical post-discharge triage system by describing "
    "a photo a patient sent in of a physical symptom (e.g. stool, vomit, a "
    "surgical wound, a rash). A nurse will read your description alongside "
    "the patient's check-in transcript to help decide whether the patient "
    "needs urgent, same-day, or routine follow-up.\n\n"
    "Describe ONLY objective visual findings: color, consistency, presence "
    "and approximate amount of blood or other unusual material, wound "
    "appearance (redness, drainage, dehiscence), size, and any other "
    "clinically relevant visual detail. Do not diagnose, speculate about "
    "causes, or offer reassurance. If the photo does not clearly show a "
    "relevant symptom (blurry, unrelated subject, etc.), say so plainly "
    "instead of guessing. Keep it to 2-4 sentences of plain, factual "
    "description."
)


def describe_symptom_photo(image_b64: str, media_type: str) -> str:
    """One multimodal Claude call -> a short plain-text description of a
    patient-submitted symptom photo.

    Raises on any API error (missing key, bad image data, network failure) —
    callers should catch and degrade gracefully, since the photo is optional
    supporting context and triage/note drafting must still work without it.
    """
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
            "model": IMAGE_MODEL,
            "max_tokens": 300,
            "system": IMAGE_SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Describe this patient-submitted symptom "
                                "photo for the check-in record."
                            ),
                        },
                    ],
                }
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text_block = next(b for b in data["content"] if b["type"] == "text")
    return text_block["text"].strip()
