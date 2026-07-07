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

    # ── Set by Agent 2 (Voice Intake) ──
    patient_id: Optional[str]               # Supabase UUID or None
    patient_name: Optional[str]
    is_new_patient: bool                    # True if registered during this call
    intent: Optional[Literal["book", "prescription", "followup", "query"]]
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
    current_agent: str                      # "language_router" | "voice_intake" | "scheduler" | etc.
                                            # Frontend reads this for Agent Activity Feed

    # ── Escalation ──
    escalation_required: bool
    escalation_reason: Optional[str]

    # ── Post-call ──
    call_id: Optional[str]                  # LiveKit room ID — used as Redis session key
    call_recording_path: Optional[str]
    call_outcome: Optional[dict]            # Written by post-call subgraph
    call_start_time: Optional[str]          # ISO timestamp

    # ── Set by Agent 3 (Scheduler) ──
    offered_slots: Optional[List[dict]]     # Slots most recently read out to the patient
    appointment_id: Optional[str]           # Appointment being booked/cancelled/confirmed

    # ── Outbound graph only ──
    job_type: Optional[Literal["confirmation", "rx_reminder", "followup"]]
    call_connected: Optional[bool]          # False if the outbound call went to voicemail / no answer

    # ── Optimistic / speculative fields (Scenarios 1–4) ──
    optimistic_patient_id: Optional[str]    # UUID reserved before DB write confirms (Scenario 1)
    prefetched_slots: Optional[List[dict]]  # Slots cached at voice_intake→scheduler boundary (Scenario 2)
    intent_classifier_scores: Optional[dict]  # {"book": float, "prescription": float} (Scenario 4)
