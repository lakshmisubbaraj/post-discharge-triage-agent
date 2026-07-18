#!/usr/bin/env python3
"""
Smoke-test script for the 3-agent post-discharge triage pipeline.

Purpose: validate that the prompts in PROMPTS.md actually work end-to-end
against the real Anthropic API, while spending as little as possible while
you're still debugging the plumbing.

Cost-control choices baked in:
  - Defaults to Claude Haiku (the cheapest/fastest current tier), not Sonnet
    or Opus. Swap to a stronger model only once you're happy with the
    prompts and want a quality pass.
  - Defaults to ONE patient and ALL 3 agents = 3 API calls total per run.
  - Every call uses tool-use (structured output) with small schemas, which
    also keeps output token counts (the expensive side of the ledger) low.
  - Prints an estimated cost after every run so you can see exactly what
    you're spending as you iterate.

Usage:
    python test_agents.py                          # all 3 agents, 1 patient (~3 calls)
    python test_agents.py --agent triage            # just the triage agent (1 call)
    python test_agents.py --patient dick            # a different demo patient
    python test_agents.py --severity orange         # target severity for the transcript generator
    python test_agents.py --model claude-sonnet-5   # quality-check pass once prompts are solid

Setup:
    pip install requests python-dotenv
    echo "ANTHROPIC_API_KEY=sk-ant-..." > .env      # never commit this — already in .gitignore

Note: this script talks to the Anthropic Messages API directly over HTTP
(via `requests`) rather than the official `anthropic` SDK, purely so it has
no dependency beyond `requests`. Functionally identical either way — feel
free to port it to the SDK later if you prefer its ergonomics.
"""
import argparse
import json
import os
import sys

from dotenv import load_dotenv
import requests

load_dotenv()

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

DEFAULT_MODEL = "claude-haiku-4-5"  # cheapest/fastest current tier

# Rough per-million-token pricing for cost estimates only — check
# https://platform.claude.com/docs/en/about-claude/pricing for current rates.
PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
}

# Same FHIR-grounded patients as the index.html mockup, so results here are
# directly comparable to what you saw in the browser demo.
DEMO_PATIENTS = {
    "ariane": {
        "name": "Ariane R.", "age": 81, "gender": "female",
        "dischargeDx": "COVID-19 with pneumonia & hypoxemia (hospital admission for isolation)",
        "history": ["Prior MI", "Hypertension", "Hyperlipidemia", "Metabolic syndrome"],
        "meds": ["Aspirin 81mg", "Atenolol 50mg", "Rosuvastatin 40mg", "Lisinopril 20mg"],
        "daysSinceDischarge": 3,
    },
    "latoyia": {
        "name": "Latoyia W.", "age": 75, "gender": "female",
        "dischargeDx": "SNF admission after hospitalization (Type 2 diabetes, CKD stage 1, anemia)",
        "history": ["Type 2 diabetes", "Chronic kidney disease (stage 1)", "Anemia", "Hypertension"],
        "meds": ["Clopidogrel 75mg", "Simvastatin 20mg", "Metoprolol succinate 100mg ER", "Nitroglycerin spray PRN"],
        "daysSinceDischarge": 2,
    },
    "monica": {
        "name": "Monica H.", "age": 76, "gender": "female",
        "dischargeDx": "SNF admission — rehabilitation and pain management",
        "history": ["Osteoporosis", "Obesity", "Hyperlipidemia", "Recent deconditioning after inpatient stay"],
        "meds": ["Meperidine 50mg", "Naproxen sodium 220mg"],
        "daysSinceDischarge": 4,
    },
    "traci": {
        "name": "Traci W.", "age": 65, "gender": "female",
        "dischargeDx": "SNF admission — type 2 diabetes stabilization and rehabilitation",
        "history": ["Type 2 diabetes", "Hyperglycemia", "Obesity", "Anemia"],
        "meds": ["Metformin (per facility MAR)", "Insulin sliding scale (per facility MAR)"],
        "daysSinceDischarge": 5,
    },
    "dick": {
        "name": "Dick L.", "age": 37, "gender": "male",
        "dischargeDx": "Follow-up after sepsis, septic shock & ARDS hospitalization",
        "history": ["Prediabetes", "Anemia", "Obesity", "Recent critical illness (sepsis/ARDS)"],
        "meds": ["None currently scheduled"],
        "daysSinceDischarge": 21,
    },
}


def patient_context_block(p):
    return (
        f"- Age/gender: {p['age']}, {p['gender']}\n"
        f"- Discharge diagnosis / encounter: {p['dischargeDx']}\n"
        f"- Relevant history: {', '.join(p['history'])}\n"
        f"- Current medications: {', '.join(p['meds'])}\n"
        f"- Days since discharge: {p['daysSinceDischarge']}"
    )


def format_transcript(transcript):
    return "\n".join(f"{t['speaker']}: {t['line']}" for t in transcript)


def call_claude(api_key, model, system, user, tool, max_tokens):
    """POST directly to the Messages API and return (tool_input, usage_dict)."""
    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
            "messages": [{"role": "user", "content": user}],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        print(f"ERROR {resp.status_code}: {resp.text}", file=sys.stderr)
        resp.raise_for_status()
    data = resp.json()
    tool_block = next(b for b in data["content"] if b["type"] == "tool_use")
    return tool_block["input"], data["usage"]


def accumulate(total, usage):
    total["input_tokens"] += usage["input_tokens"]
    total["output_tokens"] += usage["output_tokens"]


def estimate_cost(total, model):
    price = PRICING.get(model, PRICING["claude-haiku-4-5"])
    return (
        total["input_tokens"] / 1_000_000 * price["input"]
        + total["output_tokens"] / 1_000_000 * price["output"]
    )


def call_transcript_agent(api_key, model, patient, target_severity):
    system = (
        "You are simulating a realistic phone check-in conversation between a "
        "care team agent and a patient, 2-5 days after a hospital/SNF discharge "
        "or procedure. Ground every clinical detail in the patient context "
        "provided — do not invent conditions, medications, or history not given "
        "to you. Keep it to 6-10 exchanges. The AGENT should ask open-ended "
        "questions first, then condition-specific follow-up questions relevant "
        "to the patient's actual discharge diagnosis and comorbidities. You will "
        "be told a target scenario severity to simulate — make the patient's "
        "answers consistent with that severity level without being cartoonish."
    )
    user = (
        f"Patient context:\n{patient_context_block(patient)}\n\n"
        f"Target scenario severity to simulate: {target_severity}\n"
        "(red = urgent/emergent findings, orange = moderate concern warranting "
        "same-day callback, yellow = stable but labs/imaging would help confirm "
        "trajectory, green = recovering as expected)\n\n"
        "Generate the check-in transcript now."
    )
    tool = {
        "name": "record_transcript",
        "description": "Record the generated check-in call transcript",
        "input_schema": {
            "type": "object",
            "required": ["transcript"],
            "properties": {
                "transcript": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["speaker", "line"],
                        "properties": {
                            "speaker": {"type": "string", "enum": ["AGENT", "PT", "FAMILY"]},
                            "line": {"type": "string"},
                        },
                    },
                }
            },
        },
    }
    result, usage = call_claude(api_key, model, system, user, tool, max_tokens=1024)
    return result["transcript"], usage


def call_triage_agent(api_key, model, patient, transcript):
    system = (
        "You are a clinical triage assistant reviewing a post-discharge "
        "check-in transcript. Decide: red (urgent care referral, acute/emergent, "
        "do not wait), orange (physician callback today, moderate concern), "
        "yellow (labs/imaging recommended, stable but data would help), or "
        "green (routine follow-up, recovering as expected). Base your "
        "assessment only on what the PATIENT or FAMILY actually said, not the "
        "agent's questions. Pay attention to negation. Weigh findings against "
        "the patient's specific discharge diagnosis and comorbidities. When "
        "ambiguous, escalate to the more urgent tier. Cite the specific patient "
        "statements that drove your decision. This is decision support — a "
        "clinician reviews every output before action is taken."
    )
    user = (
        f"Patient context:\n{patient_context_block(patient)}\n\n"
        f"Check-in transcript:\n{format_transcript(transcript)}\n\n"
        "Determine the triage disposition using the record_triage_decision tool."
    )
    tool = {
        "name": "record_triage_decision",
        "description": "Record the triage disposition for a post-discharge check-in",
        "input_schema": {
            "type": "object",
            "required": ["severity", "label", "rationale", "flags"],
            "properties": {
                "severity": {"type": "string", "enum": ["red", "orange", "yellow", "green"]},
                "label": {"type": "string"},
                "rationale": {"type": "array", "items": {"type": "string"}},
                "flags": {"type": "array", "items": {"type": "string"}},
            },
        },
    }
    result, usage = call_claude(api_key, model, system, user, tool, max_tokens=768)
    return result, usage


def call_note_agent(api_key, model, patient, transcript, triage):
    system = (
        "You draft concise post-discharge check-in notes for clinician review, "
        "in Subjective / Assessment / Plan format. Write in clinical shorthand, "
        "not prose padding. Do not add clinical claims beyond what's in the "
        "transcript and triage decision provided. Always include a closing line "
        "noting this note was AI-drafted from a check-in call and requires "
        "clinician review before being finalized in the chart."
    )
    gender_initial = "F" if patient["gender"] == "female" else "M"
    user = (
        f"Patient: {patient['name']}, {patient['age']}{gender_initial}, "
        f"day {patient['daysSinceDischarge']} post-discharge\n"
        f"Discharge context: {patient['dischargeDx']}\n\n"
        f"Check-in transcript:\n{format_transcript(transcript)}\n\n"
        f"Triage decision: {triage['severity']} — {triage['label']}\n"
        f"Rationale: {'; '.join(triage['rationale'])}\n\n"
        "Draft the chart note using the record_chart_note tool."
    )
    tool = {
        "name": "record_chart_note",
        "description": "Record the drafted post-discharge check-in note",
        "input_schema": {
            "type": "object",
            "required": ["subjective", "assessment", "plan"],
            "properties": {
                "subjective": {"type": "string"},
                "assessment": {"type": "string"},
                "plan": {"type": "string"},
            },
        },
    }
    result, usage = call_claude(api_key, model, system, user, tool, max_tokens=768)
    return result, usage


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--patient", default="ariane", choices=DEMO_PATIENTS.keys())
    parser.add_argument("--agent", default="all", choices=["transcript", "triage", "note", "all"])
    parser.add_argument("--severity", default="red", choices=["red", "orange", "yellow", "green"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Create a .env file with:")
        print("  ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    patient = DEMO_PATIENTS[args.patient]
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    print(f"Model: {args.model} | Patient: {patient['name']} | Agent(s): {args.agent}\n")

    transcript = None
    triage = None

    if args.agent in ("transcript", "all"):
        transcript, usage = call_transcript_agent(api_key, args.model, patient, args.severity)
        accumulate(total_usage, usage)
        print("=== AGENT 1: CHECK-IN TRANSCRIPT ===")
        print(format_transcript(transcript))
        print()

    if args.agent in ("triage", "note") and transcript is None:
        print("No transcript generated this run (agent != transcript/all) — "
              "using a minimal placeholder line so this agent can still be tested standalone.\n")
        transcript = [{"speaker": "PT", "line": "I'm feeling about the same as when I left."}]

    if args.agent in ("triage", "all"):
        triage, usage = call_triage_agent(api_key, args.model, patient, transcript)
        accumulate(total_usage, usage)
        print("=== AGENT 2: TRIAGE DECISION ===")
        print(json.dumps(triage, indent=2))
        print()

    if args.agent in ("note", "all"):
        if triage is None:
            triage = {"severity": "yellow", "label": "Labs recommended", "rationale": ["placeholder — run --agent all for a real decision"]}
        note, usage = call_note_agent(api_key, args.model, patient, transcript, triage)
        accumulate(total_usage, usage)
        print("=== AGENT 3: DRAFTED CHART NOTE ===")
        print(json.dumps(note, indent=2))
        print()

    cost = estimate_cost(total_usage, args.model)
    print(f"--- Usage: {total_usage['input_tokens']} input / {total_usage['output_tokens']} output tokens "
          f"(~${cost:.5f} estimated) ---")


if __name__ == "__main__":
    main()
