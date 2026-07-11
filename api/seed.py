"""Seed demo data for the live website. Run with: python -m api.seed"""

import asyncio
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()  # MUST run before any module-level os.environ[...] reads below

from api.database import async_session, init_database
from api.models import Appointment, Bill, Doctor, DischargeFollowup, LabReport, Patient, Prescription

DOCTORS = [
    {"name": "Dr. Priya Sharma",      "department": "general",          "qualification": "MBBS, MD",                   "available_days": ["monday", "wednesday", "friday"]},
    {"name": "Dr. Rajesh Patel",      "department": "cardiology",       "qualification": "MBBS, DM Cardiology",        "available_days": ["tuesday", "thursday"]},
    {"name": "Dr. Anjali Deshmukh",   "department": "ortho",            "qualification": "MBBS, MS Ortho",             "available_days": ["monday", "thursday", "saturday"]},
    {"name": "Dr. Vikram Singh",      "department": "pediatrics",       "qualification": "MBBS, DCH",                  "available_days": ["wednesday", "friday"]},
    {"name": "Dr. Meera Joshi",       "department": "dermatology",      "qualification": "MBBS, MD Dermatology",       "available_days": ["tuesday", "saturday"]},
    {"name": "Dr. Kavita Nair",       "department": "gynecology",       "qualification": "MBBS, MS Gynecology",        "available_days": ["monday", "wednesday", "saturday"]},
    {"name": "Dr. Suresh Menon",      "department": "neurology",        "qualification": "MBBS, DM Neurology",         "available_days": ["tuesday", "friday"]},
    {"name": "Dr. Anita Saxena",      "department": "ent",              "qualification": "MBBS, MS ENT",               "available_days": ["monday", "thursday"]},
    {"name": "Dr. Ravi Kumar",        "department": "ophthalmology",    "qualification": "MBBS, MS Ophthalmology",     "available_days": ["wednesday", "saturday"]},
    {"name": "Dr. Pooja Mehta",       "department": "psychiatry",       "qualification": "MBBS, MD Psychiatry",        "available_days": ["tuesday", "thursday"]},
    {"name": "Dr. Amit Chandra",      "department": "oncology",         "qualification": "MBBS, DM Oncology",          "available_days": ["monday", "wednesday"]},
    {"name": "Dr. Sundar Rao",        "department": "nephrology",       "qualification": "MBBS, DM Nephrology",        "available_days": ["tuesday", "friday"]},
    {"name": "Dr. Preethi Krishnan",  "department": "endocrinology",    "qualification": "MBBS, DM Endocrinology",     "available_days": ["monday", "thursday", "saturday"]},
    {"name": "Dr. Harish Gupta",      "department": "gastroenterology", "qualification": "MBBS, DM Gastroenterology", "available_days": ["wednesday", "friday"]},
    {"name": "Dr. Namrata Singh",     "department": "pulmonology",      "qualification": "MBBS, MD Pulmonology",       "available_days": ["tuesday", "saturday"]},
]

PATIENTS = [
    {"name": "Ramesh Kumar", "phone": "+919876543210", "age": 45, "lang_pref": "hi-IN", "medical_history": [{"condition": "hypertension", "year": 2020}]},
    {"name": "Sunita Devi", "phone": "+919876543211", "age": 38, "lang_pref": "hi-IN"},
    {"name": "Arun Patil", "phone": "+919876543212", "age": 52, "lang_pref": "mr-IN", "medical_history": [{"condition": "diabetes", "year": 2018}]},
    {"name": "Priya Marathe", "phone": "+919876543213", "age": 29, "lang_pref": "mr-IN"},
    {"name": "Vijay Sharma", "phone": "+919876543214", "age": 67, "lang_pref": "hi-IN", "medical_history": [{"condition": "arthritis", "year": 2015}]},
    {"name": "Kavita Joshi", "phone": "+919876543215", "age": 41, "lang_pref": "mr-IN"},
    {"name": "Mohan Gupta", "phone": "+919876543216", "age": 55, "lang_pref": "hi-IN"},
    {"name": "Anita Bhosale", "phone": "+919876543217", "age": 33, "lang_pref": "mr-IN"},
    {"name": "Deepak Verma", "phone": "+919876543218", "age": 48, "lang_pref": "hi-IN"},
    {"name": "Sneha Kulkarni", "phone": "+919876543219", "age": 36, "lang_pref": "mr-IN"},
]

SLOT_TIMES = ["09:00", "10:00", "11:00", "14:00"]


async def seed():
    await init_database()

    async with async_session() as session:
        doctors = [Doctor(**d) for d in DOCTORS]
        session.add_all(doctors)
        await session.flush()  # assigns ids without committing yet

        patients = [Patient(**p) for p in PATIENTS]
        session.add_all(patients)
        await session.flush()

        # Open slots: next 5 weekdays x 4 slots each x all 15 doctors.
        appointments = []
        day = datetime.utcnow().date()
        weekdays_added = 0
        while weekdays_added < 5:
            day += timedelta(days=1)
            if day.weekday() >= 5:  # skip Sat/Sun
                continue
            day_name = day.strftime("%A").lower()
            for doctor in doctors:
                if day_name not in doctor.available_days:
                    continue
                for slot_time in SLOT_TIMES:
                    appointments.append(
                        Appointment(
                            doctor_id=doctor.id,
                            doctor_name=doctor.name,
                            department=doctor.department,
                            slot_date=day.isoformat(),
                            slot_time=slot_time,
                            status="open",
                        )
                    )
            weekdays_added += 1
        session.add_all(appointments)

        # 3 prescriptions for patients[0] (hypertension), patients[2] (diabetes),
        # patients[4] (arthritis).
        prescriptions = [
            Prescription(
                patient_id=patients[0].id,
                doctor_id=doctors[0].id,
                doctor_name=doctors[0].name,
                medicines=[
                    {"name": "Amlodipine", "dosage": "5mg", "frequency": "once daily morning", "duration": "30 days"},
                    {"name": "Aspirin", "dosage": "75mg", "frequency": "once daily after lunch", "duration": "30 days"},
                ],
                notes_en="Blood pressure well controlled. Continue current medications. Follow up in 1 month. Reduce salt intake.",
                refill_date=datetime.utcnow() + timedelta(days=30),
            ),
            Prescription(
                patient_id=patients[2].id,
                doctor_id=doctors[1].id,
                doctor_name=doctors[1].name,
                medicines=[
                    {"name": "Metformin", "dosage": "500mg", "frequency": "twice daily with meals", "duration": "30 days"},
                ],
                notes_en="Blood sugar levels improving. Continue metformin. Monitor diet closely. Follow up in 1 month.",
                refill_date=datetime.utcnow() + timedelta(days=30),
            ),
            Prescription(
                patient_id=patients[4].id,
                doctor_id=doctors[2].id,
                doctor_name=doctors[2].name,
                medicines=[
                    {"name": "Ibuprofen", "dosage": "400mg", "frequency": "twice daily after food", "duration": "14 days"},
                ],
                notes_en="Joint pain manageable with current dosage. Avoid strenuous activity. Follow up in 2 weeks.",
                refill_date=datetime.utcnow() + timedelta(days=14),
            ),
        ]
        session.add_all(prescriptions)

        # 3 discharge records for cron/Agent 5 testing.
        # patients[0] = Ramesh Kumar — used by the outbound demo simulation.
        discharge_followups = [
            DischargeFollowup(
                patient_id=patients[0].id,
                discharge_date=datetime.utcnow() - timedelta(days=3),
                diagnosis="Hypertensive crisis - stabilised",
                medications_prescribed=[
                    {"name": "Amlodipine", "dosage": "5mg", "frequency": "once daily morning"},
                    {"name": "Metformin", "dosage": "500mg", "frequency": "twice daily with meals"},
                ],
                due_at=datetime.utcnow() + timedelta(hours=1),  # due soon — pickable by cron
                status="pending",
                job_type="followup",
            ),
            DischargeFollowup(
                patient_id=patients[1].id,
                discharge_date=datetime.utcnow() - timedelta(days=2),
                diagnosis="Laparoscopic appendectomy - successful, no complications",
                medications_prescribed=[
                    {"name": "Cefixime", "dosage": "200mg", "frequency": "twice daily for 5 days"},
                    {"name": "Pantoprazole", "dosage": "40mg", "frequency": "once daily before breakfast"},
                    {"name": "Paracetamol", "dosage": "500mg", "frequency": "every 6 hours if pain"},
                ],
                due_at=datetime.utcnow() - timedelta(hours=1),  # already due — pickable by cron
                status="pending",
                job_type="followup",
            ),
            DischargeFollowup(
                patient_id=patients[3].id,
                discharge_date=datetime.utcnow() - timedelta(days=1),
                diagnosis="Viral fever - recovered",
                medications_prescribed=[{"name": "Paracetamol", "dosage": "500mg", "frequency": "as needed"}],
                due_at=datetime.utcnow() + timedelta(hours=2),
                status="pending",
                job_type="followup",
            ),
        ]
        session.add_all(discharge_followups)

        # 3 lab reports for Agent 6 testing
        lab_reports = [
            # Ready — read out in demo (Ramesh Kumar, hi-IN)
            LabReport(
                patient_id=patients[0].id,
                test_name="Complete Blood Count (CBC)",
                status="ready",
                ready_at=datetime.utcnow() - timedelta(hours=10),
                result_summary_en="Hemoglobin is slightly low at 10.8 g/dL. All other values are within normal range. Please follow up with your doctor.",
            ),
            # Pending — shows "still processing" (Arun Patil, mr-IN)
            LabReport(
                patient_id=patients[2].id,
                test_name="Lipid Panel",
                status="pending",
                ready_at=None,
                result_summary_en=None,
            ),
            # Dispatched — must NOT appear in demo (Ramesh Kumar)
            LabReport(
                patient_id=patients[0].id,
                test_name="Blood Glucose",
                status="dispatched",
                ready_at=datetime.utcnow() - timedelta(days=2),
                result_summary_en="Blood glucose is 98 mg/dL, within normal fasting range.",
            ),
        ]
        session.add_all(lab_reports)

        # 2 bills for Agent 7 testing
        bills = [
            # Unpaid — demo reads amount + dispatches link (Sunita Devi, hi-IN)
            Bill(
                patient_id=patients[1].id,
                amount_due=3200.00,
                status="unpaid",
                items_json=[
                    {"desc": "OPD Consultation", "qty": 1, "amount": 500},
                    {"desc": "Blood CBC Test", "qty": 1, "amount": 700},
                    {"desc": "Medicines", "qty": 1, "amount": 2000},
                ],
                payment_link="upi://pay?pa=hospital@okaxis&am=3200&cu=INR&tn=HospitalBill",
            ),
            # Paid — must NOT appear in demo (Priya Marathe)
            Bill(
                patient_id=patients[3].id,
                amount_due=1500.00,
                status="paid",
                items_json=[{"desc": "OPD Consultation", "qty": 1, "amount": 1500}],
                payment_link=None,
            ),
        ]
        session.add_all(bills)

        await session.commit()

    print("Seed complete: 15 doctors, 10 patients, slots per available day, 3 prescriptions, 3 discharge followups, 3 lab reports, 2 bills")


if __name__ == "__main__":
    asyncio.run(seed())
