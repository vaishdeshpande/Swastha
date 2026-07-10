"""Tests for LangGraph routing functions in graph.py.

These are pure functions (no async, no mocks needed) — they inspect
AgentState fields and return a string node name.
"""

import pytest
from tests.conftest import make_state


# Import routing functions directly — no graph compilation needed.
from agents.graph import route_after_intake, check_escalation, route_outbound_job, check_risk


class TestRouteAfterIntake:
    def test_intent_book_with_patient_routes_to_scheduler(self):
        state = make_state(intent="book", patient_id="patient-uuid")
        assert route_after_intake(state) == "scheduler"

    def test_intent_book_without_patient_awaits_phone(self):
        # Intent known but phone not yet collected — wait for next turn
        state = make_state(intent="book", patient_id=None)
        assert route_after_intake(state) == "await_input"

    def test_intent_prescription_with_patient_routes_to_prescription(self):
        state = make_state(intent="prescription", patient_id="patient-uuid")
        assert route_after_intake(state) == "prescription"

    def test_intent_none_awaits_input(self):
        state = make_state(intent=None, intake_attempt_count=0)
        assert route_after_intake(state) == "await_input"

    def test_intent_none_at_max_attempts_routes_to_human_handoff(self):
        # After MAX_INTAKE_ATTEMPTS unclear rounds, graph stops looping and escalates
        from agents.graph import MAX_INTAKE_ATTEMPTS
        state = make_state(intent=None, intake_attempt_count=MAX_INTAKE_ATTEMPTS)
        assert route_after_intake(state) == "human_handoff"

    def test_intent_followup_awaits_input(self):
        # "followup" is outbound-only — inbound stays in clarification loop
        state = make_state(intent="followup", patient_id="patient-uuid")
        assert route_after_intake(state) == "await_input"

    def test_intent_query_awaits_input(self):
        # "query" is too generic — clarify further before routing
        state = make_state(intent="query", patient_id="patient-uuid")
        assert route_after_intake(state) == "await_input"

    def test_escalation_required_always_wins(self):
        # escalation_required=True overrides even a valid intent
        state = make_state(intent="book", patient_id="patient-uuid", escalation_required=True)
        assert route_after_intake(state) == "human_handoff"


class TestCheckEscalation:
    def test_escalation_required_routes_to_human_handoff(self):
        state = make_state(escalation_required=True)
        assert check_escalation(state) == "human_handoff"

    def test_no_escalation_routes_to_post_call(self):
        state = make_state(escalation_required=False)
        assert check_escalation(state) == "post_call"

    def test_missing_escalation_key_defaults_to_post_call(self):
        state = make_state()
        state.pop("escalation_required", None)
        # state.get("escalation_required", False) returns False
        assert check_escalation(state) == "post_call"


class TestRouteOutboundJob:
    def test_confirmation_job(self):
        state = make_state(job_type="confirmation")
        assert route_outbound_job(state) == "confirmation"

    def test_rx_reminder_job(self):
        state = make_state(job_type="rx_reminder")
        assert route_outbound_job(state) == "rx_reminder"

    def test_followup_job(self):
        state = make_state(job_type="followup")
        assert route_outbound_job(state) == "followup"

    def test_unknown_job_type_raises(self):
        state = make_state(job_type="unknown_type")
        with pytest.raises(ValueError, match="Unknown job_type"):
            route_outbound_job(state)

    def test_none_job_type_raises(self):
        state = make_state(job_type=None)
        with pytest.raises(ValueError):
            route_outbound_job(state)


class TestCheckRisk:
    def test_high_risk_routes_to_escalate(self):
        state = make_state(call_outcome={"readmission_risk": 0.8, "status": "escalated"})
        assert check_risk(state) == "escalate"

    def test_risk_exactly_threshold_routes_to_end(self):
        # > 0.7, so 0.7 itself goes to "end"
        state = make_state(call_outcome={"readmission_risk": 0.7})
        assert check_risk(state) == "end"

    def test_low_risk_routes_to_end(self):
        state = make_state(call_outcome={"readmission_risk": 0.2})
        assert check_risk(state) == "end"

    def test_missing_call_outcome_defaults_to_end(self):
        state = make_state(call_outcome=None)
        assert check_risk(state) == "end"

    def test_missing_risk_field_defaults_to_end(self):
        state = make_state(call_outcome={"status": "completed"})
        assert check_risk(state) == "end"


class TestRouteAfterIntakeLabBilling:
    """Routing tests for the two new intents added in the agents/CLAUDE.md update."""

    def test_intent_lab_with_patient_routes_to_lab_status(self):
        state = make_state(intent="lab", patient_id="patient-uuid")
        assert route_after_intake(state) == "lab_status"

    def test_intent_billing_with_patient_routes_to_billing(self):
        state = make_state(intent="billing", patient_id="patient-uuid")
        assert route_after_intake(state) == "billing"

    def test_intent_lab_without_patient_awaits_phone(self):
        state = make_state(intent="lab", patient_id=None)
        assert route_after_intake(state) == "await_input"

    def test_intent_billing_without_patient_awaits_phone(self):
        state = make_state(intent="billing", patient_id=None)
        assert route_after_intake(state) == "await_input"

    def test_escalation_overrides_lab_intent(self):
        state = make_state(intent="lab", escalation_required=True)
        assert route_after_intake(state) == "human_handoff"

    def test_escalation_overrides_billing_intent(self):
        state = make_state(intent="billing", escalation_required=True)
        assert route_after_intake(state) == "human_handoff"

    def test_await_input_returned_for_unclear_intent(self):
        """intent=None with attempts remaining → graph pauses and waits."""
        state = make_state(intent=None, intake_attempt_count=0)
        assert route_after_intake(state) == "await_input"

    def test_await_input_returned_for_followup_intent(self):
        """'followup' is outbound-only inbound — stays in clarification loop."""
        state = make_state(intent="followup", patient_id="patient-uuid")
        assert route_after_intake(state) == "await_input"

    def test_await_input_returned_for_query_intent(self):
        state = make_state(intent="query", patient_id="patient-uuid")
        assert route_after_intake(state) == "await_input"

    def test_max_attempts_with_no_intent_routes_to_human_handoff(self):
        """Exhausted intake rounds without resolving intent → escalate."""
        from agents.graph import MAX_INTAKE_ATTEMPTS
        state = make_state(intent=None, patient_id="patient-uuid", intake_attempt_count=MAX_INTAKE_ATTEMPTS)
        assert route_after_intake(state) == "human_handoff"
