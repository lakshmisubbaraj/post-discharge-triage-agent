"""Clinical context loader for the synthetic-gi-data dataset, used to
evaluate Agent 2 / Agent 3 against the 6 GI demo patients in
gi_demo_patients.py.

Reuses the real, production FHIR-context builder
(services.fhir_context.build_fhir_context_block) for the pre-procedure
referral data in synthetic-gi-data/patients/PT-NNN.json, since that file is
in the same patient_context/encounter_fhir shape as the
synthetic-ambient-fhir-25 dataset Agent 2 already knows how to read.

On top of that, this adds a procedure-result block sourced from
synthetic-gi-data/procedure-results/PT-NNN.json — the post-procedure report,
pathology status, discharge disposition, and any still-open follow-up items.
That's the part of the record that doesn't exist in the pre-procedure
referral, and it's exactly the context several of the 6 demo patients'
complaints hinge on (e.g. "nobody called me about my biopsy" only makes
sense to evaluate against a chart that actually shows a pending pathology
result and an open follow-up item for it).

Deliberately NOT included: the post_procedure_followup.contacts list in the
procedure-result file. Those entries carry ground_truth_disposition /
expected_action / rationale labels — the answer key used for scoring — and
including them in the prompt would let the agent read the answer instead of
reasoning to it.
"""
from __future__ import annotations

import json
import os

from services.fhir_context import build_fhir_context_block

GI_DATA_DIR = os.path.join(os.path.dirname(__file__), "synthetic-gi-data")


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _procedure_result_block(pt_id: str) -> str:
    path = os.path.join(GI_DATA_DIR, "procedure-results", f"{pt_id}.json")
    d = _load_json(path)

    proc = d.get("procedure", {})
    findings = d.get("findings", [])
    pathology = d.get("pathology", {})
    followup = d.get("followup", [])
    planted = d.get("planted_followup_items", [])

    findings_text = "; ".join(
        f"{f.get('finding')} ({f.get('location')})" for f in findings
    ) or "none recorded"

    if pathology.get("status") == "pending":
        path_text = f"pending (expected back {pathology.get('expected_back', 'unknown date')})"
    else:
        path_text = (
            f"{pathology.get('status', 'unknown')} — "
            f"{pathology.get('report') or pathology.get('diagnosis') or 'no report text on file'}"
        )

    lines = [
        f"Procedure performed: {proc.get('name', 'unknown')} on {proc.get('date', 'unknown date')}",
        f"Indication: {proc.get('indication', 'not recorded')}",
        f"Key findings: {findings_text}",
        f"Pathology status: {path_text}",
        f"Discharge disposition/instructions given at the time: {d.get('disposition', 'not recorded')}",
    ]

    open_recs = [f for f in followup if f.get("status") == "open"]
    open_planted = [p for p in planted if p.get("status") == "open"]
    if open_recs or open_planted:
        lines.append("Still-open follow-up items on this patient's chart:")
        for item in open_recs:
            lines.append(
                f"  - {item.get('recommendation')} "
                f"(timeframe: {item.get('timeframe')}, owner: {item.get('owner')})"
            )
        for item in open_planted:
            lines.append(
                f"  - {item.get('description')} "
                f"(urgency: {item.get('urgency')}, expected action: {item.get('expected_action')})"
            )
    else:
        lines.append("No open follow-up items recorded on the chart.")

    return "\n".join(lines)


def build_gi_clinical_context(pt_id: str) -> str:
    """pt_id like 'PT-009' (matches the filename stem used in both
    synthetic-gi-data/patients/ and synthetic-gi-data/procedure-results/).

    Returns the full clinical context block: the real FHIR context (built
    from the pre-procedure referral, via the same function Agent 2 uses in
    production) plus a procedure-result summary (the post-procedure part),
    ready to feed to Agent 2 / Agent 3.
    """
    patient_path = os.path.join(GI_DATA_DIR, "patients", f"{pt_id}.json")
    referral = _load_json(patient_path)
    fhir_block = build_fhir_context_block(
        referral["patient_context"], referral["encounter_fhir"]
    )
    procedure_block = _procedure_result_block(pt_id)
    return fhir_block + "\n\nPost-procedure result:\n" + procedure_block


def pt_id_from_source_file(source_file: str) -> str:
    """Extract 'PT-009' from 'synthetic-gi-data/procedure-results/PT-009.json'."""
    return os.path.splitext(os.path.basename(source_file))[0]
