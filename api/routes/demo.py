"""Demo endpoints — interactive outbound call simulation for the /admin UI.

Two-endpoint design:
  POST /api/demo/outbound/start  — initialise session, call real outbound node, return patient context + first agent message
  POST /api/demo/outbound/reply  — send a patient reply, get the next LLM-generated agent message back

For followup: on /start we translate the greeting directly (no DB re-fetch needed).
On each /reply we call _run_checklist_turn() from agent_followup with the discharge info
that was already fetched for the left panel — so the demo works even if the DB followup
record is missing or past-due. sarvam-30b drives the checklist (fever/pain/medication).

For confirmation/rx_reminder: nodes are single-pass (called once on /start with real DB
side-effects). Each /reply calls sarvam-30b once to generate a contextual wrap-up.
"""

import asyncio
import logging
import os
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sarvamai import SarvamAI
from sqlalchemy import select, and_

from agents.agent_followup import _compute_readmission_risk, _run_checklist_turn
from agents.agent_prescription import prescription_outbound_node
from agents.agent_scheduler import scheduler_outbound_node
from agents.tools.llm_json import extract_json
from agents.tools.translate_tools import translate_text
from api.database import async_session
from api.models import Appointment, DischargeFollowup, Patient, Prescription

logger = logging.getLogger("api.demo")
router = APIRouter()

# ── In-memory session store (single-process demo, no persistence needed) ──
_sessions: dict[str, dict] = {}

# ── Stable demo phone numbers from api/seed.py ──
_DEMO_PHONE: dict[str, str] = {
    "followup":     "+919876543211",   # Sunita Devi  — laparoscopic appendectomy (post-op validation)
    "confirmation": "+919876543211",   # Sunita Devi  — has booked appointment
    "rx_reminder":  "+919876543210",   # Ramesh Kumar — has prescription
}

# ── Agent sequences per job_type (for current_agent field in responses) ──
_AGENT_SEQUENCES: dict[str, list[str]] = {
    "followup":     ["route_job", "followup_outbound", "escalate"],
    "confirmation": ["route_job", "scheduler_outbound"],
    "rx_reminder":  ["route_job", "prescription_outbound"],
}

# ── Single-pass nodes (called once on /start; /reply uses wrap-up LLM) ──
_SINGLE_PASS_NODES = {
    "confirmation": scheduler_outbound_node,
    "rx_reminder":  prescription_outbound_node,
}

RISK_THRESHOLD = 0.7


# ─────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────

class StartRequest(BaseModel):
    job_type: Literal["followup", "confirmation", "rx_reminder"]
    lang_code: Optional[Literal["hi-IN", "mr-IN"]] = "hi-IN"


class StartResponse(BaseModel):
    session_id: str
    patient_info: dict
    agent_message: str
    current_agent: str


class ReplyRequest(BaseModel):
    session_id: str
    message: str


class ReplyResponse(BaseModel):
    agent_message: str
    current_agent: str
    call_outcome: Optional[dict] = None
    done: bool


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

async def _fetch_patient_info(job_type: str, lang_code: str) -> tuple[Patient, dict]:
    phone = _DEMO_PHONE[job_type]

    async with async_session() as session:
        result = await session.execute(select(Patient).where(Patient.phone == phone))
        patient = result.scalar_one_or_none()
        if patient is None:
            raise HTTPException(
                status_code=404,
                detail="Demo patient not found. Run `python -m api.seed` first.",
            )

        patient_id = patient.id
        info: dict = {
            "name": patient.name,
            "age": patient.age,
            "phone": patient.phone,
            "lang_pref": patient.lang_pref,
            "blood_group": patient.blood_group,
            "medical_history": patient.medical_history or [],
        }

        if job_type == "followup":
            r = await session.execute(
                select(DischargeFollowup)
                .where(DischargeFollowup.patient_id == patient_id)
                .order_by(DischargeFollowup.discharge_date.desc())
                .limit(1)
            )
            d = r.scalar_one_or_none()
            if d:
                info["discharge"] = {
                    "diagnosis": d.diagnosis,
                    "discharge_date": d.discharge_date.strftime("%Y-%m-%d") if d.discharge_date else None,
                    "medications": d.medications_prescribed or [],
                    "follow_up_due": d.due_at.strftime("%Y-%m-%d %H:%M") if d.due_at else None,
                }

        elif job_type == "confirmation":
            r = await session.execute(
                select(Appointment)
                .where(and_(Appointment.patient_id == patient_id, Appointment.status == "booked"))
                .order_by(Appointment.booked_at.desc())
                .limit(1)
            )
            a = r.scalar_one_or_none()
            if a:
                info["appointment"] = {
                    "doctor_name": a.doctor_name,
                    "department": a.department,
                    "date": a.slot_date,
                    "time": a.slot_time,
                    "confirmed": a.confirmed,
                }

        elif job_type == "rx_reminder":
            r = await session.execute(
                select(Prescription)
                .where(Prescription.patient_id == patient_id)
                .order_by(Prescription.issued_date.desc())
                .limit(1)
            )
            p = r.scalar_one_or_none()
            if p:
                info["prescription"] = {
                    "doctor_name": p.doctor_name,
                    "medicines": p.medicines or [],
                    "notes_en": p.notes_en,
                    "issued_date": p.issued_date.strftime("%Y-%m-%d") if p.issued_date else None,
                }

    return patient, info


def _build_base_state(session_id: str, patient: Patient, job_type: str, lang_code: str) -> dict:
    return {
        "session_id": session_id,
        "patient_id": str(patient.id),
        "patient_name": patient.name,
        "lang_code": lang_code,
        "tts_voice": "priya" if lang_code == "hi-IN" else "kavya",
        "tts_model": "bulbul:v3",
        "messages": [],
        "current_agent": "route_job",
        "escalation_required": False,
        "call_connected": True,
        "job_type": job_type,
        "is_new_patient": False,
        "urgency": "normal",
        "intake_attempt_count": 0,
        "intake_collected": {},
        "escalation_reason": None,
        "detected_language": None,
        "detection_confidence": None,
        "intent": None,
        "department": None,
        "call_id": session_id,
        "call_recording_path": None,
        "call_outcome": None,
        "call_start_time": None,
        "offered_slots": None,
        "appointment_id": None,
    }


async def _run_prescription_turn(state: dict, patient_info: dict) -> tuple[str, bool]:
    """Multi-turn LLM for rx_reminder. Answers patient queries from prescription context.
    Returns (reply, done). Sets done=True when the conversation reaches a natural close."""
    client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
    lang = state["lang_code"]
    p = patient_info.get("prescription", {})
    medicines = p.get("medicines", [])
    med_lines = "\n".join(
        f"- {m['name']} {m.get('dosage', '')} — {m.get('frequency', '')}"
        for m in medicines
    )
    notes = p.get("notes_en", "")

    # Two-pass: ask for the reply as plain text first (more reliable than JSON),
    # then decide done separately based on a simple keyword signal.
    system = (
        f"You are a hospital receptionist calling a patient about their medicines.\n"
        f"Prescription details:\n{med_lines}\n"
        f"Doctor's notes: {notes}\n\n"
        f"Answer the patient's question using ONLY the prescription above. "
        f"Do not invent any medical advice not written above. "
        f"If the patient has no more questions and the call is naturally ending, "
        f"end your reply with the exact token [DONE].\n\n"
        f"CRITICAL: Reply entirely in {lang}. No English words at all."
    )

    def _call() -> str:
        r = client.chat.completions(
            messages=[{"role": "system", "content": system}, *state["messages"]],
            model="sarvam-30b",
            temperature=0.3,
            max_tokens=400,
        )
        return (r.choices[0].message.content or "").strip()

    # Up to 2 attempts — empty output and timeouts are both treated as retryable
    for attempt in (1, 2):
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(_call), timeout=20.0)
        except asyncio.TimeoutError:
            logger.error("demo: rx_reminder LLM timed out (attempt %d)", attempt)
            continue

        logger.info("demo: rx_reminder raw LLM output (attempt %d): %r", attempt, raw[:200])

        # Model sometimes wraps the answer in JSON despite the plain-text instruction
        if raw.startswith("{") or '"reply"' in raw:
            parsed = extract_json(raw)
            if parsed and str(parsed.get("reply", "")).strip():
                return str(parsed["reply"]).strip(), bool(parsed.get("done", False))

        done = "[DONE]" in raw
        reply = raw.replace("[DONE]", "").strip()
        if reply:
            return reply, done

        logger.warning("demo: rx_reminder empty reply on attempt %d, retrying", attempt)

    return "", False


async def _generate_wrap_up(state: dict, job_type: str, patient_info: dict) -> str:
    """One sarvam-30b call to respond to the patient's reply for single-pass nodes.

    Passes the real prescription / appointment context so the LLM can answer
    actual patient questions (e.g. 'which medicine at what time') rather than
    producing a generic closing line.
    """
    client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
    lang = state["lang_code"]

    if job_type == "rx_reminder":
        prescription = patient_info.get("prescription", {})
        medicines = prescription.get("medicines", [])
        med_lines = "\n".join(
            f"- {m['name']} {m.get('dosage', '')} — {m.get('frequency', '')}"
            for m in medicines
        )
        notes = prescription.get("notes_en", "")
        context = (
            f"You are a hospital receptionist who just called the patient to remind them about their medicines.\n"
            f"Prescription:\n{med_lines}\n"
            f"Doctor's notes: {notes}\n\n"
            f"The patient has replied. Answer their question using ONLY the prescription details above. "
            f"If they confirmed they are already taking medicines, acknowledge warmly and close. "
            f"Do NOT invent dosage advice not present in the prescription."
        )
    else:
        appointment = patient_info.get("appointment", {})
        context = (
            f"You are a hospital receptionist who called to confirm the patient's appointment.\n"
            f"Appointment: {appointment.get('doctor_name', 'the doctor')} — "
            f"{appointment.get('date', '')} at {appointment.get('time', '')} ({appointment.get('department', '')}).\n\n"
            f"The patient has replied. Respond to what they said and close the call warmly."
        )

    system = (
        f"{context}\n\n"
        f"Reply ONLY in {lang} — no English whatsoever. Keep it to 1-2 sentences. "
        f"Respond with just the spoken text — no JSON, no formatting."
    )

    def _call() -> str:
        r = client.chat.completions(
            messages=[{"role": "system", "content": system}, *state["messages"]],
            model="sarvam-30b",
            temperature=0.3,
            max_tokens=300,
        )
        return (r.choices[0].message.content or "").strip()

    try:
        reply = await asyncio.wait_for(asyncio.to_thread(_call), timeout=15.0)
    except asyncio.TimeoutError:
        logger.error("demo: wrap-up LLM timed out (%s)", job_type)
        reply = ""

    # Strip JSON wrapper if model ignored the instruction
    if reply.startswith("{"):
        parsed = extract_json(reply)
        if parsed and parsed.get("reply"):
            reply = parsed["reply"]

    logger.info("demo: wrap-up reply=%r (job_type=%s)", reply[:80], job_type)
    return reply


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@router.post("/demo/outbound/start", response_model=StartResponse, summary="Start outbound simulation")
async def start_outbound_demo(body: StartRequest) -> StartResponse:
    """Initialise a demo session.

    followup: translates the greeting directly and caches discharge_info so the
    multi-turn loop in /reply can call _run_checklist_turn without re-fetching from DB.

    confirmation/rx_reminder: calls the real outbound node once (real DB side-effects),
    caches its call_outcome, then /reply generates a contextual wrap-up via sarvam-30b.
    """
    lang_code = body.lang_code or "hi-IN"
    job_type = body.job_type

    patient, patient_info = await _fetch_patient_info(job_type, lang_code)
    session_id = str(uuid.uuid4())
    state = _build_base_state(session_id, patient, job_type, lang_code)

    if job_type == "followup":
        discharge = patient_info.get("discharge")
        patient_name = patient.name.split()[0]
        if discharge:
            raw_diagnosis = discharge.get("diagnosis", "your recent procedure")
            procedure = raw_diagnosis.split(" - ")[0].strip()
            greeting_en = (
                f"Hello {patient_name}! This is Apollo Hospital calling to check on your recovery "
                f"after your {procedure}. How are you feeling today?"
            )
        else:
            greeting_en = (
                f"Hello {patient_name}! This is Apollo Hospital calling to check on your recovery. "
                f"How are you feeling today?"
            )

        greeting = await translate_text(greeting_en, source_lang="en-IN", target_lang=lang_code)
        state["messages"].append({"role": "assistant", "content": greeting})
        state["current_agent"] = "followup_outbound"

        _sessions[session_id] = {
            "job_type": job_type,
            "lang_code": lang_code,
            "state": state,
            "done": False,
            "discharge_info": discharge,
            "patient_info": patient_info,
        }

        logger.info("demo: followup session %s started (has_discharge=%s)", session_id, discharge is not None)
        return StartResponse(
            session_id=session_id,
            patient_info=patient_info,
            agent_message=greeting,
            current_agent="followup_outbound",
        )

    else:
        node_fn = _SINGLE_PASS_NODES[job_type]
        result = await node_fn(state)

        _sessions[session_id] = {
            "job_type": job_type,
            "lang_code": lang_code,
            "state": result,
            "done": False,
            "patient_info": patient_info,
        }

        opening = result["messages"][-1]["content"] if result["messages"] else ""
        current_agent = result.get("current_agent", _AGENT_SEQUENCES[job_type][1])

        logger.info("demo: %s session %s started", job_type, session_id)
        return StartResponse(
            session_id=session_id,
            patient_info=patient_info,
            agent_message=opening,
            current_agent=current_agent,
        )


@router.post("/demo/outbound/reply", response_model=ReplyResponse, summary="Send patient reply")
async def reply_outbound_demo(body: ReplyRequest) -> ReplyResponse:
    """Receive a patient message and return the next agent message from the real LLM.

    followup: calls _run_checklist_turn() (sarvam-30b) which reads the conversation
    history and asks the next unanswered question. When all_answered=True, computes
    readmission_risk and closes the call.

    confirmation/rx_reminder: generates one contextual wrap-up reply via sarvam-30b,
    acknowledging what the patient actually said, then done=True.
    """
    session = _sessions.get(body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired. Start a new simulation.")

    if session.get("done"):
        return ReplyResponse(
            agent_message="",
            current_agent="",
            call_outcome=session["state"].get("call_outcome"),
            done=True,
        )

    job_type = session["job_type"]
    state = session["state"]

    state["messages"].append({"role": "user", "content": body.message})

    # ── Follow-up: multi-turn LLM checklist ──────────────────────────────────
    if job_type == "followup":
        discharge = session.get("discharge_info")

        if not discharge:
            msg = await translate_text(
                "I'm sorry, I don't have your discharge records on file right now. "
                "Please visit the hospital or call back during working hours.",
                source_lang="en-IN",
                target_lang=state["lang_code"],
            )
            state["messages"].append({"role": "assistant", "content": msg})
            outcome = {"status": "completed", "readmission_risk": 0.0}
            session["state"]["call_outcome"] = outcome
            session["done"] = True
            return ReplyResponse(
                agent_message=msg,
                current_agent="followup_outbound",
                call_outcome=outcome,
                done=True,
            )

        # Code-side field memory — survives across turns so the LLM is never
        # trusted to re-derive what was already answered (that trust is what
        # caused verbatim question loops at temperature=0).
        collected: dict = session.setdefault("collected", {})

        checklist = await _run_checklist_turn(state, discharge, collected)
        logger.info("demo: followup checklist turn — all_answered=%s", checklist.get("all_answered"))

        # Empty reply mid-conversation means the LLM glitched — retry once
        if not (checklist.get("reply") or "").strip() and not checklist.get("all_answered"):
            logger.warning("demo: followup returned empty reply, retrying once")
            checklist = await _run_checklist_turn(state, discharge, collected)

        # Merge newly extracted fields into session memory (set once, never cleared)
        for field in ("fever", "pain_level", "medication_adherence", "additional_concerns"):
            value = checklist.get(field)
            if value is not None and field not in collected:
                collected[field] = value

        reply = (checklist.get("reply") or "").strip()

        # Verbatim-repeat guard: the model asked the exact same question again.
        # Retry once — the updated `collected` injection breaks the loop.
        last_assistant = next(
            (m["content"] for m in reversed(state["messages"]) if m["role"] == "assistant"), None
        )
        if reply and reply == last_assistant and not checklist.get("all_answered"):
            logger.warning("demo: followup repeated itself verbatim, retrying with collected fields")
            checklist = await _run_checklist_turn(state, discharge, collected)
            reply = (checklist.get("reply") or "").strip()
            for field in ("fever", "pain_level", "medication_adherence", "additional_concerns"):
                value = checklist.get(field)
                if value is not None and field not in collected:
                    collected[field] = value

        # Code-side completion check: if the three required fields are in memory,
        # close the call even if the model forgot to set all_answered.
        required_done = all(k in collected for k in ("fever", "pain_level", "medication_adherence"))
        all_answered = bool(checklist.get("all_answered")) or required_done

        if reply:
            state["messages"].append({"role": "assistant", "content": reply})

        if not all_answered:
            return ReplyResponse(
                agent_message=reply,
                current_agent="followup_outbound",
                call_outcome=None,
                done=False,
            )

        fever = bool(collected.get("fever", checklist.get("fever")))
        pain_level = int(collected.get("pain_level", checklist.get("pain_level")) or 0)
        medication_adherence = collected.get("medication_adherence") or checklist.get("medication_adherence") or "yes"
        additional_concerns = collected.get("additional_concerns") or checklist.get("additional_concerns") or ""

        readmission_risk = _compute_readmission_risk(fever, pain_level, medication_adherence)
        is_high_risk = readmission_risk > RISK_THRESHOLD
        status = "escalated" if is_high_risk else "completed"

        if not reply:
            if is_high_risk:
                reply = await translate_text(
                    "I'm very concerned about your symptoms. I am transferring your call "
                    "to our on-call doctor right now. Please stay on the line.",
                    source_lang="en-IN", target_lang=state["lang_code"],
                )
            else:
                reply = await translate_text(
                    "Thank you for the update. Please continue your medicines and rest well. "
                    "Call us if anything changes. Take care!",
                    source_lang="en-IN", target_lang=state["lang_code"],
                )
            state["messages"].append({"role": "assistant", "content": reply})

        outcome = {
            "fever": fever,
            "pain_level": pain_level,
            "medication_adherence": medication_adherence,
            "additional_concerns": additional_concerns,
            "readmission_risk": readmission_risk,
            "status": status,
        }

        try:
            from agents.tools.db_tools import log_outcome
            await log_outcome(state["patient_id"], outcome)
            if is_high_risk:
                from agents.tools.notification_tools import escalate_to_doctor
                await escalate_to_doctor(
                    state["patient_id"],
                    reason=f"Demo follow-up: readmission_risk={readmission_risk:.2f}",
                )
        except Exception:
            logger.warning("demo: could not write outcome to DB (non-fatal)", exc_info=True)

        session["state"]["call_outcome"] = outcome
        session["done"] = True

        logger.info("demo: followup complete — risk=%.2f status=%s", readmission_risk, status)
        return ReplyResponse(
            agent_message=reply,
            current_agent="escalate" if is_high_risk else "followup_outbound",
            call_outcome=outcome,
            done=True,
        )

    # ── rx_reminder: multi-turn — LLM answers queries using prescription context ──
    else:
        patient_info = session.get("patient_info", {})
        reply, done = await _run_prescription_turn(state, patient_info)
        if reply:
            state["messages"].append({"role": "assistant", "content": reply})
        if done:
            session["done"] = True
        logger.info("demo: rx_reminder turn done=%s (session=%s)", done, body.session_id)
        return ReplyResponse(
            agent_message=reply,
            current_agent="prescription_outbound",
            call_outcome=state.get("call_outcome") if done else None,
            done=done,
        )
