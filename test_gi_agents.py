#!/usr/bin/env python3
"""
Eval script: run the 6 curated GI demo patients (gi_demo_patients.py) through
the REAL production Agent 2 (services.triage_service — TRIAGE_SYSTEM_PROMPT /
TRIAGE_TOOL / TRIAGE_MODEL) and Agent 3 (services.note_service —
NOTE_SYSTEM_PROMPT / NOTE_TOOL / NOTE_MODEL), then check whether Agent 2
lands on each patient's expected_disposition. Writes a JSON results file and
a self-contained HTML report you can open in a browser.

Why this isn't just "add the GI patients to test_agents.py": that script has
its own, older, standalone copy of the triage/note prompts (4-tier severity,
no FHIR grounding) that predates the real 6-tier, Sonnet-5, FHIR-grounded
implementation in services/triage_service.py. Running these patients through
THAT copy would test a stale prompt, not the agent that's actually live in
the app. So this script imports the real system prompts / tool schemas /
models directly from services/, guaranteeing zero drift from production.

It does NOT call triage_service.analyze_transcript_with_claude() /
note_service.draft_note_with_claude() directly, though, because those
functions load clinical context via load_fhir_record() (hardcoded to the 5
original synthetic-ambient-fhir-25 demo patients). The GI patients' context
lives in a different dataset (synthetic-gi-data/) with a different loader
(gi_context.py) -- but the actual prompts/schemas/model sent to Claude are
byte-identical to production. Same brain, different eyes.

Usage:
    python test_gi_agents.py                   # triage + note for all 6 (~12 calls)
    python test_gi_agents.py --triage-only      # just Agent 2 scoring (~6 calls, cheaper)
    python test_gi_agents.py --patient helen    # just one patient

Setup: same as test_agents.py -- needs ANTHROPIC_API_KEY in your .env file.

Output:
    gi_eval_results.json  -- raw results, for scripting/diffing across runs
    gi_eval_report.html   -- visual report, opened automatically when done
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser

from dotenv import load_dotenv
import requests

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gi_demo_patients import GI_DEMO_PATIENTS
from gi_context import build_gi_clinical_context, pt_id_from_source_file
from services.fhir_context import API_URL, API_VERSION
from services.triage_service import SEVERITY_ORDER, TRIAGE_MODEL, TRIAGE_SYSTEM_PROMPT, TRIAGE_TOOL
from services.note_service import NOTE_MODEL, NOTE_SYSTEM_PROMPT, NOTE_TOOL

PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
}

TIER_LABELS = {
    "emergency_department": "Emergency department",
    "urgent_care_same_day": "Urgent care (same day)",
    "clinician_callback_required": "Clinician callback required",
    "clinic_follow_up": "Clinic follow-up",
    "labs_imaging_needed": "Labs/imaging needed",
    "routine_follow_up": "Routine follow-up",
}
TIER_COLORS = {
    "emergency_department": "#dc2626",
    "urgent_care_same_day": "#ea580c",
    "clinician_callback_required": "#7c3aed",
    "clinic_follow_up": "#ca8a04",
    "labs_imaging_needed": "#0d9488",
    "routine_follow_up": "#16a34a",
}


def format_transcript(transcript):
    return "\n".join(f"{who}: {line}" for who, line in transcript)


def call_claude(api_key, model, system, user, tool, max_tokens):
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


def estimate_cost(total):
    cost = 0.0
    for model, tok in total.items():
        price = PRICING.get(model, PRICING["claude-haiku-4-5"])
        cost += tok["input_tokens"] / 1_000_000 * price["input"]
        cost += tok["output_tokens"] / 1_000_000 * price["output"]
    return cost


def analyze_gi_transcript(api_key, transcript, clinical_context):
    """Same TRIAGE_SYSTEM_PROMPT / TRIAGE_TOOL / TRIAGE_MODEL as the real
    Agent 2 (services.triage_service.analyze_transcript_with_claude), just
    with GI clinical context substituted for the FHIR loader that only knows
    the 5 original demo patients."""
    user = (
        f"Patient clinical context:\n{clinical_context}\n\n"
        f"Check-in transcript:\n{format_transcript(transcript)}\n\n"
        "Determine the triage disposition using the record_triage_decision tool."
    )
    return call_claude(api_key, TRIAGE_MODEL, TRIAGE_SYSTEM_PROMPT, user, TRIAGE_TOOL, max_tokens=1024)


def draft_gi_note(api_key, transcript, clinical_context, triage_result):
    """Same NOTE_SYSTEM_PROMPT / NOTE_TOOL / NOTE_MODEL as the real Agent 3
    (services.note_service.draft_note_with_claude)."""
    rationale_text = "\n".join(f"- {r}" for r in triage_result.get("rationale", []))
    user = (
        f"Patient clinical context:\n{clinical_context}\n\n"
        f"Check-in transcript:\n{format_transcript(transcript)}\n\n"
        f"Triage decision already made: {triage_result['severity']} — {triage_result['label']}\n"
        f"Rationale:\n{rationale_text}\n\n"
        "Draft the chart note using the record_chart_note tool."
    )
    return call_claude(api_key, NOTE_MODEL, NOTE_SYSTEM_PROMPT, user, NOTE_TOOL, max_tokens=768)


def run_eval(api_key, patients, triage_only):
    results = []
    total_usage = {TRIAGE_MODEL: {"input_tokens": 0, "output_tokens": 0}}
    if not triage_only:
        total_usage[NOTE_MODEL] = {"input_tokens": 0, "output_tokens": 0}

    for p in patients:
        pt_id = pt_id_from_source_file(p["source_file"])
        print(f"--- {p['name']} ({pt_id}) -> expecting {p['expected_disposition']} ---")
        context = build_gi_clinical_context(pt_id)

        triage, usage = analyze_gi_transcript(api_key, p["transcript"], context)
        accumulate(total_usage[TRIAGE_MODEL], usage)
        match = triage["severity"] == p["expected_disposition"]
        print(f"  Agent 2 -> {triage['severity']} ({'MATCH' if match else 'MISMATCH'})")

        note = None
        if not triage_only:
            note, usage = draft_gi_note(api_key, p["transcript"], context, triage)
            accumulate(total_usage[NOTE_MODEL], usage)
            print("  Agent 3 -> note drafted")

        results.append({
            "slug": p["slug"],
            "name": p["name"],
            "age": p["age"],
            "gender": p["gender"],
            "discharge_dx": p["discharge_dx"],
            "days_since_discharge": p["days_since_discharge"],
            "transcript": p["transcript"],
            "expected_disposition": p["expected_disposition"],
            "triage": triage,
            "match": match,
            "note": note,
        })

    return results, total_usage


def render_html(results, total_usage, out_path):
    n_match = sum(1 for r in results if r["match"])
    rows = []
    for r in results:
        actual = r["triage"]["severity"]
        expected = r["expected_disposition"]
        color_actual = TIER_COLORS.get(actual, "#666")
        color_expected = TIER_COLORS.get(expected, "#666")
        status_badge = (
            '<span class="status-pass">✓ MATCH</span>' if r["match"]
            else '<span class="status-fail">✗ MISMATCH</span>'
        )
        transcript_html = "".join(
            f'<div class="line {("pt" if who in ("PT","FAMILY") else "agent")}">'
            f'<span class="who">{who}</span>{line}</div>'
            for who, line in r["transcript"]
        )
        rationale_html = "".join(f"<li>{x}</li>" for x in r["triage"].get("rationale", []))
        flags_html = "".join(f"<li>{x}</li>" for x in r["triage"].get("flags", [])) or "<li>(none)</li>"

        note_html = ""
        if r["note"]:
            n = r["note"]
            action_items_html = "".join(f"<li>{x}</li>" for x in n.get("action_items", []))
            note_html = f"""
            <div class="note-block">
              <h4>Agent 3 — Drafted chart note</h4>
              <p><b>Subjective:</b> {n['subjective']}</p>
              <p><b>Assessment:</b> {n['assessment']}</p>
              <p><b>Plan:</b> {n['plan']}</p>
              <p><b>Action items:</b></p>
              <ul class="action-items">{action_items_html}</ul>
            </div>
            """

        rows.append(f"""
        <div class="card">
          <div class="card-header">
            <div>
              <h3>{r['name']} <span class="meta">{r['age']}{'F' if r['gender']=='female' else 'M'} · day {r['days_since_discharge']} · {r['discharge_dx']}</span></h3>
            </div>
            {status_badge}
          </div>
          <div class="tiers">
            <div class="tier-box">
              <div class="tier-label">Expected</div>
              <div class="tier-pill" style="background:{color_expected}">{TIER_LABELS.get(expected, expected)}</div>
            </div>
            <div class="tier-box">
              <div class="tier-label">Actual (Agent 2)</div>
              <div class="tier-pill" style="background:{color_actual}">{TIER_LABELS.get(actual, actual)}</div>
            </div>
          </div>
          <details>
            <summary>Transcript</summary>
            <div class="transcript">{transcript_html}</div>
          </details>
          <div class="rationale-block">
            <h4>Agent 2 rationale</h4>
            <ul>{rationale_html}</ul>
            <h4>Flags cited</h4>
            <ul>{flags_html}</ul>
          </div>
          {note_html}
        </div>
        """)

    cost = estimate_cost(total_usage)
    usage_lines = "".join(
        f"<li>{model}: {tok['input_tokens']} in / {tok['output_tokens']} out</li>"
        for model, tok in total_usage.items()
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>GI Triage Eval — {n_match}/{len(results)} correct</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f4f5f7; margin: 0; padding: 24px; color: #1a1a1a; }}
  .container {{ max-width: 880px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .summary {{ background: white; border-radius: 10px; padding: 16px 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .summary .score {{ font-size: 28px; font-weight: 700; }}
  .summary .cost {{ color: #666; font-size: 13px; margin-top: 6px; }}
  .card {{ background: white; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }}
  .card-header h3 {{ margin: 0; font-size: 16px; }}
  .meta {{ font-weight: 400; color: #666; font-size: 13px; display: block; margin-top: 2px; }}
  .status-pass {{ background: #dcfce7; color: #166534; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
  .status-fail {{ background: #fee2e2; color: #991b1b; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
  .tiers {{ display: flex; gap: 24px; margin-bottom: 12px; }}
  .tier-label {{ font-size: 11px; text-transform: uppercase; color: #888; margin-bottom: 4px; }}
  .tier-pill {{ color: white; padding: 5px 12px; border-radius: 6px; font-size: 13px; font-weight: 600; display: inline-block; }}
  details {{ margin: 10px 0; }}
  summary {{ cursor: pointer; color: #2563eb; font-size: 13px; }}
  .transcript {{ margin-top: 8px; border-left: 3px solid #e5e7eb; padding-left: 12px; }}
  .line {{ font-size: 13px; margin-bottom: 6px; }}
  .line .who {{ font-weight: 700; margin-right: 6px; }}
  .line.agent .who {{ color: #64748b; }}
  .line.pt .who {{ color: #0f172a; }}
  .rationale-block ul {{ margin: 4px 0; padding-left: 20px; font-size: 13px; }}
  .rationale-block h4 {{ margin: 10px 0 2px; font-size: 13px; color: #444; }}
  .note-block {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid #eee; }}
  .note-block h4 {{ margin: 0 0 8px; font-size: 13px; color: #444; }}
  .note-block p {{ font-size: 13px; margin: 6px 0; }}
  .action-items {{ font-size: 13px; padding-left: 20px; }}
  footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="container">
  <h1>GI Post-Procedure Triage — Agent 2/3 Eval</h1>
  <div class="summary">
    <div class="score">{n_match} / {len(results)} correct</div>
    <div class="cost">Model(s): {', '.join(total_usage.keys())} · Estimated cost: ${cost:.5f}</div>
    <ul class="cost">{usage_lines}</ul>
  </div>
  {''.join(rows)}
  <footer>AI-drafted eval output — clinician review required before any real use. Generated by test_gi_agents.py.</footer>
</div>
</body>
</html>
"""
    with open(out_path, "w") as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--patient", choices=[p["slug"] for p in GI_DEMO_PATIENTS], help="run just one patient (default: all 6)")
    parser.add_argument("--triage-only", action="store_true", help="skip Agent 3 (note drafting) — cheaper, just scores Agent 2")
    parser.add_argument("--no-open", action="store_true", help="don't auto-open the HTML report when done")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Create a .env file with:")
        print("  ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    patients = GI_DEMO_PATIENTS
    if args.patient:
        patients = [p for p in GI_DEMO_PATIENTS if p["slug"] == args.patient]

    results, total_usage = run_eval(api_key, patients, args.triage_only)

    with open("gi_eval_results.json", "w") as f:
        json.dump(results, f, indent=2)

    render_html(results, total_usage, "gi_eval_report.html")

    n_match = sum(1 for r in results if r["match"])
    cost = estimate_cost(total_usage)
    print(f"\n{n_match}/{len(results)} correct. ~${cost:.5f} estimated.")
    print("Wrote gi_eval_results.json and gi_eval_report.html")

    if not args.no_open:
        webbrowser.open(f"file://{os.path.abspath('gi_eval_report.html')}")


if __name__ == "__main__":
    main()
