"""Populate the database with the 5 FHIR-grounded demo patients.

These are the same patients that live in the frontend `PATIENTS` array and in
`test_agents.py` (DEMO_PATIENTS) — keep all three in sync per CLAUDE.md. All
demographics/conditions/meds come from the synthetic `synthetic-ambient-fhir-25`
dataset; only the transcripts are hand-written.

Run:
    python seed_data.py          # drops + recreates tables, inserts patients
"""
from app import create_app
from extensions import db
from models import Patient, Encounter, CheckIn, TriageResult
from services import triage_service

DEMO_PATIENTS = [
    {
        "slug": "ariane",
        "name": "Ariane R.",
        "age": 81,
        "gender": "female",
        "discharge_dx": "COVID-19 with pneumonia & hypoxemia (hospital admission for isolation)",
        "history": ["Prior MI", "Hypertension", "Hyperlipidemia", "Metabolic syndrome"],
        "meds": ["Aspirin 81mg", "Atenolol 50mg", "Rosuvastatin 40mg", "Lisinopril 20mg"],
        "days_since_discharge": 3,
        "transcript": [
            ["AGENT", "Hi Ariane, this is your care team calling to check in after your hospital stay for COVID pneumonia. How are you feeling today?"],
            ["PT", "Oh, honestly not great. I thought I'd be turning a corner by now."],
            ["AGENT", "I'm sorry to hear that. Can you tell me more about your breathing?"],
            ["PT", "It's gotten harder, not easier. Yesterday I could walk to the kitchen fine, today I'm short of breath just getting out of bed. My lips felt a little bluish this morning too, my daughter pointed it out."],
            ["AGENT", "That's important to know. Any chest pain or tightness?"],
            ["PT", "A little tightness when I take a deep breath, yes."],
            ["AGENT", "Are you keeping up with your fluids and medications?"],
            ["PT", "Trying to, but I feel too winded to eat much."],
            ["AGENT", "Okay, thank you for telling me all this, Ariane. I want to make sure you're seen right away given the breathing changes."],
        ],
    },
    {
        "slug": "latoyia",
        "name": "Latoyia W.",
        "age": 75,
        "gender": "female",
        "discharge_dx": "SNF admission after hospitalization (Type 2 diabetes, CKD stage 1, anemia)",
        "history": ["Type 2 diabetes", "Chronic kidney disease (stage 1)", "Anemia", "Hypertension"],
        "meds": ["Clopidogrel 75mg", "Simvastatin 20mg", "Metoprolol succinate 100mg ER", "Nitroglycerin spray PRN"],
        "days_since_discharge": 2,
        "transcript": [
            ["AGENT", "Hi Latoyia, checking in now that you're settled at the skilled nursing facility. How's it going?"],
            ["PT", "It's alright. The staff are nice. I'm just tired a lot."],
            ["AGENT", "That's common early on. How's your appetite and thirst been?"],
            ["PT", "Actually still pretty thirsty, and I've noticed some swelling in my ankles the last couple days."],
            ["AGENT", "Any chest pain, or have you needed to use your nitroglycerin spray?"],
            ["PT", "No, haven't needed to use that, and nothing like that."],
            ["AGENT", "Good. And how's your blood sugar been tracking with the nursing staff?"],
            ["PT", "They said it's been a bit high the last two mornings, low 200s."],
            ["AGENT", "Understood — the ankle swelling and blood sugar trend are worth a closer look given your kidney history."],
        ],
    },
    {
        "slug": "monica",
        "name": "Monica H.",
        "age": 76,
        "gender": "female",
        "discharge_dx": "SNF admission — rehabilitation and pain management",
        "history": ["Osteoporosis", "Obesity", "Hyperlipidemia", "Recent deconditioning after inpatient stay"],
        "meds": ["Meperidine 50mg", "Naproxen sodium 220mg"],
        "days_since_discharge": 4,
        "transcript": [
            ["AGENT", "Hi Monica, wanted to check in on how rehab is going and how your pain is being managed."],
            ["PT", "Physical therapy is going okay, slow but okay. The pain is the real problem though."],
            ["AGENT", "Tell me more about that — where is the pain and how would you rate it?"],
            ["PT", "It's my lower back and hip, mostly. I'd say it's a 7 out of 10 most of the day, even with the medication."],
            ["AGENT", "Has the pain changed in character, or is it the same as when you were admitted?"],
            ["PT", "Same kind of pain, just not really controlled. I mentioned it to the nurse but I don't think anything's changed with my meds."],
            ["AGENT", "No fevers, no new numbness or weakness in your legs?"],
            ["PT", "No, nothing like that. Just poorly controlled pain, it's wearing me down."],
            ["AGENT", "That makes sense — let's get your physician to take another look at your pain regimen."],
        ],
    },
    {
        "slug": "traci",
        "name": "Traci W.",
        "age": 65,
        "gender": "female",
        "discharge_dx": "SNF admission — type 2 diabetes stabilization and rehabilitation",
        "history": ["Type 2 diabetes", "Hyperglycemia", "Obesity", "Anemia"],
        "meds": ["Metformin (per facility MAR)", "Insulin sliding scale (per facility MAR)"],
        "days_since_discharge": 5,
        "transcript": [
            ["AGENT", "Hi Traci, checking in on how things are going with your diabetes stabilization."],
            ["PT", "Actually really good! The blurry vision is gone and my numbers have been steady, mostly 120s to 140s on the finger sticks."],
            ["AGENT", "That's great to hear. How's the tingling in your hands and feet that you mentioned before?"],
            ["PT", "Much better, almost back to normal. I've been walking a bit more each day too."],
            ["AGENT", "Any dizziness, chest pain, shortness of breath, or new concerns?"],
            ["PT", "No, none of that. I'm feeling like myself again, honestly."],
            ["AGENT", "Wonderful. Sounds like you're recovering right on track."],
        ],
    },
    {
        "slug": "dick",
        "name": "Dick L.",
        "age": 37,
        "gender": "male",
        "discharge_dx": "Follow-up after sepsis, septic shock & ARDS hospitalization",
        "history": ["Prediabetes", "Anemia", "Obesity", "Recent critical illness (sepsis/ARDS)"],
        "meds": ["None currently scheduled"],
        "days_since_discharge": 21,
        "transcript": [
            ["AGENT", "Hi Dick, it's been a few weeks since your sepsis hospitalization — how have you been feeling?"],
            ["PT", "Overall pretty good, actually. My energy's coming back and I can walk further each day."],
            ["AGENT", "That's great progress. Any fevers, chills, or new shortness of breath?"],
            ["PT", "No, nothing like that. I do still get more tired than I expected some days, but nothing severe."],
            ["AGENT", "And how about the anemia we were tracking — any dizziness, or has anyone mentioned you looking pale?"],
            ["PT", "A little — my sister said I still look pale sometimes, but I feel steady on my feet."],
            ["AGENT", "Good to know. Given everything you went through, it's worth confirming your labs are trending the right way before your next visit."],
        ],
    },
]


def seed():
    """Drop, recreate, and populate all tables. Idempotent for demo use."""
    db.drop_all()
    db.create_all()

    for row in DEMO_PATIENTS:
        patient = Patient(
            slug=row["slug"],
            name=row["name"],
            age=row["age"],
            gender=row["gender"],
        )
        encounter = Encounter(
            discharge_dx=row["discharge_dx"],
            history=row["history"],
            meds=row["meds"],
            days_since_discharge=row["days_since_discharge"],
        )
        check_in = CheckIn(transcript=row["transcript"])

        # Run the (stubbed) triage + note agents at seed time and persist the
        # result, mirroring how the backend would precompute the queue.
        analysis = triage_service.analyze_transcript(row["transcript"])
        note = triage_service.draft_note(
            patient.to_dict(), encounter.to_dict(), analysis
        )
        result = TriageResult(
            severity=analysis["severity"],
            label=analysis["label"],
            rationale=analysis["rationale"],
            flags=analysis["flags"],
            note=note,
        )

        check_in.triage_result = result
        encounter.check_ins.append(check_in)
        patient.encounters.append(encounter)
        db.session.add(patient)

    db.session.commit()
    print(f"Seeded {len(DEMO_PATIENTS)} patients.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed()
