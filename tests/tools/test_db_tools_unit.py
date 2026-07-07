"""Unit tests for pure logic in db_tools.py (no DB connection needed)."""

import pytest
from agents.tools.db_tools import normalize_department, VALID_DEPARTMENTS


class TestNormalizeDepartment:
    def test_exact_match_returned_unchanged(self):
        for dept in VALID_DEPARTMENTS:
            assert normalize_department(dept) == dept

    def test_synonym_general(self):
        assert normalize_department("general physician") == "general"
        assert normalize_department("physician") == "general"
        assert normalize_department("gp") == "general"

    def test_synonym_cardiology(self):
        assert normalize_department("cardiologist") == "cardiology"
        assert normalize_department("heart") == "cardiology"

    def test_synonym_ortho(self):
        assert normalize_department("orthopedic") == "ortho"
        assert normalize_department("orthopedics") == "ortho"
        assert normalize_department("bone") == "ortho"
        assert normalize_department("joint") == "ortho"

    def test_synonym_pediatrics(self):
        assert normalize_department("pediatric") == "pediatrics"
        assert normalize_department("paediatrics") == "pediatrics"
        assert normalize_department("child") == "pediatrics"

    def test_synonym_dermatology(self):
        assert normalize_department("dermatologist") == "dermatology"
        assert normalize_department("skin") == "dermatology"

    def test_case_insensitive(self):
        assert normalize_department("General Physician") == "general"
        assert normalize_department("CARDIOLOGY") == "cardiology"
        assert normalize_department("Ortho") == "ortho"

    def test_leading_trailing_whitespace(self):
        assert normalize_department("  general  ") == "general"

    def test_unknown_passthrough(self):
        # Unrecognised values pass through unchanged so the caller can decide
        # what to do (e.g. log a warning and prompt the patient again).
        assert normalize_department("neurology") == "neurology"

    def test_empty_string_passthrough(self):
        assert normalize_department("") == ""
