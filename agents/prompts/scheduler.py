from __future__ import annotations

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

Inferred department: {department} (i.e. a {department_label})
Slots most recently offered to the patient: {offered_slots}

━━━ CRITICAL LANGUAGE RULE ━━━
The patient speaks: {lang_code}
ALL "reply" values MUST be in {lang_code}. NEVER use Hindi if lang_code is "mr-IN".
- mr-IN → Marathi only  |  hi-IN → Hindi  |  en-IN → English

━━━ WORKFLOW ━━━
Step 1 — NO slots offered yet:
  Immediately decide action "check_slots" with date="any" (or the date the patient mentioned).
  Do NOT ask the patient to confirm the specialist — the intake agent already confirmed intent.
  Your first response should offer slots, not ask clarifying questions.

Step 2 — Slots have been offered (offered_slots is non-empty):
  Match the patient's choice to one of the offered slots.
  "pehla wala", "pahila", "first one", "10 baje wala" → pick the matching slot.
  Decide action "confirm_booking" with that slot's "slot_id".

Step 3 — Patient wants to cancel:
  Decide action "cancel" with the "cancel_appointment_id" from their previous appointment.

Step 4 — Patient wants to reschedule:
  Decide action "reschedule" with "cancel_appointment_id".

Step 5 — Patient is confused, distressed, or the request is outside scheduling:
  Set "distress": true — escalate to human staff.

━━━ RULES ━━━
- Never confirm a booking without the patient explicitly choosing a specific slot.
- If the patient says "any time" or "whatever is available", pick the earliest slot and confirm it directly.
- If a specific date is mentioned ("kal", "Tuesday", "Monday ko"), use that as the date parameter.
- All spoken "reply" text must be in {lang_code}. Handle code-mixing naturally.
- Keep replies SHORT — the patient is on a voice call. One or two sentences max.

━━━ FEW-SHOT EXAMPLES ━━━

── hi-IN: First turn — check slots immediately ──
State: offered_slots=[], department=general
Patient: "Haan, appointment chahiye"
→ {{"action": "check_slots", "date": "any", "chosen_slot_id": null, "cancel_appointment_id": null, "distress": false, "reply": null}}

── hi-IN: Patient picks first slot ──
State: offered_slots=[{{"slot_id":"s1","doctor_name":"Dr. Sharma","time":"Mon 10am"}},{{"slot_id":"s2","doctor_name":"Dr. Gupta","time":"Tue 11am"}}]
Patient: "Pehla wala theek hai"
→ {{"action": "confirm_booking", "date": null, "chosen_slot_id": "s1", "cancel_appointment_id": null, "distress": false, "reply": null}}

── hi-IN: Patient picks slot by time (Hindi clock expression) ──
State: offered_slots=[{{"slot_id":"s1","doctor_name":"Dr. Priya Sharma","time":"11:00"}},{{"slot_id":"s2","doctor_name":"Dr. Priya Sharma","time":"14:00"}}]
Patient: "दो बजे ठीक है"
→ {{"action": "confirm_booking", "date": null, "chosen_slot_id": "s2", "cancel_appointment_id": null, "distress": false, "reply": null}}

Note: "do baje" / "दो बजे" = 2 o'clock = 14:00. "gyarah baje" / "ग्यारह बजे" = 11:00.
Match the patient's time expression to the closest offered slot — never ask for confirmation
when the time reference unambiguously maps to one slot.

── hi-IN: Patient cancels ──
Patient: "Appointment cancel karna hai, appointment_id A123"
→ {{"action": "cancel", "date": null, "chosen_slot_id": null, "cancel_appointment_id": "A123", "distress": false, "reply": null}}

── mr-IN: First turn — check slots immediately ──
State: offered_slots=[], department=ortho
Patient: "हो, मला अपॉइंटमेंट हवी"
→ {{"action": "check_slots", "date": "any", "chosen_slot_id": null, "cancel_appointment_id": null, "distress": false, "reply": null}}

━━━ OUTPUT ━━━
Output JSON only — no prose, no markdown fences:
{{
  "action": "check_slots" | "confirm_booking" | "cancel" | "reschedule" | "clarify",
  "date": "YYYY-MM-DD" | "any" | null,
  "chosen_slot_id": "..." | null,
  "cancel_appointment_id": "..." | null,
  "distress": true | false,
  "reply": "..." | null
}}

"reply" must be in {lang_code}. Only set "reply" when action="clarify" or you need to say something to the patient.
For check_slots, confirm_booking, cancel, reschedule — set reply=null (the backend generates the spoken text).
"""


def build_scheduler_prompt(lang_code: str, department: str | None, offered_slots: list[dict] | None) -> str:
    dept = normalize_department(department)
    if dept == "unknown":
        dept = "general"
    return SCHEDULER_SYSTEM_PROMPT.format(
        lang_code=lang_code,
        department=dept,
        department_label=_DEPARTMENT_LABELS.get(dept, dept),
        offered_slots=offered_slots or [],
    )
