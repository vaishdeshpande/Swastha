from typing import TypedDict, Optional, List, Literal


class AgentState(TypedDict):
    # ── Session identity ──
    session_id: str                         # Stable UUID for the full conversation.
                                            # Never changes, even on LiveKit reconnect.
                                            # Used as the Redis session key instead of call_id.

    # ── Set by Agent 1 (Language Router) ──
    lang_code: str                          # "hi-IN" | "mr-IN" | "en-IN"
    tts_voice: str                          # "priya" | "kavya" | etc. from languages.yaml
    tts_model: str                          # "bulbul:v3"
    detected_language: Optional[str]        # Raw language hint from STT metadata
    detection_confidence: Optional[float]   # STT confidence for detected_language
    lang_mismatch_count: int                # Hysteresis counter — number of consecutive turns
                                            # where STT detected_language ≠ current lang_code.
                                            # Language only switches when this reaches 2.
                                            # Resets to 0 on any matching turn.

    # ── Set by Agent 2 (Voice Intake) ──
    patient_id: Optional[str]               # Supabase UUID or None
    patient_name: Optional[str]
    is_new_patient: bool                    # True if registered during this call
    intent: Optional[Literal["book", "prescription", "followup", "query", "lab", "billing"]]
    department: Optional[str]               # "cardiology", "general", "ortho", etc.
    urgency: Literal["normal", "urgent"]
    intake_attempt_count: int               # Clarification loop counter (max 3)
    intake_collected: dict                  # Persistent partial extraction across turns.
                                            # Keys: intent, phone, patient_name, age,
                                            #       department, urgency.
                                            # Each field is set once and never cleared —
                                            # voice_intake merges new extractions in on
                                            # every turn so the LLM never re-asks for
                                            # fields the patient already provided.

    # ── Conversation ──
    messages: List[dict]                    # Full conversation history [{role, content}]
    current_agent: str                      # "language_router" | "voice_intake" | "scheduler"
                                            # | "prescription" | "lab_status" | "billing"
                                            # | "followup" | "human_handoff" | "post_call"
                                            # Frontend reads this for Agent Activity Feed

    # ── Escalation ──
    escalation_required: bool
    escalation_reason: Optional[str]

    # ── Post-call ──
    call_id: Optional[str]                  # LiveKit room ID — used as Redis session key
    call_recording_path: Optional[str]
    call_outcome: Optional[dict]            # Written by post-call subgraph
    call_start_time: Optional[str]          # ISO timestamp

    # ── Set by Agent 2 (Voice Intake) when intent=book ──
    hospital_availability: Optional[str]    # Compact plain-text: "general: Dr. X (09:00)\ncardiology: ..."
                                            # Injected into scheduler prompt so LLM knows all
                                            # departments without extra DB calls mid-conversation.

    # ── Set by Agent 3 (Scheduler) ──
    offered_slots: Optional[List[dict]]     # Slots most recently read out to the patient
    appointment_id: Optional[str]           # Appointment being booked/cancelled/confirmed
    booked_slot_details: Optional[dict]     # {doctor_name, department, date, time} — captured
                                            # before offered_slots is cleared; powers the
                                            # booking_confirmed UI card in livekit_agent
    department_confirmed: Optional[bool]    # True once patient has confirmed the department.
                                            # Prevents re-asking confirmation on re-entry.

    # ── Outbound graph only ──
    job_type: Optional[Literal["confirmation", "rx_reminder", "followup"]]
    call_connected: Optional[bool]          # False if the outbound call went to voicemail / no answer

    # ── Optimistic / speculative fields (Scenarios 1–4) ──
    optimistic_patient_id: Optional[str]    # UUID reserved before DB write confirms (Scenario 1)
    prefetched_slots: Optional[List[dict]]  # Slots cached at voice_intake→scheduler boundary (Scenario 2)
    intent_classifier_scores: Optional[dict]  # {"book": float, "prescription": float} (Scenario 4)

    # ── Set by Agent 6 (Lab Status) — for frontend data channel event ──
    lab_reports_dispatched: Optional[List[dict]]  # [{test_name, summary}] read to patient this turn

    # ── Set by Agent 7 (Billing) — for frontend data channel event ──
    bill_amount_due: Optional[float]    # Amount read to patient (INR)
    bill_sms_sent: Optional[bool]       # True if payment link was dispatched via SMS

    # ── Error recovery ──
    use_fallback_wav: Optional[bool]    # Set by voice_intake when LLM is unrecoverable.
                                        # livekit_agent plays frontend/public/fallback/<lang>.wav
                                        # and returns "" so TTS never fires.
