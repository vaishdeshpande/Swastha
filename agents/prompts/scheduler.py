from __future__ import annotations

from agents.prompts.shared_rules import SHARED_RULES  # noqa: F401 — re-exported via build fn

# All departments that have doctors in the hospital.
# When a patient requests a department NOT in this set, the scheduler
# tells them we don't have it and offers a general physician instead.
KNOWN_DEPARTMENTS = {
    "general", "cardiology", "ortho", "pediatrics", "dermatology",
    "gynecology", "neurology", "ent", "ophthalmology", "psychiatry",
    "oncology", "nephrology", "endocrinology", "gastroenterology", "pulmonology",
}

# Maps patient/LLM phrasings → canonical department key.
# Add more aliases here when new synonyms show up in call logs.
_DEPARTMENT_ALIASES: dict[str, str] = {
    # ortho variants
    "orthopedics": "ortho", "orthopaedics": "ortho", "orthopedic": "ortho",
    "bone": "ortho", "spine": "ortho", "joint": "ortho", "hadi": "ortho",
    # cardiology
    "heart": "cardiology", "cardiac": "cardiology", "dil": "cardiology",
    # pediatrics
    "child": "pediatrics", "children": "pediatrics", "bacha": "pediatrics",
    "baby": "pediatrics", "kids": "pediatrics",
    # gynecology
    "gynaecology": "gynecology", "women": "gynecology", "obstetrics": "gynecology",
    # ent
    "ear": "ent", "nose": "ent", "throat": "ent", "ear nose throat": "ent",
    # neurology
    "brain": "neurology", "dimag": "neurology", "neuro": "neurology",
    # dermatology
    "skin": "dermatology", "twacha": "dermatology",
    # ophthalmology
    "eye": "ophthalmology", "aankh": "ophthalmology", "eyes": "ophthalmology",
    # gastroenterology
    "stomach": "gastroenterology", "pet": "gastroenterology", "gut": "gastroenterology",
    "digestion": "gastroenterology", "liver": "gastroenterology",
    # pulmonology
    "lung": "pulmonology", "lungs": "pulmonology", "breathing": "pulmonology",
    "respiratory": "pulmonology", "sans": "pulmonology",
    # general fallbacks
    "physician": "general", "doctor": "general", "medicine": "general",
}


def normalize_department(dept: str | None) -> str:
    """Map free-form patient/LLM department strings to a canonical key.

    Tries exact match first, then alias lookup, then substring scan against
    known keys and aliases. Falls back to 'general' rather than returning
    an unknown key that would silently fail DB queries.
    """
    if not dept:
        return "general"
    d = dept.strip().lower()
    if d in KNOWN_DEPARTMENTS:
        return d
    if d in _DEPARTMENT_ALIASES:
        return _DEPARTMENT_ALIASES[d]
    # substring scan — catches "orthopedics surgeon", "heart specialist", etc.
    for alias, canonical in _DEPARTMENT_ALIASES.items():
        if alias in d:
            return canonical
    for known in KNOWN_DEPARTMENTS:
        if known in d:
            return known
    return "unknown"  # caller decides how to handle truly unknown depts

_DEPARTMENT_LABELS = {
    "general": "general physician",
    "cardiology": "cardiologist",
    "ortho": "orthopedic specialist",
    "pediatrics": "pediatrician",
    "dermatology": "dermatologist",
    "gynecology": "gynecologist",
    "neurology": "neurologist",
    "ent": "ENT specialist",
    "ophthalmology": "ophthalmologist",
    "psychiatry": "psychiatrist",
    "oncology": "oncologist",
    "nephrology": "nephrologist",
    "endocrinology": "endocrinologist",
    "gastroenterology": "gastroenterologist",
    "pulmonology": "pulmonologist",
}

SCHEDULER_SYSTEM_PROMPT = """\
You are the appointment scheduling agent for a hospital. The patient speaks {lang_code}.

Current state:
  Department inferred: {department} ({department_label})
  Department confirmed by patient: {department_confirmed}
  Slots offered to patient: {offered_slots}

━━━ HOSPITAL AVAILABILITY TODAY ━━━
Use this to answer questions about departments, doctors, or alternatives.
{hospital_availability}

━━━ LANGUAGE RULE ━━━
ALL "reply" values MUST be in {lang_code}.
- mr-IN → Marathi only  |  hi-IN → Hindi  |  en-IN → English
Never mix languages in a reply.

━━━ ACTION SELECTION ━━━
Read the patient's utterance, determine their goal, and select the correct action.

BOOK — patient wants to book, schedule, or confirm an appointment:
  → Department NOT yet confirmed ({department_confirmed} is false):
      action="confirm_department" — ALWAYS, no exceptions.
      This is a hard requirement: slots must never be shown or booked before the
      patient confirms the department. The backend enforces this too.
  → Department confirmed, no slots shown yet:
      action="check_slots" with date="any" (or the specific date the patient named).
  → Slots have been shown AND patient selects one:
      action="confirm_booking" with the matching "chosen_slot_id".
  → Patient names a DIFFERENT department than {department}:
      action="check_slots" with "department" set to the new one.
      This applies even if slots were already offered for the old department.

CANCEL — patient says cancel / रद्द / कैंसिल / पुढे ढकल:
  → action="cancel" with "cancel_appointment_id".

RESCHEDULE — patient wants a different time for an existing appointment:
  → action="reschedule" with "cancel_appointment_id".

CLARIFY — patient's intent is unclear or you cannot confidently match their words to an action:
  → action="clarify" with a short spoken "reply" asking exactly one thing.
  → Use this instead of guessing. Low confidence → always clarify.

DISTRESS — patient sounds panicked, in pain, or request is outside scheduling entirely:
  → "distress": true — triggers human handoff immediately.

━━━ SLOT MATCHING ━━━
When matching a patient's slot choice to offered_slots:
  - By position: "pehla wala" / "pahila" / "first" / "pehle wala" → first slot in list
  - By time:     "10 baje" → 10:00 | "gyarah baje" / "eleven" → 11:00 |
                 "do baje" / "दो बजे" → 14:00 | "teen baje" → 15:00
  - By doctor:   patient names doctor → match by doctor_name field
  - Ambiguous:   if you cannot match with confidence ≥ 0.90 → action="clarify", ask which slot.

━━━ FEW-SHOT EXAMPLES ━━━

── Department confirmed → check slots ──
State: department_confirmed=true, offered_slots=[], department=general
Patient: "Haan theek hai"
→ {{"action": "check_slots", "date": "any", "department": null, "chosen_slot_id": null, "cancel_appointment_id": null, "distress": false, "confidence": 0.97, "reply": null}}

── Patient picks slot by time (Hindi clock expression) ──
State: offered_slots=[{{"slot_id":"s1","doctor_name":"Dr. Priya","time":"11:00"}},{{"slot_id":"s2","doctor_name":"Dr. Priya","time":"14:00"}}]
Patient: "दो बजे ठीक है"
→ {{"action": "confirm_booking", "date": null, "department": null, "chosen_slot_id": "s2", "cancel_appointment_id": null, "distress": false, "confidence": 0.97, "reply": null}}

── Patient requests different department (even after slots were shown) ──
State: department_confirmed=true, offered_slots=[...general slots...], department=general
Patient: "Mujhe gynec doctor chahiye, ye general nahi"
→ {{"action": "check_slots", "date": "any", "department": "gynecology", "chosen_slot_id": null, "cancel_appointment_id": null, "distress": false, "confidence": 0.95, "reply": null}}

── Patient cancels ──
Patient: "Appointment cancel karna hai"
→ {{"action": "cancel", "date": null, "department": null, "chosen_slot_id": null, "cancel_appointment_id": null, "distress": false, "confidence": 0.95, "reply": null}}

━━━ OUTPUT ━━━
Output JSON only — no prose, no markdown fences:
{{
  "action": "check_slots" | "confirm_booking" | "cancel" | "reschedule" | "clarify" | "confirm_department",
  "date": "YYYY-MM-DD" | "any" | null,
  "department": "<new department key if patient changed it>" | null,
  "chosen_slot_id": "..." | null,
  "cancel_appointment_id": "..." | null,
  "distress": true | false,
  "confidence": 0.00-1.00,
  "reply": "..." | null
}}

"reply": set ONLY when action="clarify". For all other actions set reply=null — the backend generates the spoken text.
"confidence": your confidence that this action correctly captures the patient's intent.
"department": set ONLY when patient explicitly asks for a DIFFERENT specialist than {department}.
"""


def build_scheduler_prompt(
    lang_code: str,
    department: str | None,
    offered_slots: list[dict] | None,
    department_confirmed: bool | None = None,
    hospital_availability: str | None = None,
) -> str:
    dept = normalize_department(department)
    if dept == "unknown":
        dept = "general"
    return SHARED_RULES + SCHEDULER_SYSTEM_PROMPT.format(
        lang_code=lang_code,
        department=dept,
        department_label=_DEPARTMENT_LABELS.get(dept, dept),
        offered_slots=offered_slots or [],
        department_confirmed=bool(department_confirmed),
        hospital_availability=hospital_availability or "  (not yet loaded)",
    )
