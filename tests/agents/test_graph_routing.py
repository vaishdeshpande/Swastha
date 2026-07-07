"""Tests for LangGraph routing functions in graph.py.

These are pure functions (no async, no mocks needed) — they inspect
AgentState fields and return a string node name.
"""

import pytest
from tests.conftest import make_state


# Import routing functions directly — no graph compilation needed.
from agents.graph import route_after_intake, check_escalation, route_outbound_job, check_risk


class TestRouteAfterIntake:
    def test_intent_book_routes_to_scheduler(self):
        state = make_state(intent="book")
        assert route_after_intake(state) == "scheduler"

    def test_intent_prescription_routes_to_prescription(self):
        state = make_state(intent="prescription")
        assert route_after_intake(state) == "prescription"

    def test_intent_none_loops_back_to_voice_intake(self):
        state = make_state(intent=None)
        assert route_after_intake(state) == "voice_intake"

    def test_intent_followup_falls_back_to_voice_intake(self):
        # "followup" is not a handled inbound intent — treated as unclear
        state = make_state(intent="followup")
        assert route_after_intake(state) == "voice_intake"

    def test_intent_query_falls_back_to_voice_intake(self):
        state = make_state(intent="query")
        assert route_after_intake(state) == "voice_intake"


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
