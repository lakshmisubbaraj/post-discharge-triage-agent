"""Six GI demo patients — one per disposition tier.

Selected from synthetic-gi-data/procedure-results/*.json by reading each file's
"post_procedure_followup" contact stream (ground_truth_disposition + urgency),
then mapped onto the 6-way disposition taxonomy in services/triage_service.py:

  1. emergency_department        <- PT-009  (escalate / emergent)
  2. urgent_care_same_day        <- PT-016  (escalate / urgent)
  3. clinician_callback_required <- PT-041  (guardrail: 88yo w/ dementia,
                                             unreliable self-report — dataset has
                                             no native "insufficient info" label,
                                             so transcript exercises the guardrail)
  4. clinic_follow_up            <- PT-004  (schedule / routine — dropped biopsy result)
  5. labs_imaging_needed         <- PT-034  (schedule / routine — dropped follow-up LFTs)
  6. routine_follow_up           <- PT-025  (reassure_selfcare — benign-only recovery)

Same shape as DEMO_PATIENTS in seed_data.py, plus `source_file` and
`expected_disposition` for scoring.
"""

GI_DEMO_PATIENTS = [
    {
        # --- 1. emergency_department ---
        # PT-009 C3 (day 6, escalate/emergent): post-polypectomy bleed —
        # bright red blood x2 with clots, presyncopal on standing.
        "slug": "helen",
        "name": "Helen R.",
        "age": 76,
        "gender": "female",
        "source_file": "synthetic-gi-data/procedure-results/PT-009.json",
        "expected_disposition": "emergency_department",
        "discharge_dx": "Screening colonoscopy with cold snare polypectomy x2 (6mm ascending, 4mm sigmoid)",
        "history": ["Essential hypertension", "Osteopenia"],
        "meds": ["Losartan 50mg", "Calcium carbonate / vitamin D"],
        "days_since_discharge": 6,
        "transcript": [
            ["AGENT", "Hi Helen, this is your GI care team checking in after your colonoscopy last week. How are you doing?"],
            ["PT", "I was doing fine until this afternoon, but something scary happened and I was about to call you."],
            ["AGENT", "I'm glad you picked up. Tell me what's going on."],
            ["PT", "I passed a big amount of bright red blood into the toilet — twice now, a couple hours apart. There were clots in it too."],
            ["AGENT", "Thank you for telling me. How are you feeling in yourself — any dizziness or weakness?"],
            ["PT", "Yes, when I stand up I feel dizzy and a bit sweaty. I've been sitting down because I'm afraid I'll fall."],
            ["AGENT", "Any belly pain or fever?"],
            ["PT", "No pain really, just the bleeding and feeling woozy."],
            ["AGENT", "Helen, bleeding like this after polyp removal with dizziness needs to be evaluated in the emergency department right away. Is someone home who can take you?"],
            ["PT", "My daughter is next door. I'll call her now."],
        ],
    },
    {
        # --- 2. urgent_care_same_day ---
        # PT-016 C3 (day 3, escalate/urgent): fever 101.5, productive cough,
        # exertional dyspnea starting the day after EGD — possible aspiration
        # pneumonia; same-day in-person evaluation.
        "slug": "miguel",
        "name": "Miguel F.",
        "age": 38,
        "gender": "male",
        "source_file": "synthetic-gi-data/procedure-results/PT-016.json",
        "expected_disposition": "urgent_care_same_day",
        "discharge_dx": "EGD with duodenal biopsy for suspected celiac disease (confirmatory biopsy)",
        "history": ["Suspected celiac disease (tTG-IgA >10x ULN)", "Iron deficiency anemia"],
        "meds": ["Multivitamin with iron", "Fish oil (OTC)"],
        "days_since_discharge": 3,
        "transcript": [
            ["AGENT", "Hi Miguel, checking in after your upper endoscopy earlier this week. How's the recovery going?"],
            ["PT", "Honestly, not great. Since yesterday I've had a fever — I measured 101.5 last night."],
            ["AGENT", "I'm sorry to hear that. Any other symptoms with the fever?"],
            ["PT", "I've got a cough with some junk coming up, and I get short of breath just walking to the kitchen. It started the day after the scope."],
            ["AGENT", "Any chest pain, or trouble swallowing?"],
            ["PT", "No chest pain exactly, it's more the coughing and being winded. Swallowing is fine."],
            ["AGENT", "Are you able to keep fluids down and eat?"],
            ["PT", "Yeah, eating and drinking okay, just tired and feverish."],
            ["AGENT", "Given the timing right after sedation, this needs an in-person exam and likely a chest X-ray today — same-day at urgent care or our clinic, not something to wait on."],
            ["PT", "Okay, I can get a ride over this afternoon."],
        ],
    },
    {
        # --- 3. clinician_callback_required (guardrail: insufficient information) ---
        # PT-041: 88yo with moderate dementia, AFib on apixaban, 4cm colon mass
        # biopsied. Contacts in the dataset are relayed by her daughter/POA; this
        # transcript models the check-in reaching the patient directly — vague,
        # inconsistent answers -> AI cannot responsibly assess -> callback.
        "slug": "harriet",
        "name": "Harriet B.",
        "age": 88,
        "gender": "female",
        "source_file": "synthetic-gi-data/procedure-results/PT-041.json",
        "expected_disposition": "clinician_callback_required",
        "discharge_dx": "Colonoscopy with biopsy of 4cm ascending colon mass (workup of iron-deficiency anemia + weight loss)",
        "history": ["Atrial fibrillation", "Moderate dementia", "Frailty", "Anemia"],
        "meds": ["Apixaban 2.5mg", "Donepezil 10mg", "Mirtazapine 15mg"],
        "days_since_discharge": 5,
        "transcript": [
            ["AGENT", "Hi Ms. Beaumont, this is your care team calling to see how you're feeling after your colonoscopy last week."],
            ["PT", "My colonoscopy? Oh... I don't think I've had one of those, dear."],
            ["AGENT", "It was about five days ago, at the hospital. How has your tummy been feeling — any pain or bleeding?"],
            ["PT", "I couldn't really say. The girls here handle all of that."],
            ["AGENT", "Have you noticed any blood when you use the bathroom, or felt dizzy?"],
            ["PT", "I don't remember. Maybe? What was it you were asking about?"],
            ["AGENT", "That's alright. Are you still taking your blood thinner, the apixaban?"],
            ["PT", "The nurse gives me my pills. I don't know their names, I just take what's in the little cup."],
            ["AGENT", "Is your daughter or one of the nurses nearby who could join the call?"],
            ["PT", "She visits on Sundays, I think. Or was that last week?"],
            ["AGENT", "No problem, Ms. Beaumont. We'll have one of our nurses call your daughter and the facility directly to check on you properly."],
        ],
    },
    {
        # --- 4. clinic_follow_up ---
        # PT-004 C3 (day 12, schedule/routine): polypectomy pathology never
        # communicated — close the loop with a near-term clinic contact/visit.
        "slug": "robert",
        "name": "Robert N.",
        "age": 67,
        "gender": "male",
        "source_file": "synthetic-gi-data/procedure-results/PT-004.json",
        "expected_disposition": "clinic_follow_up",
        "discharge_dx": "Surveillance colonoscopy (prior sessile serrated lesion) with cold snare polypectomy (5mm transverse)",
        "history": ["Prior sessile serrated lesion (2023)", "Essential hypertension"],
        "meds": ["Amlodipine 5mg", "Fish oil (OTC)"],
        "days_since_discharge": 12,
        "transcript": [
            ["AGENT", "Hi Robert, following up after your surveillance colonoscopy. How have you been feeling?"],
            ["PT", "Physically I feel completely fine — no pain, no bleeding, back to normal since a couple days after."],
            ["AGENT", "That's good to hear. Anything on your mind about the procedure?"],
            ["PT", "Actually yes. It's been almost two weeks and nobody ever called me about the biopsy from the polyp they took out. I'm starting to worry something got missed."],
            ["AGENT", "I understand — you should absolutely have heard by now. Any new symptoms at all — bleeding, belly pain, change in bowel habits?"],
            ["PT", "No, nothing. I went back to my fish oil like they said and everything's been normal."],
            ["AGENT", "Good. Your recovery sounds on track; the open item is the pathology result and your next surveillance interval."],
            ["PT", "So what happens now?"],
            ["AGENT", "I'll flag this for the GI clinic to get you seen and go over the result and your follow-up plan in the next few days."],
        ],
    },
    {
        # --- 5. labs_imaging_needed ---
        # PT-034 C4 (day 12, schedule/routine): stable post-ERCP, but promised
        # follow-up LFTs never obtained — objective data needed to confirm the
        # obstructive pattern resolved (plus pending cholecystectomy referral).
        "slug": "deshawn",
        "name": "Deshawn W.",
        "age": 41,
        "gender": "male",
        "source_file": "synthetic-gi-data/procedure-results/PT-034.json",
        "expected_disposition": "labs_imaging_needed",
        "discharge_dx": "Laparoscopic-assisted transgastric ERCP with sphincterotomy and CBD stone extraction",
        "history": ["Choledocholithiasis", "Roux-en-Y gastric bypass", "Vitamin B12 deficiency"],
        "meds": ["Bariatric multivitamin", "Vitamin B12 injections"],
        "days_since_discharge": 12,
        "transcript": [
            ["AGENT", "Hi Deshawn, checking in after your bile duct procedure. How's the recovery been?"],
            ["PT", "Pretty smooth overall. The little cuts on my belly have healed up, no more soreness."],
            ["AGENT", "Any belly pain, fever, or yellowing of your skin or eyes since you've been home?"],
            ["PT", "None of that. Eating normally again, energy's good."],
            ["AGENT", "Excellent. Any questions or loose ends from your discharge?"],
            ["PT", "One thing — I was told someone would call me about follow-up liver blood tests and about setting up the gallbladder surgery, and it's been almost two weeks with no call."],
            ["AGENT", "Thanks for flagging that. You're feeling well, which is reassuring, but we do want those liver labs to confirm everything cleared after the stone removal."],
            ["PT", "Sure, I can come in for a blood draw whenever."],
            ["AGENT", "I'll put in the lab order and make sure the surgery referral gets scheduled — you should hear from us within a couple of days."],
        ],
    },
    {
        # --- 6. routine_follow_up ---
        # PT-025 (benign-only patient): expected post-colonoscopy course, one
        # self-resolved trace of spotting — recovering as expected, no new action.
        "slug": "yolanda",
        "name": "Yolanda R.",
        "age": 57,
        "gender": "female",
        "source_file": "synthetic-gi-data/procedure-results/PT-025.json",
        "expected_disposition": "routine_follow_up",
        "discharge_dx": "First screening colonoscopy with cold snare polypectomy x2 (5mm ascending, 3mm descending)",
        "history": ["Osteoarthritis"],
        "meds": ["Aspirin 81mg PRN", "Ibuprofen 200mg PRN"],
        "days_since_discharge": 6,
        "transcript": [
            ["AGENT", "Hi Yolanda, this is your GI care team checking in after your screening colonoscopy. How are you feeling?"],
            ["PT", "I'm feeling good! The bloating and scratchy throat from the first day or two are long gone."],
            ["AGENT", "Great. Any bleeding, belly pain, or fevers since the procedure?"],
            ["PT", "There was one tiny streak of pink on the toilet paper a few days ago, but nothing since, and no pain at all."],
            ["AGENT", "A single small streak after a small polyp removal is common and nothing to worry about since it hasn't come back. Are you back to your usual routine?"],
            ["PT", "Completely. Back to my walks, eating normally, took my ibuprofen for my knees like they said I could."],
            ["AGENT", "Perfect. The team will call with your polyp results and your next screening interval — otherwise you're recovering right on track."],
            ["PT", "Wonderful, thank you for checking in."],
        ],
    },
]
