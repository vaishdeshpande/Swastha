"""PII scrubbing helpers — applied before writing conversation logs to Supabase."""

import re


def scrub_pii(text: str) -> str:
    """Redact phone numbers, Aadhaar, and dates-of-birth from a text string."""
    # 10-digit Indian mobile numbers (standalone, not part of larger number)
    text = re.sub(r'\b\d{10}\b', '[PHONE]', text)
    # Aadhaar: 12-digit, with optional spaces or hyphens between groups of 4
    text = re.sub(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[AADHAAR]', text)
    # Date of birth patterns: DD/MM/YY, DD-MM-YYYY, D/M/YY, etc.
    text = re.sub(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', '[DOB]', text)
    return text
