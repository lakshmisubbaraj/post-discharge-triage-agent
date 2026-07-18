# synthetic-gi-dataset

Synthetic GI-procedure encounter records in the style of the Abridge
`synthetic-ambient-fhir-25` dataset, generated for the UCSF GI hackathon suite
(PreChart / FollowThrough / NapGuard / LeftBehind).

**Everything is synthetic and fictional — no real patients, no PHI.**

## Status
**60 of 60** patients complete — pre-procedure referral **and** post-procedure result layers.

## What's here
| File | What it is |
|------|------------|
| `patients/PT-NNN.json` | One record per GI-procedure referral (60) |
| `synthetic-gi.jsonl` | All referral records, one JSON object per line (Abridge shape) |
| `summary.json` | Index (dates, visit titles, resource + discrepancy counts) |
| `eval-labels.json` | **Reconciliation benchmark** — every planted discrepancy, labeled |
| `format-template.json` | The reference referral record the generator matched |
| `procedure-results/PT-NNN.json` | **Post-procedure result** per patient (60) — endoscopy report, pathology, complications, follow-up, patient stream |
| `procedure-results.jsonl` | All 60 procedure results, one JSON object per line |
| `procedure-result-template.json` | The reference procedure-result record the generator matched |
| `procedure-results-summary.json` | Index of procedures, pathology status, follow-up + triage counts |
| `followup-labels.json` | **Loop-closure benchmark** — every droppable follow-up item, labeled |
| `triage-labels.json` | **Triage benchmark** — post-procedure patient contacts, labeled disposition/urgency |

## Record shape
Each record mirrors the Abridge format plus two additions built for PreChart:
- `patient_context.prior_notes` — prior GI/clinic notes (the pre-charting substrate).
- `metadata.planted_discrepancies` — **labeled ground truth**, so reconciliation
  can be *scored*, not just demoed.

Each record contains: `transcript` (speaker-labeled ambient GI-clinic conversation),
`note` (SOAP), `after_visit_summary`, `encounter_fhir.related_resources` (FHIR R4
with SNOMED/LOINC/RxNorm codes — including the deliberately conflicting chart state),
and `patient_context` (Patient + longitudinal_summary + prior_notes).

**Vitals:** every office encounter carries a full LOINC-coded vital-signs set —
height, weight, BMI, temperature, heart rate, respiratory rate, blood pressure, and
SpO2 — as FHIR `vital-signs` Observations, echoed in the note's Objective section and
spoken in a transcript rooming line (chart-vs-ambient substrate for PreChart). Values
track the presentation: obesity/OSA → high BMI + low SpO2 (the NapGuard anesthesia
flag), GI bleed/sepsis → tachycardia/fever/orthostasis, documented weight loss → a
lower weight plus a dated prior-weight Observation so the loss is objective.

## The benchmark (`eval-labels.json`)
**367 labeled discrepancies across 60 patients:**

- **by expected state** — CONFIRMED 191, NEW 105, CONTRADICTED 56, UNADDRESSED 15
- **by source of error** — chart 131, patient 34, none (routine/confirmed) 202

Each label: `{id, type, source_of_error (chart|patient|none), chart_state,
spoken_truth, significance, expected_state, note}`. Key design point: **the chart
is not assumed to be the truth** — 131 discrepancies are the chart being wrong/stale,
34 are the patient misremembering. Reconciliation is scored on catching both,
without firing on the routine/confirmed controls.

## Cohort mix
~20 routine screening/surveillance, ~22 moderate-complexity (anticoagulation,
anesthesia-risk, records-hunt flags), ~18 high-acuity/specialized (urgent ERCP for
cholangitis, EUS-FNA oncology, pregnancy ERCP, POEM/ESD/PEG, post-procedure
escalations). Built-in clinical traps for parser robustness: negation,
narrative-vs-pharmacy med conflicts, hedged collateral history, terminology traps
("blood thinner" = fish oil), allergy discrepancies, mislabeled urgency,
suspected-not-confirmed diagnoses, family-history attribution, and
prophylactic-vs-therapeutic dosing.

## Procedure results (post-procedure layer)
The referral records stop at the pre-procedure consult. `procedure-results/`
closes the loop: for each of the 60 patients, the **result of the procedure they
were referred for** (or, for the post-procedure referrals, the procedure they were
following up from), across **35 distinct procedure types** (screening/surveillance
colonoscopy, EGD ± biopsy/dilation/banding, ERCP, EUS-FNA, POEM, ESD, EMR, PEG,
and more). Each record generated from its own referral, then adversarially
verified against that chart for clinical consistency (procedure matches
indication, pathology internally coherent, follow-up items correctly derived,
no contradiction with the chart), and revised.

Each result contains:
- `procedure_report` — narrative endoscopy/procedure note (sedation, prep, extent, findings)
- `findings` / `specimens` / `pathology` — structured; pathology realistically **pending vs. final**
- `complications`, `disposition`, `followup`
- `planted_followup_items` — **labeled droppable follow-up** (see below)
- `post_procedure_followup` — the **patient-reported stream** (see below)

### `followup-labels.json` — loop-closure benchmark
**310 labeled follow-up items** — everything a post-procedure workflow could drop,
typed against a fixed taxonomy (`pending_pathology`, `critical_result`,
`surveillance_interval`, `complication_watch`, `med_resume`, `med_reconciliation`,
`referral`, `care_coordination`, `lab_followup`, `known_lesion_followup`,
`repeat_procedure`, `incidental_finding`, `record_correction`) with `urgency`
(routine 180 / urgent 92 / critical 38) and `expected_action`. Scores whether an
agent surfaces the pending path, sets the interval, resumes the held anticoagulant,
and escalates the critical result — without over-firing.

### `triage-labels.json` — triage benchmark
**201 post-procedure patient contacts** (day 0–14) across automated check-in,
portal message, and triage call — the inbound stream the **LeftBehind** product
triages. Each contact is the patient's own words plus ground truth:

- **by type** — question 98, complaint 77, complication 26
- **by disposition** — reassure_selfcare 82, answer_info 81, escalate 27, schedule 11
- **by urgency** — routine 171, urgent 9, emergent 21 · **27 red-flags** · 119 link to a follow-up item

Complications are keyed to each procedure's real risk (post-polypectomy/EMR/ESD
delayed bleeding, post-ERCP pancreatitis, PEG-site infection, dilation/POEM leak,
post-band ulcer bleed). Deliberate **triage traps** are planted:
benign-sounding-but-dangerous (mild chest pressure after POEM = possible leak →
escalate), dangerous-sounding-but-benign (a little pink on the tissue after a clean
exam → reassure), the dropped-ball complaint ("no one ever called me about my
biopsy" → links to a pending-pathology item), and med-timing confusion (anticoagulant
restarted too early). Scores whether triage reassures the benign, answers the
answerable, and escalates the true complications.
