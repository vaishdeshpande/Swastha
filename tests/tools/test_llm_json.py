"""Unit tests for agents/tools/llm_json.py — no mocking needed, pure logic."""

import pytest
from agents.tools.llm_json import extract_json


class TestExtractJson:
    def test_pure_json_string(self):
        raw = '{"action": "check_slots", "date": "2026-07-10"}'
        result = extract_json(raw)
        assert result == {"action": "check_slots", "date": "2026-07-10"}

    def test_json_inside_code_fence(self):
        raw = '```json\n{"action": "confirm_booking", "chosen_slot_id": "abc123"}\n```'
        result = extract_json(raw)
        assert result == {"action": "confirm_booking", "chosen_slot_id": "abc123"}

    def test_json_inside_plain_code_fence(self):
        raw = '```\n{"intent": "book"}\n```'
        result = extract_json(raw)
        assert result == {"intent": "book"}

    def test_json_embedded_in_prose(self):
        raw = 'Sure, here is the data you asked for: {"reply": "aapka appointment book ho gaya", "escalate": false}'
        result = extract_json(raw)
        assert result is not None
        assert result["reply"] == "aapka appointment book ho gaya"
        assert result["escalate"] is False

    def test_empty_string_returns_none(self):
        assert extract_json("") is None

    def test_none_input_returns_none(self):
        assert extract_json(None) is None

    def test_plain_text_no_json_returns_none(self):
        assert extract_json("Main aapki madad kar sakta hoon") is None

    def test_nested_json(self):
        raw = '{"outer": {"inner": 42}, "list": [1, 2, 3]}'
        result = extract_json(raw)
        assert result == {"outer": {"inner": 42}, "list": [1, 2, 3]}

    def test_json_with_unicode(self):
        raw = '{"reply": "नमस्ते, आपका अपॉइंटमेंट बुक हो गया है"}'
        result = extract_json(raw)
        assert result["reply"] == "नमस्ते, आपका अपॉइंटमेंट बुक हो गया है"

    def test_malformed_json_returns_none(self):
        assert extract_json('{"unclosed": ') is None
